import time

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel
from sqlalchemy import select

from api.services.auth import (
    clear_auth_cookie,
    get_auth_session,
    get_current_user,
    hash_password,
    normalize_email,
    set_auth_cookie,
    validate_password,
    verify_password,
)
from database.models import MonitorUser

router = APIRouter(prefix="/auth", tags=["auth"])


class AuthRequest(BaseModel):
    email: str
    password: str


def _user_payload(user: MonitorUser) -> dict:
    return {"id": user.id, "email": user.email}


@router.post("/register")
async def register(body: AuthRequest, response: Response):
    email = normalize_email(body.email)
    password = validate_password(body.password)
    async with get_auth_session() as session:
        existing = await session.execute(select(MonitorUser).where(MonitorUser.email == email))
        if existing.scalar_one_or_none():
            raise HTTPException(status_code=409, detail="邮箱已注册")

        user = MonitorUser(
            email=email,
            password_hash=hash_password(password),
            is_active=1,
            created_at=int(time.time()),
        )
        session.add(user)
        await session.flush()
        set_auth_cookie(response, user.id)
        return _user_payload(user)


@router.post("/login")
async def login(body: AuthRequest, response: Response):
    email = normalize_email(body.email)
    password = validate_password(body.password)
    async with get_auth_session() as session:
        result = await session.execute(
            select(MonitorUser).where(MonitorUser.email == email, MonitorUser.is_active == 1)
        )
        user = result.scalar_one_or_none()
        if not user or not verify_password(password, user.password_hash):
            raise HTTPException(status_code=401, detail="邮箱或密码错误")
        set_auth_cookie(response, user.id)
        return _user_payload(user)


@router.post("/logout")
async def logout(response: Response):
    clear_auth_cookie(response)
    return {"status": "ok"}


@router.get("/me")
async def me(user: MonitorUser = Depends(get_current_user)):
    return _user_payload(user)
