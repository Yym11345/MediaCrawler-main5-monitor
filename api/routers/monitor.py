# -*- coding: utf-8 -*-
import csv
import io
import re
import time
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from sqlalchemy import delete, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import sessionmaker

from api.services.monitor_sync import normalize_creator_id, safe_int, sync_monitor_snapshots
from api.services.auth import get_current_user
from database.db_session import get_async_engine, get_monitor_db_type
from database.models import (
    BilibiliVideo,
    BilibiliVideoComment,
    DouyinAweme,
    DouyinAwemeComment,
    MonitorAccount,
    MonitorSnapshot,
    MonitorUser,
    XhsNote,
    XhsNoteComment,
)

from ..schemas.crawler import SaveDataOptionEnum
from ..schemas.monitor import (
    AccountDashboard,
    DashboardResponse,
    MonitorAccountCreate,
    MonitorAccountResponse,
    MonitorSnapshotResponse,
    VideoItem,
    VideoListResponse,
)

router = APIRouter(prefix="/monitor", tags=["monitor"])

MONITOR_PLATFORMS = {"dy", "xhs", "bili"}
BILI_BV_TABLE = "FcwAPNKTMug3GV5Lj7EJnHpWsx4tb8haYeviqBz6rkCy12mUSDQX9RdoZf"
BILI_BV_DECODE_POSITIONS = (6, 4, 2, 3, 1, 5, 0, 7, 8)
BILI_BV_XOR = 23442827791579
BILI_BV_MASK = 2251799813685247


@asynccontextmanager
async def _get_session():
    engine = get_async_engine(get_monitor_db_type())
    factory = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    session = factory()
    try:
        yield session
        await session.commit()
    except Exception:
        await session.rollback()
        raise
    finally:
        await session.close()


def _ensure_monitor_platform(platform: str) -> str:
    platform = (platform or "").strip()
    if platform not in MONITOR_PLATFORMS:
        raise HTTPException(status_code=400, detail="Monitor only supports dy, xhs and bili")
    return platform


def _monitor_save_option() -> SaveDataOptionEnum:
    return SaveDataOptionEnum(get_monitor_db_type())


def _snapshot_response(snapshot: MonitorSnapshot) -> MonitorSnapshotResponse:
    return MonitorSnapshotResponse(
        id=snapshot.id,
        account_id=snapshot.account_id,
        snapshot_ts=snapshot.snapshot_ts,
        followers=snapshot.followers or 0,
        total_likes=snapshot.total_likes or 0,
        total_videos=snapshot.total_videos or 0,
        total_views=snapshot.total_views or 0,
        latest_video_likes=snapshot.latest_video_likes or 0,
        latest_video_id=snapshot.latest_video_id or "",
        raw_json=snapshot.raw_json or "{}",
    )


async def _get_account(session: AsyncSession, account_id: int) -> MonitorAccount:
    result = await session.execute(select(MonitorAccount).where(MonitorAccount.id == account_id))
    account = result.scalar_one_or_none()
    if not account:
        raise HTTPException(status_code=404, detail="Account not found")
    return account


async def _get_user_account(session: AsyncSession, account_id: int, user: MonitorUser) -> MonitorAccount:
    result = await session.execute(
        select(MonitorAccount).where(
            MonitorAccount.id == account_id,
            MonitorAccount.owner_user_id == user.id,
        )
    )
    account = result.scalar_one_or_none()
    if not account:
        raise HTTPException(status_code=404, detail="Account not found")
    return account


def _account_video_filter(platform: str, creator_id: str):
    normalized_id = normalize_creator_id(platform, creator_id)
    values = [value for value in {creator_id, normalized_id} if value]

    if platform == "dy":
        conditions = []
        for value in values:
            conditions.extend(
                [
                    DouyinAweme.sec_uid == value,
                    DouyinAweme.user_id == value,
                    DouyinAweme.user_unique_id == value,
                ]
            )
        return or_(*conditions) if conditions else None

    if platform == "xhs":
        return XhsNote.user_id.in_(values) if values else None

    if platform == "bili":
        uid = safe_int(normalized_id)
        return BilibiliVideo.user_id == uid if uid else None

    return None


def _platform_model(platform: str):
    if platform == "dy":
        return DouyinAweme
    if platform == "xhs":
        return XhsNote
    if platform == "bili":
        return BilibiliVideo
    return None


def _platform_id_column(platform: str):
    if platform == "dy":
        return DouyinAweme.aweme_id
    if platform == "xhs":
        return XhsNote.note_id
    if platform == "bili":
        return BilibiliVideo.video_id
    return None


