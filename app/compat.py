from __future__ import annotations

import json
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

    try:
        proc = subprocess.run(
            ["yt-dlp", "--dump-single-json", "--skip-download", "--no-playlist", url],
            capture_output=True,
            text=True,
            timeout=45,
            check=False,
        )
        if proc.returncode != 0 or not proc.stdout.strip():
            return result
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
        else:
            thumbs = info.get("thumbnails") or []
            images = [str(x.get("url")) for x in thumbs if isinstance(x, dict) and x.get("url")]
            result["notetype"] = "image"
            result["nt"] = "image"
            result["images"] = images
    except Exception:
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
        "video": note["video"],
        "videos": [note["video"]] if note["video"] else [],
        "live": [],
        "livephoto": [],
        "message": "gate-json-media-detect",
    }
    return JSONResponse(payload)
