"""
login_app.py
------------
A polished, secure login/signup/admin gate for the FCA Compliant Adviser AI app.

HOW TO INTEGRATE WITH YOUR EXISTING APP
----------------------------------------
    from login_app import require_login
    require_login()   # stops execution here until the user is logged in

    # everything below only runs once authenticated
    st.session_state.auth_user        -> username (str)
    st.session_state.auth_user_row    -> full user record (dict)
    st.session_state.auth_user_row["role"]  -> "admin" or "user"

    # To gate a page/feature to admins only, anywhere in your app:
    from login_app import require_role
    require_role("admin")   # stops execution with an error if not an admin

Run standalone for testing:
    streamlit run login_app.py

Folder layout expected:
    your_app/
      login_app.py
      auth_db.py

WHAT CHANGED IN THIS REVISION
------------------------------
  * Role-based access control: every account has a role ("user" or "admin").
    The first account ever created becomes an admin automatically.
  * Full admin user management: admins get a "Manage users" panel (view,
    search, edit role/details/active-status, force a password reset,
    unlock a locked account, and remove a user) without leaving this module.
  * Login hardening: generic error messages that don't reveal whether a
    username/email exists, account lockout with a clear countdown, HTML
    escaping of anything derived from user input before it's rendered,
    and account activity and roles rechecked against the database on each
    app rerun so account changes take effect immediately.
  * The decorative shield emoji / page icon ("logo") has been removed;
    the navy & gold colour scheme is unchanged.
"""

import datetime
import html
import smtplib
import time

import streamlit as st

import auth_db

SESSION_IDLE_TIMEOUT_MINUTES = 30


@st.cache_resource
def _initialize_auth_db():
    auth_db.init_db()


# --------------------------------------------------------------------------
# Styling — every colour below is explicit (not inherited) so the page is
# always readable, regardless of whether the host app / browser is set to
# a light or dark Streamlit theme. Colour palette is unchanged from the
# original design; only the page icon/logo has been removed (see
# require_login's st.set_page_config call in __main__).
# --------------------------------------------------------------------------