async def _count_account_videos(session: AsyncSession, platform: str, creator_id: str) -> int:
    video_filter = _account_video_filter(platform, creator_id)
    model = _platform_model(platform)
    if model is None or video_filter is None:
        return 0
    result = await session.execute(select(func.count()).select_from(model).where(video_filter))
    return int(result.scalar() or 0)


async def _get_account_video_ids(session: AsyncSession, platform: str, creator_id: str) -> list:
    video_filter = _account_video_filter(platform, creator_id)
    id_column = _platform_id_column(platform)
    if id_column is None or video_filter is None:
        return []
    result = await session.execute(select(id_column).where(video_filter))
    return [row[0] for row in result.all() if row[0] is not None]


async def _get_videos(
    session: AsyncSession,
    platform: str,
    creator_id: str,
    limit: Optional[int] = 10,
    offset: int = 0,
) -> list[VideoItem]:
    video_filter = _account_video_filter(platform, creator_id)
    if video_filter is None:
        return []

    if platform == "dy":
        stmt = select(DouyinAweme).where(video_filter).order_by(DouyinAweme.create_time.desc())
    elif platform == "xhs":
        stmt = select(XhsNote).where(video_filter).order_by(XhsNote.time.desc())
    elif platform == "bili":
        stmt = select(BilibiliVideo).where(video_filter).order_by(BilibiliVideo.create_time.desc())
    else:
        return []

    if offset:
        stmt = stmt.offset(offset)
    if limit is not None:
        stmt = stmt.limit(limit)

    result = await session.execute(stmt)
    return [_video_item_from_row(platform, row) for row in result.scalars().all()]


async def _get_recent_videos(session: AsyncSession, platform: str, creator_id: str) -> list[VideoItem]:
    return await _get_videos(session, platform, creator_id, limit=10)


def _normalize_video_id(platform: str, value: str) -> str:
    value = (value or "").strip()
    if not value:
        return ""

    if platform == "dy":
        for pattern in (r"[?&]modal_id=(\d+)", r"[?&]aweme_id=(\d+)", r"/video/(\d+)"):
            match = re.search(pattern, value)
            if match:
                return match.group(1)
        match = re.search(r"\b(\d{10,})\b", value)
        return match.group(1) if match else value

    if platform == "xhs":
        for pattern in (r"/explore/([^/?#]+)", r"[?&]note_id=([^&#]+)"):
            match = re.search(pattern, value)
            if match:
                return match.group(1)
        return value

    if platform == "bili":
        match = re.search(r"(?:av|/video/av)(\d+)", value, re.IGNORECASE)
        if match:
            return match.group(1)
        match = re.search(r"/video/(BV[a-zA-Z0-9]+)", value)
        if match:
            return _bili_bv_to_aid(match.group(1)) or match.group(1)
        if re.match(r"^BV[a-zA-Z0-9]+$", value):
            return _bili_bv_to_aid(value) or value
        return value

    return value


def _bili_bv_to_aid(value: str) -> str:
    if not value or not re.match(r"^BV[a-zA-Z0-9]{10}$", value):
        return ""

    table = {char: index for index, char in enumerate(BILI_BV_TABLE)}
    try:
        result = 0
        payload = value[3:]
        for position in BILI_BV_DECODE_POSITIONS:
            result = result * len(BILI_BV_TABLE) + table[payload[position]]
        return str((result & BILI_BV_MASK) ^ BILI_BV_XOR)
    except (KeyError, IndexError):
        return ""


def _video_not_found_detail(platform: str, video_id: str) -> str:
    if platform == "dy" and "v.douyin.com" in (video_id or ""):
        return "未找到该作品数据。抖音短链接无法直接匹配本地数据库，请先完成采集后使用日志里的作品ID或完整作品链接导出。"
    return "未找到该作品数据。请先确认单个作品采集已完成，并使用作品ID或完整作品链接导出。"


async def _get_video_by_id(session: AsyncSession, platform: str, video_id: str):
    normalized_id = _normalize_video_id(platform, video_id)

    if platform == "dy":
        candidates = [safe_int(value) for value in {video_id, normalized_id} if safe_int(value)]
        if not candidates:
            return None
        result = await session.execute(
            select(DouyinAweme)
            .where(DouyinAweme.aweme_id.in_(candidates))
            .order_by(DouyinAweme.last_modify_ts.desc())
            .limit(1)
        )
        row = result.scalar_one_or_none()
        if row:
            return row
        result = await session.execute(
            select(DouyinAweme)
            .where(DouyinAweme.aweme_url.ilike(f"%{normalized_id}%"))
            .order_by(DouyinAweme.last_modify_ts.desc())
            .limit(1)
        )
        return result.scalar_one_or_none()

    if platform == "xhs":
        candidates = [value for value in {video_id, normalized_id} if value]
        result = await session.execute(
            select(XhsNote)
            .where(or_(XhsNote.note_id.in_(candidates), XhsNote.note_url.ilike(f"%{normalized_id}%")))
            .order_by(XhsNote.last_modify_ts.desc())
            .limit(1)
        )
        return result.scalar_one_or_none()

    if platform == "bili":
        numeric_id = safe_int(normalized_id)
        if numeric_id:
            result = await session.execute(
                select(BilibiliVideo)
                .where(BilibiliVideo.video_id == numeric_id)
                .order_by(BilibiliVideo.last_modify_ts.desc())
                .limit(1)
            )
            return result.scalar_one_or_none()
        result = await session.execute(
            select(BilibiliVideo)
            .where(BilibiliVideo.video_url.ilike(f"%{normalized_id}%"))
            .order_by(BilibiliVideo.last_modify_ts.desc())
            .limit(1)
        )
        return result.scalar_one_or_none()

    return None


