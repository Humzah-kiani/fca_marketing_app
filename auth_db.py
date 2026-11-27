"""Authentication helpers backed by the application's PostgreSQL database."""

import base64
import hashlib
import hmac
import re
import secrets
import smtplib
import struct
import time
from datetime import datetime, timezone
from email.message import EmailMessage

import psycopg2.extras

import db
from config import (
    AUTH_SMTP_FROM,
    AUTH_SMTP_HOST,
    AUTH_SMTP_PASSWORD,
    AUTH_SMTP_PORT,
    AUTH_SMTP_USERNAME,
)

_SCRYPT_N = 32768
_SCRYPT_R = 8
_SCRYPT_P = 1
_MAX_FAILED_ATTEMPTS = 5
_LOCKOUT_MINUTES = 15
_RESET_TOKEN_MINUTES = 30
ROLES = ("user", "admin")


def init_db():
    """Initialize the shared schema, including the authentication tables."""
    db.init_db()


def password_strength(password):
    checks = [
        (len(password) >= 12, "at least 12 characters"),
        (bool(re.search(r"[a-z]", password)), "a lowercase letter"),
        (bool(re.search(r"[A-Z]", password)), "an uppercase letter"),
        (bool(re.search(r"\d", password)), "a number"),
        (bool(re.search(r"[^A-Za-z0-9]", password)), "a symbol"),
    ]
    score = sum(met for met, _ in checks)
    unmet = [description for met, description in checks if not met]
    return score, unmet


def _hash_password(password):
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(
        password.encode("utf-8"),
        salt=salt,
        n=_SCRYPT_N,
        r=_SCRYPT_R,
        p=_SCRYPT_P,
        dklen=32,
        maxmem=64 * 1024 * 1024,
    )
    return "scrypt${}${}${}${}${}".format(
        _SCRYPT_N,
        _SCRYPT_R,
        _SCRYPT_P,
        salt.hex(),
        digest.hex(),
    )


def _verify_password(password, encoded):
    try:
        algorithm, n, r, p, salt_hex, digest_hex = encoded.split("$")
        if algorithm != "scrypt":
            return False
        expected = bytes.fromhex(digest_hex)
        actual = hashlib.scrypt(
            password.encode("utf-8"),
            salt=bytes.fromhex(salt_hex),
            n=int(n),
            r=int(r),
            p=int(p),
            dklen=len(expected),
            maxmem=64 * 1024 * 1024,
        )
    except (AttributeError, ValueError, TypeError, UnicodeError):
        return False
    return hmac.compare_digest(actual, expected)


def _normalise_identifier(identifier):
    return (identifier or "").strip().lower()


def _user_by_identifier(identifier):
    key = _normalise_identifier(identifier)
    if not key:
        return None
    return db.fetch_one(
        """SELECT id, username, email, full_name, fca_number, password_hash,
                  advisor_id, failed_attempts, locked_until, totp_secret,
                  role, is_active
           FROM auth_users WHERE username = %s OR email = %s""",
        (key, key),
    )


def create_user(username, email, password, full_name, fca_number=""):
    username = _normalise_identifier(username)
    email = _normalise_identifier(email)
    full_name = (full_name or "").strip()
    fca_number = (fca_number or "").strip() or None

    if not re.fullmatch(r"[a-z0-9_.-]{3,32}", username):
        return False, "Username must be 3–32 characters: letters, numbers, dots, dashes, or underscores."
    if len(email) > 254 or not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email):
        return False, "Enter a valid email address."
    if not full_name or len(full_name) > 120:
        return False, "Enter a name of at most 120 characters."
    if len(fca_number or "") > 100:
        return False, "FCA reference number must be at most 100 characters."
    if len(password) > 256:
        return False, "Password must be at most 256 characters."
    _, unmet = password_strength(password)
    if unmet:
        return False, "Password needs " + ", ".join(unmet) + "."

    password_hash = _hash_password(password)
    try:
        with db.get_conn() as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute("LOCK TABLE auth_users IN EXCLUSIVE MODE")
                cur.execute("SELECT COUNT(*) AS user_count FROM auth_users")
                first_user = cur.fetchone()["user_count"] == 0
                cur.execute(
                    """INSERT INTO advisors (name, email) VALUES (%s, %s)
                       ON CONFLICT (email) DO NOTHING RETURNING id""",
                    (full_name, email),
                )
                advisor = cur.fetchone()
                if not advisor:
                    return False, "This email is already linked to an adviser. Please contact your administrator."
                advisor_id = advisor["id"]
                cur.execute(
                    """INSERT INTO auth_users
                           (username, email, full_name, fca_number, password_hash, advisor_id, role)
                       VALUES (%s, %s, %s, %s, %s, %s, %s)""",
                    (
                        username,
                        email,
                        full_name,
                        fca_number,
                        password_hash,
                        advisor_id,
                        "admin" if first_user else "user",
                    ),
                )
    except db.psycopg2.errors.UniqueViolation:
        return False, "That username or email is already registered."
    return True, "Account created. You can now log in."


