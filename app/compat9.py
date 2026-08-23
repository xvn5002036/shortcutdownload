from __future__ import annotations

import html
import re
from urllib.request import Request as URLRequest, urlopen

import chompjs

from . import compat8 as stable
from . import compat7 as base

app = stable.app

# 保存目前已驗證成功的普通圖片/普通影片流程；找不到實況圖時完整回退，不改它。
_stable_inspect_one_url_only = base.inspect_one_url_only

LIVE_MARKERS = (
    "livephoto", "live_photo", "livepic", "live_pic",
    "motionphoto", "motion_photo", "dynamicphoto", "dynamic_photo",
    "islivephoto", "is_live_photo", "livephotoid", "live_photo_id",
)


def _dict_has_live_marker(item: dict) -> bool:
    for key, value in item.items():
        low = str(key).replace("-", "_").lower()
        if any(marker in low for marker in LIVE_MARKERS):
            if isinstance(value, bool):
                if value:
                    return True
            elif value not in (None, "", 0, False, [], {}):
                return True
    # 有些頁面把實況資訊藏在 nested media/video 欄位，鍵名不一定直接叫 livePhoto。
    text_keys = " ".join(str(k).lower() for k in item.keys())
    return "live" in text_keys or "motion" in text_keys


def _origin_key_in_item(obj, depth: int = 0) -> str:
    if depth > 12:
        return ""
    if isinstance(obj, dict):
        for key, value in obj.items():
            normalized = str(key).replace("-", "_").lower()
            if normalized in stable.ORIGIN_VIDEO_KEY_NAMES and isinstance(value, str):
                value = base.clean_url(value).strip()
                if value and not value.startswith(("http://", "https://")):
                    return value.lstrip("/")
        for value in obj.values():
            if isinstance(value, (dict, list)):
                found = _origin_key_in_item(value, depth + 1)
                if found:
                    return found
    elif isinstance(obj, list):
        for value in obj[:100]:
            if isinstance(value, (dict, list)):
                found = _origin_key_in_item(value, depth + 1)
                if found:
                    return found
    return ""


def _collect_live_video_urls(obj, out: list[tuple[int, str]], path: tuple[str, ...] = (), depth: int = 0) -> None:
    if depth > 14 or len(out) >= 80:
        return
    if isinstance(obj, str):
        value = base.clean_url(obj)
        if not base._allowed_scoped_video(value):
            return
        p = ".".join(path).lower()
        score = 0
        if "live" in p or "motion" in p:
            score += 1000
        if "video" in p:
            score += 500
        if "masterurl" in p or "master_url" in p:
            score += 250
        if "stream" in p or "/stream/" in value.lower():
            score += 100
        if "backup" in p:
            score -= 50
        out.append((score, value))
        return
    if isinstance(obj, dict):
        for key, value in obj.items():
            if isinstance(value, (dict, list, str)):
                _collect_live_video_urls(value, out, path + (str(key),), depth + 1)
    elif isinstance(obj, list):
        for index, value in enumerate(obj[:200]):
            if isinstance(value, (dict, list, str)):
                _collect_live_video_urls(value, out, path + (str(index),), depth + 1)


def _live_video_from_item(item: dict) -> str:
    # 實況圖若有自己的 originVideoKey，優先使用該原始動態片段。
    origin_key = _origin_key_in_item(item)
    if origin_key:
        return f"https://sns-video-bd.xhscdn.com/{origin_key}"

    ranked: list[tuple[int, str]] = []
    _collect_live_video_urls(item, ranked)
    if not ranked:
        return ""
    # 去重後取最像 live/motion 專屬欄位的 URL。
    best: dict[str, int] = {}
    for score, url in ranked:
        if score > best.get(url, -10**9):
            best[url] = score
    return sorted(best.items(), key=lambda x: -x[1])[0][0]


def _live_pairs_from_note_target(target) -> tuple[list[str], list[str], list[str]]:
    if not isinstance(target, dict):
        return [], [], []
    arr = target.get("imageList")
    if not isinstance(arr, list):
        arr = target.get("image_list")
    if not isinstance(arr, list):
        return [], [], []

    live_covers: list[str] = []
    live_videos: list[str] = []
    normal_images: list[str] = []

    for item in arr:
        cover = base._item_image_url(item)
        if not cover:
            continue
        if isinstance(item, dict):
            live_video = _live_video_from_item(item)
            # 要同一個 imageList item 同時存在 cover + 動態片段，才算真正實況圖。
            if live_video and (_dict_has_live_marker(item) or live_video):
                live_covers.append(cover)
                live_videos.append(live_video)
                continue
        normal_images.append(cover)

    return base.dedupe(live_covers), base.dedupe(live_videos), base.dedupe(normal_images)


