from __future__ import annotations

import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path
from urllib.parse import quote, urlparse
from urllib.request import Request as URLRequest, urlopen

from fastapi import HTTPException, Query
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse
from starlette.background import BackgroundTask

from .compat import (
    UA,
    PUBLIC_BASE,
    app,
    inspect_note,
    log_request,
    normalize_xhs_url,
    proxy_image_url,
    verify_and_bind,
)

# 移除 compat.py 的舊 /xhszshq，保留 /media/image。
app.router.routes[:] = [
    route for route in app.router.routes
    if getattr(route, "path", None) not in {"/xhszshq", "/media/video"}
]

MAX_VIDEO_PROXY_BYTES = int(os.getenv("MAX_VIDEO_PROXY_MB", "500")) * 1024 * 1024


def proxy_video_url(remote_url: str) -> str:
    if not remote_url:
        return ""
    return f"{PUBLIC_BASE}/media/video?url={quote(remote_url, safe='')}"


def is_allowed_remote_video(value: str) -> bool:
    try:
        parsed = urlparse(value)
        host = (parsed.hostname or "").lower()
        return parsed.scheme in {"http", "https"} and (
            "xhscdn.com" in host
            or "xiaohongshu.com" in host
            or "xhslink.com" in host
        )
    except Exception:
        return False


def live_video_identity(value: str) -> str:
    """把主 CDN / bak CDN 的同一支 Live 影片視為同一素材。"""
    try:
        path = urlparse(value).path
        name = path.rsplit("/", 1)[-1].lower()
        match = re.search(r"([0-9a-f]{20,}_[0-9]+)\.mp4$", name)
        if match:
            return match.group(1)
        return name or value
    except Exception:
        return value


def dedupe_live_videos(values: list[str]) -> list[str]:
    seen = set()
    result = []
    for value in values:
        if not value:
            continue
        identity = live_video_identity(value)
        if identity in seen:
            continue
        seen.add(identity)
        result.append(value)
    return result


def _download_video_to_file(remote_url: str, source_path: Path) -> None:
    """分段完整下載來源影片，避免舊版一次 read(120MB) 把高清影片截斷。"""
    req = URLRequest(remote_url, headers={
        "User-Agent": UA,
        "Accept": "video/mp4,video/*;q=0.9,*/*;q=0.8",
        "Referer": "https://www.xiaohongshu.com/",
    })
    with urlopen(req, timeout=60) as resp:
        upstream_type = resp.headers.get_content_type() or ""
        if upstream_type and not (
            upstream_type.startswith("video/")
            or upstream_type in {"application/octet-stream", "binary/octet-stream"}
        ):
            raise HTTPException(status_code=502, detail="remote resource is not a video")

        content_length = resp.headers.get("Content-Length")
        if content_length:
            try:
                if int(content_length) > MAX_VIDEO_PROXY_BYTES:
                    raise HTTPException(status_code=413, detail="video is too large")
            except ValueError:
                pass

        total = 0
        with source_path.open("wb") as output:
            while True:
                chunk = resp.read(1024 * 1024)
                if not chunk:
                    break
                total += len(chunk)
                if total > MAX_VIDEO_PROXY_BYTES:
                    raise HTTPException(status_code=413, detail="video is too large")
                output.write(chunk)

        if total <= 0:
            raise HTTPException(status_code=502, detail="empty video response")


def _remux_file_to_ios_mp4(source_path: Path, output_path: Path) -> None:
    """只重新封裝容器，不重編碼，不加入浮水印。"""
    result = subprocess.run(
        [
            "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
            "-i", str(source_path),
            "-map", "0:v:0?", "-map", "0:a:0?",
            "-c", "copy",
            "-movflags", "+faststart",
            str(output_path),
        ],
        capture_output=True,
        timeout=180,
        check=False,
    )
    if result.returncode != 0 or not output_path.exists() or output_path.stat().st_size <= 0:
        message = (result.stderr or b"").decode("utf-8", errors="ignore")[-600:]
        raise HTTPException(status_code=502, detail=f"video remux failed: {message or 'ffmpeg error'}")


