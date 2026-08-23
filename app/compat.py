from __future__ import annotations

import html as html_lib
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


def _collect_http_urls(value) -> list[str]:
    urls: list[str] = []
    if isinstance(value, str):
        if value.startswith(("http://", "https://")):
            urls.append(value)
    elif isinstance(value, dict):
        for item in value.values():
            urls.extend(_collect_http_urls(item))
    elif isinstance(value, list):
        for item in value:
            urls.extend(_collect_http_urls(item))
    return urls


def _looks_like_image(url: str) -> bool:
    lower = url.lower().split("?", 1)[0]
    if lower.endswith((".jpg", ".jpeg", ".png", ".webp", ".heic", ".avif")):
        return True
    return any(token in url.lower() for token in ("image", "img", "sns-img", "xhscdn", "sns-webpic"))


def _dedupe_images(urls: list[str]) -> list[str]:
    images: list[str] = []
    seen = set()
    for raw in urls:
        item = html_lib.unescape(raw).replace("\\u002F", "/").replace("\\/", "/")
        if item.startswith("//"):
            item = "https:" + item
        if item.startswith("http") and _looks_like_image(item) and item not in seen:
            seen.add(item)
            images.append(item)
    return images


def inspect_with_page_html(url: str) -> dict:
    fallback = {"title": "", "author": "", "images": [], "video": "", "final_url": url}
    try:
        request = Request(
            url,
            headers={
                "User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) AppleWebKit/605.1.15 Version/18.0 Mobile/15E148 Safari/604.1",
                "Accept-Language": "zh-TW,zh;q=0.9,en;q=0.8",
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            },
        )
        with urlopen(request, timeout=20) as response:
            fallback["final_url"] = response.geturl()
            text = response.read(3_000_000).decode("utf-8", "ignore")

        # 小紅書頁面中的圖片網址通常存在 JSON/HTML script 內。
        candidates = re.findall(
            r'https?(?::|\\u003A)?(?:\\/|/){2}[^"\'<>\\\s]+',
            text,
            flags=re.I,
        )
        # 也抓被 JSON escape 的 CDN URL。
        candidates += re.findall(
            r'https?:\\/\\/[^"\'<>\s]+',
            text,
            flags=re.I,
        )
        fallback["images"] = _dedupe_images(candidates)

        title_match = re.search(r'<meta[^>]+property=["\']og:title["\'][^>]+content=["\']([^"\']+)', text, re.I)
        if not title_match:
            title_match = re.search(r'<title>(.*?)</title>', text, re.I | re.S)
        if title_match:
            fallback["title"] = html_lib.unescape(re.sub(r"\s+", " ", title_match.group(1))).strip()

        video_patterns = [
            r'"masterUrl"\s*:\s*"([^"]+)"',
            r'"originVideoKey"\s*:\s*"([^"]+)"',
            r'"url"\s*:\s*"(https?:\\/\\/[^" ]+\.mp4[^" ]*)"',
        ]
        for pattern in video_patterns:
            match = re.search(pattern, text, re.I)
            if match:
                fallback["video"] = html_lib.unescape(match.group(1)).replace("\\u002F", "/").replace("\\/", "/")
                break
    except Exception:
        pass
    return fallback


