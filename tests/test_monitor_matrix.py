import time

import pytest
from httpx import ASGITransport, AsyncClient

from api.main import app
from database.db_session import create_tables, close_all_engines
from database.models import DouyinAweme, MonitorAccount, MonitorSnapshot, XhsNote
from api.routers.monitor import _get_session


@pytest.mark.asyncio
async def test_matrix_dashboard_summarizes_platforms_top_videos_and_deltas(tmp_path, monkeypatch):
    db_path = tmp_path / "matrix.db"
    monkeypatch.setenv("MONITOR_DB_TYPE", "sqlite")

    import config.db_config as db_config
    monkeypatch.setitem(db_config.sqlite_db_config, "db_path", str(db_path))

    await close_all_engines()
    await create_tables("sqlite")

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        register = await client.post(
            "/api/auth/register",
            json={"email": "matrix@qq.com", "password": "Password123"},
        )
        assert register.status_code == 200, register.text
        user_id = register.json()["id"]

    now = int(time.time())
    async with _get_session() as session:
        dy = MonitorAccount(platform="dy", creator_id="sec_dy", display_name="抖音旗舰", is_active=1, created_at=now, owner_user_id=user_id)
        xhs = MonitorAccount(platform="xhs", creator_id="xhs_1", display_name="小红书店播", is_active=1, created_at=now, owner_user_id=user_id)
        session.add_all([dy, xhs])
        await session.flush()
        session.add_all([
            MonitorSnapshot(account_id=dy.id, snapshot_ts=now - 90000, followers=100, total_likes=1000, total_videos=1),
            MonitorSnapshot(account_id=dy.id, snapshot_ts=now, followers=130, total_likes=1200, total_videos=2),
            MonitorSnapshot(account_id=xhs.id, snapshot_ts=now, followers=70, total_likes=300, total_videos=1),
            DouyinAweme(
                aweme_id=101,
                sec_uid="sec_dy",
                title="爆款短视频",
                liked_count="500",
                comment_count="40",
                share_count="20",
                create_time=now - 3600,
                aweme_url="https://www.douyin.com/video/101",
            ),
            XhsNote(
                note_id="xhs101",
                user_id="xhs_1",
                title="种草笔记",
                liked_count="100",
                comment_count="10",
                share_count="5",
                time=now - 7200,
                note_url="https://www.xiaohongshu.com/explore/xhs101",
            ),
        ])

    async with AsyncClient(transport=transport, base_url="http://test") as client:
        login = await client.post(
            "/api/auth/login",
            json={"email": "matrix@qq.com", "password": "Password123"},
        )
        assert login.status_code == 200, login.text
        response = await client.get("/api/monitor/matrix")

    assert response.status_code == 200
    data = response.json()
    assert data["kpis"]["total_accounts"] == 2
    assert data["kpis"]["total_followers"] == 200
    assert data["kpis"]["total_interactions"] == 675
    assert data["kpis"]["followers_delta_24h"] == 30
    assert data["kpis"]["interactions_delta_24h"] == 200
    assert data["platforms"][0]["platform"] == "dy"
    assert data["platforms"][0]["interaction_count"] == 560
    assert data["top_videos"][0]["video_id"] == "101"
    assert data["top_videos"][0]["interaction_count"] == 560
    assert data["top_videos"][1]["video_id"] == "xhs101"
    assert data["collection_plan"]["recommended_interval_hours"] == "2-4"
    assert "starter_limit" not in data["collection_plan"]
    assert "stable_limit" not in data["collection_plan"]

    await close_all_engines()