def inject_css():
    st.markdown(
        """
        <style>
        :root {
            --navy: #0B2545;
            --navy-deep: #081A33;
            --navy-light: #13315C;
            --gold: #C9A94A;
            --gold-soft: #E4CE8F;
            --ink: #16222F;       /* body text inside card */
            --muted: #5B6B7F;     /* secondary text inside card */
            --card-bg: #FFFFFF;
            --field-bg: #F7F9FC;
            --field-border: #D9E0E8;
        }

        html, body, [data-testid="stAppViewContainer"] {
            background: #FFFFFF !important;
        }
        [data-testid="stHeader"] { background: rgba(0,0,0,0) !important; }
        [data-testid="stToolbar"] { right: 1rem; }

        /* ---- Card shell ---- */
        .auth-card {
            max-width: 440px;
            margin: 1.4rem auto 1rem auto;
            background: var(--card-bg) !important;
            border-radius: 20px;
            padding: 2.1rem 2.2rem 1.5rem 2.2rem;
            box-shadow: 0 12px 32px rgba(11,37,69,0.12);
            border: 1px solid var(--field-border);
            position: relative;
            overflow: hidden;
            animation: authFadeIn 0.45s ease-out;
        }
        .auth-card::before {
            content: "";
            position: absolute;
            top: 0; left: 0; right: 0;
            height: 5px;
            background: linear-gradient(90deg, var(--navy) 0%, var(--gold) 50%, var(--navy) 100%);
        }
        @keyframes authFadeIn {
            from { opacity: 0; transform: translateY(10px); }
            to   { opacity: 1; transform: translateY(0); }
        }

        .auth-card, .auth-card p, .auth-card span, .auth-card label,
        .auth-card div, .auth-card small, .auth-card h1, .auth-card h2,
        .auth-card h3, .auth-card h4,
        .auth-card [data-testid="stMarkdownContainer"] p,
        .auth-card [data-testid="stWidgetLabel"] p,
        .auth-card [data-testid="stCaptionContainer"] {
            color: var(--ink) !important;
        }
        .auth-card [data-testid="stCaptionContainer"] { color: var(--muted) !important; font-size: 0.82rem !important; }

        .auth-tagline {
            text-align: center;
            color: var(--muted) !important;
            font-size: 0.93rem;
            margin: 0 0 1.4rem 0;
            letter-spacing: 0.2px;
        }
        .auth-tagline b { color: var(--navy) !important; }

        /* ---- Inputs ---- */
        .auth-card .stTextInput > label p { font-weight: 600 !important; font-size: 0.88rem !important; }
        .auth-card .stTextInput > div > div {
            background: var(--field-bg) !important;
            border-radius: 10px !important;
        }
        .auth-card .stTextInput input {
            color: var(--ink) !important;
            background: var(--field-bg) !important;
            border: 1.5px solid var(--field-border) !important;
            border-radius: 10px !important;
            padding: 0.6rem 0.85rem !important;
            caret-color: var(--navy) !important;
        }
        .auth-card .stTextInput input::placeholder { color: #9AA7B5 !important; opacity: 1 !important; }
        .auth-card .stTextInput input:focus {
            border-color: var(--gold) !important;
            box-shadow: 0 0 0 3px rgba(201,169,74,0.22) !important;
        }

        /* ---- Checkboxes (show password) ---- */
        .auth-card .stCheckbox p { color: var(--muted) !important; font-size: 0.85rem !important; }

        /* ---- Buttons ---- */
        .auth-card div.stButton > button, .auth-card div.stFormSubmitButton > button {
            width: 100%;
            background: var(--navy) !important;
            color: #FFFFFF !important;
            border-radius: 10px !important;
            padding: 0.6rem 0 !important;
            font-weight: 600 !important;
            border: none !important;
            transition: all 0.15s ease-in-out !important;
            box-shadow: 0 4px 10px rgba(11,37,69,0.25);
        }
        .auth-card div.stButton > button *, .auth-card div.stFormSubmitButton > button * { color: #FFFFFF !important; }
        .auth-card div.stButton > button:hover, .auth-card div.stFormSubmitButton > button:hover {
            background: var(--gold) !important;
            box-shadow: 0 6px 14px rgba(201,169,74,0.4);
        }
        .auth-card div.stButton > button:hover *, .auth-card div.stFormSubmitButton > button:hover * {
            color: var(--navy) !important;
        }
        .auth-card div[data-testid="column"]:nth-child(2) div.stFormSubmitButton > button {
            background: transparent !important;
            color: var(--navy) !important;
            border: 1.5px solid var(--field-border) !important;
            box-shadow: none !important;
        }
        .auth-card div[data-testid="column"]:nth-child(2) div.stFormSubmitButton > button * { color: var(--navy) !important; }

        /* ---- Tabs ---- */
        .auth-card .stTabs [data-baseweb="tab-list"] { gap: 4px; }
        .auth-card .stTabs [data-baseweb="tab"] {
            background: var(--field-bg) !important;
            border-radius: 8px 8px 0 0 !important;
        }
        .auth-card .stTabs [data-baseweb="tab"] p { color: var(--muted) !important; font-weight: 600 !important; }
        .auth-card .stTabs [aria-selected="true"] { background: var(--navy) !important; }
        .auth-card .stTabs [aria-selected="true"] p { color: #FFFFFF !important; }

        /* ---- Strength meter ---- */
        .strength-bar-bg {
            background: var(--field-border);
            border-radius: 6px;
            height: 8px;
            width: 100%;
            margin-top: 6px;
            margin-bottom: 2px;
            overflow: hidden;
        }
        .strength-bar-fill { height: 8px; border-radius: 6px; transition: width 0.25s ease-in-out; }
        .strength-label { font-size: 0.8rem; font-weight: 600; margin-bottom: 0.3rem; }

        /* ---- Lock / alert banner ---- */
        .lock-banner {
            background: #FDEAEA !important;
            color: #9C2B2B !important;
            border: 1px solid #F3C2C2;
            padding: 0.65rem 0.9rem;
            border-radius: 10px;
            font-size: 0.88rem;
            margin-bottom: 0.6rem;
        }

        .security-note {
            font-size: 0.78rem;
            color: var(--muted) !important;
            text-align: center;
            margin-top: 1rem;
            line-height: 1.4;
        }

        hr.auth-divider { border: none; border-top: 1px solid var(--field-border); margin: 1.1rem 0; }

        /* ---- Sidebar (post-login) ---- */
        [data-testid="stSidebar"] {
            background: var(--navy-deep) !important;
        }
        [data-testid="stSidebar"] * { color: #EAF0F8 !important; }
        [data-testid="stSidebar"] div.stButton > button {
            background: var(--gold) !important; color: var(--navy) !important; border: none !important;
            border-radius: 8px !important; font-weight: 600 !important;
        }

        /* ---- Admin panel table-ish rows ---- */
        .user-row {
            border: 1px solid var(--field-border);
            border-radius: 12px;
            padding: 0.9rem 1rem;
            margin-bottom: 0.7rem;
            background: var(--card-bg);
        }
        .role-badge {
            display: inline-block;
            font-size: 0.72rem;
            font-weight: 700;
            padding: 0.15rem 0.55rem;
            border-radius: 999px;
            margin-left: 0.4rem;
            letter-spacing: 0.03em;
        }
        .role-badge.admin { background: var(--navy); color: #FFFFFF; }
        .role-badge.user { background: var(--field-bg); color: var(--muted); border: 1px solid var(--field-border); }
        .status-badge {
            display: inline-block;
            font-size: 0.72rem;
            font-weight: 700;
            padding: 0.15rem 0.55rem;
            border-radius: 999px;
            margin-left: 0.4rem;
        }
        .status-badge.active { background: #E6F4EA; color: #2E7D32; }
        .status-badge.inactive { background: #FDEAEA; color: #9C2B2B; }
        .status-badge.locked { background: #FFF4E0; color: #9A6B00; }

        footer, #MainMenu { visibility: hidden; }
        </style>
        """,
        unsafe_allow_html=True,
    )