def inspect_with_gallery_dl(url: str) -> dict:
    fallback = {"title": "", "author": "", "images": []}
    try:
        proc = subprocess.run(
            ["gallery-dl", "--dump-json", url],
            capture_output=True,
            text=True,
            timeout=45,
            check=False,
        )
        if proc.returncode != 0 or not proc.stdout.strip():
            return fallback
        parsed_items = []
        text = proc.stdout.strip()
        try:
            parsed_items.append(json.loads(text))
        except Exception:
            for line in text.splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    parsed_items.append(json.loads(line))
                except Exception:
                    continue
        all_urls: list[str] = []
        for item in parsed_items:
            all_urls.extend(_collect_http_urls(item))
        fallback["images"] = _dedupe_images(all_urls)
        for key in ("title", "description", "caption"):
            match = re.search(rf'"{key}"\s*:\s*"([^"\\]*(?:\\.[^"\\]*)*)"', text)
            if match:
                try:
                    fallback["title"] = json.loads('"' + match.group(1) + '"')
                except Exception:
                    fallback["title"] = match.group(1)
                break
        for key in ("author", "uploader", "user", "nickname"):
            match = re.search(rf'"{key}"\s*:\s*"([^"\\]*(?:\\.[^"\\]*)*)"', text)
            if match:
                try:
                    fallback["author"] = json.loads('"' + match.group(1) + '"')
                except Exception:
                    fallback["author"] = match.group(1)
                break
    except Exception:
        return fallback
    return fallback


def inspect_note(url: str) -> dict:
    result = {"notetype": "", "nt": "", "title": "", "author": "", "video": "", "images": [], "resolved_url": url}
    if not url:
        return result

    # 1. 先用 yt-dlp 判斷影片。
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
            result["resolved_url"] = str(info.get("webpage_url") or url)
            formats = info.get("formats") or []
            ext = str(info.get("ext") or "").lower()
            duration = info.get("duration")
            has_video_stream = any(isinstance(f, dict) and str(f.get("vcodec") or "none").lower() not in {"", "none"} for f in formats)
            if has_video_stream or ext in {"mp4", "mov", "m4v", "webm"} or bool(duration):
                result["notetype"] = "video"
                result["nt"] = "video"
                result["video"] = str(info.get("url") or info.get("webpage_url") or url)
                return result
            thumbs = info.get("thumbnails") or []
            images = _dedupe_images([str(x.get("url")) for x in thumbs if isinstance(x, dict) and x.get("url")])
            if images:
                result["notetype"] = "pic"
                result["nt"] = "pic"
                result["images"] = images
                return result
    except Exception:
        pass

    # 2. 直接抓小紅書頁面 HTML/JSON；這對圖文筆記比 yt-dlp 穩定。
    page = inspect_with_page_html(url)
    result["resolved_url"] = page["final_url"] or result["resolved_url"]
    result["title"] = result["title"] or page["title"]
    if page["video"]:
        result["notetype"] = "video"
        result["nt"] = "video"
        result["video"] = page["video"]
        return result
    if page["images"]:
        result["notetype"] = "pic"
        result["nt"] = "pic"
        result["images"] = page["images"]
        return result

    # 3. 再試 gallery-dl。
    gallery = inspect_with_gallery_dl(result["resolved_url"] or url)
    if gallery["images"]:
        result["notetype"] = "pic"
        result["nt"] = "pic"
        result["images"] = gallery["images"]
        result["title"] = result["title"] or gallery["title"]
        result["author"] = result["author"] or gallery["author"]
        return result

    # 4. 小紅書圖文筆記常因反爬讓伺服器拿不到內容。
    # 影片已經在第一階段優先判斷；若仍無法識別，先按 pic 回傳，
    # 讓原捷徑的「原圖保存」分支不會被 nt 空值直接擋掉。
    host = (urlparse(result["resolved_url"] or url).hostname or "").lower()
    if host.endswith("xiaohongshu.com") or host.endswith("xhslink.com"):
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

    url = normalize_xhs_url(c)
    note = inspect_note(url)
    resolved = note.get("resolved_url") or url
    payload = {
        "status": 1,
        "gate": 1,
        "notetype": note["notetype"],
        "nt": note["nt"],
        "url": resolved,
        "title": note["title"],
        "author": note["author"],
        "images": note["images"],
        "image": note["images"],
        "pic": note["images"],
        "pics": note["images"],
        "video": note["video"],
        "videos": [note["video"]] if note["video"] else [],
        "live": [],
        "livephoto": [],
        "message": "gate-json-media-detect-v4",
    }
    return JSONResponse(payload)
