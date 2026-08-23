from __future__ import annotations

import html
import json
import re
import subprocess
from urllib.parse import urlparse, urlunparse
from urllib.request import Request, urlopen

from fastapi import Query
from fastapi.responses import JSONResponse, PlainTextResponse

from .main import app
from .db import log_request, verify_and_bind

app.router.routes[:] = [
    route for route in app.router.routes
    if getattr(route, "path", None) != "/xhszshq"
]

UA = "Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) AppleWebKit/605.1.15 Version/18.0 Mobile/15E148 Safari/604.1"


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
        req = Request(value, headers={"User-Agent": UA, "Accept-Language": "zh-TW,zh;q=0.9,en;q=0.8"})
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
    host = (urlparse(value).hostname or "").lower()
    if any(x in host for x in ("xhscdn", "sns-img", "qpic", "alicdn")):
        return True
    path = urlparse(value).path.lower()
    return path.endswith((".jpg", ".jpeg", ".png", ".webp", ".heic", ".avif"))


def extract_images_from_html(url: str) -> tuple[str, list[str]]:
    try:
        req = Request(url, headers={
            "User-Agent": UA,
            "Accept": "text/html,application/xhtml+xml,application/json;q=0.9,*/*;q=0.8",
            "Accept-Language": "zh-TW,zh;q=0.9,en;q=0.8",
            "Referer": "https://www.xiaohongshu.com/",
        })
        with urlopen(req, timeout=20) as resp:
            final_url = resp.geturl() or url
            raw = resp.read(4 * 1024 * 1024).decode("utf-8", errors="ignore")
    except Exception:
        return url, []

    candidates: list[str] = []
    decoded = html.unescape(raw)
    variants = [raw, decoded, decoded.replace("\\u002F", "/").replace("\\/", "/").replace("\\u0026", "&")]

    patterns = [
        r'https?:\\?/\\?/[A-Za-z0-9._~:/?#\[\]@!$&\'()*+,;=%-]+',
        r'"(?:urlDefault|urlPre|url|imageUrl|image_url)"\s*:\s*"([^"]+)"',
    ]
    for text in variants:
        for pattern in patterns:
            for match in re.finditer(pattern, text):
                value = match.group(1) if match.lastindex else match.group(0)
                value = clean_url(value)
                if looks_like_image(value):
                    candidates.append(value)

    return final_url, dedupe(candidates)


def inspect_with_gallery_dl(url: str) -> list[str]:
    commands = [
        ["gallery-dl", "--get-urls", url],
        ["gallery-dl", "-g", url],
    ]
    for command in commands:
        try:
            proc = subprocess.run(command, capture_output=True, text=True, timeout=45, check=False)
            if proc.returncode == 0 and proc.stdout.strip():
                urls = [clean_url(line.strip()) for line in proc.stdout.splitlines()]
                images = dedupe([x for x in urls if looks_like_image(x)])
                if images:
                    return images
        except Exception:
            pass
    return []


def inspect_note(input_url: str) -> dict:
    result = {
        "notetype": "",
        "nt": "",
        "title": "",
        "author": "",
        "video": "",
        "images": [],
        "resolved_url": resolve_url(input_url),
        "parser": "none",
    }
    url = result["resolved_url"] or normalize_xhs_url(input_url)
    if not url:
        return result

    # 1. yt-dlp：影片優先；若能提供縮圖也先收集。
    try:
        proc = subprocess.run(
            ["yt-dlp", "--dump-single-json", "--skip-download", "--no-playlist", url],
            capture_output=True,
            text=True,
            timeout=45,
            check=False,
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
            result["images"] = dedupe([x for x in thumb_urls if looks_like_image(x)])
    except Exception:
        pass

    # 2. 直接解析小紅書頁面，取真正 CDN 圖片網址。
    final_url, html_images = extract_images_from_html(url)
    if final_url:
        result["resolved_url"] = final_url
    if html_images:
        result["images"] = dedupe(result["images"] + html_images)
        result["parser"] = "html"

    # 3. gallery-dl 作為備援，直接輸出圖片 URL。
    gallery_images = inspect_with_gallery_dl(result["resolved_url"] or url)
    if gallery_images:
        result["images"] = dedupe(result["images"] + gallery_images)
        if result["parser"] == "none":
            result["parser"] = "gallery-dl"

    # 只有真的取得至少一張圖片才宣告 pic，避免顯示成功但相簿沒有照片。
    if result["images"]:
        result["notetype"] = "pic"
        result["nt"] = "pic"

    return result


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
    images = note["images"]
    first_image = images[0] if images else ""
    payload = {
        "status": 1,
        "gate": 1,
        "notetype": note["notetype"],
        "nt": note["nt"],
        "url": note["resolved_url"] or normalize_xhs_url(c),
        "title": note["title"],
        "author": note["author"],

        # 圖片欄位：同時提供常見命名，讓原捷徑不同分支都能取到實際 URL。
        "images": images,
        "image": images,
        "pic": images,
        "pics": images,
        "img": images,
        "imgs": images,
        "imageList": images,
        "image_list": images,
        "picList": images,
        "pic_list": images,
        "urls": images,
        "url_list": images,
        "originals": images,
        "original_images": images,
        "first_image": first_image,
        "image_url": first_image,
        "pic_url": first_image,

        "video": note["video"],
        "videos": [note["video"]] if note["video"] else [],
        "live": [],
        "livephoto": [],

        # 診斷欄位，方便後台排查，不影響捷徑。
        "image_count": len(images),
        "parser": note["parser"],
        "message": "gate-json-media-detect-v5" if images or note["video"] else "parse_failed_no_media",
    }
    return JSONResponse(payload)