async def _get_user_video_by_id(session: AsyncSession, platform: str, video_id: str, user: MonitorUser, account_id: Optional[int] = None):
    video = await _get_video_by_id(session, platform, video_id)
    if not video:
        return None

    account_stmt = select(MonitorAccount).where(
        MonitorAccount.owner_user_id == user.id,
        MonitorAccount.platform == platform,
    )
    if account_id:
        account_stmt = account_stmt.where(MonitorAccount.id == account_id)
    result = await session.execute(account_stmt)
    accounts = result.scalars().all()
    if not accounts:
        return None

    allowed_creator_ids = {
        normalize_creator_id(platform, account.creator_id)
        for account in accounts
        if account.creator_id
    }
    video_creator_id = ""
    if platform == "dy":
        video_creator_id = normalize_creator_id(platform, str(getattr(video, "sec_uid", "") or getattr(video, "user_id", "") or getattr(video, "user_unique_id", "")))
    elif platform == "xhs":
        video_creator_id = normalize_creator_id(platform, str(getattr(video, "user_id", "") or ""))
    elif platform == "bili":
        video_creator_id = normalize_creator_id(platform, str(getattr(video, "user_id", "") or ""))

    if video_creator_id and video_creator_id in allowed_creator_ids:
        return video
    return None


def _video_item_from_row(platform: str, row) -> VideoItem:
    if platform == "dy":
        return VideoItem(
            video_id=str(row.aweme_id or ""),
            title=row.title or "",
            desc=row.desc or "",
            liked_count=safe_int(row.liked_count),
            comment_count=safe_int(row.comment_count),
            share_count=safe_int(row.share_count),
            create_time=safe_int(row.create_time),
            video_url=row.aweme_url or "",
        )

    if platform == "xhs":
        return VideoItem(
            video_id=row.note_id or "",
            title=row.title or "",
            desc=row.desc or "",
            liked_count=safe_int(row.liked_count),
            comment_count=safe_int(row.comment_count),
            share_count=safe_int(row.share_count),
            create_time=safe_int(row.time),
            video_url=row.note_url or "",
        )

    return VideoItem(
        video_id=str(row.video_id or ""),
        title=row.title or "",
        desc=row.desc or "",
        liked_count=safe_int(row.liked_count),
        comment_count=safe_int(row.video_comment),
        share_count=safe_int(row.video_share_count),
        create_time=safe_int(row.create_time),
        video_url=row.video_url or "",
    )


async def _get_video_comments(session: AsyncSession, platform: str, video_id: str):
    if platform == "dy":
        aweme_id = safe_int(video_id)
        if not aweme_id:
            return []
        result = await session.execute(
            select(DouyinAwemeComment)
            .where(DouyinAwemeComment.aweme_id == aweme_id)
            .order_by(DouyinAwemeComment.create_time.desc())
        )
        return result.scalars().all()

    if platform == "xhs":
        result = await session.execute(
            select(XhsNoteComment)
            .where(XhsNoteComment.note_id == str(video_id))
            .order_by(XhsNoteComment.create_time.desc())
        )
        return result.scalars().all()

    if platform == "bili":
        bili_video_id = safe_int(video_id)
        if not bili_video_id:
            return []
        result = await session.execute(
            select(BilibiliVideoComment)
            .where(BilibiliVideoComment.video_id == bili_video_id)
            .order_by(BilibiliVideoComment.create_time.desc())
        )
        return result.scalars().all()

    return []


