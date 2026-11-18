import auth_db


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
    assert "password_hash" not in result["user"]
    assert updates[0][1] == (7,)


def test_authentication_records_failed_attempt(monkeypatch):
    user = {
        "id": 7,
        "username": "adviser",
        "password_hash": auth_db._hash_password("Correct-Horse-42!"),
        "locked_until": None,
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


def test_totp_matches_rfc_sha1_vector(monkeypatch):
    secret = "GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQ"
    assert auth_db._totp_code(secret, 1) == "287082"

    monkeypatch.setattr(
        auth_db,
        "_user_by_identifier",
        lambda username: {"totp_secret": secret},
    )
    monkeypatch.setattr(auth_db.time, "time", lambda: 59)
    assert auth_db.verify_totp_code("adviser", "287082")
    assert not auth_db.verify_totp_code("adviser", "bad-code")