@app.get("/media/video")
def media_video(url: str = Query(...)):
    if not is_allowed_remote_video(url):
        raise HTTPException(status_code=400, detail="unsupported video host")

    temp_dir = Path(tempfile.mkdtemp(prefix="xhs-video-"))
    source_path = temp_dir / "source.bin"
    output_path = temp_dir / "xhs-original-video.mp4"
    try:
        _download_video_to_file(url, source_path)
        _remux_file_to_ios_mp4(source_path, output_path)
        return FileResponse(
            path=output_path,
            filename="xhs-original-video.mp4",
            media_type="video/mp4",
            headers={
                "Cache-Control": "public, max-age=1800",
                "X-Content-Type-Options": "nosniff",
            },
            background=BackgroundTask(shutil.rmtree, temp_dir, True),
        )
    except HTTPException:
        shutil.rmtree(temp_dir, ignore_errors=True)
        raise
    except Exception as exc:
        shutil.rmtree(temp_dir, ignore_errors=True)
        raise HTTPException(status_code=502, detail=f"video fetch failed: {type(exc).__name__}") from exc


@app.get("/xhszshq")
def xhszshq_gate(
    a: str = Query(default=""),
    b: str = Query(default="ios"),
    c: str = Query(default=""),
    device_id: str = Query(default=""),
):
    ok, reason = verify_and_bind(a, device_id, b)
    log_request(a, device_id, b, reason, c)
    if not ok:
        return PlainTextResponse("0")

    note = inspect_note(c)
    note_url = note["resolved_url"] or normalize_xhs_url(c)

    if not note["nt"]:
        return JSONResponse({
            "error": "parse_failed",
            "message": "無法解析該筆記媒體",
            "note_url": note_url,
        })

    raw_images = note["images"]
    images = [proxy_image_url(x) for x in raw_images]
    first_image = images[0] if images else ""

    live_images_raw = note["live_images"] or (raw_images if note["nt"] == "livepic" else [])
    live_covers = [proxy_image_url(x) for x in live_images_raw]
    raw_live_videos = dedupe_live_videos(note["live_videos"])
    live_videos = [proxy_video_url(x) for x in raw_live_videos]
    pair_count = min(len(live_covers), len(live_videos))
    ligl = [
        {
            "cover": live_covers[index],
            "livevideo": live_videos[index],
            "image": live_covers[index],
            "video": live_videos[index],
        }
        for index in range(pair_count)
    ]

    paired_cover_set = set(live_covers[:pair_count])
    nigl = [x for x in images if x not in paired_cover_set] if note["nt"] == "livepic" else []
    gigl = images if note["nt"] == "pic" else []
    first_live_cover = live_covers[0] if live_covers else ""
    first_live_video = live_videos[0] if live_videos else ""

    payload = {
        "status": 1,
        "gate": 1,
        "notetype": note["notetype"],
        "nt": note["nt"],
        "note_url": note_url,
        "source_url": note_url,
        "title": note["title"],
        "author": note["author"],
        "gigl": gigl,
        "ligl": ligl,
        "nigl": nigl,
        "url": (
            first_image if note["nt"] == "pic"
            else first_live_cover if note["nt"] == "livepic"
            else (note["video"] or note_url)
        ),
        "image": first_image,
        "images": images,
        "pic": first_image,
        "pics": images,
        "video": note["video"],
        "videos": [note["video"]] if note["video"] else [],
        "livepic": ligl,
        "livepics": ligl,
        "live": ligl,
        "livephoto": ligl,
        "livePhoto": ligl,
        "live_photos": ligl,
        "live_image": first_live_cover,
        "live_images": live_covers,
        "live_video": first_live_video,
        "live_videos": live_videos,
        "cover": first_live_cover,
        "livevideo": first_live_video,
        "image_count": len(images),
        "live_count": len(ligl),
        "normal_count": len(nigl),
        "parser": note["parser"],
        "message": "ok",
    }
    return JSONResponse(payload)
