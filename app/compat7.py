from __future__ import annotations

import html
import re
import subprocess
from urllib.parse import quote, urlparse
from urllib.request import Request as URLRequest, urlopen

import chompjs
from fastapi import Query
from fastapi.responses import JSONResponse, PlainTextResponse

from .compat import (
    PUBLIC_BASE,
    UA,
    clean_url,
    inspect_note,
    log_request,
    normalize_xhs_url,
    resolve_url,
    verify_and_bind,
)
from .compat2 import proxy_video_url
from .compat3 import app, canonical_video_key, dedupe

# 只取代 GATE；圖片與影片代理沿用既有版本。
app.router.routes[:] = [r for r in app.router.routes if getattr(r, "path", None) != "/xhszshq"]


def _allowed_scoped_image(value: str) -> bool:
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


def _note_id_from_url(value: str) -> str:
    m = re.search(r"/(?:explore|discovery/item|item)/([0-9a-fA-F]{16,32})(?:[/?#]|$)", value or "")
    return m.group(1).lower() if m else ""


def _balanced_object(text: str, start: int, max_len: int = 700_000) -> str:
    if start < 0 or start >= len(text) or text[start] != "{":
        return ""
    depth = 0
    quote_ch = ""
    escaped = False
    stop = min(len(text), start + max_len)
    for i in range(start, stop):
        ch = text[i]
        if quote_ch:
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == quote_ch:
                quote_ch = ""
            continue
        if ch in ('"', "'", "`"):
            quote_ch = ch
            continue
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return text[start:i + 1]
    return ""


def _item_image_url(item) -> str:
    if isinstance(item, str):
        return clean_url(item) if _allowed_scoped_image(clean_url(item)) else ""
    if not isinstance(item, dict):
        return ""

    keys = (
        "urlDefault", "originalUrl", "original_url", "urlPre", "url",
        "imageUrl", "image_url", "url_default", "url_pre",
    )
    for key in keys:
        value = item.get(key)
        if isinstance(value, str):
            value = clean_url(value)
            if _allowed_scoped_image(value):
                return value

    for info_key in ("infoList", "info_list"):
        info = item.get(info_key)
        if isinstance(info, list):
            for row in info:
                if not isinstance(row, dict):
                    continue
                for key in ("url", "urlDefault", "urlPre", "originalUrl"):
                    value = row.get(key)
                    if isinstance(value, str):
                        value = clean_url(value)
                        if _allowed_scoped_image(value):
                            return value
    return ""


def _images_from_note_obj(obj) -> list[str]:
    if not isinstance(obj, dict):
        return []
    arr = obj.get("imageList")
    if not isinstance(arr, list):
        arr = obj.get("image_list")
    if not isinstance(arr, list):
        return []
    return dedupe([u for u in (_item_image_url(x) for x in arr) if u])


def _obj_note_id(obj) -> str:
    if not isinstance(obj, dict):
        return ""
    for key in ("noteId", "note_id", "id"):
        value = obj.get(key)
        if isinstance(value, str) and re.fullmatch(r"[0-9a-fA-F]{16,32}", value):
            return value.lower()
    return ""


def _find_exact_note_images(obj, nid: str, depth: int = 0, seen=None) -> list[str]:
    if depth > 14:
        return []
    if seen is None:
        seen = set()
    oid = id(obj)
    if oid in seen:
        return []
    seen.add(oid)

    if isinstance(obj, dict):
        if _obj_note_id(obj) == nid:
            images = _images_from_note_obj(obj)
            if images:
                return images

        note = obj.get("note")
        if isinstance(note, dict) and _obj_note_id(note) == nid:
            images = _images_from_note_obj(note)
            if images:
                return images

        direct = obj.get(nid)
        if isinstance(direct, dict):
            if _obj_note_id(direct) in {"", nid}:
                images = _images_from_note_obj(direct)
                if images:
                    return images
                nested = direct.get("note")
                if isinstance(nested, dict) and _obj_note_id(nested) in {"", nid}:
                    images = _images_from_note_obj(nested)
                    if images:
                        return images

        for value in obj.values():
            if isinstance(value, (dict, list)):
                images = _find_exact_note_images(value, nid, depth + 1, seen)
                if images:
                    return images

    elif isinstance(obj, list):
        for value in obj[:500]:
            if isinstance(value, (dict, list)):
                images = _find_exact_note_images(value, nid, depth + 1, seen)
                if images:
                    return images
    return []


