import pytest
import sys

from api.schemas.crawler import (
    CrawlerStartRequest,
    CrawlerTypeEnum,
    LoginTypeEnum,
    PlatformEnum,
    SaveDataOptionEnum,
)
from api.services.crawler_manager import CrawlerManager


def _creator_config() -> CrawlerStartRequest:
    return CrawlerStartRequest(
        platform=PlatformEnum.XHS,
        login_type=LoginTypeEnum.QRCODE,
        crawler_type=CrawlerTypeEnum.CREATOR,
        creator_ids="creator-1",
        monitor_account_id=23,
        save_option=SaveDataOptionEnum.POSTGRES,
    )


def test_finished_crawler_log_moves_manager_to_finishing_phase():
    manager = CrawlerManager()
    manager.status = "running"
    manager.current_config = _creator_config()

    entry = manager._handle_process_log_line(
        "2026-05-08 11:16:47 MediaCrawler INFO (core.py:128) - "
        "[XiaoHongShuCrawler.start] Xhs Crawler finished ..."
    )

    assert entry is not None
    assert manager.status == "finishing"
    assert manager.business_finished_at is not None
    assert any("业务采集已完成，正在关闭浏览器和数据库连接" in item.message for item in manager.logs)


def test_verbose_xhs_note_log_is_compacted():
    manager = CrawlerManager()
    raw = (
        "[store.xhs.update_xhs_note] xhs note: {'note_id': 'n1', "
        "'title': 'very long title', 'desc': '" + ("x" * 600) + "', "
        "'image_list': 'https://example.com/a.jpg,https://example.com/b.jpg'}"
    )

    compacted = manager._normalize_process_log_line(raw)

    assert "小红书作品已保存" in compacted
    assert "desc" not in compacted
    assert len(compacted) < 180


def test_crawler_subprocess_uses_running_python_before_project_venv():
    manager = CrawlerManager()

    assert manager._resolve_python_executable() == sys.executable


@pytest.mark.asyncio
async def test_stop_after_business_finished_syncs_success_instead_of_marking_failed():
    manager = CrawlerManager()
    manager.status = "finishing"
    manager.current_config = _creator_config()
    manager.business_finished_at = 1.0

    class FakeProcess:
        returncode = None

        def poll(self):
            return self.returncode

        def send_signal(self, _signal):
            self.returncode = 0

        def kill(self):
            self.returncode = -9

    manager.process = FakeProcess()
    marked = []
    synced = []

    async def fake_mark(platform, status, account_id=None):
        marked.append((platform, status, account_id))

    async def fake_sync(platform, status, account_id=None):
        synced.append((platform, status, account_id))

    manager._mark_monitor_status = fake_mark
    manager._sync_monitor = fake_sync

    assert await manager.stop() is True

    assert marked == []
    assert synced == [("xhs", "success", 23)]


@pytest.mark.asyncio
async def test_forced_cleanup_after_business_finish_finalizes_without_waiting_for_log_reader():
    manager = CrawlerManager()
    manager.status = "finishing"
    manager.current_config = _creator_config()
    manager.business_finished_at = 1.0

    class FakeProcess:
        returncode = -9

        def poll(self):
            return self.returncode

    manager.process = FakeProcess()
    synced = []

    async def fake_sync(platform, status, account_id=None):
        synced.append((platform, status, account_id))

    manager._sync_monitor = fake_sync

    await manager._finalize_process_result()

    assert synced == [("xhs", "success", 23)]
    assert manager.status == "idle"
    assert manager.current_config is None
