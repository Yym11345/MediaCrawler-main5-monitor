import pytest


class DummySession:
    async def commit(self):
        pass

    async def rollback(self):
        pass

    async def close(self):
        pass


def _fake_sessionmaker(captured):
    def fake_sessionmaker(engine, **_kwargs):
        captured["engine"] = engine

        class Factory:
            def __call__(self):
                return DummySession()

        return Factory()

    return fake_sessionmaker


def test_monitor_db_type_defaults_to_postgres(monkeypatch):
    from database import db_session

    monkeypatch.delenv("MONITOR_DB_TYPE", raising=False)

    assert db_session.get_monitor_db_type() == "postgres"


def test_monitor_db_type_can_use_sqlite(monkeypatch):
    from database import db_session

    monkeypatch.setenv("MONITOR_DB_TYPE", " sqlite ")

    assert db_session.get_monitor_db_type() == "sqlite"


def test_monitor_db_type_rejects_unsupported_values(monkeypatch):
    from database import db_session

    monkeypatch.setenv("MONITOR_DB_TYPE", "mysql")

    with pytest.raises(ValueError, match="MONITOR_DB_TYPE"):
        db_session.get_monitor_db_type()


@pytest.mark.asyncio
async def test_monitor_router_session_uses_configured_db(monkeypatch):
    from api.routers import monitor

    captured = {}
    monkeypatch.setenv("MONITOR_DB_TYPE", "sqlite")
    monkeypatch.setattr(monitor, "get_async_engine", lambda db_type: f"engine:{db_type}")
    monkeypatch.setattr(monitor, "sessionmaker", _fake_sessionmaker(captured))

    async with monitor._get_session() as session:
        assert isinstance(session, DummySession)

    assert captured["engine"] == "engine:sqlite"


@pytest.mark.asyncio
async def test_monitor_sync_session_uses_configured_db(monkeypatch):
    from api.services import monitor_sync

    captured = {}
    monkeypatch.setenv("MONITOR_DB_TYPE", "sqlite")
    monkeypatch.setattr(monitor_sync, "get_async_engine", lambda db_type: f"engine:{db_type}")
    monkeypatch.setattr(monitor_sync, "sessionmaker", _fake_sessionmaker(captured))

    async with monitor_sync._get_session() as session:
        assert isinstance(session, DummySession)

    assert captured["engine"] == "engine:sqlite"


def test_monitor_router_save_option_matches_configured_db(monkeypatch):
    from api.routers import monitor

    monkeypatch.setenv("MONITOR_DB_TYPE", "sqlite")

    assert monitor._monitor_save_option().value == "sqlite"