def authenticate(identifier, password):
    user = _user_by_identifier(identifier)
    if not user or not user["is_active"]:
        return {"ok": False, "reason": "Invalid username or password.", "needs_2fa": False}

    locked_until = user["locked_until"]
    if locked_until and locked_until > datetime.now(timezone.utc):
        return {
            "ok": False,
            "reason": "This account is temporarily locked after repeated failed attempts. Try again later.",
            "needs_2fa": False,
        }

    if len(password) > 256 or not _verify_password(password, user["password_hash"]):
        db.execute(
            """UPDATE auth_users
               SET failed_attempts = failed_attempts + 1,
                   locked_until = CASE
                       WHEN failed_attempts + 1 >= %s
                       THEN now() + (%s * interval '1 minute')
                       ELSE locked_until
                   END
               WHERE id = %s""",
            (_MAX_FAILED_ATTEMPTS, _LOCKOUT_MINUTES, user["id"]),
        )
        return {"ok": False, "reason": "Invalid username or password.", "needs_2fa": False}

    db.execute(
        "UPDATE auth_users SET failed_attempts = 0, locked_until = NULL WHERE id = %s",
        (user["id"],),
    )
    public_user = {
        key: user[key]
        for key in (
            "id", "username", "email", "full_name", "fca_number",
            "advisor_id", "role",
        )
    }
    return {
        "ok": True,
        "reason": "",
        "needs_2fa": bool(user["totp_secret"]),
        "user": public_user,
    }


def get_user(username):
    user = _user_by_identifier(username)
    if not user or not user["is_active"]:
        return None
    return {
        key: user[key]
        for key in (
            "id", "username", "email", "full_name", "fca_number",
            "advisor_id", "role",
        )
    }


def _is_active_admin(cur, identifier):
    key = _normalise_identifier(identifier)
    if not key:
        return None
    cur.execute(
        """SELECT id, username FROM auth_users
           WHERE (username = %s OR email = %s)
             AND role = 'admin' AND is_active = TRUE""",
        (key, key),
    )
    return cur.fetchone()


def _write_audit(cur, actor, action, target):
    cur.execute(
        "INSERT INTO auth_audit_log (actor, action, target) VALUES (%s, %s, %s)",
        (actor, action, target),
    )


def _validate_profile(full_name, email, fca_number):
    full_name = (full_name or "").strip()
    email = _normalise_identifier(email)
    fca_number = (fca_number or "").strip() or None
    if not full_name or len(full_name) > 120:
        return None, "Enter a name of at most 120 characters."
    if len(email) > 254 or not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email):
        return None, "Enter a valid email address."
    if len(fca_number or "") > 100:
        return None, "FCA reference number must be at most 100 characters."
    return (full_name, email, fca_number), None


def list_users(actor, search=""):
    """Return user-management records for an active administrator."""
    if not db.fetch_one(
        """SELECT id FROM auth_users
           WHERE (username = %s OR email = %s)
             AND role = 'admin' AND is_active = TRUE""",
        (_normalise_identifier(actor), _normalise_identifier(actor)),
    ):
        return []
    term = "%" + (search or "").strip() + "%"
    return db.fetch_all(
        """SELECT username, email, full_name, fca_number, role, is_active,
                  locked_until, (totp_secret IS NOT NULL) AS totp_enabled
           FROM auth_users
           WHERE username ILIKE %s OR email ILIKE %s OR full_name ILIKE %s
           ORDER BY username""",
        (term, term, term),
    )


