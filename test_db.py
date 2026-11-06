import db


def test_get_conn_uses_bounded_connection_timeout(monkeypatch):
    calls = {}

    class FakeConnection:
        def commit(self):
            pass

        def rollback(self):
            pass

        def close(self):
            pass

    def fake_connect(database_url, **kwargs):
        calls["database_url"] = database_url
        calls.update(kwargs)
        return FakeConnection()

    monkeypatch.setattr(db, "DATABASE_URL", "postgresql://example/database")
    monkeypatch.setattr(db.psycopg2, "connect", fake_connect)

    with db.get_conn():
        pass

    assert calls["database_url"] == "postgresql://example/database"
    assert calls["connect_timeout"] == db.DB_CONNECT_TIMEOUT_SECONDS