def _find_exact_note_live_pairs(obj, nid: str, depth: int = 0, seen=None):
    if depth > 14:
        return [], [], []
    if seen is None:
        seen = set()
    oid = id(obj)
    if oid in seen:
        return [], [], []
    seen.add(oid)

    if isinstance(obj, dict):
        if base._obj_note_id(obj) == nid:
            pairs = _live_pairs_from_note_target(obj)
            if pairs[0] and pairs[1]:
                return pairs

        note = obj.get("note")
        if isinstance(note, dict) and base._obj_note_id(note) == nid:
            pairs = _live_pairs_from_note_target(note)
            if pairs[0] and pairs[1]:
                return pairs

        direct = obj.get(nid)
        if isinstance(direct, dict):
            target = direct.get("note") if isinstance(direct.get("note"), dict) else direct
            if base._obj_note_id(target) in {"", nid}:
                pairs = _live_pairs_from_note_target(target)
                if pairs[0] and pairs[1]:
                    return pairs

        for child in obj.values():
            if isinstance(child, (dict, list)):
                found = _find_exact_note_live_pairs(child, nid, depth + 1, seen)
                if found[0] and found[1]:
                    return found

    elif isinstance(obj, list):
        for child in obj[:500]:
            if isinstance(child, (dict, list)):
                found = _find_exact_note_live_pairs(child, nid, depth + 1, seen)
                if found[0] and found[1]:
                    return found
    return [], [], []


def _same_url_exact_note_live(input_url: str):
    resolved = base.resolve_url(input_url) or base.normalize_xhs_url(input_url)
    if not resolved:
        return "", [], [], [], "live_url_missing"
    nid = base._note_id_from_url(resolved)
    if not nid:
        return resolved, [], [], [], "live_note_id_missing"

    try:
        req = URLRequest(resolved, headers={
            "User-Agent": base.UA,
            "Accept": "text/html,application/xhtml+xml,application/json;q=0.9,*/*;q=0.8",
            "Accept-Language": "zh-TW,zh;q=0.9,en;q=0.8",
            "Referer": "https://www.xiaohongshu.com/",
        })
        with urlopen(req, timeout=25) as resp:
            raw = resp.read(12 * 1024 * 1024).decode("utf-8", errors="ignore")
    except Exception as exc:
        return resolved, [], [], [], f"live_fetch_{type(exc).__name__}"

    variants = [
        raw,
        html.unescape(raw).replace("\\u002F", "/").replace("\\/", "/").replace("\\u0026", "&"),
    ]
    for variant_index, text in enumerate(variants):
        positions = [m.start() for m in re.finditer(re.escape(nid), text, flags=re.I)]
        for pos in positions[:30]:
            left = max(0, pos - 180_000)
            starts = [m.start() for m in re.finditer(r"\{", text[left:pos])]
            for rel_start in reversed(starts[-500:]):
                start = left + rel_start
                chunk = base._balanced_object(text, start)
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
                    covers, videos, normals = _find_exact_note_live_pairs(obj, nid)
                    if covers and videos:
                        # compat7 的 payload 會把前 N 張圖片與 N 支影片配成 ligl，剩下放 nigl。
                        ordered_images = covers + [x for x in normals if x not in covers]
                        return resolved, ordered_images, videos, normals, f"same-url-exact-livephoto-v{variant_index + 1}"

    return resolved, [], [], [], "same_url_exact_livephoto_not_found"


def _inspect_one_url_only_live_first(input_url: str):
    resolved, ordered_images, live_videos, _normals, reason = _same_url_exact_note_live(input_url)
    if ordered_images and live_videos:
        return resolved, ordered_images, live_videos, reason

    # 找不到實況圖時，完整使用已驗證成功的普通圖片/普通影片流程。
    return _stable_inspect_one_url_only(input_url)


base.inspect_one_url_only = _inspect_one_url_only_live_first
