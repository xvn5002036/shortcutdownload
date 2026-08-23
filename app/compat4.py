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
from .compat3 import app, canonical_video_key, dedupe, to_original_xhs_image_url

# 只取代 GATE 路由；沿用 compat3 的 /media/image 與 compat2 的 /media/video。
app.router.routes[:] = [
    route for route in app.router.routes
    if getattr(route, "path", None) != "/xhszshq"
]


def _clean_url(value: str) -> str:
    value = html.unescape(value or "")
    return value.replace("\\u002F", "/").replace("\\/", "/").replace("\\u0026", "&").strip('"\' ')


def _extract_note_id(url: str) -> str:
    path = urlparse(url).path
    patterns = [
        r"/(?:explore|discovery/item)/([0-9a-fA-F]{16,32})",
        r"/item/([0-9a-fA-F]{16,32})",
    ]
    for pattern in patterns:
        m = re.search(pattern, path)
        if m:
            return m.group(1)
    return ""


def _balanced_array(text: str, start: int) -> str:
    """從 start 後第一個 [ 開始擷取完整 JSON array，忽略字串內括號。"""
    pos = text.find("[", start)
    if pos < 0:
        return ""
    depth = 0
    in_string = False
    escaped = False
    for i in range(pos, min(len(text), pos + 800_000)):
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


def _collect_urls(obj) -> list[str]:
    urls: list[str] = []
    if isinstance(obj, dict):
        # 小紅書 imageList 常見 URL 欄位。
        for key in ("urlDefault", "urlPre", "url", "imageUrl", "image_url", "originalUrl", "original_url"):
            value = obj.get(key)
            if isinstance(value, str) and value.startswith(("http://", "https://")):
                urls.append(_clean_url(value))
        for value in obj.values():
            urls.extend(_collect_urls(value))
    elif isinstance(obj, list):
        for value in obj:
            urls.extend(_collect_urls(value))
    return urls


def _is_note_image(url: str) -> bool:
    try:
        parsed = urlparse(url)
        host = (parsed.hostname or "").lower()
        path = parsed.path.lower()
        if "xhscdn.com" not in host and host != "ci.xiaohongshu.com":
            return False
        if any(x in host for x in ("avatar", "fe-static", "picasso-static")):
            return False
        return any(token in path for token in ("notes_uhdr", "note_pre_post", "spectrum")) or "imageview2" in url.lower()
    except Exception:
        return False


def extract_current_note_images(note_url: str) -> list[str]:
    """只解析目前筆記自己的 imageList，不掃整頁推薦/相關筆記。"""
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
        return []

    normalized = html.unescape(raw).replace("\\u002F", "/").replace("\\/", "/").replace("\\u0026", "&")
    note_id = _extract_note_id(final_url) or _extract_note_id(note_url)

    # 找出所有 imageList，優先選「距離當前 noteId 最近」的那一組。
    candidates: list[tuple[int, list[str]]] = []
    for match in re.finditer(r'"imageList"\s*:', normalized):
        array_text = _balanced_array(normalized, match.end())
        if not array_text:
            continue
        try:
            data = json.loads(array_text)
        except Exception:
            continue
        urls = dedupe([u for u in _collect_urls(data) if _is_note_image(u)])
        if not urls:
            continue

        if note_id:
            left = max(0, match.start() - 80_000)
            right = min(len(normalized), match.start() + 80_000)
            window = normalized[left:right]
            distance = 0 if note_id in window else 1_000_000
            # 若 noteId 在視窗內，距離越近分數越好。
            if distance == 0:
                nearest = min((abs(match.start() - p) for p in [m.start() for m in re.finditer(re.escape(note_id), window)]), default=999_999)
                distance = nearest
        else:
            distance = match.start()
        candidates.append((distance, urls))

    if not candidates:
        return []

    candidates.sort(key=lambda x: x[0])
    return candidates[0][1]


def _proxy_originals(items: list[str]) -> list[str]:
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

    # 關鍵修正：整頁掃描只用來判型；真正圖片清單改成目前筆記的 imageList。
    scoped_images = extract_current_note_images(note_url)
    if scoped_images:
        note["images"] = scoped_images
        if note["nt"] == "livepic":
            note["live_images"] = scoped_images
        elif note["nt"] != "video":
            note["nt"] = "pic"
            note["notetype"] = "pic"
        note["parser"] = f"{note['parser']}+scoped-imageList"

    if not note["nt"]:
        return JSONResponse({"error": "parse_failed", "message": "無法解析該筆記媒體", "note_url": note_url})

    images = _proxy_originals(note["images"])
    first_image = images[0] if images else ""

    live_source_images = note["live_images"] or (note["images"] if note["nt"] == "livepic" else [])
    live_covers = _proxy_originals(live_source_images)

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
        "message": "ok-current-note-only-v1",
    }
    return JSONResponse(payload)
