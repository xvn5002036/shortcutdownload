from __future__ import annotations

import html
import re
from urllib.parse import quote, urlparse
from urllib.request import Request as URLRequest, urlopen

import chompjs
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


def _unwrap(value):
    # Vue/ref SSR state sometimes wraps values as {_value: ...} or {value: ...}.
    for _ in range(4):
        if isinstance(value, dict):
            if "_value" in value and len(value) <= 3:
                value = value["_value"]
                continue
            if "value" in value and len(value) <= 3:
                value = value["value"]
                continue
        break
    return value


def _valid_note_image(url: str) -> bool:
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
    item = _unwrap(item)
    if not isinstance(item, dict):
        return ""

    # imageList item itself only. Do not recursively walk the whole page/note object.
    for key in (
        "urlDefault", "originalUrl", "original_url", "urlPre", "url",
        "imageUrl", "image_url", "url_default", "url_pre",
    ):
        value = item.get(key)
        if isinstance(value, str):
            value = _clean(value)
            if value.startswith(("http://", "https://")) and _valid_note_image(value):
                return value

    info = _unwrap(item.get("infoList") or item.get("info_list"))
    if isinstance(info, list):
        # Prefer original/default entries if type/name metadata is present.
        ranked = []
        for entry in info:
            entry = _unwrap(entry)
            if not isinstance(entry, dict):
                continue
            label = str(entry.get("imageScene") or entry.get("type") or entry.get("name") or "").lower()
            score = 0
            if "original" in label or "default" in label:
                score = -2
            elif "pre" in label:
                score = -1
            for key in ("url", "urlDefault", "urlPre", "url_default", "url_pre"):
                value = entry.get(key)
                if isinstance(value, str):
                    value = _clean(value)
                    if value.startswith(("http://", "https://")) and _valid_note_image(value):
                        ranked.append((score, value))
                        break
        if ranked:
            ranked.sort(key=lambda x: x[0])
            return ranked[0][1]
    return ""


def _parse_initial_state(text: str):
    markers = (
        "window.__INITIAL_STATE__",
        "window.__INITIAL_STATE__=",
        "__INITIAL_STATE__",
    )
    for marker in markers:
        pos = text.find(marker)
        if pos < 0:
            continue
        eq = text.find("=", pos)
        if eq < 0:
            continue
        brace = text.find("{", eq)
        if brace < 0:
            continue
        try:
            return chompjs.parse_js_object(text[brace:])
        except Exception:
            continue
    return None


def _find_note_detail_map(state):
    state = _unwrap(state)
    if not isinstance(state, dict):
        return None
    note = _unwrap(state.get("note"))
    if isinstance(note, dict):
        detail = _unwrap(note.get("noteDetailMap") or note.get("note_detail_map"))
        if isinstance(detail, dict):
            return detail
    return None


def _find_note_entry(detail_map: dict, nid: str):
    # Exact key first; no recommendation/comment fallback.
    direct = _unwrap(detail_map.get(nid))
    if isinstance(direct, dict):
        return direct

    # Some SSR states key entries differently but store noteId inside the exact note entry.
    for value in detail_map.values():
        value = _unwrap(value)
        if not isinstance(value, dict):
            continue
        note = _unwrap(value.get("note"))
        if isinstance(note, dict):
            candidate_id = str(note.get("noteId") or note.get("note_id") or note.get("id") or "")
            if candidate_id == nid:
                return value
        candidate_id = str(value.get("noteId") or value.get("note_id") or value.get("id") or "")
        if candidate_id == nid:
            return value
    return None


def extract_state_note_images(note_url: str) -> tuple[list[str], str]:
    try:
        req = URLRequest(note_url, headers={
            "User-Agent": UA,
            "Accept": "text/html,application/xhtml+xml,*/*;q=0.8",
            "Accept-Language": "zh-TW,zh;q=0.9,en;q=0.8",
            "Referer": "https://www.xiaohongshu.com/",
        })
        with urlopen(req, timeout=25) as resp:
            raw = resp.read(10 * 1024 * 1024).decode("utf-8", errors="ignore")
            final_url = resp.geturl() or note_url
    except Exception:
        return [], "fetch_failed"

    nid = _note_id(final_url) or _note_id(note_url)
    if not nid:
        return [], "note_id_missing"

    state = _parse_initial_state(raw)
    if not isinstance(state, dict):
        return [], "initial_state_missing"

    detail_map = _find_note_detail_map(state)
    if not isinstance(detail_map, dict):
        return [], "note_detail_map_missing"

    entry = _find_note_entry(detail_map, nid)
    if not isinstance(entry, dict):
        return [], "current_note_entry_missing"

    note_obj = _unwrap(entry.get("note"))
    if not isinstance(note_obj, dict):
        note_obj = entry

    image_list = _unwrap(note_obj.get("imageList") or note_obj.get("image_list"))
    if not isinstance(image_list, list):
        return [], "current_note_image_list_missing"

    images = dedupe([u for u in (_one_image_url(item) for item in image_list) if u])
    if not images:
        return [], "current_note_image_list_empty"
    return images, "initial_state.note.noteDetailMap.current.imageList"


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

    # For any non-video note, media list MUST come from the current note's own noteDetailMap entry.
    if note["nt"] != "video":
        exact_images, exact_parser = extract_state_note_images(note_url)
        if not exact_images:
            return JSONResponse({
                "error": "current_note_images_not_found",
                "message": "無法從目前文章自己的 imageList 取得圖片，已停止以避免抓到留言或其他文章",
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
        "message": "ok-current-note-initial-state-v1",
    })
