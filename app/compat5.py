from __future__ import annotations

import html
import json
import re
from urllib.parse import quote, urlparse
from urllib.request import Request as URLRequest, urlopen

from fastapi import Query
from fastapi.responses import JSONResponse, PlainTextResponse

from .compat import UA, PUBLIC_BASE, inspect_note, log_request, normalize_xhs_url, verify_and_bind
from .compat2 import proxy_video_url
from .compat3 import canonical_video_key, dedupe, to_original_xhs_image_url
from .compat4 import app

app.router.routes[:] = [r for r in app.router.routes if getattr(r, "path", None) != "/xhszshq"]


def _clean(value: str) -> str:
    value = html.unescape(value or "")
    return value.replace("\\u002F", "/").replace("\\/", "/").replace("\\u0026", "&").strip('"\' ')


def _note_id(url: str) -> str:
    path = urlparse(url).path
    for pattern in (
        r"/(?:explore|discovery/item)/([0-9a-fA-F]{16,32})",
        r"/item/([0-9a-fA-F]{16,32})",
    ):
        m = re.search(pattern, path)
        if m:
            return m.group(1)
    return ""


def _balanced_array(text: str, start: int, limit: int = 500_000) -> str:
    pos = text.find("[", start)
    if pos < 0:
        return ""
    depth = 0
    in_string = False
    escaped = False
    for i in range(pos, min(len(text), pos + limit)):
        ch = text[i]
        if in_string:
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == '"':
                in_string = False
            continue
        if ch == '"':
            in_string = True
        elif ch == "[":
            depth += 1
        elif ch == "]":
            depth -= 1
            if depth == 0:
                return text[pos:i + 1]
    return ""


def _valid_note_image(url: str) -> bool:
    """此函式只套用在已鎖定『當前筆記 imageList』內，因此不再用路徑字樣誤殺正式 UUID 圖片。"""
    try:
        parsed = urlparse(url)
        host = (parsed.hostname or "").lower()
        path = parsed.path.lower()
        if host != "ci.xiaohongshu.com" and "xhscdn.com" not in host:
            return False
        if any(x in host for x in ("avatar", "static", "picasso")):
            return False
        if any(x in path for x in ("/avatar", "/comment", "/emoji", "/icon", "/logo")):
            return False
        return bool(path and path != "/")
    except Exception:
        return False


def _one_image_url(item) -> str:
    if not isinstance(item, dict):
        return ""

    for key in ("urlDefault", "originalUrl", "original_url", "urlPre", "url", "imageUrl", "image_url"):
        value = item.get(key)
        if isinstance(value, str):
            value = _clean(value)
            if value.startswith(("http://", "https://")) and _valid_note_image(value):
                return value

    info = item.get("infoList") or item.get("info_list")
    if isinstance(info, list):
        for entry in info:
            if not isinstance(entry, dict):
                continue
            for key in ("url", "urlDefault", "urlPre"):
                value = entry.get(key)
                if isinstance(value, str):
                    value = _clean(value)
                    if value.startswith(("http://", "https://")) and _valid_note_image(value):
                        return value
    return ""


def _images_from_array(array_text: str) -> list[str]:
    try:
        data = json.loads(array_text)
    except Exception:
        return []
    if not isinstance(data, list):
        return []
    return dedupe([u for u in (_one_image_url(item) for item in data) if u])


def extract_exact_note_images(note_url: str) -> tuple[list[str], str]:
    try:
        req = URLRequest(note_url, headers={
            "User-Agent": UA,
            "Accept": "text/html,application/xhtml+xml,*/*;q=0.8",
            "Accept-Language": "zh-TW,zh;q=0.9,en;q=0.8",
            "Referer": "https://www.xiaohongshu.com/",
        })
        with urlopen(req, timeout=25) as resp:
            raw = resp.read(8 * 1024 * 1024).decode("utf-8", errors="ignore")
            final_url = resp.geturl() or note_url
    except Exception:
        return [], "fetch_failed"

    text = html.unescape(raw).replace("\\u002F", "/").replace("\\/", "/").replace("\\u0026", "&")
    nid = _note_id(final_url) or _note_id(note_url)
    if not nid:
        return [], "note_id_missing"

    anchors = [m.start() for m in re.finditer(re.escape(nid), text)]
    if not anchors:
        return [], "note_id_not_in_page"

    candidates: list[tuple[int, list[str]]] = []
    for anchor in anchors:
        left = max(0, anchor - 20_000)
        right = min(len(text), anchor + 220_000)
        segment = text[left:right]
        for m in re.finditer(r'"imageList"\s*:', segment):
            global_pos = left + m.start()
            distance = abs(global_pos - anchor)
            if distance > 180_000:
                continue
            array_text = _balanced_array(text, left + m.end())
            urls = _images_from_array(array_text)
            if urls:
                candidates.append((distance, urls))

    if not candidates:
        return [], "exact_imageList_missing"
    candidates.sort(key=lambda x: (x[0], -len(x[1])))
    return candidates[0][1], "exact_note_imageList"


def _proxy(items: list[str]) -> list[str]:
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

    note = inspect_note(c)
    note_url = note["resolved_url"] or normalize_xhs_url(c)
    exact_images, exact_parser = extract_exact_note_images(note_url)

    if note["nt"] != "video":
        if not exact_images:
            return JSONResponse({
                "error": "current_note_images_not_found",
                "message": "找不到目前文章自己的圖片清單，已停止下載以避免抓到留言或其他文章圖片",
                "note_url": note_url,
                "parser": exact_parser,
            })
        note["images"] = exact_images
        if note["nt"] == "livepic":
            note["live_images"] = exact_images
        else:
            note["nt"] = "pic"
            note["notetype"] = "pic"
        note["parser"] = f"{note['parser']}+{exact_parser}"

    if not note["nt"]:
        return JSONResponse({"error": "parse_failed", "message": "無法解析該筆記媒體", "note_url": note_url})

    images = _proxy(note["images"])
    first_image = images[0] if images else ""

    live_source_images = note["live_images"] or (note["images"] if note["nt"] == "livepic" else [])
    live_covers = _proxy(live_source_images)

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
        {"cover": live_covers[i], "livevideo": live_videos[i], "image": live_covers[i], "video": live_videos[i]}
        for i in range(pair_count)
    ]
    nigl = live_covers[pair_count:] if note["nt"] == "livepic" else []
    gigl = images if note["nt"] == "pic" else []

    first_live_cover = live_covers[0] if live_covers else ""
    first_live_video = live_videos[0] if live_videos else ""

    return JSONResponse({
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
        "message": "ok-exact-current-note-only-v3",
    })