async def _get_comments_for_video_ids(session: AsyncSession, platform: str, video_ids: list):
    if not video_ids:
        return []

    if platform == "dy":
        ids = [safe_int(value) for value in video_ids if safe_int(value)]
        if not ids:
            return []
        stmt = (
            select(DouyinAwemeComment)
            .where(DouyinAwemeComment.aweme_id.in_(ids))
            .order_by(DouyinAwemeComment.create_time.desc())
        )
    elif platform == "xhs":
        ids = [str(value) for value in video_ids if value]
        stmt = (
            select(XhsNoteComment)
            .where(XhsNoteComment.note_id.in_(ids))
            .order_by(XhsNoteComment.create_time.desc())
        )
    elif platform == "bili":
        ids = [safe_int(value) for value in video_ids if safe_int(value)]
        if not ids:
            return []
        stmt = (
            select(BilibiliVideoComment)
            .where(BilibiliVideoComment.video_id.in_(ids))
            .order_by(BilibiliVideoComment.create_time.desc())
        )
    else:
        return []

    result = await session.execute(stmt)
    return result.scalars().all()


def _comment_fields(platform: str, comment) -> tuple[str, str, str, int, str]:
    if platform == "dy":
        return (
            str(comment.aweme_id or ""),
            comment.content or "",
            comment.nickname or "",
            safe_int(comment.like_count),
            _format_ts(comment.create_time),
        )
    if platform == "xhs":
        return (
            str(comment.note_id or ""),
            comment.content or "",
            comment.nickname or "",
            safe_int(comment.like_count),
            _format_ts(comment.create_time),
        )
    return (
        str(comment.video_id or ""),
        comment.content or "",
        comment.nickname or "",
        safe_int(comment.like_count),
        _format_ts(comment.create_time),
    )


def _format_ts(ts) -> str:
    value = safe_int(ts)
    if not value:
        return ""
    if value > 9999999999:
        value = value // 1000
    try:
        return datetime.fromtimestamp(value).strftime("%Y-%m-%d %H:%M")
    except (OSError, OverflowError, ValueError):
        return str(ts)


def _csv_response(buffer: io.StringIO, filename: str) -> StreamingResponse:
    buffer.seek(0)
    return StreamingResponse(
        iter([buffer.getvalue()]),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )


# ---- Accounts ----


@router.get("/accounts", response_model=list[MonitorAccountResponse])
async def list_accounts(platform: Optional[str] = None, user: MonitorUser = Depends(get_current_user)):
    async with _get_session() as session:
        stmt = select(MonitorAccount).where(MonitorAccount.owner_user_id == user.id)
        if platform:
            stmt = stmt.where(MonitorAccount.platform == platform)
        result = await session.execute(stmt.order_by(MonitorAccount.id))
        accounts = result.scalars().all()
        return [
            MonitorAccountResponse(
                id=account.id,
                platform=account.platform,
                creator_id=account.creator_id,
                display_name=account.display_name or "",
                avatar_url=account.avatar_url or "",
                is_active=1 if account.is_active is None else account.is_active,
                created_at=account.created_at,
                last_crawl_at=account.last_crawl_at,
                last_crawl_status=account.last_crawl_status or "never",
            )
            for account in accounts
        ]


@router.post("/accounts", response_model=MonitorAccountResponse)
async def create_account(body: MonitorAccountCreate, user: MonitorUser = Depends(get_current_user)):
    platform = _ensure_monitor_platform(body.platform.value)
    async with _get_session() as session:
        existing = await session.execute(
            select(MonitorAccount).where(
                MonitorAccount.owner_user_id == user.id,
                MonitorAccount.platform == platform,
                MonitorAccount.creator_id == body.creator_id,
            )
        )
        if existing.scalar_one_or_none():
            raise HTTPException(status_code=409, detail="Account already exists")

        account = MonitorAccount(
            owner_user_id=user.id,
            platform=platform,
            creator_id=body.creator_id.strip(),
            display_name=body.display_name.strip(),
            is_active=1,
            created_at=int(time.time()),
            last_crawl_status="never",
        )
        session.add(account)
        await session.flush()
        return MonitorAccountResponse(
            id=account.id,
            platform=account.platform,
            creator_id=account.creator_id,
            display_name=account.display_name or "",
            avatar_url="",
            is_active=1,
            created_at=account.created_at,
            last_crawl_at=None,
            last_crawl_status="never",
        )


@router.delete("/accounts/{account_id}")
async def delete_account(account_id: int, user: MonitorUser = Depends(get_current_user)):
    async with _get_session() as session:
        await _get_user_account(session, account_id, user)
        await session.execute(delete(MonitorSnapshot).where(MonitorSnapshot.account_id == account_id))
        await session.execute(delete(MonitorAccount).where(MonitorAccount.id == account_id))
        return {"status": "deleted"}


