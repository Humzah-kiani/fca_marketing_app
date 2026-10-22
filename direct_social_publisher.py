"""
Direct Social Publisher for Streamlit  (v2: scheduling + timestamps + video/story/carousel)
-------------------------------------------------------------------------------------------
Official Meta Graph API (Instagram + Facebook Pages) and LinkedIn REST APIs.

What's new vs v1
  * Automatic posting: every post goes into a SQLite queue and a background scheduler
    publishes it at the scheduled time ("Publish now" = scheduled for this second).
  * Time stamping: created / scheduled / started / published timestamps are stored in
    UTC for every job and shown in the timezone you pick. Failed attempts are logged.
  * Formats: Post (image or video), Carousel (images; Instagram also allows videos),
    Story (image or video).  Reels are used for Instagram feed videos.
  * Multi-platform: tick several platforms and one job per platform is queued.

Embed in your existing Streamlit app
  from direct_social_publisher import render_direct_social_publisher, configure
  # optional, once at startup (otherwise st.secrets / env vars / ./social_queue.db are used):
  configure(db_path="/data/social_queue.db")
  # anywhere in your app (a page, tab, or sidebar section):
  render_direct_social_publisher(owner=st.session_state.get("user_id"))
  The module never calls st.set_page_config and prefixes every widget key, so it will not
  clash with your app. The scheduler thread is started lazily and shared by all sessions.

Run standalone
  streamlit run direct_social_publisher.py          # UI + in-process scheduler thread
  python direct_social_publisher.py --worker        # optional: always-on headless worker

IMPORTANT - scheduler lifetime
  The in-app scheduler thread only runs while the Streamlit server process is running.
  For reliable 24/7 auto-posting, deploy on a machine that stays on, or run the
  --worker command (e.g. systemd / Docker / pm2) next to the UI. Both share the same
  SQLite file (SCHEDULER_DB secret, default ./social_queue.db).

Secrets (.streamlit/secrets.toml)
  META_ACCESS_TOKEN, INSTAGRAM_USER_ID,
  FACEBOOK_PAGE_ACCESS_TOKEN, FACEBOOK_PAGE_ID,
  LINKEDIN_ACCESS_TOKEN, LINKEDIN_AUTHOR_URN,
  optional: META_GRAPH_VERSION, LINKEDIN_VERSION, SCHEDULER_DB

Media input: direct public HTTPS URLs (.jpg/.png/.gif for images, .mp4/.mov for video).

Platform support matrix
  Instagram : post (image/video->Reel), carousel (2-10 images/videos), story (image/video)
  Facebook  : post (image/video), carousel (2-10 images, no videos), story (image/video)
  LinkedIn  : post (image/video), carousel (2-20 images)   -- LinkedIn has no stories API
"""

from __future__ import annotations

import json
import os
import sqlite3
import sys
import threading
from contextlib import contextmanager
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

import requests

try:
    import streamlit as st
except Exception:  # allows --worker mode without streamlit installed
    st = None  # type: ignore

# --------------------------------------------------------------------------------------
# Constants
# --------------------------------------------------------------------------------------
FORMATS = ["Post", "Carousel", "Story"]
PLATFORMS = ["Instagram", "Facebook Page", "LinkedIn"]
VIDEO_EXT = (".mp4", ".mov", ".m4v")
MAX_ATTEMPTS = 3
RETRY_DELAY_MIN = 5
POLL_SECONDS = 10
MAX_VIDEO_BYTES = 500 * 1024 * 1024  # LinkedIn path downloads video into memory

# --------------------------------------------------------------------------------------
# Time helpers (everything stored in UTC, ISO-8601)
# --------------------------------------------------------------------------------------
def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat(timespec="seconds")


def parse_iso(s: str | None) -> datetime | None:
    return datetime.fromisoformat(s) if s else None


def fmt_local(s: str | None, tz: str) -> str:
    dt = parse_iso(s)
    return dt.astimezone(ZoneInfo(tz)).strftime("%Y-%m-%d %H:%M:%S %Z") if dt else ""


