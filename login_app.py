"""
login_app.py
------------
A polished, secure login/signup gate for the FCA Marketing Copy Generator.

HOW TO INTEGRATE WITH YOUR EXISTING APP
----------------------------------------
Option A (two files, recommended):
    Keep this file as-is. At the very top of YOUR main app file, add:

        from login_app import require_login
        require_login()   # stops execution here until the user is logged in

    Everything below that line in your app only runs once authenticated.
    `st.session_state.auth_user` will hold the logged-in username, and
    `st.session_state.auth_user_row` holds the full user record
    (email, full_name, fca_number, etc.) if you want to personalise output
    or write it into your generation log.

Option B (single file):
    Paste the contents of this file above your app's own code, remove the
    `if __name__ == "__main__":` block at the bottom, and call
    `require_login()` before the rest of your app's logic.

Run standalone for testing:
    streamlit run login_app.py
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
# Styling
# --------------------------------------------------------------------------

def inject_css():
    st.markdown(
        """
        <style>
        :root {
            --navy: #0B2545;
            --navy-light: #13315C;
            --gold: #C9A94A;
            --bg-grad-1: #0B2545;
            --bg-grad-2: #1B4965;
        }

        /* Page background */
        [data-testid="stAppViewContainer"] {
            background: linear-gradient(135deg, var(--bg-grad-1) 0%, var(--bg-grad-2) 100%);
        }
        [data-testid="stHeader"] { background: rgba(0,0,0,0); }

        .auth-card {
            max-width: 440px;
            margin: 2.0rem auto 1rem auto;
            background: rgba(255, 255, 255, 0.97);
            border-radius: 18px;
            padding: 2.2rem 2.3rem 1.6rem 2.3rem;
            box-shadow: 0 20px 45px rgba(0,0,0,0.35);
        }

        .auth-logo {
            text-align: center;
            font-size: 1.6rem;
            font-weight: 700;
            color: var(--navy);
            margin-bottom: 0.1rem;
            letter-spacing: 0.3px;
        }
        .auth-sub {
            text-align: center;
            color: #5b6b7f;
            font-size: 0.92rem;
            margin-bottom: 1.3rem;
        }

        .stTextInput > div > div > input {
            border-radius: 10px !important;
            border: 1.5px solid #d9e0e8 !important;
            padding: 0.55rem 0.8rem !important;
        }
        .stTextInput > div > div > input:focus {
            border-color: var(--gold) !important;
            box-shadow: 0 0 0 2px rgba(201,169,74,0.25) !important;
        }

        div.stButton > button {
            width: 100%;
            background: var(--navy);
            color: white;
            border-radius: 10px;
            padding: 0.55rem 0;
            font-weight: 600;
            border: none;
            transition: all 0.15s ease-in-out;
        }
        div.stButton > button:hover {
            background: var(--gold);
            color: var(--navy);
        }

        .strength-bar-bg {
            background: #e8edf2;
            border-radius: 6px;
            height: 8px;
            width: 100%;
            margin-top: 4px;
            margin-bottom: 2px;
            overflow: hidden;
        }
        .strength-bar-fill {
            height: 8px;
            border-radius: 6px;
            transition: width 0.2s ease-in-out;
        }

        .security-note {
            font-size: 0.78rem;
            color: #7c8a9a;
            text-align: center;
            margin-top: 0.8rem;
        }

        .lock-banner {
            background: #fdeaea;
            color: #9c2b2b;
            border: 1px solid #f3c2c2;
            padding: 0.6rem 0.9rem;
            border-radius: 10px;
            font-size: 0.88rem;
            margin-bottom: 0.8rem;
        }
        </style>
        """,
        unsafe_allow_html=True,
    )


def strength_meter(password: str):
    score, unmet = auth_db.password_strength(password)
    pct = int(score / 5 * 100)
    colors = ["#d9534f", "#d9534f", "#f0ad4e", "#f0ad4e", "#5cb85c", "#2e7d32"]
    labels = ["", "Very weak", "Weak", "Fair", "Good", "Strong"]
    st.markdown(
        f"""
        <div class="strength-bar-bg">
            <div class="strength-bar-fill" style="width:{pct}%; background:{colors[score]};"></div>
        </div>
        <div style="font-size:0.8rem; color:#5b6b7f;">{labels[score]}</div>
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
    with st.form("login_form", clear_on_submit=False):
        username = st.text_input("Username or email", key="login_username")
        password = st.text_input("Password", type="password", key="login_password")
        col1, col2 = st.columns([1, 1])
        with col1:
            submitted = st.form_submit_button("Log in")
        with col2:
            forgot = st.form_submit_button("Forgot password?")

    if forgot:
        st.session_state.auth_view = "forgot"
        st.rerun()

    if submitted:
        if not username or not password:
            st.warning("Please enter both username and password.")
            return
        result = auth_db.authenticate(username, password)
        if not result["ok"]:
            st.error(result["reason"])
            return
        if result["needs_2fa"]:
            st.session_state.pending_2fa_user = result["user"]["username"]
            st.session_state.auth_view = "2fa"
            st.rerun()
        else:
            st.session_state.auth_user = result["user"]["username"]
            st.session_state.auth_user_row = dict(result["user"])
            _touch_session()
            st.success("Logged in successfully.")
            time.sleep(0.4)
            st.rerun()