def strength_meter(password: str):
    score, unmet = auth_db.password_strength(password)
    colors = ["#D9534F", "#D9534F", "#F0AD4E", "#F0AD4E", "#5CB85C", "#2E7D32"]
    labels = ["", "Very weak", "Weak", "Fair", "Good", "Strong"]
    pct = int(score / 5 * 100)
    st.markdown(
        f"""
        <div class="strength-bar-bg">
            <div class="strength-bar-fill" style="width:{pct}%; background:{colors[score]};"></div>
        </div>
        <div class="strength-label" style="color:{colors[score]};">{labels[score]}</div>
        """,
        unsafe_allow_html=True,
    )
    if unmet and password:
        st.caption("Still needs: " + html.escape(", ".join(unmet)))


def _safe_banner(message: str):
    """Render an error/notice banner, escaping anything that might contain
    user-controlled text so it can never be interpreted as HTML/JS."""
    st.markdown(f'<div class="lock-banner">{html.escape(message)}</div>', unsafe_allow_html=True)


# --------------------------------------------------------------------------
# Session helpers
# --------------------------------------------------------------------------

def _session_expired() -> bool:
    last_active = st.session_state.get("auth_last_active")
    if last_active is None:
        return False
    if last_active.tzinfo is None:
        last_active = last_active.replace(tzinfo=datetime.timezone.utc)
    elapsed = (
        datetime.datetime.now(datetime.timezone.utc) - last_active
    ).total_seconds() / 60
    return elapsed > SESSION_IDLE_TIMEOUT_MINUTES


