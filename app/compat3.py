from __future__ import annotations

from urllib.parse import quote, urlparse
from urllib.request import Request as URLRequest, urlopen

from fastapi import HTTPException, Query
from fastapi.responses import JSONResponse, PlainTextResponse, Response

from .compat import UA, PUBLIC_BASE, inspect_note, log_request, normalize_xhs_url, verify_and_bind
from .compat2 import app, proxy_video_url

# 取代 compat/compat2 的舊圖片代理與 GATE 路由；保留 /media/video。
app.router.routes[:] = [
    route for route in app.router.routes
    if getattr(route, "path", None) not in {"/xhszshq", "/media/image"}
]


def dedupe(items: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for item in items:
        if item and item not in seen:
            seen.add(item)
            out.append(item)
    return out


def to_original_xhs_image_url(value: str) -> str:
    """把網頁展示/帶水印 CDN 圖轉成小紅書原圖來源 ci.xiaohongshu.com。"""
    if not value:
        return ""
    try:
        parsed = urlparse(value)
        host = (parsed.hostname or "").lower()
        path = parsed.path.strip("/")
        if host == "ci.xiaohongshu.com":
            base = f"https://ci.xiaohongshu.com/{path}"
            return f"{base}?imageView2/2/w/format/png"

        if "xhscdn.com" not in host:
            return value

        parts = [p for p in path.split("/") if p]
        if not parts:
            return value

        trace = parts[-1].split("!", 1)[0]
        if not trace:
            return value

        # 2025~2026 小紅書原圖會保留這些檔案命名空間。
        prefix = ""
        for marker in ("notes_uhdr", "note_pre_post_uhdr", "notes_pre_post", "note_pre_post", "spectrum"):
            if marker in parts:
                prefix = marker + "/"
                break

        return f"https://ci.xiaohongshu.com/{prefix}{trace}?imageView2/2/w/format/png"
    except Exception:
        return value


def proxy_original_image_url(remote_url: str) -> str:
    original = to_original_xhs_image_url(remote_url)
    if not original:
        return ""
    return f"{PUBLIC_BASE}/media/image?url={quote(original, safe='')}"


def is_allowed_image(value: str) -> bool:
    try:
        parsed = urlparse(value)
        host = (parsed.hostname or "").lower()
        return parsed.scheme in {"http", "https"} and (
            host == "ci.xiaohongshu.com"
            or host.endswith(".xhscdn.com")
            or host == "xhscdn.com"
        )
    except Exception:
        return False


@app.get("/media/image")
def media_image(url: str = Query(...)):
    if not is_allowed_image(url):
        raise HTTPException(status_code=400, detail="unsupported image host")
    try:
        req = URLRequest(url, headers={
            "User-Agent": UA,
            "Accept": "image/avif,image/webp,image/apng,image/*,*/*;q=0.8",
            "Referer": "https://www.xiaohongshu.com/",
        })
        with urlopen(req, timeout=30) as resp:
            data = resp.read(35 * 1024 * 1024)
            media_type = resp.headers.get_content_type() or "image/png"
            if not media_type.startswith("image/") and media_type != "application/octet-stream":
                raise HTTPException(status_code=502, detail="remote resource is not an image")
            if media_type == "application/octet-stream":
                media_type = "image/png"
            return Response(content=data, media_type=media_type, headers={"Cache-Control": "public, max-age=3600"})
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"image fetch failed: {type(exc).__name__}")


def canonical_video_key(value: str) -> str:
    try:
        path = urlparse(value).path
        return path.rsplit("/", 1)[-1]
    except Exception:
        return value


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
        return JSONResponse({"error": "parse_failed", "message": "無法解析該筆記媒體", "note_url": note_url})

    raw_images = dedupe([to_original_xhs_image_url(x) for x in note["images"]])
    images = [f"{PUBLIC_BASE}/media/image?url={quote(x, safe='')}" for x in raw_images]
    first_image = images[0] if images else ""

    live_raw_images = note["live_images"] or (note["images"] if note["nt"] == "livepic" else [])
    live_originals = dedupe([to_original_xhs_image_url(x) for x in live_raw_images])
    live_covers = [f"{PUBLIC_BASE}/media/image?url={quote(x, safe='')}" for x in live_originals]

    seen_video: set[str] = set()
    raw_live_videos: list[str] = []
    for video in note["live_videos"]:
        key = canonical_video_key(video)
        if video and key not in seen_video:
            seen_video.add(key)
            raw_live_videos.append(video)
    live_videos = [proxy_video_url(x) for x in raw_live_videos]

    pair_count = min(len(live_covers), len(live_videos))
    ligl = [
        {
            "cover": live_covers[i],
            "livevideo": live_videos[i],
            "image": live_covers[i],
            "video": live_videos[i],
        }
        for i in range(pair_count)
    ]

    # 沒配到 livevideo 的圖片，按照捷徑既有 nigl 普通圖片流程保存。
    nigl = live_covers[pair_count:] if note["nt"] == "livepic" else []
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
        "url": first_image if note["nt"] == "pic" else first_live_cover if note["nt"] == "livepic" else (note["video"] or note_url),
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
        "message": "ok-original-image-v1",
    }
    return JSONResponse(payload)
