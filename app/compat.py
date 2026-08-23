from __future__ import annotations

import html
import json
import logging
import re
import subprocess
from urllib.parse import quote, urlparse, urlunparse
from urllib.request import Request as URLRequest, urlopen

from fastapi import HTTPException, Query
from fastapi.responses import JSONResponse, PlainTextResponse, Response

from .main import app
from .db import log_request, verify_and_bind

logger = logging.getLogger("xhs.compat")

app.router.routes[:] = [
    route for route in app.router.routes
    if getattr(route, "path", None) not in {"/xhszshq", "/media/image"}
]

UA = "Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) AppleWebKit/605.1.15 Version/18.0 Mobile/15E148 Safari/604.1"
PUBLIC_BASE = "https://shortcutdownload.onrender.com"
ALLOWED_IMAGE_HOST_TOKENS = ("xhscdn", "sns-img", "qpic", "alicdn")
LIVE_MARKERS = (
    "livephoto", "live_photo", "livepic", "motionphoto", "motion_photo",
    "isLivePhoto", "livePhoto", "live_photo_file_id",
)


def normalize_xhs_url(value: str) -> str:
    value = (value or "").strip()
    if not value:
        return value
    try:
        parsed = urlparse(value)
        if parsed.scheme == "http" and (parsed.hostname or "").lower().endswith("xhslink.com"):
            return urlunparse(parsed._replace(scheme="https"))
    except Exception:
        pass
    return value


def resolve_url(value: str) -> str:
    value = normalize_xhs_url(value)
    if not value:
        return value
    try:
        req = URLRequest(value, headers={"User-Agent": UA, "Accept-Language": "zh-TW,zh;q=0.9,en;q=0.8"})
        with urlopen(req, timeout=15) as resp:
            return resp.geturl() or value
    except Exception:
        return value


def dedupe(items: list[str]) -> list[str]:
    seen = set()
    result = []
    for item in items:
        if not item or item in seen:
            continue
        seen.add(item)
        result.append(item)
    return result


def clean_url(value: str) -> str:
    value = html.unescape(value)
    value = value.replace("\\u002F", "/").replace("\\/", "/")
    value = value.replace("\\u0026", "&")
    return value.strip('"\' ')


def looks_like_image(value: str) -> bool:
    low = value.lower()
    if not low.startswith(("http://", "https://")):
        return False

    parsed = urlparse(value)
    host = (parsed.hostname or "").lower()
    path = parsed.path.lower()

    # 排除頁面靜態資源、頭像與空主機。這些先前被誤當圖片塞進 gigl，
    # 會讓捷徑在「取得 URL 內容 / 轉 PNG」時直接報錯。
    if not path or path == "/":
        return False
    if any(token in host for token in ("fe-static", "picasso-static", "avatar")):
        return False
    if path.endswith((".css", ".js", ".json", ".html", ".map", ".svg", ".txt", ".woff", ".woff2")):
        return False

    # 小紅書筆記正文圖片常見 CDN。
    if host.startswith("sns-na-") and "xhscdn.com" in host:
        return True
    if "sns-webpic" in host and "xhscdn.com" in host:
        return True
    if "sns-img" in host:
        return True

    # 其他 CDN 只有在網址本身明確是圖片時才接受。
    if path.endswith((".jpg", ".jpeg", ".png", ".webp", ".heic", ".avif")):
        return True
    if any(token in low for token in ("imageview2", "imagemogr2", "format%2fjpg", "format/jpg", "format%2fwebp", "format/webp")):
        return any(x in host for x in ALLOWED_IMAGE_HOST_TOKENS)
    return False


def looks_like_video(value: str) -> bool:
    low = value.lower()
    if not low.startswith(("http://", "https://")):
        return False
    path = urlparse(value).path.lower()
    if path.endswith((".mp4", ".mov", ".m4v", ".webm")):
        return True
    return any(token in low for token in ("video", "stream", "playurl", "masterurl")) and "xhscdn" in low


def is_allowed_remote_image(value: str) -> bool:
    try:
        parsed = urlparse(value)
        host = (parsed.hostname or "").lower()
        return parsed.scheme in {"http", "https"} and (
            "xhscdn" in host or "sns-img" in host or "qpic" in host or "alicdn" in host
        ) and looks_like_image(value)
    except Exception:
        return False


def proxy_image_url(remote_url: str) -> str:
    if not remote_url:
        return ""
    return f"{PUBLIC_BASE}/media/image?url={quote(remote_url, safe='')}"