# --------------------------------------------------------------------------------------
# Secrets (works inside Streamlit, in a background thread, and in --worker mode)
# --------------------------------------------------------------------------------------
_SECRETS_CACHE: dict[str, Any] = {}
_DB_PATH_OVERRIDE: str | None = None


def configure(secrets: dict[str, Any] | None = None, db_path: str | None = None) -> None:
    """Optional. Override secrets (dict) and/or the SQLite path from your host app."""
    global _DB_PATH_OVERRIDE
    _SECRETS_CACHE.clear()
    if secrets:
        _SECRETS_CACHE.update(secrets)
    if db_path:
        _DB_PATH_OVERRIDE = db_path


def _load_secrets() -> dict[str, Any]:
    if _SECRETS_CACHE.get("_loaded") or any(k in _SECRETS_CACHE for k in (
            "META_ACCESS_TOKEN", "FACEBOOK_PAGE_ACCESS_TOKEN", "LINKEDIN_ACCESS_TOKEN")):
        return _SECRETS_CACHE
    data: dict[str, Any] = dict(_SECRETS_CACHE)
    try:
        if st is not None and hasattr(st, "secrets"):
            data = dict(st.secrets)
    except Exception:
        data = {}
    if not data:
        p = Path(".streamlit/secrets.toml")
        if p.exists():
            import tomllib
            data = tomllib.loads(p.read_text(encoding="utf-8"))
    for k, v in os.environ.items():  # env vars override / fill in
        if k.isupper() and k not in data:
            data[k] = v
    _SECRETS_CACHE.update(data)
    _SECRETS_CACHE["_loaded"] = True
    return _SECRETS_CACHE


def _required_secret(name: str) -> str:
    value = _load_secrets().get(name, "")
    if not value:
        raise RuntimeError(f"Missing secret {name}. Add it to .streamlit/secrets.toml.")
    return str(value)


# --------------------------------------------------------------------------------------
# HTTP helpers
# --------------------------------------------------------------------------------------
def _http_error(response: requests.Response) -> None:
    if response.ok:
        return
    try:
        body: Any = response.json()
    except Exception:
        body = response.text[:1500]
    raise RuntimeError(f"HTTP {response.status_code}: {body}")


def _meta_version() -> str:
    return str(_load_secrets().get("META_GRAPH_VERSION", "v26.0"))


def _meta_call(method: str, path: str, token: str, data: dict[str, Any] | None = None,
               timeout: int = 120) -> dict[str, Any]:
    url = f"https://graph.facebook.com/{_meta_version()}/{path.lstrip('/')}"
    payload = dict(data or {})
    payload["access_token"] = token
    if method == "GET":
        r = requests.get(url, params=payload, timeout=timeout)
    else:
        r = requests.post(url, data=payload, timeout=timeout)
    _http_error(r)
    result = r.json()
    if "error" in result:
        raise RuntimeError(result["error"].get("message", str(result["error"])))
    return result


def _meta_post(path: str, token: str, data: dict[str, Any]) -> dict[str, Any]:
    return _meta_call("POST", path, token, data)


