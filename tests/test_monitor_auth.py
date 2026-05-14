import time

import pytest
from sqlalchemy import text
from httpx import ASGITransport, AsyncClient

from api.main import app
from api.routers.monitor import _get_session
from database.db_session import close_all_engines, create_tables
from database.models import DouyinAweme, MonitorAccount


async def _reset_sqlite(tmp_path, monkeypatch):
    db_path = tmp_path / "auth.db"
    monkeypatch.setenv("MONITOR_DB_TYPE", "sqlite")
    monkeypatch.setenv("AUTH_SECRET", "test-secret")

    import config.db_config as db_config

    monkeypatch.setitem(db_config.sqlite_db_config, "db_path", str(db_path))
    await close_all_engines()
    await create_tables("sqlite")


async def _register(client, email, password="Password123"):
    response = await client.post("/api/auth/register", json={"email": email, "password": password})
    assert response.status_code == 200, response.text
    return response.json()


@pytest.mark.asyncio
async def test_auth_register_login_logout_and_email_domain_validation(tmp_path, monkeypatch):
    await _reset_sqlite(tmp_path, monkeypatch)
    transport = ASGITransport(app=app)

    async with AsyncClient(transport=transport, base_url="http://test") as client:
        blocked = await client.post("/api/auth/register", json={"email": "user@gmail.com", "password": "Password123"})
        assert blocked.status_code == 400
        assert "QQ" in blocked.json()["detail"]

        registered = await _register(client, "User@qq.com")
        assert registered["email"] == "user@qq.com"

        me = await client.get("/api/auth/me")
        assert me.status_code == 200
        assert me.json()["email"] == "user@qq.com"

        await client.post("/api/auth/logout")
        me_after_logout = await client.get("/api/auth/me")
        assert me_after_logout.status_code == 401

        login = await client.post("/api/auth/login", json={"email": "USER@qq.com", "password": "Password123"})
        assert login.status_code == 200
        assert login.json()["email"] == "user@qq.com"

    await close_all_engines()


@pytest.mark.asyncio
async def test_monitor_accounts_are_isolated_by_logged_in_user(tmp_path, monkeypatch):
    await _reset_sqlite(tmp_path, monkeypatch)
    transport = ASGITransport(app=app)

    async with AsyncClient(transport=transport, base_url="http://test") as anon:
        unauthenticated = await anon.get("/api/monitor/accounts")
        assert unauthenticated.status_code == 401

    async with AsyncClient(transport=transport, base_url="http://test") as first:
        await _register(first, "first@qq.com")
        create_first = await first.post(
            "/api/monitor/accounts",
            json={"platform": "dy", "creator_id": "https://www.douyin.com/user/first", "display_name": "First"},
        )
        assert create_first.status_code == 200, create_first.text
        first_account_id = create_first.json()["id"]

        accounts = await first.get("/api/monitor/accounts")
        assert [item["display_name"] for item in accounts.json()] == ["First"]

    async with AsyncClient(transport=transport, base_url="http://test") as second:
        await _register(second, "second@163.com")
        accounts = await second.get("/api/monitor/accounts")
        assert accounts.status_code == 200
        assert accounts.json() == []

        forbidden_delete = await second.delete(f"/api/monitor/accounts/{first_account_id}")
        assert forbidden_delete.status_code == 404

        create_second_same_creator = await second.post(
            "/api/monitor/accounts",
            json={"platform": "dy", "creator_id": "https://www.douyin.com/user/first", "display_name": "Second"},
        )
        assert create_second_same_creator.status_code == 200, create_second_same_creator.text

    async with _get_session() as session:
        account = MonitorAccount(
            platform="xhs",
            creator_id="legacy_user",
            display_name="Legacy",
            is_active=1,
            created_at=int(time.time()),
        )
        session.add(account)

    async with AsyncClient(transport=transport, base_url="http://test") as first_again:
        login = await first_again.post("/api/auth/login", json={"email": "first@qq.com", "password": "Password123"})
        assert login.status_code == 200
        accounts = await first_again.get("/api/monitor/accounts")
        assert [item["display_name"] for item in accounts.json()] == ["First"]

    await close_all_engines()


@pytest.mark.asyncio
async def test_legacy_monitor_account_table_gets_owner_column(tmp_path, monkeypatch):
    await _reset_sqlite(tmp_path, monkeypatch)

    async with _get_session() as session:
        await session.execute(text("DROP TABLE monitor_account"))
        await session.execute(
            text(
                """
                CREATE TABLE monitor_account (
                    id INTEGER PRIMARY KEY,
                    platform VARCHAR(16) NOT NULL,
                    creator_id VARCHAR(255) NOT NULL,
                    display_name VARCHAR(255) DEFAULT '',
                    avatar_url TEXT DEFAULT '',
                    is_active INTEGER DEFAULT 1,
                    created_at BIGINT,
                    last_crawl_at BIGINT,
                    last_crawl_status VARCHAR(32) DEFAULT 'never'
                )
                """
            )
        )

    await close_all_engines()
    await create_tables("sqlite")

    async with _get_session() as session:
        result = await session.execute(text("PRAGMA table_info(monitor_account)"))
        columns = [row[1] for row in result.all()]

    assert "owner_user_id" in columns


@pytest.mark.asyncio
async def test_single_video_export_is_scoped_to_current_user(tmp_path, monkeypatch):
    await _reset_sqlite(tmp_path, monkeypatch)
    transport = ASGITransport(app=app)

    async with AsyncClient(transport=transport, base_url="http://test") as owner:
        await _register(owner, "owner@qq.com")
        create_owner = await owner.post(
            "/api/monitor/accounts",
            json={"platform": "dy", "creator_id": "https://www.douyin.com/user/owner", "display_name": "Owner"},
        )
        assert create_owner.status_code == 200, create_owner.text

    async with _get_session() as session:
        await session.execute(text("DELETE FROM douyin_aweme"))
        session.add(
            DouyinAweme(
                aweme_id=111,
                sec_uid="owner",
                user_id="owner",
                user_unique_id="owner",
                title="Owned video",
                liked_count="12",
                comment_count="3",
                share_count="4",
                create_time=int(time.time()),
                aweme_url="https://www.douyin.com/video/111",
            )
        )

    async with AsyncClient(transport=transport, base_url="http://test") as owner_again:
        login = await owner_again.post("/api/auth/login", json={"email": "owner@qq.com", "password": "Password123"})
        assert login.status_code == 200
        owner_export = await owner_again.get("/api/monitor/videos/export", params={"platform": "dy", "video_id": "111"})
        assert owner_export.status_code == 200

    async with AsyncClient(transport=transport, base_url="http://test") as stranger:
        await _register(stranger, "stranger@163.com")
        response = await stranger.get("/api/monitor/videos/export", params={"platform": "dy", "video_id": "111"})
        assert response.status_code == 404

    await close_all_engines()