@router.patch("/accounts/{account_id}", response_model=MonitorAccountResponse)
async def update_account(
    account_id: int,
    display_name: Optional[str] = None,
    is_active: Optional[int] = None,
    user: MonitorUser = Depends(get_current_user),
):
    async with _get_session() as session:
        account = await _get_user_account(session, account_id, user)

        if display_name is not None:
            account.display_name = display_name
        if is_active is not None:
            account.is_active = 1 if is_active else 0

        await session.flush()
        return MonitorAccountResponse(
            id=account.id,
            platform=account.platform,
            creator_id=account.creator_id,
            display_name=account.display_name or "",
            avatar_url=account.avatar_url or "",
            is_active=1 if account.is_active is None else account.is_active,
            created_at=account.created_at,
            last_crawl_at=account.last_crawl_at,
            last_crawl_status=account.last_crawl_status or "never",
        )


@router.get("/accounts/{account_id}/videos", response_model=VideoListResponse)
async def get_account_videos(
    account_id: int,
    limit: int = Query(default=10, ge=1, le=5000),
    offset: int = Query(default=0, ge=0),
    include_all: bool = Query(default=False, alias="all"),
    user: MonitorUser = Depends(get_current_user),
):
    async with _get_session() as session:
        account = await _get_user_account(session, account_id, user)
        total = await _count_account_videos(session, account.platform, account.creator_id)
        videos = await _get_videos(
            session,
            account.platform,
            account.creator_id,
            limit=None if include_all else limit,
            offset=0 if include_all else offset,
        )
        return VideoListResponse(account_id=account.id, platform=account.platform, total=total, videos=videos)


# ---- Snapshots ----


@router.get("/accounts/{account_id}/snapshots", response_model=list[MonitorSnapshotResponse])
async def get_snapshots(
    account_id: int,
    days: int = Query(default=30, ge=1, le=365),
    user: MonitorUser = Depends(get_current_user),
):
    async with _get_session() as session:
        await _get_user_account(session, account_id, user)
        cutoff = int(time.time()) - days * 86400
        stmt = (
            select(MonitorSnapshot)
            .where(MonitorSnapshot.account_id == account_id, MonitorSnapshot.snapshot_ts >= cutoff)
            .order_by(MonitorSnapshot.snapshot_ts)
        )
        result = await session.execute(stmt)
        return [_snapshot_response(snapshot) for snapshot in result.scalars().all()]


# ---- Trigger ----


@router.post("/trigger")
async def trigger_crawl(
    platform: Optional[str] = Query(default=None),
    account_id: Optional[int] = Query(default=None),
    login_type: str = Query(default="qrcode"),
    cookies: str = Query(default=""),
    user: MonitorUser = Depends(get_current_user),
):
    """Start account crawl with QR code or cookie login."""
    from ..schemas.crawler import CrawlerStartRequest, CrawlerTypeEnum, LoginTypeEnum, PlatformEnum
    from ..services.crawler_manager import crawler_manager

    if crawler_manager.is_active():
        raise HTTPException(status_code=409, detail="Crawler is already running, please wait")

    lt = login_type.strip().lower()
    if lt not in ("qrcode", "cookie"):
        lt = "qrcode"
    cookie_str = (cookies or "").strip()
    if lt == "cookie" and not cookie_str:
        raise HTTPException(status_code=400, detail="Cookie 登录需要填写 cookie")

    async with _get_session() as session:
        if account_id:
            account = await _get_user_account(session, account_id, user)
            platform = _ensure_monitor_platform(account.platform)
            if not account.is_active:
                raise HTTPException(status_code=400, detail="This monitor account is disabled. Please enable it first.")
            accounts = [account]
        else:
            if not platform:
                raise HTTPException(status_code=400, detail="platform or account_id is required")
            platform = _ensure_monitor_platform(platform)
            result = await session.execute(
                select(MonitorAccount).where(
                    MonitorAccount.owner_user_id == user.id,
                    MonitorAccount.platform == platform,
                    MonitorAccount.is_active == 1,
                )
            )
            accounts = result.scalars().all()

    creator_ids = ",".join(account.creator_id for account in accounts if account.creator_id)
    if not creator_ids:
        raise HTTPException(
            status_code=400,
            detail=f"No active accounts found for platform '{platform}'. Please add monitor accounts first.",
        )

    config = CrawlerStartRequest(
        platform=PlatformEnum(platform),
        login_type=LoginTypeEnum(lt),
        crawler_type=CrawlerTypeEnum.CREATOR,
        creator_ids=creator_ids,
        monitor_account_id=account_id,
        enable_comments=False,
        enable_sub_comments=False,
        max_comments_count_singlenotes=0,
        save_option=_monitor_save_option(),
        cookies=cookie_str,
        headless=False,
    )
    started = await crawler_manager.start(config)
    if not started:
        raise HTTPException(status_code=500, detail="Failed to start crawler")
    if lt == "cookie":
        message = "Cookie 登录采集已启动，等待采集完成"
    else:
        message = "浏览器已启动，请扫码登录后等待采集完成"
    if account_id:
        message = f"单账号采集已启动：{accounts[0].display_name or accounts[0].creator_id}"
    return {"status": "started", "message": message, "creator_ids": creator_ids, "account_id": account_id}


