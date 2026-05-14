# -*- coding: utf-8 -*-
import json
import re
import time
from contextlib import asynccontextmanager
from typing import Any, Optional

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import sessionmaker

from database.db_session import get_async_engine, get_monitor_db_type
from database.models import (
    BilibiliUpInfo,
    BilibiliVideo,
    DouyinAweme,
    DyCreator,
    MonitorAccount,
    MonitorSnapshot,
    XhsCreator,
    XhsNote,
)


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


def safe_int(value: Any) -> int:
    if value is None:
        return 0
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, (int, float)):
        return int(value)

    text = str(value).strip().replace(",", "")
    if not text or text in {"-", "--", "None", "null"}:
        return 0

    multiplier = 1
    lowered = text.lower()
    if "\u4ebf" in lowered:
        multiplier = 100000000
    elif "\u4e07" in lowered or "w" in lowered:
        multiplier = 10000
    elif "k" in lowered:
        multiplier = 1000

    match = re.search(r"-?\d+(?:\.\d+)?", lowered)
    if not match:
        return 0

    number_text = match.group(0)
    if multiplier == 1 and "." not in number_text:
        return int(number_text)
    return int(float(number_text) * multiplier)


def normalize_creator_id(platform: str, creator_id: str) -> str:
    value = (creator_id or "").strip()

    if platform == "bili":
        if value.isdigit():
            return value
        match = re.search(r"space\.bilibili\.com/(\d+)", value)
        if match:
            return match.group(1)
        match = re.search(r"/(\d+)(?:[/?#]|$)", value)
        return match.group(1) if match else value

    if platform == "dy":
        match = re.search(r"/user/([^/?#]+)", value)
        return match.group(1) if match else value

    if platform == "xhs":
        match = re.search(r"/user/profile/([^/?#]+)", value)
        return match.group(1) if match else value

    return value


async def mark_monitor_accounts(platform: str, status: str, account_id: Optional[int] = None) -> int:
    now = int(time.time())
    async with _get_session() as session:
        stmt = select(MonitorAccount).where(MonitorAccount.platform == platform)
        if account_id:
            stmt = stmt.where(MonitorAccount.id == account_id)
        else:
            stmt = stmt.where(MonitorAccount.is_active == 1)
        result = await session.execute(stmt)
        accounts = result.scalars().all()
        for account in accounts:
            account.last_crawl_status = status
            account.last_crawl_at = now
        return len(accounts)


async def sync_monitor_snapshots(
    platform: Optional[str] = None,
    account_id: Optional[int] = None,
    mark_status: Optional[str] = None,
    owner_user_id: Optional[int] = None,
) -> dict:
    now = int(time.time())
    async with _get_session() as session:
        stmt = select(MonitorAccount).order_by(MonitorAccount.id)
        if platform:
            stmt = stmt.where(MonitorAccount.platform == platform)
        if account_id:
            stmt = stmt.where(MonitorAccount.id == account_id)
        if owner_user_id:
            stmt = stmt.where(MonitorAccount.owner_user_id == owner_user_id)

        result = await session.execute(stmt)
        accounts = result.scalars().all()

        synced = 0
        with_source_data = 0
        for account in accounts:
            metrics = await collect_account_metrics(session, account)
            if metrics["has_source_data"]:
                with_source_data += 1

            if not account.display_name and metrics["display_name"]:
                account.display_name = metrics["display_name"]
            if metrics["avatar_url"]:
                account.avatar_url = metrics["avatar_url"]

            session.add(
                MonitorSnapshot(
                    account_id=account.id,
                    snapshot_ts=now,
                    followers=metrics["followers"],
                    total_likes=metrics["total_likes"],
                    total_videos=metrics["total_videos"],
                    total_views=metrics["total_views"],
                    latest_video_likes=metrics["latest_video_likes"],
                    latest_video_id=metrics["latest_video_id"],
                    raw_json=json.dumps(metrics["raw"], ensure_ascii=False),
                )
            )
            account.last_crawl_at = now
            if mark_status:
                account.last_crawl_status = mark_status
            elif metrics["has_source_data"]:
                account.last_crawl_status = "success"
            elif account.last_crawl_status == "running":
                account.last_crawl_status = "failed"

            synced += 1

        return {
            "status": "ok",
            "synced_accounts": synced,
            "accounts_with_source_data": with_source_data,
            "snapshot_ts": now,
        }


async def collect_account_metrics(session: AsyncSession, account: MonitorAccount) -> dict:
    normalized_id = normalize_creator_id(account.platform, account.creator_id)

    if account.platform == "dy":
        return await _collect_douyin_metrics(session, normalized_id)
    if account.platform == "xhs":
        return await _collect_xhs_metrics(session, normalized_id)
    if account.platform == "bili":
        return await _collect_bili_metrics(session, normalized_id)

    return _empty_metrics(normalized_id)


def _empty_metrics(normalized_id: str) -> dict:
    return {
        "followers": 0,
        "total_likes": 0,
        "total_videos": 0,
        "total_views": 0,
        "latest_video_likes": 0,
        "latest_video_id": "",
        "display_name": "",
        "avatar_url": "",
        "has_source_data": False,
        "raw": {"normalized_creator_id": normalized_id},
    }


async def _count(session: AsyncSession, model, *conditions) -> int:
    stmt = select(func.count()).select_from(model)
    if conditions:
        stmt = stmt.where(*conditions)
    result = await session.execute(stmt)
    return int(result.scalar() or 0)


