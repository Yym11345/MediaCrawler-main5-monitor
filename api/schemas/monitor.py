# -*- coding: utf-8 -*-
from typing import Optional, List
from pydantic import BaseModel

from .crawler import PlatformEnum


class MonitorAccountCreate(BaseModel):
    platform: PlatformEnum
    creator_id: str
    display_name: str = ""


class MonitorAccountResponse(BaseModel):
    id: int
    platform: str
    creator_id: str
    display_name: str
    avatar_url: str
    is_active: int
    created_at: Optional[int] = None
    last_crawl_at: Optional[int] = None
    last_crawl_status: str


class MonitorSnapshotResponse(BaseModel):
    id: int
    account_id: int
    snapshot_ts: int
    followers: int
    total_likes: int
    total_videos: int
    total_views: int
    latest_video_likes: int
    latest_video_id: str
    raw_json: str = "{}"


class VideoItem(BaseModel):
    video_id: str
    title: str
    desc: str = ""
    liked_count: int = 0
    comment_count: int = 0
    share_count: int = 0
    create_time: int = 0
    video_url: str = ""


class VideoListResponse(BaseModel):
    account_id: int
    platform: str
    total: int = 0
    videos: List[VideoItem] = []


class AccountDashboard(BaseModel):
    account_id: int
    platform: str
    creator_id: str
    display_name: str
    avatar_url: str
    latest_snapshot: Optional[MonitorSnapshotResponse] = None
    video_total: int = 0
    recent_videos: List[VideoItem] = []


class DashboardResponse(BaseModel):
    accounts: List[AccountDashboard] = []
    totals: dict = {}