@router.post("/videos/trigger")
async def trigger_video_crawl(
    platform: str = Query(...),
    video_id: str = Query(...),
    max_comments: int = Query(default=2000, ge=0, le=10000),
    include_sub_comments: bool = Query(default=False),
    login_type: str = Query(default="qrcode"),
    cookies: str = Query(default=""),
    user: MonitorUser = Depends(get_current_user),
):
    """Start a single video/post detail crawl and save it to the configured monitor database."""
    from ..schemas.crawler import CrawlerStartRequest, CrawlerTypeEnum, LoginTypeEnum, PlatformEnum
    from ..services.crawler_manager import crawler_manager

    platform = _ensure_monitor_platform(platform)
    video_id = (video_id or "").strip()
    if not video_id:
        raise HTTPException(status_code=400, detail="video_id is required")

    if crawler_manager.is_active():
        raise HTTPException(status_code=409, detail="Crawler is already running, please wait")

    lt = login_type.strip().lower()
    if lt not in ("qrcode", "cookie"):
        lt = "qrcode"
    cookie_str = (cookies or "").strip()
    if lt == "cookie" and not cookie_str:
        raise HTTPException(status_code=400, detail="Cookie 登录需要填写 cookie")

    config = CrawlerStartRequest(
        platform=PlatformEnum(platform),
        login_type=LoginTypeEnum(lt),
        crawler_type=CrawlerTypeEnum.DETAIL,
        specified_ids=video_id,
        enable_comments=max_comments > 0,
        enable_sub_comments=include_sub_comments,
        max_comments_count_singlenotes=max_comments,
        save_option=_monitor_save_option(),
        cookies=cookie_str,
        headless=False,
    )
    started = await crawler_manager.start(config)
    if not started:
        raise HTTPException(status_code=500, detail="Failed to start crawler")
    msg = "Cookie 登录采集已启动，等待完成" if lt == "cookie" else "单个作品采集已启动，请扫码登录后等待完成"
    return {"status": "started", "message": msg, "video_id": video_id}


# ---- Dashboard ----


@router.get("/dashboard", response_model=DashboardResponse)
async def get_dashboard(user: MonitorUser = Depends(get_current_user)):
    async with _get_session() as session:
        result = await session.execute(
            select(MonitorAccount)
            .where(MonitorAccount.owner_user_id == user.id, MonitorAccount.is_active == 1)
            .order_by(MonitorAccount.id)
        )
        accounts = result.scalars().all()

        account_dashboards = []
        total_followers = 0
        total_likes = 0
        total_videos = 0
        total_views = 0

        for account in accounts:
            snap_result = await session.execute(
                select(MonitorSnapshot)
                .where(MonitorSnapshot.account_id == account.id)
                .order_by(MonitorSnapshot.snapshot_ts.desc())
                .limit(1)
            )
            latest_snap = snap_result.scalar_one_or_none()
            latest_snapshot = _snapshot_response(latest_snap) if latest_snap else None

            video_total = await _count_account_videos(session, account.platform, account.creator_id)
            recent_videos = await _get_recent_videos(session, account.platform, account.creator_id)

            if latest_snap:
                total_followers += latest_snap.followers or 0
                total_likes += latest_snap.total_likes or 0
                total_views += latest_snap.total_views or 0
                total_videos += latest_snap.total_videos or video_total
            else:
                total_videos += video_total

            account_dashboards.append(
                AccountDashboard(
                    account_id=account.id,
                    platform=account.platform,
                    creator_id=account.creator_id,
                    display_name=account.display_name or "",
                    avatar_url=account.avatar_url or "",
                    latest_snapshot=latest_snapshot,
                    video_total=video_total,
                    recent_videos=recent_videos,
                )
            )

        return DashboardResponse(
            accounts=account_dashboards,
            totals={
                "total_accounts": len(accounts),
                "total_users": len(accounts),
                "total_followers": total_followers,
                "total_likes": total_likes,
                "total_videos": total_videos,
                "total_views": total_views,
            },
        )


def _video_interaction_count(video: VideoItem) -> int:
    return safe_int(video.liked_count) + safe_int(video.comment_count) + safe_int(video.share_count)