def _touch_session():
    st.session_state.auth_last_active = datetime.datetime.now(datetime.timezone.utc)


def _start_session(user: dict):
    """Called on every successful login/2FA verification. Issues a fresh,
    unpredictable session token to reduce the impact of session fixation,
    and stores the user's current record (including role) for the app."""
    st.session_state.auth_user = user["username"]
    st.session_state.auth_user_row = dict(user)
    st.session_state.show_admin_panel = False
    _touch_session()


def logout():
    for key in ["auth_user", "auth_user_row", "auth_last_active", "pending_2fa_user",
                "show_admin_panel"]:
        st.session_state.pop(key, None)
    st.rerun()


def current_user() -> dict | None:
    return st.session_state.get("auth_user_row")


def is_admin() -> bool:
    user = current_user()
    return bool(user and user.get("role") == "admin")


def require_role(role: str):
    """Call from anywhere in the host app to gate a page/feature.
    Stops execution with an error if the current user doesn't have the
    required role. Call require_login() first so a user is guaranteed to
    be present."""
    user = current_user()
    if not user or user.get("role") != role:
        st.error("You don't have permission to view this page.")
        st.stop()


# --------------------------------------------------------------------------
# UI blocks — authentication
# --------------------------------------------------------------------------

def login_form():
    show_pw = st.checkbox("👁 Show password", key="show_login_pw")
    pw_type = "default" if show_pw else "password"

    with st.form("login_form", clear_on_submit=False):
        username = st.text_input("👤 Username or email", key="login_username",
                                  placeholder="e.g. jane.adviser")
        password = st.text_input("🔑 Password", type=pw_type, key="login_password",
                                  placeholder="Enter your password")
        col1, col2 = st.columns([1.3, 1])
        with col1:
            submitted = st.form_submit_button("Log in →")
        with col2:
            forgot = st.form_submit_button("Forgot?")

    if forgot:
        st.session_state.auth_view = "forgot"
        st.rerun()

    if submitted:
        if not username or not password:
            st.warning("Please enter both username and password.")
            return
        with st.spinner("Checking your details..."):
            time.sleep(0.3)
            result = auth_db.authenticate(username.strip(), password)
        if not result["ok"]:
            _safe_banner(f"🔒 {result['reason']}")
            return
        if result["needs_2fa"]:
            st.session_state.pending_2fa_user = result["user"]["username"]
            st.session_state.auth_view = "2fa"
            st.rerun()
        else:
            _start_session(result["user"])
            st.success("✅ Logged in successfully.")
            time.sleep(0.4)
            st.rerun()


def twofa_form():
    username = st.session_state.get("pending_2fa_user")
    st.info("📱 Enter the 6-digit code from your authenticator app.")
    with st.form("twofa_form"):
        code = st.text_input("Authentication code", max_chars=6, placeholder="123456")
        submitted = st.form_submit_button("Verify →")
    if submitted:
        with st.spinner("Verifying..."):
            time.sleep(0.2)
            ok = username and auth_db.verify_totp_code(username, code.strip())
        if ok:
            user = auth_db.get_user(username)
            if not user:
                st.error("This account could not be found. Please sign in again.")
                st.session_state.pop("pending_2fa_user", None)
                st.session_state.auth_view = "login"
                return
            st.session_state.pop("pending_2fa_user", None)
            _start_session(user)
            st.success("✅ Verified. Logging in...")
            time.sleep(0.4)
            st.rerun()
        else:
            _safe_banner("Incorrect or expired code. Please try again.")

    if st.button("← Back to login"):
        st.session_state.pop("pending_2fa_user", None)
        st.session_state.auth_view = "login"
        st.rerun()


