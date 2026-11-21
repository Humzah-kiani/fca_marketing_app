"""
login_app.py
------------
A polished, secure login/signup gate for the FCA Compliant Adviser AI app.

HOW TO INTEGRATE WITH YOUR EXISTING APP
----------------------------------------
    from login_app import require_login
    require_login()   # stops execution here until the user is logged in

    # everything below only runs once authenticated
    st.session_state.auth_user        -> username (str)
    st.session_state.auth_user_row    -> full user record (dict)

Run standalone for testing:
    streamlit run login_app.py

Folder layout expected:
    your_app/
      login_app.py
      auth_db.py
"""

import time
import datetime
import smtplib
import streamlit as st
import auth_db

SESSION_IDLE_TIMEOUT_MINUTES = 30


@st.cache_resource
def _initialize_auth_db():
    auth_db.init_db()


# --------------------------------------------------------------------------
# Styling — every colour below is explicit (not inherited) so the page is
# always readable, regardless of whether the host app / browser is set to
# a light or dark Streamlit theme. This is what fixes the "can't see the
# text" problem: nothing in the card relies on a theme default.
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

        /* Force every piece of text inside the card to a readable, explicit
           colour -- this overrides Streamlit's dark-theme defaults, which is
           what was making text invisible before. */
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
        /* Secondary ("Forgot password?" / "back") button look */
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

        footer, #MainMenu { visibility: hidden; }
        </style>
        """,
        unsafe_allow_html=True,
    )


def strength_meter(password: str):
    score, unmet = auth_db.password_strength(password)
    pct = int(score / 5 * 100)
    colors = ["#D9534F", "#D9534F", "#F0AD4E", "#F0AD4E", "#5CB85C", "#2E7D32"]
    labels = ["", "Very weak", "Weak", "Fair", "Good", "Strong"]
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
        st.caption("Still needs: " + ", ".join(unmet))


# --------------------------------------------------------------------------
# Session helpers
# --------------------------------------------------------------------------

def _session_expired() -> bool:
    last_active = st.session_state.get("auth_last_active")
    if last_active is None:
        return False
    elapsed = (datetime.datetime.utcnow() - last_active).total_seconds() / 60
    return elapsed > SESSION_IDLE_TIMEOUT_MINUTES


def _touch_session():
    st.session_state.auth_last_active = datetime.datetime.utcnow()


def logout():
    for key in ["auth_user", "auth_user_row", "auth_last_active", "pending_2fa_user"]:
        st.session_state.pop(key, None)
    st.rerun()


# --------------------------------------------------------------------------
# UI blocks
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
            result = auth_db.authenticate(username, password)
        if not result["ok"]:
            st.markdown(f'<div class="lock-banner">🔒 {result["reason"]}</div>', unsafe_allow_html=True)
            return
        if result["needs_2fa"]:
            st.session_state.pending_2fa_user = result["user"]["username"]
            st.session_state.auth_view = "2fa"
            st.rerun()
        else:
            st.session_state.auth_user = result["user"]["username"]
            st.session_state.auth_user_row = dict(result["user"])
            _touch_session()
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
            st.session_state.auth_user = username
            st.session_state.auth_user_row = dict(user)
            st.session_state.pop("pending_2fa_user", None)
            _touch_session()
            st.success("✅ Verified. Logging in...")
            time.sleep(0.4)
            st.rerun()
        else:
            st.markdown('<div class="lock-banner">Incorrect or expired code. Please try again.</div>',
                        unsafe_allow_html=True)

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
            st.markdown('<div class="lock-banner">Passwords do not match.</div>', unsafe_allow_html=True)
            return
        with st.spinner("Creating your account..."):
            ok, msg = auth_db.create_user(username, email, password, full_name, fca_number)
        if ok:
            st.success(f"✅ {msg}")
            st.session_state.auth_view = "login"
        else:
            st.markdown(f'<div class="lock-banner">{msg}</div>', unsafe_allow_html=True)


def forgot_password_form():
    st.write("Enter your username or email. If an account exists, you'll receive reset instructions.")
    with st.form("forgot_form"):
        identifier = st.text_input("👤 Username or email", placeholder="jane.adviser or jane@yourfirm.co.uk")
        submitted = st.form_submit_button("Send reset instructions →")

    if submitted and identifier:
        try:
            with st.spinner("Sending reset instructions..."):
                auth_db.deliver_password_reset(identifier)
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
            st.success(f"✅ {msg}")
            st.session_state.auth_view = "login"
        else:
            st.markdown(f'<div class="lock-banner">{msg}</div>', unsafe_allow_html=True)

    if st.button("← Back to login"):
        st.session_state.auth_view = "login"
        st.rerun()


# --------------------------------------------------------------------------
# Public entry point
# --------------------------------------------------------------------------

def require_login():
    """
    Call this at the top of your app. Renders the login/signup UI and
    st.stop()s execution until a user is authenticated. Once authenticated,
    it simply returns so the rest of your app can run.
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
        _touch_session()
        with st.sidebar:
            st.markdown(f"**🟢 Signed in as:** {st.session_state.auth_user}")
            if st.button("Log out"):
                logout()
        return  # authenticated — let the caller's app continue

    if st.session_state.get("auth_user") and _session_expired():
        for key in ["auth_user", "auth_user_row", "auth_last_active"]:
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
        page_icon="🛡️",
        layout="centered",
    )
    require_login()
    st.title("✅ You're in")
    st.write(f"Welcome, **{st.session_state.auth_user}**.")
    st.write("Replace this block with your existing FCA Marketing Copy Generator app code.")