def _same_url_exact_note_images(resolved: str) -> tuple[list[str], str]:
    """只抓目前網址自己的 HTML，再以 URL 裡的 exact noteId 鎖定物件。

    不掃整頁所有圖片；只有「包含目前 noteId 且物件內有自己的 imageList」才接受。
    這是 gallery-dl 失敗時的同網址 fallback。
    """
    nid = _note_id_from_url(resolved)
    if not nid:
        return [], "same_url_note_id_missing"
    try:
        req = URLRequest(resolved, headers={
            "User-Agent": UA,
            "Accept": "text/html,application/xhtml+xml,application/json;q=0.9,*/*;q=0.8",
            "Accept-Language": "zh-TW,zh;q=0.9,en;q=0.8",
            "Referer": "https://www.xiaohongshu.com/",
        })
        with urlopen(req, timeout=25) as resp:
            raw = resp.read(12 * 1024 * 1024).decode("utf-8", errors="ignore")
    except Exception as exc:
        return [], f"same_url_fetch_{type(exc).__name__}"

    variants = [
        raw,
        html.unescape(raw).replace("\\u002F", "/").replace("\\/", "/").replace("\\u0026", "&"),
    ]
    for variant_index, text in enumerate(variants):
        positions = [m.start() for m in re.finditer(re.escape(nid), text, flags=re.I)]
        if not positions:
            continue

        for pos in positions[:30]:
            left = max(0, pos - 180_000)
            starts = [m.start() for m in re.finditer(r"\{", text[left:pos])]
            # 從最靠近 noteId 的物件開始，逐層往外找 enclosing object。
            for rel_start in reversed(starts[-500:]):
                start = left + rel_start
                chunk = _balanced_object(text, start)
                if not chunk or len(chunk) < (pos - start) or nid not in chunk.lower():
                    continue
                if "imageList" not in chunk and "image_list" not in chunk:
                    continue
                candidates = [chunk]
                if '\\"' in chunk:
                    candidates.append(chunk.replace('\\"', '"'))
                for candidate in candidates:
                    try:
                        obj = chompjs.parse_js_object(candidate)
                    except Exception:
                        continue
                    images = _find_exact_note_images(obj, nid)
                    if images:
                        return images, f"same-url-exact-object-v{variant_index + 1}"

    return [], "same_url_exact_note_object_not_found"


def inspect_one_url_only(input_url: str) -> tuple[str, list[str], list[str], str]:
    """一個網址就是一篇文章。先使用 gallery-dl；失敗再只解析同一網址內 exact noteId 物件。"""
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

    exact_images, exact_reason = _same_url_exact_note_images(resolved)
    if exact_images:
        return resolved, exact_images, [], exact_reason

    return resolved, [], [], f"{last_reason}+{exact_reason}"


def _proxy_images(items: list[str]) -> list[str]:
    scoped = dedupe([x for x in items if x and _allowed_scoped_image(x)])
    return [f"{PUBLIC_BASE}/media/image?url={quote(x, safe='')}" for x in scoped]


def _success_payload(note_url: str, scoped_images: list[str], scoped_videos: list[str], scoped_parser: str):
    images = _proxy_images(scoped_images)
    live_videos_raw = []
    seen_video: set[str] = set()
    for video in scoped_videos:
        key = canonical_video_key(video)
        if key and key not in seen_video:
            seen_video.add(key)
            live_videos_raw.append(video)
    live_videos = [proxy_video_url(x) for x in live_videos_raw]

    if images and live_videos:
        nt = notetype = "livepic"
    elif images:
        nt = notetype = "pic"
    else:
        nt = notetype = "video"

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
        "status": 1, "gate": 1, "notetype": notetype, "nt": nt,
        "note_url": note_url, "source_url": note_url, "title": "", "author": "",
        "gigl": gigl, "ligl": ligl, "nigl": nigl,
        "url": first_image if nt in {"pic", "livepic"} else first_video,
        "image": first_image, "images": images, "pic": first_image, "pics": images,
        "video": first_video if nt == "video" else "", "videos": live_videos if nt == "video" else [],
        "livepic": ligl, "livepics": ligl, "live": ligl, "livephoto": ligl,
        "livePhoto": ligl, "live_photos": ligl,
        "live_image": first_image if nt == "livepic" else "",
        "live_images": images if nt == "livepic" else [],
        "live_video": first_video if nt == "livepic" else "",
        "live_videos": live_videos if nt == "livepic" else [],
        "cover": first_image if nt == "livepic" else "",
        "livevideo": first_video if nt == "livepic" else "",
        "image_count": len(images), "live_count": len(ligl), "normal_count": len(nigl),
        "parser": scoped_parser, "message": "ok-single-url-scope-v3-exact-object",
    })


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
    if scoped_images or scoped_videos:
        return _success_payload(note_url, scoped_images, scoped_videos, scoped_parser)

    # 影片才允許 yt-dlp 的 URL 專屬 fallback；圖片不採用 inspect_note 的整頁 HTML 結果。
    fallback = inspect_note(c)
    if fallback.get("nt") == "video" and fallback.get("video"):
        return JSONResponse({
            "status": 1, "gate": 1, "notetype": "video", "nt": "video",
            "note_url": fallback.get("resolved_url") or note_url,
            "source_url": fallback.get("resolved_url") or note_url,
            "title": fallback.get("title") or "", "author": fallback.get("author") or "",
            "gigl": [], "ligl": [], "nigl": [], "url": fallback["video"],
            "image": "", "images": [], "pic": "", "pics": [],
            "video": fallback["video"], "videos": [fallback["video"]],
            "livepic": [], "livepics": [], "live": [], "livephoto": [], "livePhoto": [], "live_photos": [],
            "live_image": "", "live_images": [], "live_video": "", "live_videos": [],
            "cover": "", "livevideo": "", "image_count": 0, "live_count": 0, "normal_count": 0,
            "parser": f"{scoped_parser}+yt-dlp-video-only", "message": "ok-single-url-video-fallback",
        })

    return JSONResponse({
        "error": "current_url_media_not_found",
        "message": "這個網址自己的文章內容仍無法取得媒體；已停止，不會抓留言、推薦或其他文章",
        "note_url": note_url,
        "parser": scoped_parser,
    })