def twofa_form():
    username = st.session_state.get("pending_2fa_user")
    st.info("Enter the 6-digit code from your authenticator app.")
    with st.form("twofa_form"):
        code = st.text_input("Authentication code", max_chars=6)
        submitted = st.form_submit_button("Verify")
    if submitted:
        if username and auth_db.verify_totp_code(username, code.strip()):
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
            st.success("Verified. Logging in...")
            time.sleep(0.4)
            st.rerun()
        else:
            st.error("Incorrect or expired code. Please try again.")

    if st.button("← Back to login"):
        st.session_state.pop("pending_2fa_user", None)
        st.session_state.auth_view = "login"
        st.rerun()


def signup_form():
    with st.form("signup_form"):
        full_name = st.text_input("Full name")
        fca_number = st.text_input("FCA reference number (optional)")
        email = st.text_input("Work email")
        username = st.text_input("Choose a username")
        password = st.text_input("Create a password", type="password", key="signup_pw")
        confirm = st.text_input("Confirm password", type="password")
        submitted = st.form_submit_button("Create account")

    strength_meter(st.session_state.get("signup_pw", ""))

    if submitted:
        if not all([full_name, email, username, password, confirm]):
            st.warning("Please fill in all required fields.")
            return
        if password != confirm:
            st.error("Passwords do not match.")
            return
        ok, msg = auth_db.create_user(username, email, password, full_name, fca_number)
        if ok:
            st.success(msg)
            st.session_state.auth_view = "login"
        else:
            st.error(msg)


def forgot_password_form():
    st.write("Enter your username or email. If an account exists, you'll receive reset instructions.")
    with st.form("forgot_form"):
        identifier = st.text_input("Username or email")
        submitted = st.form_submit_button("Send reset link")

    if submitted and identifier:
        try:
            auth_db.deliver_password_reset(identifier)
        except (RuntimeError, OSError, smtplib.SMTPException) as exc:
            st.error(f"Could not send reset email: {exc}")
        else:
            st.success(
                "If an account exists for that username/email, reset instructions have been sent."
            )

    st.divider()
    st.write("Already have a reset token?")
    with st.form("reset_apply_form"):
        token_in = st.text_input("Reset token")
        new_pw = st.text_input("New password", type="password", key="reset_pw")
        apply_submit = st.form_submit_button("Set new password")

    strength_meter(st.session_state.get("reset_pw", ""))

    if apply_submit:
        ok, msg = auth_db.reset_password_with_token(token_in.strip(), new_pw)
        (st.success if ok else st.error)(msg)
        if ok:
            st.session_state.auth_view = "login"

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
            st.markdown(f"**Signed in as:** {st.session_state.auth_user}")
            if st.button("Log out"):
                logout()
        return  # authenticated — let the caller's app continue

    if st.session_state.get("auth_user") and _session_expired():
        logout_msg = "Your session timed out due to inactivity. Please log in again."
        for key in ["auth_user", "auth_user_row", "auth_last_active"]:
            st.session_state.pop(key, None)
        st.session_state["_timeout_msg"] = logout_msg

    st.session_state.setdefault("auth_view", "login")

    inject_css()

    st.markdown('<div class="auth-card">', unsafe_allow_html=True)
    st.markdown('<div class="auth-logo">📈 FCA Marketing Copy Generator</div>', unsafe_allow_html=True)
    st.markdown('<div class="auth-sub">Secure adviser sign-in</div>', unsafe_allow_html=True)

    if st.session_state.pop("_timeout_msg", None):
        st.info("Your session timed out due to inactivity. Please log in again.")

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
        '<div class="security-note">🔒 Passwords are salted and hashed with scrypt, never stored in plain text. '
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
        page_title="FCA Marketing Copy Generator — Sign in",
        page_icon="🔒",
        layout="centered",
    )
    require_login()
    st.title("✅ You're in")
    st.write(f"Welcome, **{st.session_state.auth_user}**.")
    st.write("Replace this block with your existing FCA Marketing Copy Generator app code.")
