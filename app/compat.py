from __future__ import annotations

import json
import re
import subprocess
from urllib.parse import urlparse, urlunparse

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
    return any(token in url.lower() for token in ("image", "img", "sns-img", "xhscdn"))


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

        # Keep likely image CDN URLs only, de-duplicated in original order.
        images: list[str] = []
        seen = set()
        for item in all_urls:
            if item not in seen and _looks_like_image(item):
                seen.add(item)
                images.append(item)

        fallback["images"] = images

        # Best-effort metadata extraction from JSON text.
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
    result = {
        "notetype": "",
        "nt": "",
        "title": "",
        "author": "",
        "video": "",
        "images": [],
    }
    if not url:
        return result

    # First try yt-dlp. It is stronger for video notes.
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
                return result

            thumbs = info.get("thumbnails") or []
            images = [str(x.get("url")) for x in thumbs if isinstance(x, dict) and x.get("url")]
            if images:
                result["notetype"] = "pic"
                result["nt"] = "pic"
                result["images"] = images
                return result
    except Exception:
        pass

    # Photo notes often are not exposed by yt-dlp. Fall back to gallery-dl.
    gallery = inspect_with_gallery_dl(url)
    if gallery["images"]:
        result["notetype"] = "pic"
        result["nt"] = "pic"
        result["images"] = gallery["images"]
        result["title"] = result["title"] or gallery["title"]
        result["author"] = result["author"] or gallery["author"]
        return result

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
    payload = {
        "status": 1,
        "gate": 1,
        "notetype": note["notetype"],
        "nt": note["nt"],
        "url": url,
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
        "message": "gate-json-media-detect-v3",
    }
    return JSONResponse(payload)
