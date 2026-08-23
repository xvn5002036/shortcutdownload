from __future__ import annotations

import html
import re
from urllib.parse import urlsplit
from urllib.request import Request as URLRequest, urlopen

import chompjs

from . import compat7 as base

app = base.app


def _raw_same_note_image(value) -> str:
    """把目前文章 exact imageList 的展示 URL 轉成同一張圖片的無水印原圖 URL。"""
    if not isinstance(value, str):
        return ""
    value = base.clean_url(value)
    if not base._allowed_scoped_image(value):
        return ""

    try:
        p = urlsplit(value)
        path = p.path
        m = re.search(r"/(notes_pre_post|note_pre_post_uhdr|spectrum)/([^/?#!]+)", path, flags=re.I)
        if m:
            prefix = m.group(1)
            media_id = m.group(2).split("!", 1)[0]
            if media_id:
                return f"https://ci.xiaohongshu.com/{prefix}/{media_id}?imageView2/format/jpeg"

        last = (path.rsplit("/", 1)[-1] or "").split("!", 1)[0]
        if last and len(last) >= 16 and all(ch.isalnum() or ch in "_-" for ch in last):
            return f"https://ci.xiaohongshu.com/{last}?imageView2/format/jpeg"
    except Exception:
        pass

    return value


# 已驗證成功的圖片原圖邏輯：保持不變。
base._clean_image = _raw_same_note_image


def _collect_video_urls(obj, out: list[str], depth: int = 0) -> None:
    """只在目前 exact note 物件內找影片 URL，不掃其他文章。"""
    if depth > 18 or len(out) >= 30:
        return
    if isinstance(obj, str):
        value = base.clean_url(obj)
        if base._allowed_scoped_video(value):
            out.append(value)
        return
    if isinstance(obj, dict):
        # 影片資料常藏在 video/media/stream/h264/h265/masterUrl 等層級；
        # 仍然只遞迴目前文章物件，不跨頁面其他 note。
        for value in obj.values():
            if isinstance(value, (dict, list, str)):
                _collect_video_urls(value, out, depth + 1)
        return
    if isinstance(obj, list):
        for value in obj[:300]:
            if isinstance(value, (dict, list, str)):
                _collect_video_urls(value, out, depth + 1)


def _videos_from_exact_note_obj(obj, nid: str) -> list[str]:
    """找到 exact noteId 後，只收集該文章物件自己的影片。"""
    if not isinstance(obj, (dict, list)):
        return []

    seen = set()

    def walk(value, depth=0):
        if depth > 14:
            return []
        oid = id(value)
        if oid in seen:
            return []
        seen.add(oid)

        if isinstance(value, dict):
            if base._obj_note_id(value) == nid:
                found: list[str] = []
                _collect_video_urls(value, found)
                return base.dedupe(found)

            note = value.get("note")
            if isinstance(note, dict) and base._obj_note_id(note) == nid:
                found: list[str] = []
                _collect_video_urls(note, found)
                return base.dedupe(found)

            direct = value.get(nid)
            if isinstance(direct, dict):
                target = direct.get("note") if isinstance(direct.get("note"), dict) else direct
                if base._obj_note_id(target) in {"", nid}:
                    found: list[str] = []
                    _collect_video_urls(target, found)
                    if found:
                        return base.dedupe(found)

            for child in value.values():
                if isinstance(child, (dict, list)):
                    found = walk(child, depth + 1)
                    if found:
                        return found

        elif isinstance(value, list):
            for child in value[:500]:
                if isinstance(child, (dict, list)):
                    found = walk(child, depth + 1)
                    if found:
                        return found
        return []

    return walk(obj)


def _same_url_exact_note_videos(resolved: str) -> tuple[list[str], str]:
    """以網址中的 noteId 鎖定同一文章，再從該物件讀影片 stream。"""
    nid = base._note_id_from_url(resolved)
    if not nid:
        return [], "video_note_id_missing"

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
        return [], f"video_fetch_{type(exc).__name__}"

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
                candidates = [chunk]
                if '\\"' in chunk:
                    candidates.append(chunk.replace('\\"', '"'))
                for candidate in candidates:
                    try:
                        obj = chompjs.parse_js_object(candidate)
                    except Exception:
                        continue
                    videos = _videos_from_exact_note_obj(obj, nid)
                    if videos:
                        return videos, f"same-url-exact-note-video-v{variant_index + 1}"

    return [], "same_url_exact_note_video_not_found"


_original_inspect_one_url_only = base.inspect_one_url_only


def _inspect_one_url_only_video_first(input_url: str):
    resolved = base.resolve_url(input_url) or base.normalize_xhs_url(input_url)
    if resolved:
        # 第一順位：網址 → exact noteId → 該文章自己的 video stream。
        videos, reason = _same_url_exact_note_videos(resolved)
        if videos:
            return resolved, [], videos, reason

    # 第二順位：保留既有 yt-dlp 判斷。
    try:
        note = base.inspect_note(input_url)
        if note.get("nt") == "video" and note.get("video"):
            resolved2 = note.get("resolved_url") or resolved or input_url
            return resolved2, [], [str(note["video"])], "yt-dlp-same-url-video-first"
    except Exception:
        pass

    # 不是影片時，完整回到已經成功的圖片原圖流程。
    return _original_inspect_one_url_only(input_url)


base.inspect_one_url_only = _inspect_one_url_only_video_first