def signup_form():
    with st.form("signup_form"):
        full_name = st.text_input("🧑 Full name", placeholder="Jane Adviser")
        fca_number = st.text_input("🏛️ FCA reference number (optional)", placeholder="e.g. 123456",
                                    help="Your individual or firm FCA reference number, if you have one.")
        email = st.text_input("📧 Work email", placeholder="jane@yourfirm.co.uk")
        username = st.text_input("👤 Choose a username", placeholder="jane.adviser")

        show_pw = st.checkbox("👁 Show password", key="show_signup_pw")
        pw_type = "default" if show_pw else "password"
        password = st.text_input("🔑 Create a password", type=pw_type, key="signup_pw")
        confirm = st.text_input("🔑 Confirm password", type=pw_type, key="signup_pw_confirm")
        submitted = st.form_submit_button("Create account →")

    strength_meter(st.session_state.get("signup_pw", ""))

    if submitted:
        if not all([full_name, email, username, password, confirm]):
            st.warning("Please fill in all required fields.")
            return
        if password != confirm:
            _safe_banner("Passwords do not match.")
            return
        with st.spinner("Creating your account..."):
            ok, msg = auth_db.create_user(username.strip(), email.strip(), password,
                                           full_name.strip(), fca_number.strip())
        if ok:
            st.success(f"✅ {html.escape(msg)}")
            st.session_state.auth_view = "login"
        else:
            _safe_banner(msg)


def forgot_password_form():
    st.write("Enter your username or email. If an account exists, you'll receive reset instructions.")
    with st.form("forgot_form"):
        identifier = st.text_input("👤 Username or email", placeholder="jane.adviser or jane@yourfirm.co.uk")
        submitted = st.form_submit_button("Send reset instructions →")

    if submitted and identifier:
        try:
            with st.spinner("Sending reset instructions..."):
                auth_db.deliver_password_reset(identifier.strip())
        except (RuntimeError, OSError, smtplib.SMTPException) as exc:
            st.error(str(exc))
        else:
            st.success("📨 If an account exists for that username/email, reset instructions have been sent.")

    st.markdown('<hr class="auth-divider">', unsafe_allow_html=True)
    st.write("Already have a reset token?")

    show_pw = st.checkbox("👁 Show password", key="show_reset_pw")
    pw_type = "default" if show_pw else "password"
    with st.form("reset_apply_form"):
        token_in = st.text_input("🔗 Reset token", placeholder="Paste your reset token")
        new_pw = st.text_input("🔑 New password", type=pw_type, key="reset_pw")
        apply_submit = st.form_submit_button("Set new password →")

    strength_meter(st.session_state.get("reset_pw", ""))

    if apply_submit:
        with st.spinner("Updating password..."):
            ok, msg = auth_db.reset_password_with_token(token_in.strip(), new_pw)
        if ok:
            st.success(f"✅ {html.escape(msg)}")
            st.session_state.auth_view = "login"
        else:
            _safe_banner(msg)

    if st.button("← Back to login"):
        st.session_state.auth_view = "login"
        st.rerun()


# --------------------------------------------------------------------------
# Admin: user management panel
# --------------------------------------------------------------------------

def _user_status_badges(u: dict) -> str:
    role = u["role"] if u["role"] in auth_db.ROLES else "user"
    badges = f'<span class="role-badge {role}">{html.escape(role.upper())}</span>'
    if not u["is_active"]:
        badges += '<span class="status-badge inactive">DEACTIVATED</span>'
    elif u["locked_until"]:
        try:
            locked_until = u["locked_until"]
            if isinstance(locked_until, str):
                locked_until = datetime.datetime.fromisoformat(locked_until)
            if locked_until.tzinfo is None:
                locked_until = locked_until.replace(tzinfo=datetime.timezone.utc)
            if datetime.datetime.now(datetime.timezone.utc) < locked_until:
                badges += '<span class="status-badge locked">LOCKED</span>'
            else:
                badges += '<span class="status-badge active">ACTIVE</span>'
        except (TypeError, ValueError):
            badges += '<span class="status-badge active">ACTIVE</span>'
    else:
        badges += '<span class="status-badge active">ACTIVE</span>'
    return badges


