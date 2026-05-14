# -*- coding: utf-8 -*-
# Copyright (c) 2025 relakkes@gmail.com
#
# This file is part of MediaCrawler project.
# Repository: https://github.com/NanmiCoder/MediaCrawler/blob/main/api/services/crawler_manager.py
# GitHub: https://github.com/NanmiCoder
# Licensed under NON-COMMERCIAL LEARNING LICENSE 1.1
#
# 声明：本代码仅供学习和研究目的使用。使用者应遵守以下原则：
# 1. 不得用于任何商业用途。
# 2. 使用时应遵守目标平台的使用条款和robots.txt规则。
# 3. 不得进行大规模爬取或对平台造成运营干扰。
# 4. 应合理控制请求频率，避免给目标平台带来不必要的负担。
# 5. 不得用于任何非法或不当的用途。
#
# 详细许可条款请参阅项目根目录下的LICENSE文件。
# 使用本代码即表示您同意遵守上述原则和LICENSE中的所有条款。

import asyncio
import subprocess
import signal
import os
import re
import sys
from typing import Optional, List
from datetime import datetime
from pathlib import Path

from ..schemas import CrawlerStartRequest, LogEntry
from .monitor_sync import mark_monitor_accounts, sync_monitor_snapshots


class CrawlerManager:
    """Crawler process manager"""

    ACTIVE_STATUSES = {"running", "finishing", "syncing", "stopping"}
    POST_FINISH_EXIT_TIMEOUT_SECONDS = 45.0

    def __init__(self):
        self._lock = asyncio.Lock()
        self.process: Optional[subprocess.Popen] = None
        self.status = "idle"
        self.status_detail: Optional[str] = None
        self.started_at: Optional[datetime] = None
        self.business_finished_at: Optional[datetime] = None
        self.current_config: Optional[CrawlerStartRequest] = None
        self._log_id = 0
        self._logs: List[LogEntry] = []
        self._read_task: Optional[asyncio.Task] = None
        self._watchdog_task: Optional[asyncio.Task] = None
        self._finalized = False
        self._xhs_login_hint_pushed = False
        # Project root directory
        self._project_root = Path(__file__).parent.parent.parent
        # Log queue - for pushing to WebSocket
        self._log_queue: Optional[asyncio.Queue] = None

    @property
    def logs(self) -> List[LogEntry]:
        return self._logs

    def get_log_queue(self) -> asyncio.Queue:
        """Get or create log queue"""
        if self._log_queue is None:
            self._log_queue = asyncio.Queue()
        return self._log_queue

    def _create_log_entry(self, message: str, level: str = "info") -> LogEntry:
        """Create log entry"""
        self._log_id += 1
        entry = LogEntry(
            id=self._log_id,
            timestamp=datetime.now().strftime("%H:%M:%S"),
            level=level,
            message=message
        )
        self._logs.append(entry)
        # Keep last 500 logs
        if len(self._logs) > 500:
            self._logs = self._logs[-500:]
        return entry

    async def _push_log(self, entry: LogEntry):
        """Push log to queue"""
        if self._log_queue is not None:
            try:
                self._log_queue.put_nowait(entry)
            except asyncio.QueueFull:
                pass

    def _parse_log_level(self, line: str) -> str:
        """Parse log level"""
        line_upper = line.upper()
        if "ERROR" in line_upper or "FAILED" in line_upper:
            return "error"
        elif "WARNING" in line_upper or "WARN" in line_upper:
            return "warning"
        elif (
            "SUCCESS" in line_upper
            or "COMPLETED" in line_upper
            or "SAVED" in line_upper
            or "完成" in line
            or "成功" in line
            or "已保存" in line
        ):
            return "success"
        elif "DEBUG" in line_upper:
            return "debug"
        return "info"

    def is_active(self) -> bool:
        """Return whether the manager is still owning a live or finishing crawler."""
        process_alive = self.process is not None and self.process.poll() is None
        return process_alive or self.status in self.ACTIVE_STATUSES

    def _normalize_process_log_line(self, line: str) -> str:
        """Compact noisy crawler logs before sending them to the dashboard."""
        if "[store.xhs.update_xhs_note] xhs note:" in line:
            note_id = self._extract_dict_value(line, "note_id") or "-"
            title = self._extract_dict_value(line, "title") or self._extract_dict_value(line, "desc") or "-"
            liked = self._extract_dict_value(line, "liked_count") or "-"
            comments = self._extract_dict_value(line, "comment_count") or "-"
            return (
                "[store.xhs.update_xhs_note] 小红书作品已保存: "
                f"note_id={note_id}, title={self._shorten(title, 48)}, "
                f"likes={liked}, comments={comments}"
            )

        if "[store.xhs.update_xhs_note_comment]" in line:
            note_id = self._extract_dict_value(line, "note_id") or "-"
            comment_id = self._extract_dict_value(line, "comment_id") or "-"
            return (
                "[store.xhs.update_xhs_note_comment] 小红书评论已保存: "
                f"note_id={note_id}, comment_id={comment_id}"
            )

        if len(line) > 1200:
            return line[:1000] + " ... [log truncated]"
        return line

    @staticmethod
    def _extract_dict_value(line: str, key: str) -> str:
        match = re.search(rf"['\"]{re.escape(key)}['\"]\s*:\s*(['\"])(.*?)\1", line)
        if match:
            return match.group(2)
        match = re.search(rf"['\"]{re.escape(key)}['\"]\s*:\s*([^,}}]+)", line)
        return match.group(1).strip() if match else ""

    @staticmethod
    def _shorten(value: str, limit: int) -> str:
        value = str(value or "").replace("\n", " ").strip()
        return value if len(value) <= limit else value[:limit] + "..."

    def _is_business_finished_line(self, line: str) -> bool:
        return ".start]" in line and "Crawler finished" in line

    def _handle_process_log_line(self, line: str) -> Optional[LogEntry]:
        """Create log entries and update lifecycle phase from one crawler output line."""
        normalized = self._normalize_process_log_line(line.strip())
        if not normalized:
            return None

        entry = self._create_log_entry(normalized, self._parse_log_level(normalized))

        if self._is_business_finished_line(line) and self.status == "running":
            self.status = "finishing"
            self.status_detail = "业务采集已完成，正在关闭浏览器和数据库连接"
            self.business_finished_at = datetime.now()
            self._create_log_entry(self.status_detail, "success")

        if (
            self.current_config
            and self.current_config.platform.value == "xhs"
            and not self._xhs_login_hint_pushed
            and ("登录已过期" in line or "login expired" in line.lower())
        ):
            self._xhs_login_hint_pushed = True
            self._create_log_entry(
                "小红书登录状态已过期：请在打开的浏览器里重新确认登录，或重新粘贴带 xsec_token 的主页链接后再采集。",
                "warning",
            )

        return entry

    async def start(self, config: CrawlerStartRequest) -> bool:
        """Start crawler process"""
        async with self._lock:
            if self.process and self.process.poll() is None:
                return False

            # Clear old logs
            self._logs = []
            self._log_id = 0

            # Clear pending queue (don't replace object to avoid WebSocket broadcast coroutine holding old queue reference)
            if self._log_queue is None:
                self._log_queue = asyncio.Queue()
            else:
                try:
                    while True:
                        self._log_queue.get_nowait()
                except asyncio.QueueEmpty:
                    pass

            # Build command line arguments
            cmd = self._build_command(config)

            # Log start information
            entry = self._create_log_entry(f"Starting crawler: {' '.join(cmd)}", "info")
            await self._push_log(entry)

            try:
                # Start subprocess
                self.process = subprocess.Popen(
                    cmd,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    encoding='utf-8',
                    bufsize=1,
                    cwd=str(self._project_root),
                    env={**os.environ, "PYTHONUNBUFFERED": "1"}
                )

                self.status = "running"
                self.status_detail = "采集进程已启动，等待平台登录和数据返回"
                self.started_at = datetime.now()
                self.business_finished_at = None
                self.current_config = config
                self._finalized = False
                self._xhs_login_hint_pushed = False

                entry = self._create_log_entry(
                    f"Crawler started on platform: {config.platform.value}, type: {config.crawler_type.value}",
                    "success"
                )
                await self._push_log(entry)
                if config.crawler_type.value == "creator":
                    await self._mark_monitor_status(
                        config.platform.value,
                        "running",
                        config.monitor_account_id,
                    )

                # Start log reading task
                self._read_task = asyncio.create_task(self._read_output())
                self._watchdog_task = asyncio.create_task(self._watch_process_exit())

                return True
            except Exception as e:
                self.status = "error"
                self.status_detail = str(e)
                entry = self._create_log_entry(f"Failed to start crawler: {str(e)}", "error")
                await self._push_log(entry)
                return False

    async def stop(self) -> bool:
        """Stop crawler process"""
        async with self._lock:
            if not self.process or self.process.poll() is not None:
                return False

            self.status = "stopping"
            self.status_detail = "正在终止采集进程"

            await self._terminate_process(
                "Sending SIGTERM to crawler process...",
                "Process not responding, sending SIGKILL...",
            )

            if self.current_config and self.current_config.crawler_type.value == "creator":
                if self.business_finished_at:
                    await self._sync_monitor(
                        self.current_config.platform.value,
                        "success",
                        self.current_config.monitor_account_id,
                    )
                else:
                    await self._mark_monitor_status(
                        self.current_config.platform.value,
                        "cancelled",
                        self.current_config.monitor_account_id,
                    )

            self.status = "idle"
            self.status_detail = None
            self.business_finished_at = None
            self.current_config = None
            self._finalized = True

            # Cancel log reading task
            if self._read_task:
                self._read_task.cancel()
                self._read_task = None
            if self._watchdog_task:
                self._watchdog_task.cancel()
                self._watchdog_task = None

            return True

    def get_status(self) -> dict:
        """Get current status"""
        return {
            "status": self.status,
            "platform": self.current_config.platform.value if self.current_config else None,
            "crawler_type": self.current_config.crawler_type.value if self.current_config else None,
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "business_finished_at": self.business_finished_at.isoformat() if self.business_finished_at else None,
            "monitor_account_id": self.current_config.monitor_account_id if self.current_config else None,
            "status_detail": self.status_detail,
            "error_message": None
        }

    def _build_command(self, config: CrawlerStartRequest) -> list:
        """Build main.py command line arguments"""
        cmd = [self._resolve_python_executable(), "main.py"]

        cmd.extend(["--platform", config.platform.value])
        cmd.extend(["--lt", config.login_type.value])
        cmd.extend(["--type", config.crawler_type.value])
        cmd.extend(["--save_data_option", config.save_option.value])

        # Pass different arguments based on crawler type
        if config.crawler_type.value == "search" and config.keywords:
            cmd.extend(["--keywords", config.keywords])
        elif config.crawler_type.value == "detail" and config.specified_ids:
            cmd.extend(["--specified_id", config.specified_ids])
        elif config.crawler_type.value == "creator" and config.creator_ids:
            cmd.extend(["--creator_id", config.creator_ids])

        if config.start_page != 1:
            cmd.extend(["--start", str(config.start_page)])

        cmd.extend(["--get_comment", "true" if config.enable_comments else "false"])
        cmd.extend(["--get_sub_comment", "true" if config.enable_sub_comments else "false"])
        cmd.extend(["--max_comments_count_singlenotes", str(config.max_comments_count_singlenotes)])

        if config.cookies:
            cmd.extend(["--cookies", config.cookies])

        cmd.extend(["--headless", "true" if config.headless else "false"])

        return cmd

    def _resolve_python_executable(self) -> str:
        """Use the API runtime Python unless explicitly overridden."""
        configured_python = os.getenv("CRAWLER_PYTHON")
        if configured_python:
            return configured_python

        if sys.executable:
            return sys.executable

        candidates = [
            self._project_root / ".venv" / "Scripts" / "python.exe",
            self._project_root / ".venv" / "bin" / "python",
        ]
        for candidate in candidates:
            if candidate.exists():
                return str(candidate)

        return "python"

    async def _read_output(self):
        """Asynchronously read process output"""
        loop = asyncio.get_event_loop()

        try:
            while self.process and self.process.poll() is None:
                # Read a line in thread pool
                line = await loop.run_in_executor(
                    None, self.process.stdout.readline
                )
                if line:
                    before_count = len(self._logs)
                    entry = self._handle_process_log_line(line)
                    if entry:
                        for item in self._logs[before_count:]:
                            await self._push_log(item)

            # Read remaining output
            if self.process and self.process.stdout:
                remaining = await loop.run_in_executor(
                    None, self.process.stdout.read
                )
                if remaining:
                    for line in remaining.strip().split('\n'):
                        if line.strip():
                            before_count = len(self._logs)
                            entry = self._handle_process_log_line(line)
                            if entry:
                                for item in self._logs[before_count:]:
                                    await self._push_log(item)

            await self._finalize_process_result()

        except asyncio.CancelledError:
            pass
        except Exception as e:
            entry = self._create_log_entry(f"Error reading output: {str(e)}", "error")
            await self._push_log(entry)

    async def _mark_monitor_status(self, platform: str, status: str, account_id: Optional[int] = None) -> None:
        try:
            count = await mark_monitor_accounts(platform, status, account_id=account_id)
            if count:
                entry = self._create_log_entry(f"Monitor accounts marked as {status}: {count}", "info")
                await self._push_log(entry)
        except Exception as e:
            entry = self._create_log_entry(f"Monitor status update failed: {str(e)}", "warning")
            await self._push_log(entry)

    async def _sync_monitor(self, platform: str, status: str, account_id: Optional[int] = None) -> None:
        try:
            result = await sync_monitor_snapshots(platform=platform, account_id=account_id, mark_status=status)
            entry = self._create_log_entry(
                "Monitor snapshots saved: "
                f"{result.get('synced_accounts', 0)} accounts, "
                f"{result.get('accounts_with_source_data', 0)} with source data",
                "success" if status == "success" else "warning",
            )
            await self._push_log(entry)
        except Exception as e:
            entry = self._create_log_entry(f"Monitor snapshot sync failed: {str(e)}", "error")
            await self._push_log(entry)

    async def _watch_process_exit(self) -> None:
        """Force a crawler process out if it gets stuck after business completion."""
        try:
            while self.process and self.process.poll() is None:
                if self.status == "finishing" and self.business_finished_at:
                    elapsed = (datetime.now() - self.business_finished_at).total_seconds()
                    if elapsed >= self.POST_FINISH_EXIT_TIMEOUT_SECONDS:
                        await self._terminate_process(
                            "业务采集已完成，但进程收尾超时，正在终止残留采集进程...",
                            "残留采集进程未响应，正在强制结束...",
                            graceful_timeout_seconds=5.0,
                        )
                        await self._finalize_process_result()
                        return
                await asyncio.sleep(1)
        except asyncio.CancelledError:
            pass

    async def _finalize_process_result(self) -> None:
        """Sync monitor data once the crawler process is no longer useful."""
        if self._finalized or self.status not in {"running", "finishing"} or not self.current_config:
            return

        self._finalized = True
        exit_code = self.process.returncode if self.process else -1
        platform = self.current_config.platform.value
        account_id = self.current_config.monitor_account_id
        should_sync_monitor = self.current_config.crawler_type.value == "creator"
        success = exit_code == 0 or self.business_finished_at is not None

        if success:
            entry = self._create_log_entry("Crawler completed successfully", "success")
            await self._push_log(entry)
            if should_sync_monitor and platform:
                self.status = "syncing"
                self.status_detail = "采集进程已退出，正在保存监控快照"
                entry = self._create_log_entry(self.status_detail, "info")
                await self._push_log(entry)
                await self._sync_monitor(platform, "success", account_id)
        else:
            entry = self._create_log_entry(f"Crawler exited with code: {exit_code}", "warning")
            await self._push_log(entry)
            if should_sync_monitor and platform:
                await self._sync_monitor(platform, "failed", account_id)

        self.status = "idle"
        self.status_detail = None
        self.business_finished_at = None
        self.current_config = None

    async def _terminate_process(
        self,
        graceful_message: str,
        force_message: str,
        *,
        graceful_timeout_seconds: float = 15.0,
    ) -> None:
        if not self.process or self.process.poll() is not None:
            return

        entry = self._create_log_entry(graceful_message, "warning")
        await self._push_log(entry)

        try:
            self.process.send_signal(signal.SIGTERM)
        except Exception as e:
            entry = self._create_log_entry(f"Error sending SIGTERM: {str(e)}", "error")
            await self._push_log(entry)

        deadline = datetime.now().timestamp() + graceful_timeout_seconds
        while self.process and self.process.poll() is None and datetime.now().timestamp() < deadline:
            await asyncio.sleep(0.5)

        if self.process and self.process.poll() is None:
            entry = self._create_log_entry(force_message, "warning")
            await self._push_log(entry)
            try:
                self.process.kill()
            except Exception as e:
                entry = self._create_log_entry(f"Error killing crawler process: {str(e)}", "error")
                await self._push_log(entry)

        deadline = datetime.now().timestamp() + 5.0
        while self.process and self.process.poll() is None and datetime.now().timestamp() < deadline:
            await asyncio.sleep(0.2)

        if self.process and self.process.poll() is None:
            entry = self._create_log_entry("Crawler process termination requested, but the process still reports running.", "warning")
        else:
            entry = self._create_log_entry("Crawler process terminated", "info")
        await self._push_log(entry)


# Global singleton
crawler_manager = CrawlerManager()