def extract_media_from_html(url: str) -> tuple[str, list[str], list[str], bool]:
    try:
        req = URLRequest(url, headers={
            "User-Agent": UA,
            "Accept": "text/html,application/xhtml+xml,application/json;q=0.9,*/*;q=0.8",
            "Accept-Language": "zh-TW,zh;q=0.9,en;q=0.8",
            "Referer": "https://www.xiaohongshu.com/",
        })
        with urlopen(req, timeout=20) as resp:
            final_url = resp.geturl() or url
            raw = resp.read(6 * 1024 * 1024).decode("utf-8", errors="ignore")
    except Exception:
        return url, [], [], False

    decoded = html.unescape(raw)
    normalized = decoded.replace("\\u002F", "/").replace("\\/", "/").replace("\\u0026", "&")
    live_hint = any(marker.lower() in normalized.lower() for marker in LIVE_MARKERS)

    candidates: list[str] = []
    patterns = [
        r'https?://[A-Za-z0-9._~:/?#\[\]@!$&\'()*+,;=%-]+',
        r'"(?:urlDefault|urlPre|url|imageUrl|image_url|videoUrl|video_url|masterUrl|master_url|playUrl|play_url)"\s*:\s*"([^"]+)"',
    ]
    for text in (raw, decoded, normalized):
        for pattern in patterns:
            for match in re.finditer(pattern, text):
                value = clean_url(match.group(1) if match.lastindex else match.group(0))
                if value.startswith(("http://", "https://")):
                    candidates.append(value)

    candidates = dedupe(candidates)
    images = dedupe([x for x in candidates if looks_like_image(x)])
    videos = dedupe([x for x in candidates if looks_like_video(x)])
    return final_url, images, videos, live_hint


def inspect_with_gallery_dl(url: str) -> tuple[list[str], list[str]]:
    commands = [["gallery-dl", "--get-urls", url], ["gallery-dl", "-g", url]]
    for command in commands:
        try:
            proc = subprocess.run(command, capture_output=True, text=True, timeout=45, check=False)
            if proc.returncode == 0 and proc.stdout.strip():
                urls = dedupe([clean_url(line.strip()) for line in proc.stdout.splitlines()])
                images = dedupe([x for x in urls if looks_like_image(x)])
                videos = dedupe([x for x in urls if looks_like_video(x)])
                if images or videos:
                    return images, videos
        except Exception:
            pass
    return [], []


def inspect_note(input_url: str) -> dict:
    result = {
        "notetype": "", "nt": "", "title": "", "author": "", "video": "",
        "images": [], "live_images": [], "live_videos": [],
        "resolved_url": resolve_url(input_url), "parser": "none", "live_hint": False,
    }
    url = result["resolved_url"] or normalize_xhs_url(input_url)
    if not url:
        return result

    final_url, html_images, html_videos, live_hint = extract_media_from_html(url)
    if final_url:
        result["resolved_url"] = final_url
    result["images"] = html_images
    result["live_videos"] = html_videos
    result["live_hint"] = live_hint
    if html_images or html_videos:
        result["parser"] = "html"

    if live_hint and html_images and html_videos:
        result["notetype"] = "livepic"
        result["nt"] = "livepic"
        result["live_images"] = html_images
        result["live_videos"] = html_videos
        return result

    try:
        proc = subprocess.run(
            ["yt-dlp", "--dump-single-json", "--skip-download", "--no-playlist", result["resolved_url"] or url],
            capture_output=True, text=True, timeout=45, check=False,
        )
        if proc.returncode == 0 and proc.stdout.strip():
            info = json.loads(proc.stdout)
            result["title"] = str(info.get("title") or "")
            result["author"] = str(info.get("uploader") or info.get("channel") or "")
            formats = info.get("formats") or []
            ext = str(info.get("ext") or "").lower()
            duration = info.get("duration")
            has_video_stream = any(
                isinstance(f, dict) and str(f.get("vcodec") or "none").lower() not in {"", "none"}
                for f in formats
            )
            is_video = has_video_stream or ext in {"mp4", "mov", "m4v", "webm"} or bool(duration)
            if is_video:
                result["notetype"] = "video"
                result["nt"] = "video"
                result["video"] = str(info.get("url") or info.get("webpage_url") or url)
                result["parser"] = "yt-dlp"
                return result
            thumbs = info.get("thumbnails") or []
            thumb_urls = [str(x.get("url")) for x in thumbs if isinstance(x, dict) and x.get("url")]
            result["images"] = dedupe(result["images"] + [x for x in thumb_urls if looks_like_image(x)])
    except Exception:
        pass

    gallery_images, gallery_videos = inspect_with_gallery_dl(result["resolved_url"] or url)
    if gallery_images:
        result["images"] = dedupe(result["images"] + gallery_images)
    if gallery_videos:
        result["live_videos"] = dedupe(result["live_videos"] + gallery_videos)
    if gallery_images or gallery_videos:
        result["parser"] = "gallery-dl" if result["parser"] == "none" else result["parser"] + "+gallery-dl"

    if result["live_hint"] and result["images"] and result["live_videos"]:
        result["notetype"] = "livepic"
        result["nt"] = "livepic"
        result["live_images"] = result["images"]
        return result

    if result["images"]:
        result["notetype"] = "pic"
        result["nt"] = "pic"
    return result


