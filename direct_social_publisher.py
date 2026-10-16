"""
Direct Social Publisher for Streamlit
------------------------------------
Uses official Meta Graph API (Instagram + Facebook Pages) and LinkedIn REST APIs.

This starter publishes posts immediately. It does NOT implement OAuth login or token
refresh; configure valid tokens in Streamlit secrets. For production multi-user apps,
implement OAuth and encrypted per-user token storage.

Image input: public HTTPS URLs. Instagram and Facebook fetch media from URLs; LinkedIn
downloads the URL server-side and uploads the bytes to LinkedIn.
"""

from __future__ import annotations

import json
import re
from typing import Any
from urllib.parse import quote

import requests
import streamlit as st


def _secrets() -> dict[str, Any]:
    return dict(st.secrets) if hasattr(st, "secrets") else {}


def _required_secret(name: str) -> str:
    value = _secrets().get(name, "")
    if not value:
        raise RuntimeError(
            f"Missing secret {name}. Add it to .streamlit/secrets.toml; see README.md."
        )
    return str(value)


def _http_error(response: requests.Response) -> None:
    try:
        body = response.json()
    except Exception:
        body = response.text[:1500]
    if not response.ok:
        raise RuntimeError(f"HTTP {response.status_code}: {body}")


def _meta_version() -> str:
    return str(_secrets().get("META_GRAPH_VERSION", "v26.0"))


def _meta_post(path: str, token: str, data: dict[str, Any]) -> dict[str, Any]:
    url = f"https://graph.facebook.com/{_meta_version()}/{path.lstrip('/')}"
    payload = dict(data)
    payload["access_token"] = token
    r = requests.post(url, data=payload, timeout=90)
    _http_error(r)
    result = r.json()
    if "error" in result:
        raise RuntimeError(result["error"].get("message", str(result["error"])))
    return result


