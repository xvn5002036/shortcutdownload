from __future__ import annotations

import asyncio
import ipaddress
import os
import shutil
import socket
import subprocess
import tempfile
import zipfile
from pathlib import Path
from urllib.parse import urlparse

from fastapi import BackgroundTasks, FastAPI, Header, HTTPException, Query
from fastapi.responses import FileResponse, HTMLResponse, PlainTextResponse
from pydantic import BaseModel, Field

from .db import (
    create_license,
    delete_license,
    list_devices,
    list_licenses,
    list_logs,
    log_request,
    set_license_status,
    unbind_device,
    verify_and_bind,
)


ALLOWED_HOSTS = {
    "facebook.com", "www.facebook.com", "m.facebook.com", "fb.watch",
    "instagram.com", "www.instagram.com",
    "x.com", "www.x.com", "twitter.com", "www.twitter.com", "t.co",
    "bilibili.com", "www.bilibili.com", "m.bilibili.com", "b23.tv",
    "youtube.com", "www.youtube.com", "m.youtube.com", "youtu.be",
    "xiaohongshu.com", "www.xiaohongshu.com", "m.xiaohongshu.com",
    "live.xiaohongshu.com", "xhslink.com", "www.xhslink.com",
}
MAX_BYTES = int(os.getenv("MAX_DOWNLOAD_MB", "250")) * 1024 * 1024
TIMEOUT_SECONDS = int(os.getenv("DOWNLOAD_TIMEOUT_SECONDS", "180"))
API_KEY = os.getenv("DOWNLOAD_API_KEY", "")
ADMIN_TOKEN = os.getenv("XHS_ADMIN_TOKEN", "change-me")

app = FastAPI(title="XHS Pro System", version="1.1.0")


class DownloadRequest(BaseModel):
    url: str = Field(min_length=10, max_length=2048)


class LicenseCreateRequest(BaseModel):
    days: int | None = Field(default=None, ge=1, le=3650)
    max_devices: int = Field(default=1, ge=1, le=50)
    note: str = Field(default="", max_length=500)
    key: str | None = Field(default=None, max_length=100)


class StatusRequest(BaseModel):
    status: str


def validate_url(raw_url: str) -> str:
    try:
        parsed = urlparse(raw_url.strip())
    except ValueError as exc:
        raise HTTPException(400, "網址格式不正確") from exc
    host = (parsed.hostname or "").lower().rstrip(".")
    if parsed.scheme != "https" or host not in ALLOWED_HOSTS:
        raise HTTPException(400, "只接受 Facebook、Instagram、X、Bilibili、YouTube、小紅書的 HTTPS 網址")
    try:
        for result in socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM):
            address = ipaddress.ip_address(result[4][0])
            if not address.is_global:
                raise HTTPException(400, "網址解析到不安全的位址")
    except socket.gaierror as exc:
        raise HTTPException(400, "找不到網址主機") from exc
    return raw_url.strip()


def check_key(value: str | None) -> None:
    if API_KEY and value != API_KEY:
        raise HTTPException(401, "API 金鑰錯誤")


def check_admin(value: str | None) -> None:
    if not value or value != ADMIN_TOKEN:
        raise HTTPException(401, "管理員權杖錯誤")


def run_command(command: list[str], cwd: Path) -> tuple[int, str]:
    try:
        result = subprocess.run(
            command, cwd=cwd, capture_output=True, text=True,
            timeout=TIMEOUT_SECONDS, check=False,
        )
        return result.returncode, (result.stderr or result.stdout)[-3000:]
    except subprocess.TimeoutExpired:
        return 124, "下載逾時"


def media_files(folder: Path) -> list[Path]:
    ignored = {"result.zip", "metadata.json"}
    return sorted(
        p for p in folder.rglob("*")
        if p.is_file() and p.name not in ignored and not p.name.endswith((".part", ".ytdl"))
    )


