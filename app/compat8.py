from __future__ import annotations

import html
import logging
import re
from urllib.parse import urlsplit
from urllib.request import Request as URLRequest, urlopen

import chompjs

from . import compat7 as base

app = base.app
logger = logging.getLogger("xhs.compat8")


def _raw_same_note_image(value) -> str:
    """已驗證成功的圖片無水印原圖邏輯；不要改動。"""
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


# 圖片部分保持原樣。
base._clean_image = _raw_same_note_image


ORIGIN_VIDEO_KEY_NAMES = {
    "originvideokey",
    "origin_video_key",
    "originalvideokey",
    "original_video_key",
}


def _origin_video_key_from_target(obj, depth: int = 0) -> str:
    """只在目前 exact note 物件內尋找平台保存的原始影片 key。

    masterUrl/一般 stream 可能已燒入右下角小紅書浮水印，因此不再把它視為原檔。
    """
    if depth > 18:
        return ""
    if isinstance(obj, dict):
        for key, value in obj.items():
            normalized = str(key).replace("-", "_").lower()
            if normalized in ORIGIN_VIDEO_KEY_NAMES and isinstance(value, str):
                value = base.clean_url(value).strip()
                if value and not value.startswith(("http://", "https://")):
                    return value.lstrip("/")
        for value in obj.values():
            if isinstance(value, (dict, list)):
                found = _origin_video_key_from_target(value, depth + 1)
                if found:
                    return found
    elif isinstance(obj, list):
        for value in obj[:300]:
            if isinstance(value, (dict, list)):
                found = _origin_video_key_from_target(value, depth + 1)
                if found:
                    return found
    return ""


def _origin_video_url_from_target(target) -> str:
    key = _origin_video_key_from_target(target)
    if not key:
        return ""
    # XHS 舊/原始影片資源：originVideoKey 對應 sns-video-bd CDN 原檔。
    return f"https://sns-video-bd.xhscdn.com/{key}"


def _origin_video_from_exact_note_obj(obj, nid: str) -> str:
    if not isinstance(obj, (dict, list)):
        return ""
    seen = set()

    def walk(value, depth=0):
        if depth > 14:
            return ""
        oid = id(value)
        if oid in seen:
            return ""
        seen.add(oid)

        if isinstance(value, dict):
            if base._obj_note_id(value) == nid:
                return _origin_video_url_from_target(value)

            note = value.get("note")
            if isinstance(note, dict) and base._obj_note_id(note) == nid:
                return _origin_video_url_from_target(note)

            direct = value.get(nid)
            if isinstance(direct, dict):
                target = direct.get("note") if isinstance(direct.get("note"), dict) else direct
                if base._obj_note_id(target) in {"", nid}:
                    found = _origin_video_url_from_target(target)
                    if found:
                        return found

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
        return ""

    return walk(obj)


def _same_url_exact_note_origin_video(resolved: str) -> tuple[list[str], str]:
    """網址 -> exact noteId -> originVideoKey -> 原始影片 URL。"""
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
                    origin_url = _origin_video_from_exact_note_obj(obj, nid)
                    if origin_url and base._allowed_scoped_video(origin_url):
                        logger.info("XHS_ORIGIN_VIDEO_KEY_SELECTED note=%s", nid)
                        return [origin_url], f"same-url-exact-note-originVideoKey-v{variant_index + 1}"

    logger.info("XHS_ORIGIN_VIDEO_KEY_NOT_EXPOSED note=%s", nid)
    return [], "same_url_exact_note_originVideoKey_not_exposed"


_original_inspect_one_url_only = base.inspect_one_url_only


def _inspect_one_url_only_origin_video_first(input_url: str):
    resolved = base.resolve_url(input_url) or base.normalize_xhs_url(input_url)
    if resolved:
        videos, reason = _same_url_exact_note_origin_video(resolved)
        if videos:
            return resolved, [], videos, reason

    # 圖片仍走既有成功流程。
    fallback_resolved, fallback_images, fallback_videos, fallback_reason = _original_inspect_one_url_only(input_url)
    if fallback_images:
        return fallback_resolved, fallback_images, fallback_videos, fallback_reason

    # 只有 masterUrl / 一般 stream 時，一律拒絕回傳，避免右下角浮水印影片。
    if fallback_videos:
        logger.info("XHS_PROCESSED_VIDEO_BLOCKED source=%s", fallback_resolved or resolved or input_url)
        return fallback_resolved, [], [], f"{reason}+processed_video_blocked"

    return fallback_resolved, [], [], f"{reason}+{fallback_reason}"


base.inspect_one_url_only = _inspect_one_url_only_origin_video_first