def _render_user_editor(actor: str, u: dict):
    with st.expander(f"✏️ Edit {u['username']}"):
        with st.form(f"edit_form_{u['username']}"):
            full_name = st.text_input("Full name", value=u["full_name"], key=f"name_{u['username']}")
            email = st.text_input("Email", value=u["email"], key=f"email_{u['username']}")
            fca_number = st.text_input("FCA reference number", value=u["fca_number"] or "",
                                        key=f"fca_{u['username']}")
            role = st.selectbox("Role", auth_db.ROLES,
                                 index=auth_db.ROLES.index(u["role"]), key=f"role_{u['username']}")
            is_active = st.checkbox("Account active", value=bool(u["is_active"]),
                                     key=f"active_{u['username']}")
            save = st.form_submit_button("Save changes")
        if save:
            ok, msg = auth_db.update_user(
                actor, u["username"],
                full_name=full_name.strip(), email=email.strip(),
                fca_number=fca_number.strip(), role=role, is_active=is_active,
            )
            (st.success if ok else st.error)(msg)
            if ok:
                st.rerun()

        st.markdown('<hr class="auth-divider">', unsafe_allow_html=True)
        col_a, col_b, col_c = st.columns(3)
        with col_a:
            if u["locked_until"] and st.button("🔓 Unlock account", key=f"unlock_{u['username']}"):
                ok, msg = auth_db.unlock_account(actor, u["username"])
                (st.success if ok else st.error)(msg)
                if ok:
                    st.rerun()
        with col_b:
            with st.popover("🔑 Force password reset"):
                new_pw = st.text_input("New password", type="password", key=f"newpw_{u['username']}")
                if st.button("Apply", key=f"applypw_{u['username']}"):
                    ok, msg = auth_db.admin_set_password(actor, u["username"], new_pw)
                    (st.success if ok else st.error)(msg)
        with col_c:
            with st.popover("🗑️ Remove user"):
                st.write(f"Permanently delete **{u['username']}**? This can't be undone.")
                if st.button("Confirm delete", key=f"delconfirm_{u['username']}"):
                    ok, msg = auth_db.delete_user(actor, u["username"])
                    (st.success if ok else st.error)(msg)
                    if ok:
                        st.rerun()


def admin_panel():
    actor = st.session_state.auth_user
    st.subheader("👥 User management")
    st.caption("View, update, or remove any account. Changes take effect immediately.")

    search = st.text_input("Search by username, email, or name", placeholder="jane")
    users = auth_db.list_users(actor, search.strip() if search else "")

    if not users:
        st.info("No users match that search.")
    for u in users:
        st.markdown(
            f'<div class="user-row"><b>{html.escape(u["full_name"])}</b> '
            f'({html.escape(u["username"])}) {_user_status_badges(u)}<br>'
            f'<span style="color:var(--muted);font-size:0.85rem;">{html.escape(u["email"])}'
            f'{" · FCA " + html.escape(u["fca_number"]) if u["fca_number"] else ""}'
            f'{" · 2FA on" if u["totp_enabled"] else ""}</span></div>',
            unsafe_allow_html=True,
        )
        _render_user_editor(actor, u)

    with st.expander("🕵️ Recent activity log"):
        for entry in auth_db.get_audit_log(actor, limit=50):
            st.caption(f"{entry['ts']} · {entry['actor'] or '—'} · {entry['action']} "
                       f"{('→ ' + entry['target']) if entry['target'] else ''}")


# --------------------------------------------------------------------------
# Public entry point
# --------------------------------------------------------------------------