@app.get("/media/image")
def media_image(url: str = Query(...)):
    if not is_allowed_remote_image(url):
        raise HTTPException(status_code=400, detail="unsupported image host")
    try:
        req = URLRequest(url, headers={
            "User-Agent": UA,
            "Accept": "image/avif,image/webp,image/apng,image/*,*/*;q=0.8",
            "Referer": "https://www.xiaohongshu.com/",
        })
        with urlopen(req, timeout=25) as resp:
            data = resp.read(25 * 1024 * 1024)
            media_type = resp.headers.get_content_type() or "image/jpeg"
            if not media_type.startswith("image/"):
                raise HTTPException(status_code=502, detail="remote resource is not an image")
            logger.info("XHS_IMAGE_PROXY bytes=%s type=%s host=%s", len(data), media_type, urlparse(url).hostname or "")
            return Response(content=data, media_type=media_type, headers={"Cache-Control": "public, max-age=3600"})
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"image fetch failed: {type(exc).__name__}")


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
    raw_images = note["images"]
    images = [proxy_image_url(x) for x in raw_images]
    first_image = images[0] if images else ""
    note_url = note["resolved_url"] or normalize_xhs_url(c)

    live_images_raw = note["live_images"] or (raw_images if note["nt"] == "livepic" else [])
    live_images = [proxy_image_url(x) for x in live_images_raw]
    live_videos = note["live_videos"]
    live_pairs = [
        {"image": live_images[i], "video": live_videos[i] if i < len(live_videos) else (live_videos[0] if live_videos else "")}
        for i in range(len(live_images))
    ]
    first_live_image = live_images[0] if live_images else ""
    first_live_video = live_videos[0] if live_videos else ""

    if not note["nt"]:
        logger.info("XHS_GATE_PARSE_FAILED source=%s parser=%s", note_url, note["parser"])
        return JSONResponse({
            "error": "parse_failed",
            "message": "無法解析該筆記媒體",
            "note_url": note_url,
        })

    payload = {
        "status": 1,
        "gate": 1,
        "notetype": note["notetype"],
        "nt": note["nt"],
        "url": (
            first_image if note["nt"] == "pic"
            else first_live_image if note["nt"] == "livepic"
            else (note["video"] or note_url)
        ),
        "note_url": note_url,
        "source_url": note_url,
        "title": note["title"],
        "author": note["author"],
        "image": first_image,
        "images": images,
        "pic": first_image,
        "pics": images,
        "img": first_image,
        "imgs": images,
        "photo": first_image,
        "photos": images,
        "original": first_image,
        "originals": images,
        "urls": images,
        "gigl": images,
        "imageList": images,
        "image_list": images,
        "picList": images,
        "pic_list": images,
        "imageUrls": images,
        "image_urls": images,
        "picurl": first_image,
        "picurls": images,
        "picUrl": first_image,
        "picUrls": images,
        "video": note["video"],
        "videos": [note["video"]] if note["video"] else [],
        "livepic": live_pairs,
        "livepics": live_pairs,
        "live": live_pairs,
        "livephoto": live_pairs,
        "livePhoto": live_pairs,
        "live_photos": live_pairs,
        "live_image": first_live_image,
        "live_images": live_images,
        "livepic_image": first_live_image,
        "livepic_images": live_images,
        "live_video": first_live_video,
        "live_videos": live_videos,
        "livepic_video": first_live_video,
        "livepic_videos": live_videos,
        "image_count": len(images),
        "live_count": len(live_pairs),
        "parser": note["parser"],
        "message": "ok",
    }

    logger.info(
        "XHS_GATE_RESULT nt=%s image_count=%s live_count=%s parser=%s source=%s",
        note["nt"], len(images), len(live_pairs), note["parser"], note_url,
    )
    return JSONResponse(payload)
