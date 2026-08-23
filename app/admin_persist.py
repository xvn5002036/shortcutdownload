from __future__ import annotations

import hashlib
import os
import secrets
from pathlib import Path

from fastapi import Cookie, HTTPException, Response
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field

from .main import ADMIN_TOKEN_SHA256, SESSION_COOKIE, SESSION_MAX_AGE, app, hash_matches, session_value


class RestoreRequest(BaseModel):
    token: str = Field(min_length=32, max_length=200)


class LoginRequest(BaseModel):
    password: str = Field(min_length=1, max_length=200)


def restore_value() -> str:
    secret = os.getenv("XHS_ADMIN_TOKEN", "")
    if not secret:
        return ""
    return hashlib.sha256((secret + ":xhs-admin-iphone-restore-v1").encode("utf-8")).hexdigest()


def _set_admin_cookie(response: Response) -> None:
    token = session_value()
    if not token:
        raise HTTPException(503, "伺服器尚未設定管理密鑰")
    response.set_cookie(
        key=SESSION_COOKIE,
        value=token,
        max_age=SESSION_MAX_AGE,
        httponly=True,
        secure=True,
        samesite="lax",
        path="/",
    )


# 只接管管理後台登入相關路由，不碰下載、授權、媒體解析。
app.router.routes[:] = [
    route for route in app.router.routes
    if getattr(route, "path", None) not in {
        "/admin",
        "/api/admin/login",
        "/api/admin/logout",
        "/api/admin/restore",
    }
]


@app.get("/admin", response_class=HTMLResponse)
def admin_page_persistent(xhs_admin_session: str | None = Cookie(default=None)) -> str:
    expected = session_value()
    filename = "admin.html" if expected and xhs_admin_session and secrets.compare_digest(xhs_admin_session, expected) else "login.html"
    return Path(__file__).with_name(filename).read_text(encoding="utf-8")


@app.post("/api/admin/login")
def admin_login_persistent(request: LoginRequest, response: Response):
    if not hash_matches(request.password, ADMIN_TOKEN_SHA256):
        raise HTTPException(401, "管理密碼錯誤")
    _set_admin_cookie(response)
    restore = restore_value()
    if not restore:
        raise HTTPException(503, "伺服器尚未設定管理密鑰")
    return {"success": True, "restore_token": restore}


@app.post("/api/admin/restore")
def admin_restore(request: RestoreRequest, response: Response):
    expected = restore_value()
    if not expected or not secrets.compare_digest(request.token, expected):
        raise HTTPException(401, "自動登入憑證已失效")
    _set_admin_cookie(response)
    return {"success": True}


@app.post("/api/admin/logout")
def admin_logout_persistent(response: Response):
    response.delete_cookie(SESSION_COOKIE, path="/")
    return {"success": True, "keep_device_restore": True}
