import base64
import hashlib
import hmac
import os
import re
import secrets
import time
from contextlib import asynccontextmanager
from typing import Optional

from fastapi import Cookie, HTTPException, Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import sessionmaker

from database.db_session import get_async_engine, get_monitor_db_type
from database.models import MonitorUser

AUTH_COOKIE_NAME = "monitor_session"
SESSION_TTL_SECONDS = 7 * 86400
_EMAIL_RE = re.compile(r"^[A-Za-z0-9._%+-]+@(qq\.com|163\.com)$", re.IGNORECASE)


@asynccontextmanager
async def get_auth_session():
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


def normalize_email(email: str) -> str:
    value = (email or "").strip().lower()
    if not _EMAIL_RE.match(value):
        raise HTTPException(status_code=400, detail="仅支持 QQ 邮箱或 163 邮箱")
    return value


def validate_password(password: str) -> str:
    value = (password or "").strip()
    if len(value) < 6:
        raise HTTPException(status_code=400, detail="密码至少 6 位")
    return value


def _auth_secret() -> bytes:
    return os.getenv("AUTH_SECRET", "monitor-dev-secret-change-me").encode("utf-8")


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, 120000)
    return "pbkdf2_sha256$120000$" + base64.urlsafe_b64encode(salt).decode() + "$" + base64.urlsafe_b64encode(digest).decode()


def verify_password(password: str, password_hash: str) -> bool:
    try:
        algorithm, rounds_text, salt_text, digest_text = password_hash.split("$", 3)
        if algorithm != "pbkdf2_sha256":
            return False
        salt = base64.urlsafe_b64decode(salt_text.encode())
        expected = base64.urlsafe_b64decode(digest_text.encode())
        actual = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, int(rounds_text))
        return hmac.compare_digest(actual, expected)
    except Exception:
        return False


def create_session_token(user_id: int, issued_at: Optional[int] = None) -> str:
    issued_at = issued_at or int(time.time())
    payload = f"{user_id}:{issued_at}"
    signature = hmac.new(_auth_secret(), payload.encode("utf-8"), hashlib.sha256).hexdigest()
    raw = f"{payload}:{signature}".encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("ascii")


def read_session_token(token: str) -> tuple[int, int]:
    try:
        raw = base64.urlsafe_b64decode(token.encode("ascii")).decode("utf-8")
        user_id_text, issued_at_text, signature = raw.split(":", 2)
        payload = f"{user_id_text}:{issued_at_text}"
        expected = hmac.new(_auth_secret(), payload.encode("utf-8"), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(signature, expected):
            raise ValueError("bad signature")
        issued_at = int(issued_at_text)
        if int(time.time()) - issued_at > SESSION_TTL_SECONDS:
            raise ValueError("expired")
        return int(user_id_text), issued_at
    except Exception:
        raise HTTPException(status_code=401, detail="请先登录")


def set_auth_cookie(response: Response, user_id: int) -> None:
    response.set_cookie(
        AUTH_COOKIE_NAME,
        create_session_token(user_id),
        max_age=SESSION_TTL_SECONDS,
        httponly=True,
        samesite="lax",
        path="/",
    )


def clear_auth_cookie(response: Response) -> None:
    response.delete_cookie(AUTH_COOKIE_NAME, path="/")


async def get_current_user(monitor_session: Optional[str] = Cookie(default=None, alias=AUTH_COOKIE_NAME)) -> MonitorUser:
    if not monitor_session:
        raise HTTPException(status_code=401, detail="请先登录")
    user_id, _issued_at = read_session_token(monitor_session)
    async with get_auth_session() as session:
        result = await session.execute(
            select(MonitorUser).where(MonitorUser.id == user_id, MonitorUser.is_active == 1)
        )
        user = result.scalar_one_or_none()
        if not user:
            raise HTTPException(status_code=401, detail="请先登录")
        return user