def _meta_get(path: str, token: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
    url = f"https://graph.facebook.com/{_meta_version()}/{path.lstrip('/')}"
    payload = dict(params or {})
    payload["access_token"] = token
    r = requests.get(url, params=payload, timeout=30)
    _http_error(r)
    result = r.json()
    if "error" in result:
        raise RuntimeError(result["error"].get("message", str(result["error"])))
    return result


def publish_instagram(image_urls: list[str], caption: str) -> dict[str, Any]:
    """Publish one image or a 2-10 image carousel to an Instagram professional account."""
    token = _required_secret("META_ACCESS_TOKEN")
    ig_user_id = _required_secret("INSTAGRAM_USER_ID")

    if not 1 <= len(image_urls) <= 10:
        raise ValueError("Instagram supports 1-10 images in this module.")
    if len(image_urls) == 1:
        container = _meta_post(
            f"{ig_user_id}/media",
            token,
            {"image_url": image_urls[0], "caption": caption},
        )
    else:
        child_ids: list[str] = []
        for url in image_urls:
            child = _meta_post(
                f"{ig_user_id}/media",
                token,
                {"image_url": url, "is_carousel_item": "true"},
            )
            child_ids.append(str(child["id"]))

        container = _meta_post(
            f"{ig_user_id}/media",
            token,
            {
                "media_type": "CAROUSEL",
                "children": ",".join(child_ids),
                "caption": caption,
            },
        )

    creation_id = str(container["id"])

    # Poll container status briefly so publishing doesn't race media processing.
    status = ""
    for _ in range(12):
        info = _meta_get(creation_id, token, {"fields": "status_code,status"})
        status = str(info.get("status_code", ""))
        if status == "FINISHED":
            break
        if status == "ERROR":
            raise RuntimeError(f"Instagram media processing failed: {info}")
        import time
        time.sleep(5)

    if status and status not in ("FINISHED", "PUBLISHED"):
        raise RuntimeError(
            f"Instagram media is not ready yet (status={status}). "
            "Wait and retry publishing after checking the container."
        )

    return _meta_post(
        f"{ig_user_id}/media_publish",
        token,
        {"creation_id": creation_id},
    )


def publish_facebook_page(image_urls: list[str], caption: str) -> dict[str, Any]:
    """Publish a single photo or multi-photo post to a Facebook Page."""
    token = _required_secret("FACEBOOK_PAGE_ACCESS_TOKEN")
    page_id = _required_secret("FACEBOOK_PAGE_ID")

    if not 1 <= len(image_urls) <= 10:
        raise ValueError("This module supports 1-10 images for Facebook Page posts.")

    if len(image_urls) == 1:
        return _meta_post(
            f"{page_id}/photos",
            token,
            {"url": image_urls[0], "caption": caption},
        )

    photo_ids: list[str] = []
    for url in image_urls:
        photo = _meta_post(
            f"{page_id}/photos",
            token,
            {"url": url, "published": "false"},
        )
        photo_ids.append(str(photo["id"]))

    data: dict[str, Any] = {"message": caption}
    for i, photo_id in enumerate(photo_ids):
        data[f"attached_media[{i}]"] = json.dumps({"media_fbid": photo_id})
    return _meta_post(f"{page_id}/feed", token, data)


def _linkedin_headers(token: str) -> dict[str, str]:
    version = str(_secrets().get("LINKEDIN_VERSION", "202608"))
    return {
        "Authorization": f"Bearer {token}",
        "LinkedIn-Version": version,
        "X-Restli-Protocol-Version": "2.0.0",
        "Content-Type": "application/json",
    }


def _linkedin_upload_image(token: str, author_urn: str, image_url: str) -> str:
    """Download public image URL, initialize LinkedIn image upload, then PUT bytes."""
    headers = _linkedin_headers(token)
    img = requests.get(image_url, timeout=60)
    _http_error(img)
    content_type = img.headers.get("Content-Type", "").split(";")[0].strip().lower()
    if content_type not in ("image/jpeg", "image/png", "image/gif"):
        raise RuntimeError(
            f"LinkedIn image URL returned Content-Type {content_type!r}. "
            "Use a direct JPG, PNG, or GIF URL."
        )

    init = requests.post(
        "https://api.linkedin.com/rest/images?action=initializeUpload",
        headers=headers,
        json={"initializeUploadRequest": {"owner": author_urn}},
        timeout=45,
    )
    _http_error(init)
    value = init.json().get("value", {})
    upload_url = value.get("uploadUrl")
    image_urn = value.get("image")
    if not upload_url or not image_urn:
        raise RuntimeError(f"LinkedIn did not return uploadUrl/image: {init.text[:1000]}")

    # LinkedIn's upload URL is pre-signed; do not attach the bearer token.
    put = requests.put(
        upload_url,
        data=img.content,
        headers={"Content-Type": content_type},
        timeout=120,
    )
    _http_error(put)
    return str(image_urn)


def publish_linkedin(image_urls: list[str], caption: str) -> dict[str, Any]:
    """
    Publish a LinkedIn image post or multi-image post.
    Requires a valid author URN such as urn:li:person:123 or urn:li:organization:123.
    """
    token = _required_secret("LINKEDIN_ACCESS_TOKEN")
    author = _required_secret("LINKEDIN_AUTHOR_URN")

    if not 1 <= len(image_urls) <= 20:
        raise ValueError("This module supports 1-20 images for LinkedIn.")
    headers = _linkedin_headers(token)

    image_urns = [_linkedin_upload_image(token, author, url) for url in image_urls]
    if len(image_urns) == 1:
        media_content: dict[str, Any] = {
            "media": image_urns[0],
            "title": {"text": "Image post"},
        }
        content: dict[str, Any] = {"media": media_content}
    else:
        content = {
            "multiImage": {
                "images": [
                    {"id": urn, "altText": f"Carousel image {i + 1}"}
                    for i, urn in enumerate(image_urns)
                ]
            }
        }

    payload = {
        "author": author,
        "commentary": caption,
        "visibility": "PUBLIC",
        "distribution": {
            "feedDistribution": "MAIN_FEED",
            "targetEntities": [],
            "thirdPartyDistributionChannels": [],
        },
        "content": content,
        "lifecycleState": "PUBLISHED",
        "isReshareDisabledByAuthor": False,
    }

    r = requests.post(
        "https://api.linkedin.com/rest/posts",
        headers=headers,
        json=payload,
        timeout=90,
    )
    _http_error(r)
    # Posts API commonly returns the created post ID in the x-restli-id header.
    return {"id": r.headers.get("x-restli-id", ""), "status_code": r.status_code, "body": r.text[:1000]}


def render_direct_social_publisher() -> None:
    """Embed this UI in an existing Streamlit app by calling this function."""
    st.header("Direct Social Publisher")
    st.caption("Publish to Instagram, Facebook Pages, or LinkedIn using their official APIs.")

    platform = st.selectbox(
        "Platform",
        ["Instagram", "Facebook Page", "LinkedIn"],
        key="direct_social_platform",
    )
    caption = st.text_area("Caption / post text", height=150, key="direct_social_caption")
    count = st.number_input(
        "Number of images",
        min_value=1,
        max_value=20,
        value=3,
        step=1,
        key="direct_social_image_count",
        help="Instagram and Facebook Page support up to 10 images here; LinkedIn up to 20.",
    )

    urls: list[str] = []
    st.markdown("#### Carousel images")
    st.caption("Enter direct, public HTTPS image URLs in the order you want them displayed.")
    for i in range(int(count)):
        url = st.text_input(
            f"Image {i + 1} URL",
            key=f"direct_social_image_url_{i}",
            placeholder="https://your-domain.com/images/slide-1.jpg",
        ).strip()
        if url:
            urls.append(url)
            with st.expander(f"Preview image {i + 1}"):
                st.image(url, use_container_width=True)

    if platform in ("Instagram", "Facebook Page") and int(count) > 10:
        st.warning("This module supports at most 10 images for Instagram and Facebook Page posts.")
    if platform == "LinkedIn" and int(count) > 20:
        st.warning("This module supports at most 20 images for LinkedIn.")

    st.warning(
        "Publishing is live. Test with a private/test page or account first. "
        "Posts cannot always be undone through the API."
    )
    confirmed = st.checkbox("I confirm these images and this caption are ready to publish.")
    if st.button("Publish now", type="primary", use_container_width=True):
        if not caption.strip():
            st.error("Enter a caption or post text.")
            return
        if len(urls) != int(count):
            st.error(f"Enter all {int(count)} image URLs.")
            return
        if not all(u.startswith("https://") for u in urls):
            st.error("Every image URL must start with https://.")
            return
        if not confirmed:
            st.error("Confirm before publishing.")
            return

        try:
            with st.spinner(f"Publishing to {platform}…"):
                if platform == "Instagram":
                    result = publish_instagram(urls, caption.strip())
                elif platform == "Facebook Page":
                    result = publish_facebook_page(urls, caption.strip())
                else:
                    result = publish_linkedin(urls, caption.strip())
            st.success(f"API accepted the publishing request for {platform}.")
            st.json(result)
            st.info("Verify the final post on the destination account. API acceptance is not a guarantee of final visibility.")
        except Exception as exc:
            st.error(f"Publishing failed: {exc}")
            st.caption(
                "Check tokens, app permissions/review, account eligibility, API version, "
                "image URL accessibility, file type, and platform restrictions."
            )