def update_user(actor, username, *, full_name, email, fca_number, role, is_active):
    if role not in ROLES:
        return False, "Select a valid account role."
    profile, error = _validate_profile(full_name, email, fca_number)
    if error:
        return False, error
    full_name, email, fca_number = profile
    actor_key = _normalise_identifier(actor)
    target_key = _normalise_identifier(username)

    try:
        with db.get_conn() as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute("LOCK TABLE auth_users IN EXCLUSIVE MODE")
                admin = _is_active_admin(cur, actor_key)
                if not admin:
                    return False, "Administrator access is required."
                cur.execute(
                    """SELECT id, username, advisor_id, role, is_active
                       FROM auth_users WHERE username = %s FOR UPDATE""",
                    (target_key,),
                )
                target = cur.fetchone()
                if not target:
                    return False, "User not found."
                removes_active_admin = (
                    target["role"] == "admin"
                    and target["is_active"]
                    and (role != "admin" or not is_active)
                )
                if removes_active_admin:
                    cur.execute(
                        """SELECT COUNT(*) AS admin_count FROM auth_users
                           WHERE role = 'admin' AND is_active = TRUE"""
                    )
                    if cur.fetchone()["admin_count"] <= 1:
                        return False, "At least one active administrator must remain."
                if target_key == _normalise_identifier(admin["username"]) and (
                    role != "admin" or not is_active
                ):
                    return False, "You cannot remove your own administrator access."
                cur.execute(
                    "UPDATE advisors SET name = %s, email = %s WHERE id = %s",
                    (full_name, email, target["advisor_id"]),
                )
                cur.execute(
                    """UPDATE auth_users
                       SET email = %s, full_name = %s, fca_number = %s,
                           role = %s, is_active = %s
                       WHERE id = %s""",
                    (email, full_name, fca_number, role, is_active, target["id"]),
                )
                _write_audit(cur, admin["username"], "user_updated", target["username"])
    except db.psycopg2.errors.UniqueViolation:
        return False, "That email address is already in use."
    return True, "User updated."


def unlock_account(actor, username):
    target_key = _normalise_identifier(username)
    with db.get_conn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("LOCK TABLE auth_users IN EXCLUSIVE MODE")
            admin = _is_active_admin(cur, actor)
            if not admin:
                return False, "Administrator access is required."
            cur.execute(
                """UPDATE auth_users
                   SET failed_attempts = 0, locked_until = NULL
                   WHERE username = %s RETURNING username""",
                (target_key,),
            )
            target = cur.fetchone()
            if not target:
                return False, "User not found."
            _write_audit(cur, admin["username"], "account_unlocked", target["username"])
    return True, "Account unlocked."


def admin_set_password(actor, username, new_password):
    if len(new_password) > 256:
        return False, "Password must be at most 256 characters."
    _, unmet = password_strength(new_password)
    if unmet:
        return False, "Password needs " + ", ".join(unmet) + "."
    password_hash = _hash_password(new_password)
    target_key = _normalise_identifier(username)
    with db.get_conn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("LOCK TABLE auth_users IN EXCLUSIVE MODE")
            admin = _is_active_admin(cur, actor)
            if not admin:
                return False, "Administrator access is required."
            cur.execute(
                """UPDATE auth_users
                   SET password_hash = %s, reset_token_hash = NULL,
                       reset_expires_at = NULL, failed_attempts = 0, locked_until = NULL
                   WHERE username = %s RETURNING username""",
                (password_hash, target_key),
            )
            target = cur.fetchone()
            if not target:
                return False, "User not found."
            _write_audit(cur, admin["username"], "password_reset_by_admin", target["username"])
    return True, "Password updated."


def delete_user(actor, username):
    actor_key = _normalise_identifier(actor)
    target_key = _normalise_identifier(username)
    with db.get_conn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("LOCK TABLE auth_users IN EXCLUSIVE MODE")
            admin = _is_active_admin(cur, actor_key)
            if not admin:
                return False, "Administrator access is required."
            if target_key == _normalise_identifier(admin["username"]):
                return False, "You cannot remove your own administrator account."
            cur.execute(
                """SELECT id, username, role, is_active FROM auth_users
                   WHERE username = %s FOR UPDATE""",
                (target_key,),
            )
            target = cur.fetchone()
            if not target:
                return False, "User not found."
            if target["role"] == "admin" and target["is_active"]:
                cur.execute(
                    """SELECT COUNT(*) AS admin_count FROM auth_users
                       WHERE role = 'admin' AND is_active = TRUE"""
                )
                if cur.fetchone()["admin_count"] <= 1:
                    return False, "At least one active administrator must remain."
            cur.execute("DELETE FROM auth_users WHERE id = %s", (target["id"],))
            _write_audit(cur, admin["username"], "user_deleted", target["username"])
    return True, "User removed."