def _meta_get(path: str, token: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
    return _meta_call("GET", path, token, params, timeout=30)


def is_video_url(url: str) -> bool:
    return urlparse(url).path.lower().endswith(VIDEO_EXT)


def media_item(url: str, kind: str | None = None) -> dict[str, str]:
    return {"url": url, "type": kind or ("video" if is_video_url(url) else "image")}


# --------------------------------------------------------------------------------------
# Validation (shared by UI and scheduler)
# --------------------------------------------------------------------------------------
def validate_job(platform: str, fmt: str, media: list[dict[str, str]], caption: str) -> None:
    n = len(media)
    videos = sum(m["type"] == "video" for m in media)
    if n == 0:
        raise ValueError("Add at least one media URL.")
    if not all(m["url"].startswith("https://") for m in media):
        raise ValueError("Every media URL must start with https://")

    if fmt in ("Post", "Story") and n != 1:
        raise ValueError(f"{fmt} needs exactly 1 media item.")
    if fmt == "Carousel":
        if not 2 <= n <= (20 if platform == "LinkedIn" else 10):
            raise ValueError(f"{platform} carousel needs 2-{20 if platform == 'LinkedIn' else 10} items.")
        if platform != "Instagram" and videos:
            raise ValueError(f"{platform} carousels support images only.")

    if platform == "LinkedIn" and fmt == "Story":
        raise ValueError("LinkedIn has no Stories API.")
    if fmt != "Story" and not caption.strip():
        raise ValueError("Enter a caption / post text.")


# --------------------------------------------------------------------------------------
# Instagram
# --------------------------------------------------------------------------------------
def _ig_wait(container_id: str, token: str, timeout_s: int) -> None:
    deadline = time.time() + timeout_s
    status = ""
    while time.time() < deadline:
        info = _meta_get(container_id, token, {"fields": "status_code,status"})
        status = str(info.get("status_code", ""))
        if status == "FINISHED":
            return
        if status in ("ERROR", "EXPIRED"):
            raise RuntimeError(f"Instagram media processing failed: {info}")
        time.sleep(5)
    raise RuntimeError(f"Instagram media not ready after {timeout_s}s (status={status}).")


def publish_instagram(fmt: str, media: list[dict[str, str]], caption: str) -> dict[str, Any]:
    token = _required_secret("META_ACCESS_TOKEN")
    ig = _required_secret("INSTAGRAM_USER_ID")
    has_video = any(m["type"] == "video" for m in media)
    wait = 600 if has_video else 90

    if fmt == "Post":
        m = media[0]
        if m["type"] == "video":
            data = {"media_type": "REELS", "video_url": m["url"], "caption": caption,
                    "share_to_feed": "true"}
        else:
            data = {"image_url": m["url"], "caption": caption}
        container_id = str(_meta_post(f"{ig}/media", token, data)["id"])

    elif fmt == "Story":
        m = media[0]
        key = "video_url" if m["type"] == "video" else "image_url"
        container_id = str(_meta_post(f"{ig}/media", token,
                                      {"media_type": "STORIES", key: m["url"]})["id"])

    else:  # Carousel
        children: list[str] = []
        for m in media:
            if m["type"] == "video":
                d = {"media_type": "VIDEO", "video_url": m["url"], "is_carousel_item": "true"}
            else:
                d = {"image_url": m["url"], "is_carousel_item": "true"}
            children.append(str(_meta_post(f"{ig}/media", token, d)["id"]))
        for cid in children:
            _ig_wait(cid, token, wait)
        container_id = str(_meta_post(f"{ig}/media", token, {
            "media_type": "CAROUSEL", "children": ",".join(children), "caption": caption,
        })["id"])

    _ig_wait(container_id, token, wait)
    return _meta_post(f"{ig}/media_publish", token, {"creation_id": container_id})


# --------------------------------------------------------------------------------------
# Facebook Page
# --------------------------------------------------------------------------------------
def _fb_video_story(page_id: str, token: str, video_url: str) -> dict[str, Any]:
    start = _meta_post(f"{page_id}/video_stories", token, {"upload_phase": "start"})
    video_id, upload_url = str(start["video_id"]), start["upload_url"]
    up = requests.post(upload_url, headers={"Authorization": f"OAuth {token}",
                                            "file_url": video_url}, timeout=300)
    _http_error(up)
    return _meta_post(f"{page_id}/video_stories", token,
                      {"upload_phase": "finish", "video_id": video_id})


def publish_facebook_page(fmt: str, media: list[dict[str, str]], caption: str) -> dict[str, Any]:
    token = _required_secret("FACEBOOK_PAGE_ACCESS_TOKEN")
    page = _required_secret("FACEBOOK_PAGE_ID")

    if fmt == "Post":
        m = media[0]
        if m["type"] == "video":
            return _meta_post(f"{page}/videos", token,
                              {"file_url": m["url"], "description": caption})
        return _meta_post(f"{page}/photos", token, {"url": m["url"], "caption": caption})

    if fmt == "Story":
        m = media[0]
        if m["type"] == "video":
            return _fb_video_story(page, token, m["url"])
        photo = _meta_post(f"{page}/photos", token, {"url": m["url"], "published": "false"})
        return _meta_post(f"{page}/photo_stories", token, {"photo_id": str(photo["id"])})

    photo_ids = [str(_meta_post(f"{page}/photos", token,
                                {"url": m["url"], "published": "false"})["id"]) for m in media]
    data: dict[str, Any] = {"message": caption}
    for i, pid in enumerate(photo_ids):
        data[f"attached_media[{i}]"] = json.dumps({"media_fbid": pid})
    return _meta_post(f"{page}/feed", token, data)


# --------------------------------------------------------------------------------------
# LinkedIn
# --------------------------------------------------------------------------------------
def _li_headers(token: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {token}",
        "LinkedIn-Version": str(_load_secrets().get("LINKEDIN_VERSION", "202608")),
        "X-Restli-Protocol-Version": "2.0.0",
        "Content-Type": "application/json",
    }


def _download(url: str, max_bytes: int) -> tuple[bytes, str]:
    with requests.get(url, timeout=120, stream=True) as r:
        _http_error(r)
        ctype = r.headers.get("Content-Type", "").split(";")[0].strip().lower()
        buf = bytearray()
        for chunk in r.iter_content(1024 * 1024):
            buf.extend(chunk)
            if len(buf) > max_bytes:
                raise RuntimeError(f"Media too large (> {max_bytes // 1048576} MB).")
    return bytes(buf), ctype


def _li_upload_image(token: str, author: str, url: str) -> str:
    content, ctype = _download(url, 20 * 1024 * 1024)
    if ctype not in ("image/jpeg", "image/png", "image/gif"):
        raise RuntimeError(f"LinkedIn image URL returned Content-Type {ctype!r}.")
    init = requests.post("https://api.linkedin.com/rest/images?action=initializeUpload",
                         headers=_li_headers(token),
                         json={"initializeUploadRequest": {"owner": author}}, timeout=45)
    _http_error(init)
    value = init.json().get("value", {})
    if not value.get("uploadUrl") or not value.get("image"):
        raise RuntimeError(f"LinkedIn init failed: {init.text[:800]}")
    put = requests.put(value["uploadUrl"], data=content,
                       headers={"Content-Type": ctype}, timeout=120)
    _http_error(put)
    return str(value["image"])


def _li_upload_video(token: str, author: str, url: str) -> str:
    content, _ = _download(url, MAX_VIDEO_BYTES)
    init = requests.post("https://api.linkedin.com/rest/videos?action=initializeUpload",
                         headers=_li_headers(token), timeout=60,
                         json={"initializeUploadRequest": {
                             "owner": author, "fileSizeBytes": len(content),
                             "uploadCaptions": False, "uploadThumbnail": False}})
    _http_error(init)
    value = init.json().get("value", {})
    urn, upload_token = value.get("video"), value.get("uploadToken", "")
    parts: list[str] = []
    for ins in value.get("uploadInstructions", []):
        chunk = content[int(ins["firstByte"]): int(ins["lastByte"]) + 1]
        put = requests.put(ins["uploadUrl"], data=chunk, timeout=300,
                           headers={"Content-Type": "application/octet-stream"})
        _http_error(put)
        parts.append(put.headers.get("ETag", "").strip('"'))
    fin = requests.post("https://api.linkedin.com/rest/videos?action=finalizeUpload",
                        headers=_li_headers(token), timeout=60,
                        json={"finalizeUploadRequest": {
                            "video": urn, "uploadToken": upload_token,
                            "uploadedPartIds": parts}})
    _http_error(fin)
    return str(urn)


def publish_linkedin(fmt: str, media: list[dict[str, str]], caption: str) -> dict[str, Any]:
    token = _required_secret("LINKEDIN_ACCESS_TOKEN")
    author = _required_secret("LINKEDIN_AUTHOR_URN")

    if fmt == "Post":
        m = media[0]
        urn = (_li_upload_video if m["type"] == "video" else _li_upload_image)(token, author, m["url"])
        content: dict[str, Any] = {"media": {"id": urn, "title": caption[:60] or "Post"}}
    else:  # Carousel (multiImage)
        urns = [_li_upload_image(token, author, m["url"]) for m in media]
        content = {"multiImage": {"images": [{"id": u, "altText": f"Image {i + 1}"}
                                             for i, u in enumerate(urns)]}}

    payload = {
        "author": author, "commentary": caption, "visibility": "PUBLIC",
        "distribution": {"feedDistribution": "MAIN_FEED", "targetEntities": [],
                         "thirdPartyDistributionChannels": []},
        "content": content, "lifecycleState": "PUBLISHED", "isReshareDisabledByAuthor": False,
    }
    r = requests.post("https://api.linkedin.com/rest/posts", headers=_li_headers(token),
                      json=payload, timeout=90)
    _http_error(r)
    return {"id": r.headers.get("x-restli-id", ""), "status_code": r.status_code}


def publish_now(platform: str, fmt: str, media: list[dict[str, str]], caption: str) -> dict[str, Any]:
    validate_job(platform, fmt, media, caption)
    zkey = str(_load_secrets().get("ZERNIO_API_KEY", ""))
    if zkey:
        from zernio_backend import publish_zernio
        return publish_zernio(zkey, platform, fmt, media, caption, _load_secrets())
    if platform == "Instagram":
        return publish_instagram(fmt, media, caption)
    if platform == "Facebook Page":
        return publish_facebook_page(fmt, media, caption)
    return publish_linkedin(fmt, media, caption)


# --------------------------------------------------------------------------------------
# Queue (SQLite) with timestamps
# --------------------------------------------------------------------------------------
_DB_LOCK = threading.Lock()
_WAKE = threading.Event()


def _db() -> sqlite3.Connection:
    path = _DB_PATH_OVERRIDE or str(_load_secrets().get("SCHEDULER_DB", "social_queue.db"))
    conn = sqlite3.connect(path, timeout=30, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("""CREATE TABLE IF NOT EXISTS jobs(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        platform TEXT, format TEXT, caption TEXT, media_json TEXT,
        status TEXT,               -- pending | running | published | failed | cancelled
        created_at TEXT, scheduled_at TEXT, started_at TEXT, published_at TEXT,
        attempts INTEGER DEFAULT 0, result_json TEXT, error TEXT, log TEXT DEFAULT '',
        owner TEXT)""")
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(jobs)")}
    if "owner" not in cols:  # migrate DBs created by v2.0
        conn.execute("ALTER TABLE jobs ADD COLUMN owner TEXT")
    return conn


@contextmanager
def _conn():
    c = _db()
    try:
        yield c
        c.commit()
    except Exception:
        c.rollback()
        raise
    finally:
        c.close()


def enqueue(platform: str, fmt: str, caption: str, media: list[dict[str, str]],
            scheduled_at: datetime, owner: str | None = None) -> int:
    """Public API: queue a job from your own code. Returns the job id."""
    validate_job(platform, fmt, media, caption)
    with _DB_LOCK, _conn() as c:
        cur = c.execute(
            "INSERT INTO jobs(platform,format,caption,media_json,status,created_at,scheduled_at,owner)"
            " VALUES(?,?,?,?,?,?,?,?)",
            (platform, fmt, caption, json.dumps(media), "pending", iso(utcnow()),
             iso(scheduled_at), None if owner is None else str(owner)))
        job_id = int(cur.lastrowid)
    _WAKE.set()
    return job_id


def list_jobs(limit: int = 200, owner: str | None = None) -> list[dict[str, Any]]:
    with _DB_LOCK, _conn() as c:
        if owner is None:
            cur = c.execute("SELECT * FROM jobs ORDER BY scheduled_at DESC, id DESC LIMIT ?", (limit,))
        else:
            cur = c.execute("SELECT * FROM jobs WHERE owner=? ORDER BY scheduled_at DESC, id DESC "
                            "LIMIT ?", (str(owner), limit))
        return [dict(r) for r in cur]


def cancel_job(job_id: int) -> None:
    with _DB_LOCK, _conn() as c:
        c.execute("UPDATE jobs SET status='cancelled' WHERE id=? AND status='pending'", (job_id,))


def retry_job(job_id: int) -> None:
    with _DB_LOCK, _conn() as c:
        c.execute("UPDATE jobs SET status='pending', scheduled_at=?, attempts=0, error=NULL "
                  "WHERE id=? AND status IN ('failed','cancelled')", (iso(utcnow()), job_id))
    _WAKE.set()


def _claim_due_job() -> dict[str, Any] | None:
    with _DB_LOCK:
        c = _db()
        try:
            c.execute("BEGIN IMMEDIATE")
            row = c.execute("SELECT * FROM jobs WHERE status='pending' AND scheduled_at<=? "
                            "ORDER BY scheduled_at LIMIT 1", (iso(utcnow()),)).fetchone()
            if not row:
                c.execute("COMMIT")
                return None
            c.execute("UPDATE jobs SET status='running', started_at=?, attempts=attempts+1 WHERE id=?",
                      (iso(utcnow()), row["id"]))
            c.execute("COMMIT")
            return dict(row)
        finally:
            c.close()


def _finish_job(job: dict[str, Any], result: dict[str, Any] | None, error: str | None) -> None:
    now = iso(utcnow())
    attempts = int(job["attempts"]) + 1  # claim incremented it in DB
    with _DB_LOCK, _conn() as c:
        if error is None:
            c.execute("UPDATE jobs SET status='published', published_at=?, result_json=?, error=NULL,"
                      " log=log||? WHERE id=?",
                      (now, json.dumps(result), f"[{now}] published\n", job["id"]))
        elif attempts < MAX_ATTEMPTS:
            nxt = iso(utcnow() + timedelta(minutes=RETRY_DELAY_MIN))
            c.execute("UPDATE jobs SET status='pending', scheduled_at=?, error=?, log=log||? WHERE id=?",
                      (nxt, error, f"[{now}] attempt {attempts} failed: {error}\n", job["id"]))
        else:
            c.execute("UPDATE jobs SET status='failed', error=?, log=log||? WHERE id=?",
                      (error, f"[{now}] attempt {attempts} failed (giving up): {error}\n", job["id"]))


def process_due_jobs() -> int:
    done = 0
    while (job := _claim_due_job()) is not None:
        try:
            result = publish_now(job["platform"], job["format"],
                                 json.loads(job["media_json"]), job["caption"])
            _finish_job(job, result, None)
        except Exception as exc:
            _finish_job(job, None, str(exc)[:1500])
        done += 1
    return done


def _recover_interrupted() -> None:
    """Jobs left 'running' by a crashed process are NOT retried (could double-post)."""
    with _DB_LOCK, _conn() as c:
        c.execute("UPDATE jobs SET status='failed', error='Interrupted while running; check the "
                  "platform before retrying to avoid a duplicate post.' WHERE status='running'")


def _scheduler_loop() -> None:
    while True:
        try:
            process_due_jobs()
        except Exception as exc:  # never let the thread die
            print("scheduler error:", exc, file=sys.stderr)
        _WAKE.wait(timeout=POLL_SECONDS)
        _WAKE.clear()


_SCHED_LOCK = threading.Lock()
_SCHED: threading.Thread | None = None


def start_scheduler() -> threading.Thread:
    """Idempotent: starts one shared scheduler thread per process (safe on every rerun)."""
    global _SCHED
    with _SCHED_LOCK:
        if _SCHED is not None and _SCHED.is_alive():
            return _SCHED
        _load_secrets()  # snapshot secrets on the calling (Streamlit) thread
        if _SCHED is None:
            _recover_interrupted()
        _SCHED = threading.Thread(target=_scheduler_loop, name="social-scheduler", daemon=True)
        _SCHED.start()
        return _SCHED


# --------------------------------------------------------------------------------------
# Streamlit UI (embeddable)
# --------------------------------------------------------------------------------------
def _common_timezones() -> list[str]:
    return ["UTC", "Asia/Karachi", "Asia/Dubai", "Asia/Kolkata", "Europe/London",
            "Europe/Berlin", "America/New_York", "America/Chicago",
            "America/Los_Angeles", "Australia/Sydney"]


def _queue_view(kp: str, owner: str | None, tz_name: str) -> None:
    jobs = list_jobs(owner=owner)
    if not jobs:
        st.caption("No jobs yet.")
        return
    rows = [{
        "ID": j["id"], "Platform": j["platform"], "Format": j["format"],
        "Status": j["status"], "Tries": j["attempts"],
        "Created": fmt_local(j["created_at"], tz_name),
        "Scheduled": fmt_local(j["scheduled_at"], tz_name),
        "Started": fmt_local(j["started_at"], tz_name),
        "Published": fmt_local(j["published_at"], tz_name),
        "Caption": (j["caption"] or "")[:50],
        "Error": (j["error"] or "")[:120],
    } for j in jobs]
    st.dataframe(rows, use_container_width=True, hide_index=True)

    st.markdown("**Manage a job**")
    m1, m2, m3 = st.columns([2, 1, 1])
    sel = m1.selectbox("Job", [j["id"] for j in jobs], key=f"{kp}_sel")
    m2.button("Cancel pending", key=f"{kp}_cancel", use_container_width=True,
              on_click=cancel_job, args=(int(sel),))
    m3.button("Retry / requeue", key=f"{kp}_retry", use_container_width=True,
              on_click=retry_job, args=(int(sel),))
    job = next(j for j in jobs if j["id"] == sel)
    with st.expander("Timestamped log & API result"):
        st.code(job["log"] or "(no events yet)")
        if job["result_json"]:
            st.json(json.loads(job["result_json"]))


def render_direct_social_publisher(
    key_prefix: str = "dsp",
    owner: str | None = None,
    platforms: list[str] | None = None,
    default_tz: str = "UTC",
    show_header: bool = True,
    auto_refresh_seconds: int = 10,
) -> None:
    """
    Embed in your Streamlit app. Safe to call on every rerun and from several pages.

    key_prefix   : change if you render this component more than once on a page.
    owner        : e.g. the logged-in user id; each user then sees only their own jobs.
    platforms    : restrict the selectable platforms, e.g. ["Instagram", "LinkedIn"].
    default_tz   : initial timezone for scheduling / display.
    show_header  : set False if your app already provides a title.
    auto_refresh_seconds : queue table refresh interval (0 disables; needs Streamlit>=1.37).
    """
    if st is None:
        raise RuntimeError("streamlit is not installed")
    kp = key_prefix
    owner = None if owner is None else str(owner)
    allowed = platforms or PLATFORMS
    ss = st.session_state

    start_scheduler()

    if show_header:
        st.subheader("Direct Social Publisher")
        st.caption("Schedule or instantly publish posts, carousels, videos and stories. "
                   "Times are stored in UTC and shown in your timezone.")

    tzs = _common_timezones()
    if default_tz not in tzs:
        tzs = [default_tz] + tzs
    tz_name = st.selectbox("Timezone", tzs, index=tzs.index(default_tz), key=f"{kp}_tz")

    tab_new, tab_queue = st.tabs(["Compose & schedule", "Queue & timestamps"])

    # ---------------- Compose ----------------
    with tab_new:
        chosen = st.multiselect("Platforms", allowed, default=allowed[:1], key=f"{kp}_platforms")
        fmt = st.radio("Format", FORMATS, horizontal=True, key=f"{kp}_format",
                       help="Post = single image/video. Carousel = multiple items. Story = 24h story.")
        st.text_area("Caption / post text", height=140, key=f"{kp}_caption",
                     help="Ignored by Instagram/Facebook stories.")

        count = int(st.number_input("Number of items", 2, 20, 3, 1, key=f"{kp}_count")) \
            if fmt == "Carousel" else 1

        st.markdown("**Media** — direct public HTTPS URLs (.jpg/.png/.gif, .mp4/.mov), in order.")
        for i in range(count):
            c1, c2 = st.columns([4, 1])
            url = c1.text_input(f"Item {i + 1} URL", key=f"{kp}_url_{i}",
                                placeholder="https://your-domain.com/media/file.mp4").strip()
            if url:
                kind = c2.selectbox("Type", ["image", "video"],
                                    index=1 if is_video_url(url) else 0, key=f"{kp}_kind_{i}")
                with st.expander(f"Preview item {i + 1}"):
                    try:
                        (st.video if kind == "video" else st.image)(url)
                    except Exception:
                        st.caption("Preview unavailable.")

        mode = st.radio("Publish", ["Now (automatic)", "Schedule for later"],
                        horizontal=True, key=f"{kp}_mode")
        if mode.startswith("Schedule"):
            tz = ZoneInfo(tz_name)
            default_dt = datetime.now(tz) + timedelta(hours=1)
            d1, d2 = st.columns(2)
            d1.date_input("Date", default_dt.date(), key=f"{kp}_date")
            d2.time_input("Time", default_dt.time().replace(second=0, microsecond=0),
                          key=f"{kp}_time", step=60)

        st.warning("Publishing is live and often can't be undone through the API. "
                   "Test with a private/test account first.")
        st.checkbox("I confirm the media and caption are ready.", key=f"{kp}_confirm")

        def _submit() -> None:
            """Runs as a button callback, so it may reset widget state safely."""
            msgs: list[tuple[str, str]] = []
            try:
                picked = ss.get(f"{kp}_platforms", [])
                n = int(ss.get(f"{kp}_count", 3)) if ss[f"{kp}_format"] == "Carousel" else 1
                items: list[dict[str, str]] = []
                for i in range(n):
                    u = (ss.get(f"{kp}_url_{i}") or "").strip()
                    if u:
                        items.append(media_item(u, ss.get(f"{kp}_kind_{i}")))
                if not picked:
                    raise ValueError("Pick at least one platform.")
                if len(items) != n:
                    raise ValueError(f"Enter all {n} media URL(s).")
                if not ss.get(f"{kp}_confirm"):
                    raise ValueError("Tick the confirmation box first.")
                when = utcnow()
                if ss[f"{kp}_mode"].startswith("Schedule"):
                    local = datetime.combine(ss[f"{kp}_date"], ss[f"{kp}_time"],
                                             tzinfo=ZoneInfo(ss[f"{kp}_tz"]))
                    when = local.astimezone(timezone.utc)
                    if when <= utcnow():
                        raise ValueError("Scheduled time is in the past.")
                for p in picked:
                    try:
                        jid = enqueue(p, ss[f"{kp}_format"], (ss.get(f"{kp}_caption") or "").strip(),
                                      items, when, owner)
                        msgs.append(("success", f"{p}: queued as job #{jid}, "
                                                f"publishes at {fmt_local(iso(when), ss[f'{kp}_tz'])}."))
                    except Exception as exc:
                        msgs.append(("error", f"{p}: {exc}"))
                ss[f"{kp}_confirm"] = False
            except Exception as exc:
                msgs.append(("error", str(exc)))
            ss[f"{kp}_msgs"] = msgs

        st.button("Queue it", type="primary", use_container_width=True,
                  key=f"{kp}_submit", on_click=_submit)
        for level, text in ss.pop(f"{kp}_msgs", []):
            getattr(st, level)(text)

    # ---------------- Queue ----------------
    with tab_queue:
        view = _queue_view
        if auto_refresh_seconds and hasattr(st, "fragment"):
            view = st.fragment(run_every=auto_refresh_seconds)(_queue_view)
        view(kp, owner, tz_name)
        st.caption("The scheduler runs inside this server process; jobs publish even if you close the tab.")


# --------------------------------------------------------------------------------------
# Entrypoints
# --------------------------------------------------------------------------------------
def run_worker() -> None:
    print("Social worker started. Ctrl+C to stop.")
    _load_secrets()
    _recover_interrupted()
    _scheduler_loop()


if __name__ == "__main__":
    if "--worker" in sys.argv:
        run_worker()
    elif st is not None:
        st.set_page_config(page_title="Direct Social Publisher", layout="centered")
        render_direct_social_publisher()