@router.get("/matrix")
async def get_matrix_dashboard(user: MonitorUser = Depends(get_current_user)):
    async with _get_session() as session:
        result = await session.execute(
            select(MonitorAccount)
            .where(MonitorAccount.owner_user_id == user.id, MonitorAccount.is_active == 1)
            .order_by(MonitorAccount.id)
        )
        accounts = result.scalars().all()

        cutoff = int(time.time()) - 86400
        platform_map = {}
        top_videos = []
        totals = {
            "total_accounts": len(accounts),
            "total_videos": 0,
            "total_followers": 0,
            "total_likes": 0,
            "total_views": 0,
            "total_interactions": 0,
            "followers_delta_24h": None,
            "likes_delta_24h": None,
            "interactions_delta_24h": None,
        }
        followers_delta = 0
        interactions_delta = 0
        has_delta = False

        for account in accounts:
            platform_stats = platform_map.setdefault(
                account.platform,
                {
                    "platform": account.platform,
                    "account_count": 0,
                    "video_count": 0,
                    "follower_count": 0,
                    "like_count": 0,
                    "interaction_count": 0,
                },
            )
            platform_stats["account_count"] += 1

            snap_result = await session.execute(
                select(MonitorSnapshot)
                .where(MonitorSnapshot.account_id == account.id)
                .order_by(MonitorSnapshot.snapshot_ts.desc())
                .limit(1)
            )
            latest_snap = snap_result.scalar_one_or_none()

            previous_result = await session.execute(
                select(MonitorSnapshot)
                .where(MonitorSnapshot.account_id == account.id, MonitorSnapshot.snapshot_ts <= cutoff)
                .order_by(MonitorSnapshot.snapshot_ts.desc())
                .limit(1)
            )
            previous_snap = previous_result.scalar_one_or_none()

            videos = await _get_videos(session, account.platform, account.creator_id, limit=None)
            video_count = len(videos)
            video_interactions = sum(_video_interaction_count(video) for video in videos)

            followers = safe_int(getattr(latest_snap, "followers", 0))
            likes = safe_int(getattr(latest_snap, "total_likes", 0))
            views = safe_int(getattr(latest_snap, "total_views", 0))
            snapshot_videos = safe_int(getattr(latest_snap, "total_videos", 0))
            effective_video_count = snapshot_videos or video_count
            effective_interactions = video_interactions or likes

            totals["total_videos"] += effective_video_count
            totals["total_followers"] += followers
            totals["total_likes"] += likes
            totals["total_views"] += views
            totals["total_interactions"] += effective_interactions

            platform_stats["video_count"] += effective_video_count
            platform_stats["follower_count"] += followers
            platform_stats["like_count"] += likes
            platform_stats["interaction_count"] += effective_interactions

            if latest_snap and previous_snap:
                followers_delta += followers - safe_int(previous_snap.followers)
                interactions_delta += likes - safe_int(previous_snap.total_likes)
                has_delta = True

            account_name = account.display_name or account.creator_id
            for video in videos:
                top_videos.append(
                    {
                        "platform": account.platform,
                        "account_id": account.id,
                        "account_name": account_name,
                        "video_id": video.video_id,
                        "title": video.title or video.desc or video.video_id,
                        "liked_count": video.liked_count,
                        "comment_count": video.comment_count,
                        "share_count": video.share_count,
                        "interaction_count": _video_interaction_count(video),
                        "create_time": video.create_time,
                        "video_url": video.video_url,
                    }
                )

        if has_delta:
            totals["followers_delta_24h"] = followers_delta
            totals["likes_delta_24h"] = interactions_delta
            totals["interactions_delta_24h"] = interactions_delta

        platforms = sorted(
            platform_map.values(),
            key=lambda item: (item["interaction_count"], item["follower_count"], item["video_count"]),
            reverse=True,
        )
        top_videos.sort(key=lambda item: item["interaction_count"], reverse=True)

        return {
            "kpis": totals,
            "platforms": platforms,
            "top_videos": top_videos[:10],
            "collection_plan": {
                "recommended_interval_hours": "2-4",
                "risk_notes": [
                    "优先使用爬虫专用号，不要用矩阵大号扫码。",
                    "关闭 VPN、代理或网络加速器后再采集；如需代理池，应统一配置并降低频率。",
                    "全量采集更慢且更容易触发平台校验，建议保持稳定巡检节奏。",
                    "建议每周检查一次登录态，失效后重新扫码。",
                ],
            },
        }


@router.post("/sync")
async def sync_data(
    platform: Optional[str] = None,
    account_id: Optional[int] = None,
    user: MonitorUser = Depends(get_current_user),
):
    """Create monitor snapshots from crawler data already saved in the configured monitor database."""
    if platform:
        _ensure_monitor_platform(platform)
    return await sync_monitor_snapshots(platform=platform, account_id=account_id, owner_user_id=user.id)


