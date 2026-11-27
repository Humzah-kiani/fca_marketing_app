import auth_db


class FakeCursor:
    def __init__(self, user_count):
        self.user_count = user_count
        self.query = ""
        self.params = ()
        self.executed = []

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def execute(self, query, params=()):
        self.query = query
        self.params = params
        self.executed.append((query, params))

    def fetchone(self):
        if "SELECT COUNT(*) AS user_count" in self.query:
            return {"user_count": self.user_count}
        if "INSERT INTO advisors" in self.query:
            return {"id": 12}
        return None


class FakeConnection:
    def __init__(self, cursor):
        self.fake_cursor = cursor

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def cursor(self, cursor_factory=None):
        return self.fake_cursor


def test_password_hash_is_salted_and_verifies():
    first = auth_db._hash_password("Correct-Horse-42!")
    second = auth_db._hash_password("Correct-Horse-42!")

    assert first != second
    assert auth_db._verify_password("Correct-Horse-42!", first)
    assert not auth_db._verify_password("incorrect", first)
    assert not auth_db._verify_password("anything", "invalid-hash")


def test_password_strength_reports_all_requirements():
    assert auth_db.password_strength("Correct-Horse-42!") == (5, [])
    score, unmet = auth_db.password_strength("weak")
    assert score == 1
    assert len(unmet) == 4


def test_authentication_returns_public_user_and_resets_failed_attempts(monkeypatch):
    user = {
        "id": 7,
        "username": "adviser",
        "email": "adviser@example.com",
        "full_name": "A Adviser",
        "fca_number": None,
        "password_hash": auth_db._hash_password("Correct-Horse-42!"),
        "advisor_id": 12,
        "failed_attempts": 2,
        "locked_until": None,
        "totp_secret": None,
        "role": "admin",
        "is_active": True,
    }
    updates = []
    monkeypatch.setattr(auth_db, "_user_by_identifier", lambda identifier: user)
    monkeypatch.setattr(
        auth_db.db,
        "execute",
        lambda query, params=None, returning=False: updates.append((query, params)),
    )

    result = auth_db.authenticate("adviser", "Correct-Horse-42!")

    assert result["ok"]
    assert result["user"]["advisor_id"] == 12
    assert result["user"]["role"] == "admin"
    assert "password_hash" not in result["user"]
    assert updates[0][1] == (7,)


def test_authentication_records_failed_attempt(monkeypatch):
    user = {
        "id": 7,
        "username": "adviser",
        "password_hash": auth_db._hash_password("Correct-Horse-42!"),
        "locked_until": None,
        "is_active": True,
    }
    updates = []
    monkeypatch.setattr(auth_db, "_user_by_identifier", lambda identifier: user)
    monkeypatch.setattr(
        auth_db.db,
        "execute",
        lambda query, params=None, returning=False: updates.append((query, params)),
    )

    result = auth_db.authenticate("adviser", "wrong")

    assert not result["ok"]
    assert updates[0][1] == (auth_db._MAX_FAILED_ATTEMPTS, auth_db._LOCKOUT_MINUTES, 7)


def test_inactive_user_cannot_authenticate(monkeypatch):
    user = {
        "is_active": False,
        "password_hash": auth_db._hash_password("Correct-Horse-42!"),
    }
    monkeypatch.setattr(auth_db, "_user_by_identifier", lambda identifier: user)
    monkeypatch.setattr(
        auth_db.db,
        "execute",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("unexpected update")),
    )

    result = auth_db.authenticate("adviser", "Correct-Horse-42!")

    assert not result["ok"]
    assert result["reason"] == "Invalid username or password."


def test_first_account_is_admin_and_later_accounts_are_users(monkeypatch):
    for existing_users, expected_role in ((0, "admin"), (1, "user")):
        cursor = FakeCursor(existing_users)
        monkeypatch.setattr(
            auth_db.db,
            "get_conn",
            lambda cursor=cursor: FakeConnection(cursor),
        )

        ok, _message = auth_db.create_user(
            "adviser",
            "adviser@example.com",
            "Correct-Horse-42!",
            "A Adviser",
        )

        assert ok
        insert = next(
            params
            for query, params in cursor.executed
            if "INSERT INTO auth_users" in query
        )
        assert insert[-1] == expected_role


def test_totp_matches_rfc_sha1_vector(monkeypatch):
    secret = "GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQ"
    assert auth_db._totp_code(secret, 1) == "287082"

    monkeypatch.setattr(
        auth_db,
        "_user_by_identifier",
        lambda username: {"totp_secret": secret, "is_active": True},
    )
    monkeypatch.setattr(auth_db.time, "time", lambda: 59)
    assert auth_db.verify_totp_code("adviser", "287082")
    assert not auth_db.verify_totp_code("adviser", "bad-code")
