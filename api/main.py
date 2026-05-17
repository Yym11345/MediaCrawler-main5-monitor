# -*- coding: utf-8 -*-
# Copyright (c) 2025 relakkes@gmail.com
#
# This file is part of MediaCrawler project.
# Repository: https://github.com/NanmiCoder/MediaCrawler/blob/main/api/main.py
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

"""
MediaCrawler Monitor API Server
Start command: uvicorn api.main:app --host 0.0.0.0 --port 8080 --reload
Or: python -m api.main
"""
import asyncio
import os
import subprocess
import uvicorn
from fastapi import FastAPI
from fastapi.responses import FileResponse, RedirectResponse

from .routers import auth_router, crawler_router, websocket_router, monitor_router

app = FastAPI(
    title="MediaCrawler Monitor API",
    description="API for the MediaCrawler Monitor dashboard",
    version="1.0.0"
)


def resolve_uvicorn_bind() -> tuple[str, int]:
    host = os.getenv("API_HOST", "0.0.0.0").strip() or "0.0.0.0"
    port_text = os.getenv("API_PORT", "8080").strip() or "8080"
    try:
        port = int(port_text)
    except ValueError:
        port = 8080
    return host, port

# Register routers
app.include_router(crawler_router, prefix="/api")
app.include_router(websocket_router, prefix="/api")
app.include_router(monitor_router, prefix="/api")
app.include_router(auth_router, prefix="/api")


@app.get("/")
async def serve_root():
    """Redirect root to monitor dashboard"""
    return RedirectResponse(url="/monitor")


@app.get("/api/health")
async def health_check():
    return {"status": "ok"}


# Monitor dashboard page
MONITOR_HTML = os.path.join(os.path.dirname(__file__), "static", "monitor.html")


@app.get("/monitor")
async def serve_monitor():
    """Serve the monitoring dashboard page"""
    if os.path.exists(MONITOR_HTML):
        return FileResponse(MONITOR_HTML)
    return {"error": "Monitor page not found"}


# Startup: create monitor tables
@app.on_event("startup")
async def startup_monitor():
    import logging
    logger = logging.getLogger("monitor")
    try:
        from database.db_session import create_tables, get_monitor_db_type
        monitor_db_type = get_monitor_db_type()
        await create_tables(monitor_db_type)
        logger.info("Monitor tables ready (%s)", monitor_db_type)
    except Exception as e:
        logger.warning(f"Monitor table creation skipped: {e}")


@app.get("/api/env/check")
async def check_environment():
    """Check if the crawler runtime environment is configured correctly"""
    try:
        # Run uv run main.py --help command to check environment
        process = await asyncio.create_subprocess_exec(
            "uv", "run", "main.py", "--help",
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            cwd="."  # Project root directory
        )
        stdout, stderr = await asyncio.wait_for(
            process.communicate(),
            timeout=30.0  # 30 seconds timeout
        )

        if process.returncode == 0:
            return {
                "success": True,
                "message": "Crawler environment configured correctly",
                "output": stdout.decode("utf-8", errors="ignore")[:500]  # Truncate to first 500 characters
            }
        else:
            error_msg = stderr.decode("utf-8", errors="ignore") or stdout.decode("utf-8", errors="ignore")
            return {
                "success": False,
                "message": "Environment check failed",
                "error": error_msg[:500]
            }
    except asyncio.TimeoutError:
        return {
            "success": False,
            "message": "Environment check timeout",
            "error": "Command execution exceeded 30 seconds"
        }
    except FileNotFoundError:
        return {
            "success": False,
            "message": "uv command not found",
            "error": "Please ensure uv is installed and configured in system PATH"
        }
    except Exception as e:
        return {
            "success": False,
            "message": "Environment check error",
            "error": str(e)
        }


@app.get("/api/config/platforms")
async def get_platforms():
    """Get list of supported monitor platforms"""
    from .routers.monitor import MONITOR_PLATFORMS
    platform_labels = {"dy": "Douyin", "xhs": "Xiaohongshu", "bili": "Bilibili"}
    return {
        "platforms": [
            {"value": p, "label": platform_labels.get(p, p)}
            for p in sorted(MONITOR_PLATFORMS)
        ]
    }


@app.get("/api/config/options")
async def get_config_options():
    """Get all configuration options"""
    from .schemas.crawler import LoginTypeEnum, CrawlerTypeEnum, SaveDataOptionEnum
    return {
        "login_types": [{"value": e.value, "label": e.value} for e in LoginTypeEnum],
        "crawler_types": [{"value": e.value, "label": e.value} for e in CrawlerTypeEnum],
        "save_options": [{"value": e.value, "label": e.value} for e in SaveDataOptionEnum],
    }



if __name__ == "__main__":
    bind_host, bind_port = resolve_uvicorn_bind()
    uvicorn.run(app, host=bind_host, port=bind_port)