@router.get("/export")
async def export_data(
    account_id: Optional[int] = None,
    platform: Optional[str] = None,
    days: int = Query(default=30, ge=1, le=365),
    user: MonitorUser = Depends(get_current_user),
):
    """Export monitoring snapshots and video metrics as CSV."""
    if platform:
        _ensure_monitor_platform(platform)

    async with _get_session() as session:
        stmt = select(MonitorAccount).where(
            MonitorAccount.owner_user_id == user.id,
            MonitorAccount.is_active == 1,
        )
        if account_id:
            stmt = stmt.where(MonitorAccount.id == account_id)
        elif platform:
            stmt = stmt.where(MonitorAccount.platform == platform)
        result = await session.execute(stmt.order_by(MonitorAccount.id))
        accounts = result.scalars().all()

        buffer = io.StringIO()
        buffer.write("\ufeff")
        writer = csv.writer(buffer)

        writer.writerow(["=== 账号快照数据 ==="])
        writer.writerow(["账号ID", "平台", "创作者ID", "名称", "采集时间", "粉丝数", "总获赞", "作品数", "总播放", "最新作品点赞"])

        cutoff = int(time.time()) - days * 86400
        for account in accounts:
            snap_result = await session.execute(
                select(MonitorSnapshot)
                .where(MonitorSnapshot.account_id == account.id, MonitorSnapshot.snapshot_ts >= cutoff)
                .order_by(MonitorSnapshot.snapshot_ts)
            )
            for snapshot in snap_result.scalars().all():
                writer.writerow(
                    [
                        account.id,
                        account.platform,
                        account.creator_id,
                        account.display_name or "",
                        _format_ts(snapshot.snapshot_ts),
                        snapshot.followers or 0,
                        snapshot.total_likes or 0,
                        snapshot.total_videos or 0,
                        snapshot.total_views or 0,
                        snapshot.latest_video_likes or 0,
                    ]
                )

        writer.writerow([])
        writer.writerow(["=== 作品数据 ==="])
        writer.writerow(["账号ID", "平台", "创作者ID", "名称", "作品ID", "标题", "点赞量", "评论量", "分享", "发布时间", "链接"])

        for account in accounts:
            videos = await _get_videos(session, account.platform, account.creator_id, limit=None)
            for video in videos:
                writer.writerow(
                    [
                        account.id,
                        account.platform,
                        account.creator_id,
                        account.display_name or "",
                        video.video_id,
                        video.title or video.desc,
                        video.liked_count,
                        video.comment_count,
                        video.share_count,
                        _format_ts(video.create_time),
                        video.video_url,
                    ]
                )

        filename = f"monitor_export_{account_id or platform or 'all'}_{days}d_{int(time.time())}.csv"
        return _csv_response(buffer, filename)


@router.get("/videos/export")
async def export_video_data(
    platform: str = Query(...),
    video_id: str = Query(...),
    account_id: Optional[int] = Query(default=None),
    include_comments: bool = Query(default=False),
    user: MonitorUser = Depends(get_current_user),
):
    """Export a single video/post as CSV, optionally including saved comments."""
    platform = _ensure_monitor_platform(platform)
    normalized_video_id = _normalize_video_id(platform, video_id)
    if not normalized_video_id:
        raise HTTPException(status_code=400, detail="video_id is required")

    async with _get_session() as session:
        if account_id:
            await _get_user_account(session, account_id, user)
        video = await _get_user_video_by_id(session, platform, normalized_video_id, user, account_id=account_id)
        if not video:
            raise HTTPException(status_code=404, detail=_video_not_found_detail(platform, video_id))

        item = _video_item_from_row(platform, video)
        comments = await _get_video_comments(session, platform, item.video_id) if include_comments else []

        buffer = io.StringIO()
        buffer.write("\ufeff")
        writer = csv.writer(buffer)

        writer.writerow(["=== 作品数据 ==="])
        writer.writerow(["平台", "作品ID", "标题", "描述", "点赞数", "评论数", "分享数", "发布时间", "链接"])
        writer.writerow(
            [
                platform,
                item.video_id,
                item.title,
                item.desc,
                item.liked_count,
                item.comment_count,
                item.share_count,
                _format_ts(item.create_time),
                item.video_url,
            ]
        )

        if include_comments:
            writer.writerow([])
            writer.writerow(["=== 评论数据 ==="])
            writer.writerow(["作品ID", "评论内容", "评论者", "点赞数", "评论时间"])
            for comment in comments:
                comment_video_id, content, nickname, like_count, comment_time = _comment_fields(platform, comment)
                writer.writerow([comment_video_id, content, nickname, like_count, comment_time])

        safe_video_id = re.sub(r"[^0-9A-Za-z_-]+", "_", item.video_id)[:80] or "video"
        export_kind = "monitor_video" if include_comments else "monitor_video_metrics"
        filename = f"{export_kind}_{platform}_{safe_video_id}.csv"
        return _csv_response(buffer, filename)