async def _collect_douyin_metrics(session: AsyncSession, creator_id: str) -> dict:
    creator = (
        await session.execute(
            select(DyCreator)
            .where(DyCreator.user_id == creator_id)
            .order_by(DyCreator.last_modify_ts.desc())
            .limit(1)
        )
    ).scalar_one_or_none()

    video_filter = or_(
        DouyinAweme.sec_uid == creator_id,
        DouyinAweme.user_id == creator_id,
        DouyinAweme.user_unique_id == creator_id,
    )
    video_count = await _count(session, DouyinAweme, video_filter)
    latest = (
        await session.execute(
            select(DouyinAweme).where(video_filter).order_by(DouyinAweme.create_time.desc()).limit(1)
        )
    ).scalar_one_or_none()

    liked_rows = (await session.execute(select(DouyinAweme.liked_count).where(video_filter))).all()
    total_likes_from_videos = sum(safe_int(row[0]) for row in liked_rows)

    return {
        "followers": safe_int(getattr(creator, "fans", None)),
        "total_likes": safe_int(getattr(creator, "interaction", None)) or total_likes_from_videos,
        "total_videos": safe_int(getattr(creator, "videos_count", None)) or video_count,
        "total_views": 0,
        "latest_video_likes": safe_int(getattr(latest, "liked_count", None)),
        "latest_video_id": str(getattr(latest, "aweme_id", "") or ""),
        "display_name": getattr(creator, "nickname", "") or getattr(latest, "nickname", "") or "",
        "avatar_url": getattr(creator, "avatar", "") or getattr(latest, "avatar", "") or "",
        "has_source_data": bool(creator or video_count),
        "raw": {
            "normalized_creator_id": creator_id,
            "video_count_from_db": video_count,
            "total_likes_from_videos": total_likes_from_videos,
        },
    }


async def _collect_xhs_metrics(session: AsyncSession, creator_id: str) -> dict:
    creator = (
        await session.execute(
            select(XhsCreator)
            .where(XhsCreator.user_id == creator_id)
            .order_by(XhsCreator.last_modify_ts.desc())
            .limit(1)
        )
    ).scalar_one_or_none()

    note_filter = XhsNote.user_id == creator_id
    note_count = await _count(session, XhsNote, note_filter)
    latest = (
        await session.execute(select(XhsNote).where(note_filter).order_by(XhsNote.time.desc()).limit(1))
    ).scalar_one_or_none()

    liked_rows = (await session.execute(select(XhsNote.liked_count).where(note_filter))).all()
    total_likes_from_notes = sum(safe_int(row[0]) for row in liked_rows)

    return {
        "followers": safe_int(getattr(creator, "fans", None)),
        "total_likes": safe_int(getattr(creator, "interaction", None)) or total_likes_from_notes,
        "total_videos": note_count,
        "total_views": 0,
        "latest_video_likes": safe_int(getattr(latest, "liked_count", None)),
        "latest_video_id": str(getattr(latest, "note_id", "") or ""),
        "display_name": getattr(creator, "nickname", "") or getattr(latest, "nickname", "") or "",
        "avatar_url": getattr(creator, "avatar", "") or getattr(latest, "avatar", "") or "",
        "has_source_data": bool(creator or note_count),
        "raw": {
            "normalized_creator_id": creator_id,
            "note_count_from_db": note_count,
            "total_likes_from_notes": total_likes_from_notes,
        },
    }


async def _collect_bili_metrics(session: AsyncSession, creator_id: str) -> dict:
    uid = safe_int(creator_id)
    if not uid:
        return _empty_metrics(creator_id)

    creator = (
        await session.execute(
            select(BilibiliUpInfo)
            .where(BilibiliUpInfo.user_id == uid)
            .order_by(BilibiliUpInfo.last_modify_ts.desc())
            .limit(1)
        )
    ).scalar_one_or_none()

    video_filter = BilibiliVideo.user_id == uid
    video_count = await _count(session, BilibiliVideo, video_filter)
    latest = (
        await session.execute(
            select(BilibiliVideo).where(video_filter).order_by(BilibiliVideo.create_time.desc()).limit(1)
        )
    ).scalar_one_or_none()

    stat_rows = (
        await session.execute(
            select(BilibiliVideo.liked_count, BilibiliVideo.video_play_count).where(video_filter)
        )
    ).all()
    total_likes_from_videos = sum(safe_int(row[0]) for row in stat_rows)
    total_views = sum(safe_int(row[1]) for row in stat_rows)

    return {
        "followers": safe_int(getattr(creator, "total_fans", None)),
        "total_likes": safe_int(getattr(creator, "total_liked", None)) or total_likes_from_videos,
        "total_videos": video_count,
        "total_views": total_views,
        "latest_video_likes": safe_int(getattr(latest, "liked_count", None)),
        "latest_video_id": str(getattr(latest, "video_id", "") or ""),
        "display_name": getattr(creator, "nickname", "") or getattr(latest, "nickname", "") or "",
        "avatar_url": getattr(creator, "avatar", "") or getattr(latest, "avatar", "") or "",
        "has_source_data": bool(creator or video_count),
        "raw": {
            "normalized_creator_id": str(uid),
            "video_count_from_db": video_count,
            "total_likes_from_videos": total_likes_from_videos,
        },
    }