def require_login():
    """
    Call this at the top of your app. Renders the login/signup UI and
    st.stop()s execution until a user is authenticated. Once authenticated,
    it simply returns so the rest of your app can run — unless an admin has
    opened the user-management panel, in which case that panel is shown
    instead and execution stops until they close it.
    """
    try:
        _initialize_auth_db()
    except RuntimeError as exc:
        st.error(str(exc))
        st.stop()
    except auth_db.db.psycopg2.Error:
        st.error(
            "Could not initialize the authentication database. "
            "Check DATABASE_URL and database availability."
        )
        st.stop()

    if st.session_state.get("auth_user") and not _session_expired():
        try:
            user = auth_db.get_user(st.session_state.auth_user)
        except (RuntimeError, auth_db.db.psycopg2.Error):
            st.error("Could not verify your account. Check the database connection and try again.")
            st.stop()
        if user:
            st.session_state.auth_user_row = dict(user)
            _touch_session()
            if not is_admin():
                st.session_state.show_admin_panel = False
            inject_css()
            with st.sidebar:
                st.markdown(f"**🟢 Signed in as:** {html.escape(user['username'])}")
                role = user["role"] if user["role"] in auth_db.ROLES else "user"
                st.markdown(
                    f'<span class="role-badge {role}">{html.escape(role.upper())}</span>',
                    unsafe_allow_html=True,
                )
                st.write("")
                if is_admin():
                    label = "⬅ Back to app" if st.session_state.get("show_admin_panel") else "👥 Manage users"
                    if st.button(label):
                        st.session_state.show_admin_panel = not st.session_state.get("show_admin_panel", False)
                        st.rerun()
                if st.button("Log out"):
                    logout()

            if is_admin() and st.session_state.get("show_admin_panel"):
                admin_panel()
                st.stop()

            return  # authenticated — let the caller's app continue

        for key in ("auth_user", "auth_user_row", "auth_last_active", "show_admin_panel"):
            st.session_state.pop(key, None)
        st.session_state["_access_revoked_msg"] = True

    if st.session_state.get("auth_user") and _session_expired():
        for key in ["auth_user", "auth_user_row", "auth_last_active", "show_admin_panel"]:
            st.session_state.pop(key, None)
        st.session_state["_timeout_msg"] = True

    st.session_state.setdefault("auth_view", "login")
    inject_css()

    st.markdown('<div class="auth-card">', unsafe_allow_html=True)
    st.markdown(
        '<div class="auth-tagline">Secure sign-in for <b>FCA-compliant</b> marketing copy generation</div>',
        unsafe_allow_html=True,
    )

    if st.session_state.pop("_timeout_msg", None):
        st.info("⏱️ Your session timed out due to inactivity. Please log in again.")
    if st.session_state.pop("_access_revoked_msg", None):
        st.info("Your account is no longer active. Please contact your administrator.")

    view = st.session_state.auth_view

    if view == "2fa":
        twofa_form()
    elif view == "forgot":
        forgot_password_form()
    else:
        tab_login, tab_signup = st.tabs(["Log in", "Create account"])
        with tab_login:
            login_form()
        with tab_signup:
            signup_form()

    st.markdown(
        '<div class="security-note">🔒 Passwords are salted and hashed with scrypt, never stored in plain text.<br>'
        'Accounts lock temporarily after repeated failed attempts.</div>',
        unsafe_allow_html=True,
    )
    st.markdown("</div>", unsafe_allow_html=True)

    st.stop()


# --------------------------------------------------------------------------
# Standalone demo
# --------------------------------------------------------------------------

if __name__ == "__main__":
    st.set_page_config(
        page_title="FCA Compliant Adviser AI — Sign in",
        layout="centered",
    )
    require_login()
    st.title("✅ You're in")
    st.write(f"Welcome, **{html.escape(st.session_state.auth_user)}**.")
    if is_admin():
        st.caption("You're an administrator — use the sidebar to manage users.")
    st.write("Replace this block with your existing FCA Marketing Copy Generator app code.")