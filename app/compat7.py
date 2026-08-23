from __future__ import annotations

import subprocess
from urllib.parse import quote, urlparse

from fastapi import Query
from fastapi.responses import JSONResponse, PlainTextResponse

from .compat import (
    PUBLIC_BASE,
    clean_url,
    inspect_note,
    log_request,
    normalize_xhs_url,
    resolve_url,
    verify_and_bind,
)
from .compat2 import proxy_video_url
from .compat3 import app, canonical_video_key, dedupe, to_original_xhs_image_url

# 只取代 GATE；圖片與影片代理沿用既有版本。
app.router.routes[:] = [r for r in app.router.routes if getattr(r, "path", None) != "/xhszshq"]


def _allowed_scoped_image(value: str) -> bool:
    """gallery-dl 已經把範圍鎖在單一文章 URL，所以這裡只驗證是否為小紅書媒體主機，不再用路徑格式鎖死圖片。"""
    try:
        p = urlparse(value)
        host = (p.hostname or "").lower()
        path = p.path.lower()
        if p.scheme not in {"http", "https"} or not path or path == "/":
            return False
        if host == "ci.xiaohongshu.com" or host.endswith(".xhscdn.com") or host == "xhscdn.com":
            if any(x in host for x in ("avatar", "static", "picasso")):
                return False
            if any(x in path for x in ("/avatar", "/comment", "/emoji", "/icon", "/logo")):
                return False
            return True
        return False
    except Exception:
        return False


def _allowed_scoped_video(value: str) -> bool:
    try:
        p = urlparse(value)
        host = (p.hostname or "").lower()
        path = p.path.lower()
        return (
            p.scheme in {"http", "https"}
            and (host.endswith(".xhscdn.com") or host == "xhscdn.com")
            and (path.endswith((".mp4", ".mov", ".m4v", ".webm")) or "stream" in value.lower() or "video" in value.lower())
        )
    except Exception:
        return False


def inspect_one_url_only(input_url: str) -> tuple[str, list[str], list[str], str]:
    """一個網址就是一篇文章：只讓 gallery-dl 對該網址取媒體，不掃整頁推薦/留言。"""
    resolved = resolve_url(input_url) or normalize_xhs_url(input_url)
    if not resolved:
        return "", [], [], "url_missing"

    commands = [
        ["gallery-dl", "--get-urls", resolved],
        ["gallery-dl", "-g", resolved],
    ]
    last_reason = "gallery_dl_empty"
    for command in commands:
        try:
            proc = subprocess.run(command, capture_output=True, text=True, timeout=50, check=False)
        except Exception as exc:
            last_reason = f"gallery_dl_{type(exc).__name__}"
            continue
        if proc.returncode != 0 or not proc.stdout.strip():
            last_reason = "gallery_dl_failed"
            continue

        urls = dedupe([clean_url(line.strip()) for line in proc.stdout.splitlines() if line.strip()])
        images = dedupe([u for u in urls if _allowed_scoped_image(u)])
        videos = dedupe([u for u in urls if _allowed_scoped_video(u)])
        if images or videos:
            return resolved, images, videos, "gallery-dl-single-url"
        last_reason = "gallery_dl_no_supported_media"

    return resolved, [], [], last_reason


def _proxy_images(items: list[str]) -> list[str]:
    originals = dedupe([to_original_xhs_image_url(x) for x in items if x])
    return [f"{PUBLIC_BASE}/media/image?url={quote(x, safe='')}" for x in originals]


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

    note_url, scoped_images, scoped_videos, scoped_parser = inspect_one_url_only(c)

    # URL 專屬解析成功時，完全以該網址輸出的媒體為準。
    if scoped_images or scoped_videos:
        images = _proxy_images(scoped_images)
        live_videos_raw = []
        seen_video: set[str] = set()
        for video in scoped_videos:
            key = canonical_video_key(video)
            if key and key not in seen_video:
                seen_video.add(key)
                live_videos_raw.append(video)
        live_videos = [proxy_video_url(x) for x in live_videos_raw]

        # gallery-dl 對一般圖文只會回圖片；對實況可能同時回圖片與影片。
        if images and live_videos:
            nt = "livepic"
            notetype = "livepic"
        elif images:
            nt = "pic"
            notetype = "pic"
        else:
            nt = "video"
            notetype = "video"

        pair_count = min(len(images), len(live_videos)) if nt == "livepic" else 0
        ligl = [
            {"cover": images[i], "livevideo": live_videos[i], "image": images[i], "video": live_videos[i]}
            for i in range(pair_count)
        ]
        nigl = images[pair_count:] if nt == "livepic" else []
        gigl = images if nt == "pic" else []
        first_image = images[0] if images else ""
        first_video = live_videos[0] if live_videos else ""

        return JSONResponse({
            "status": 1,
            "gate": 1,
            "notetype": notetype,
            "nt": nt,
            "note_url": note_url,
            "source_url": note_url,
            "title": "",
            "author": "",
            "gigl": gigl,
            "ligl": ligl,
            "nigl": nigl,
            "url": first_image if nt in {"pic", "livepic"} else first_video,
            "image": first_image,
            "images": images,
            "pic": first_image,
            "pics": images,
            "video": first_video if nt == "video" else "",
            "videos": live_videos if nt == "video" else [],
            "livepic": ligl,
            "livepics": ligl,
            "live": ligl,
            "livephoto": ligl,
            "livePhoto": ligl,
            "live_photos": ligl,
            "live_image": first_image if nt == "livepic" else "",
            "live_images": images if nt == "livepic" else [],
            "live_video": first_video if nt == "livepic" else "",
            "live_videos": live_videos if nt == "livepic" else [],
            "cover": first_image if nt == "livepic" else "",
            "livevideo": first_video if nt == "livepic" else "",
            "image_count": len(images),
            "live_count": len(ligl),
            "normal_count": len(nigl),
            "parser": scoped_parser,
            "message": "ok-single-url-scope-v1",
        })

    # 若 gallery-dl 對影片網址沒有輸出，僅允許 URL 專屬的 yt-dlp 影片解析；
    # 不採用 inspect_note 的 HTML 圖片結果，避免再次掃到推薦/留言。
    fallback = inspect_note(c)
    if fallback.get("nt") == "video" and fallback.get("video"):
        return JSONResponse({
            "status": 1,
            "gate": 1,
            "notetype": "video",
            "nt": "video",
            "note_url": fallback.get("resolved_url") or note_url,
            "source_url": fallback.get("resolved_url") or note_url,
            "title": fallback.get("title") or "",
            "author": fallback.get("author") or "",
            "gigl": [], "ligl": [], "nigl": [],
            "url": fallback["video"],
            "image": "", "images": [], "pic": "", "pics": [],
            "video": fallback["video"], "videos": [fallback["video"]],
            "livepic": [], "livepics": [], "live": [], "livephoto": [], "livePhoto": [], "live_photos": [],
            "live_image": "", "live_images": [], "live_video": "", "live_videos": [],
            "cover": "", "livevideo": "",
            "image_count": 0, "live_count": 0, "normal_count": 0,
            "parser": f"{scoped_parser}+yt-dlp-video-only",
            "message": "ok-single-url-video-fallback",
        })

    return JSONResponse({
        "error": "current_url_media_not_found",
        "message": "這個網址範圍內沒有取得可下載媒體；已停止，不會抓留言或其他文章",
        "note_url": note_url,
        "parser": scoped_parser,
    })
