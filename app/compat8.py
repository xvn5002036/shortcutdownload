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


def _is_explicit_original_video_path(path: tuple[str, ...]) -> bool:
    """只接受文章資料明確標示為原始/主影片來源的欄位。

    不把一般 stream/play/share URL 猜成原片，避免再下載到帶浮水印版本。
    """
    p = ".".join(path).lower()
    blocked = ("watermark", "water_mark", "wmurl", "wm_url", "marked", "share", "ogvideo", "og:video")
    if any(x in p for x in blocked):
        return False

    # 常見原始來源欄位；尤其 video_info_v2.media.stream.h264[].master_url / masterUrl。
    explicit = (
        "masterurl", "master_url",
        "originvideo", "origin_video",
        "originalvideo", "original_video",
        "originurl", "origin_url",
        "originalurl", "original_url",
        "sourceurl", "source_url",
    )
    return any(x in p for x in explicit)


def _original_video_score(path: tuple[str, ...], value: str) -> int:
    p = ".".join(path).lower()
    score = 0
    if "masterurl" in p or "master_url" in p:
        score += 2000
    if "h264" in p:
        score += 500
    if "h265" in p or "hevc" in p:
        score += 350
    if "origin" in p or "original" in p:
        score += 900
    if "source" in p:
        score += 700
    if "backup" in p:
        score -= 100
    if value.lower().startswith("https://"):
        score += 20
    return score


def _collect_original_video_urls(obj, out: list[tuple[int, int, str, str]], path: tuple[str, ...] = (), depth: int = 0) -> None:
    """只在目前 exact note 物件內收集『明確原始來源欄位』的影片 URL。"""
    if depth > 18 or len(out) >= 120:
        return

    if isinstance(obj, str):
        value = base.clean_url(obj)
        if base._allowed_scoped_video(value) and _is_explicit_original_video_path(path):
            path_text = ".".join(path)
            out.append((_original_video_score(path, value), len(out), value, path_text))
        return

    if isinstance(obj, dict):
        for key, value in obj.items():
            if isinstance(value, (dict, list, str)):
                _collect_original_video_urls(value, out, path + (str(key),), depth + 1)
        return

    if isinstance(obj, list):
        for index, value in enumerate(obj[:300]):
            if isinstance(value, (dict, list, str)):
                _collect_original_video_urls(value, out, path + (str(index),), depth + 1)


def _best_original_videos_from_target(target) -> tuple[list[str], list[str]]:
    ranked: list[tuple[int, int, str, str]] = []
    _collect_original_video_urls(target, ranked)
    if not ranked:
        return [], []

    best_by_url: dict[str, tuple[int, int, str, str]] = {}
    for row in ranked:
        old = best_by_url.get(row[2])
        if old is None or row[0] > old[0]:
            best_by_url[row[2]] = row

    ordered = sorted(best_by_url.values(), key=lambda x: (-x[0], x[1]))
    return [x[2] for x in ordered], [x[3] for x in ordered]


def _videos_from_exact_note_obj(obj, nid: str) -> tuple[list[str], list[str]]:
    """找到 exact noteId 後，只取該文章明確標示的原始影片來源。"""
    if not isinstance(obj, (dict, list)):
        return [], []

    seen = set()

    def walk(value, depth=0):
        if depth > 14:
            return [], []
        oid = id(value)
        if oid in seen:
            return [], []
        seen.add(oid)

        if isinstance(value, dict):
            if base._obj_note_id(value) == nid:
                return _best_original_videos_from_target(value)

            note = value.get("note")
            if isinstance(note, dict) and base._obj_note_id(note) == nid:
                return _best_original_videos_from_target(note)

            direct = value.get(nid)
            if isinstance(direct, dict):
                target = direct.get("note") if isinstance(direct.get("note"), dict) else direct
                if base._obj_note_id(target) in {"", nid}:
                    found, paths = _best_original_videos_from_target(target)
                    if found:
                        return found, paths

            for child in value.values():
                if isinstance(child, (dict, list)):
                    found, paths = walk(child, depth + 1)
                    if found:
                        return found, paths

        elif isinstance(value, list):
            for child in value[:500]:
                if isinstance(child, (dict, list)):
                    found, paths = walk(child, depth + 1)
                    if found:
                        return found, paths
        return [], []

    return walk(obj)


def _same_url_exact_note_original_videos(resolved: str) -> tuple[list[str], str]:
    """網址 -> exact noteId -> 明確原始影片欄位；找不到就不退回水印展示流。"""
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
                    videos, paths = _videos_from_exact_note_obj(obj, nid)
                    if videos:
                        # 只記錄欄位路徑，不把帶簽名的影片網址寫進日誌。
                        logger.info("XHS_ORIGINAL_VIDEO_SELECTED note=%s field=%s", nid, paths[0] if paths else "unknown")
                        return [videos[0]], f"same-url-exact-note-original-field-v{variant_index + 1}"

    logger.info("XHS_ORIGINAL_VIDEO_NOT_EXPOSED note=%s", nid)
    return [], "same_url_exact_note_original_video_not_exposed"


_original_inspect_one_url_only = base.inspect_one_url_only


def _inspect_one_url_only_video_first(input_url: str):
    resolved = base.resolve_url(input_url) or base.normalize_xhs_url(input_url)
    if resolved:
        videos, reason = _same_url_exact_note_original_videos(resolved)
        if videos:
            return resolved, [], videos, reason

    # 重要：不再用 yt-dlp / 一般 stream 當影片兜底。
    # 使用者要求「寧可沒有，也不要下載小紅書加工/帶浮水印的影片」。
    # 如果文章資料沒有明確暴露 original/masterUrl，就不把一般 stream 當原片。
    return _original_inspect_one_url_only(input_url)


base.inspect_one_url_only = _inspect_one_url_only_video_first