def get_audit_log(actor, limit=50):
    if not db.fetch_one(
        """SELECT id FROM auth_users
           WHERE (username = %s OR email = %s)
             AND role = 'admin' AND is_active = TRUE""",
        (_normalise_identifier(actor), _normalise_identifier(actor)),
    ):
        return []
    limit = max(1, min(int(limit), 200))
    return db.fetch_all(
        """SELECT created_at AS ts, actor, action, target
           FROM auth_audit_log ORDER BY created_at DESC, id DESC LIMIT %s""",
        (limit,),
    )


def _totp_code(secret, counter):
    key = base64.b32decode(secret.upper() + "=" * (-len(secret) % 8))
    digest = hmac.new(key, struct.pack(">Q", counter), hashlib.sha1).digest()
    offset = digest[-1] & 0x0F
    number = struct.unpack(">I", digest[offset:offset + 4])[0] & 0x7FFFFFFF
    return f"{number % 1_000_000:06d}"


def verify_totp_code(username, code):
    if not re.fullmatch(r"\d{6}", code or ""):
        return False
    user = _user_by_identifier(username)
    secret = user and user["totp_secret"]
    if not secret or not user["is_active"]:
        return False
    counter = int(time.time() // 30)
    try:
        return any(
            hmac.compare_digest(_totp_code(secret, counter + offset), code)
            for offset in (-1, 0, 1)
        )
    except (ValueError, TypeError):
        return False


def request_password_reset(identifier):
    user = _user_by_identifier(identifier)
    if not user or not user["is_active"]:
        return None
    token = secrets.token_urlsafe(32)
    token_hash = hashlib.sha256(token.encode("ascii")).hexdigest()
    db.execute(
        """UPDATE auth_users
           SET reset_token_hash = %s,
               reset_expires_at = now() + (%s * interval '1 minute')
           WHERE id = %s""",
        (token_hash, _RESET_TOKEN_MINUTES, user["id"]),
    )
    return token


def deliver_password_reset(identifier):
    if not AUTH_SMTP_HOST or not AUTH_SMTP_FROM:
        raise RuntimeError(
            "Password-reset email is not configured. Please contact your administrator."
        )
    user = _user_by_identifier(identifier)
    if not user:
        return False

    token = request_password_reset(identifier)
    message = EmailMessage()
    message["Subject"] = "FCA Marketing Generator password reset"
    message["From"] = AUTH_SMTP_FROM
    message["To"] = user["email"]
    message.set_content(
        "Use this one-time password reset token in the sign-in page within 30 minutes:\n\n"
        + token
    )

    if AUTH_SMTP_PORT == 465:
        with smtplib.SMTP_SSL(AUTH_SMTP_HOST, AUTH_SMTP_PORT, timeout=10) as smtp:
            if AUTH_SMTP_USERNAME:
                smtp.login(AUTH_SMTP_USERNAME, AUTH_SMTP_PASSWORD)
            smtp.send_message(message)
    else:
        with smtplib.SMTP(AUTH_SMTP_HOST, AUTH_SMTP_PORT, timeout=10) as smtp:
            smtp.starttls()
            if AUTH_SMTP_USERNAME:
                smtp.login(AUTH_SMTP_USERNAME, AUTH_SMTP_PASSWORD)
            smtp.send_message(message)
    return True


def reset_password_with_token(token, new_password):
    if not token or len(token) > 256:
        return False, "Reset token is invalid or expired."
    if len(new_password) > 256:
        return False, "Password must be at most 256 characters."
    _, unmet = password_strength(new_password)
    if unmet:
        return False, "Password needs " + ", ".join(unmet) + "."

    token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
    updated = db.execute(
        """UPDATE auth_users
           SET password_hash = %s,
               reset_token_hash = NULL,
               reset_expires_at = NULL,
               failed_attempts = 0,
               locked_until = NULL
           WHERE reset_token_hash = %s AND reset_expires_at > now()
           RETURNING id""",
        (_hash_password(new_password), token_hash),
        returning=True,
    )
    if not updated:
        return False, "Reset token is invalid or expired."
    return True, "Password updated. You can now log in."