def build_download(raw_url: str) -> tuple[Path, Path]:
    folder = Path(tempfile.mkdtemp(prefix="media-download-"))
    output = "%(title).80B-%(id)s.%(ext)s"
    yt_command = [
        "yt-dlp", "--no-playlist", "--restrict-filenames", "--no-write-info-json",
        "--max-filesize", str(MAX_BYTES), "--merge-output-format", "mp4",
        "-f", "bv*+ba/b", "-o", output, raw_url,
    ]
    yt_code, yt_error = run_command(yt_command, folder)
    files = media_files(folder)

    input_host = (urlparse(raw_url).hostname or "").lower()
    if not files and (input_host.endswith("xiaohongshu.com") or input_host.endswith("xhslink.com")):
        image_command = [
            "yt-dlp", "--no-playlist", "--restrict-filenames", "--skip-download",
            "--write-all-thumbnails", "--convert-thumbnails", "jpg",
            "-o", "%(title).80B-%(id)s-%(thumbnail_id)s.%(ext)s", raw_url,
        ]
        image_code, image_error = run_command(image_command, folder)
        files = media_files(folder)
    else:
        image_code, image_error = 0, ""

    if not files:
        gallery_command = [
            "gallery-dl", "--no-mtime", "--dest", str(folder),
            "--filename", "{category}_{id}_{num}.{extension}", raw_url,
        ]
        gallery_code, gallery_error = run_command(gallery_command, folder)
        files = media_files(folder)
        if not files:
            shutil.rmtree(folder, ignore_errors=True)
            combined_error = f"{yt_error}\n{image_error}\n{gallery_error}".lower()
            if "login" in combined_error or "cookie" in combined_error:
                raise RuntimeError("AUTH_REQUIRED")
            if yt_code == 124 or image_code == 124 or gallery_code == 124:
                raise RuntimeError("下載逾時，請稍後重試或使用較短的內容")
            raise RuntimeError("找不到可下載的公開媒體；連結可能已失效、不是公開內容，或平台解析規則已改變")

    total = sum(item.stat().st_size for item in files)
    if total > MAX_BYTES:
        shutil.rmtree(folder, ignore_errors=True)
        raise RuntimeError(f"檔案超過 {MAX_BYTES // 1024 // 1024} MB 上限")

    if len(files) == 1:
        return folder, files[0]

    archive = folder / "result.zip"
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as bundle:
        for item in files:
            bundle.write(item, item.name)
    return folder, archive


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok", "service": "xhs-pro-system"}


@app.get("/", response_class=HTMLResponse)
def home() -> str:
    return Path(__file__).with_name("index.html").read_text(encoding="utf-8")


@app.get("/admin", response_class=HTMLResponse)
def admin_page() -> str:
    return Path(__file__).with_name("admin.html").read_text(encoding="utf-8")


@app.get("/xhszshq", response_class=PlainTextResponse)
def xhszshq(
    a: str = Query(default=""),
    b: str = Query(default="ios"),
    c: str = Query(default=""),
    device_id: str = Query(default=""),
) -> str:
    """Compatibility endpoint for the iOS Shortcut.

    Current phase reproduces the verified activation/device-binding behavior:
    invalid/disabled/expired/device-limit -> 0, success -> 1.
    The remaining original payload semantics will be filled in as the shortcut
    workflow is decoded and compared action-by-action.
    """
    ok, reason = verify_and_bind(a, device_id, b)
    log_request(a, device_id, b, reason, c)
    return "1" if ok else "0"


@app.get("/api/verify")
def verify_license(
    license_key: str,
    device_id: str,
    platform: str = "ios",
):
    ok, reason = verify_and_bind(license_key, device_id, platform)
    log_request(license_key, device_id, platform, reason)
    return {"success": ok, "reason": reason}


@app.post("/api/download")
async def download(
    request: DownloadRequest,
    background_tasks: BackgroundTasks,
    x_api_key: str | None = Header(default=None),
):
    check_key(x_api_key)
    url = validate_url(request.url)
    try:
        folder, result = await asyncio.to_thread(build_download, url)
    except RuntimeError as exc:
        message = str(exc)
        if message == "AUTH_REQUIRED":
            message = "這則內容需要登入或 Cookie；公開下載服務不支援私人／登入限定內容。"
        raise HTTPException(422, message) from exc
    background_tasks.add_task(shutil.rmtree, folder, True)
    media_type = "application/zip" if result.suffix == ".zip" else "application/octet-stream"
    return FileResponse(result, filename=result.name, media_type=media_type, background=background_tasks)


@app.get("/api/admin/licenses")
def admin_list_licenses(x_admin_token: str | None = Header(default=None)):
    check_admin(x_admin_token)
    return {"items": list_licenses()}


@app.post("/api/admin/licenses")
def admin_create_license(
    request: LicenseCreateRequest,
    x_admin_token: str | None = Header(default=None),
):
    check_admin(x_admin_token)
    try:
        item = create_license(days=request.days, max_devices=request.max_devices, note=request.note, key=request.key)
    except Exception as exc:
        raise HTTPException(400, f"建立啟用碼失敗：{exc}") from exc
    return item


@app.patch("/api/admin/licenses/{license_id}/status")
def admin_change_status(
    license_id: int,
    request: StatusRequest,
    x_admin_token: str | None = Header(default=None),
):
    check_admin(x_admin_token)
    try:
        set_license_status(license_id, request.status)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {"success": True}


@app.delete("/api/admin/licenses/{license_id}")
def admin_delete_license(license_id: int, x_admin_token: str | None = Header(default=None)):
    check_admin(x_admin_token)
    delete_license(license_id)
    return {"success": True}


@app.get("/api/admin/devices")
def admin_list_devices(x_admin_token: str | None = Header(default=None)):
    check_admin(x_admin_token)
    return {"items": list_devices()}


@app.delete("/api/admin/devices/{device_row_id}")
def admin_unbind_device(device_row_id: int, x_admin_token: str | None = Header(default=None)):
    check_admin(x_admin_token)
    unbind_device(device_row_id)
    return {"success": True}


@app.get("/api/admin/logs")
def admin_logs(
    limit: int = Query(default=200, ge=1, le=1000),
    x_admin_token: str | None = Header(default=None),
):
    check_admin(x_admin_token)
    return {"items": list_logs(limit)}
