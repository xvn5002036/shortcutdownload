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


def _video_url_score(path: tuple[str, ...], value: str) -> int:
    """同一 exact note 裡，優先選 masterUrl / h264 原始影片流，避開水印/分享播放欄位。"""
    p = ".".join(path).lower()
    v = value.lower()
    score = 0

    # 小紅書目前無水印原流的首選欄位。
    if "masterurl" in p or "master_url" in p:
        score += 1000
    if ".h264" in p or "h264" in p:
        score += 350
    if ".h265" in p or "h265" in p or "hevc" in p:
        score += 250
    if any(x in p for x in ("original", "origin", "source")):
        score += 500
    if "backupurls" in p or "backup_urls" in p:
        score += 150

    # 一般可播放流次之。
    if "/stream/" in v:
        score += 80
    if v.endswith(".mp4") or ".mp4?" in v:
        score += 60

    # 明確可能是展示/水印/分享兜底的欄位降權。
    if any(x in p for x in ("watermark", "water_mark", "wmurl", "wm_url", "marked", "share", "ogvideo", "og:video")):
        score -= 1200
    if any(x in v for x in ("watermark", "water_mark", "/wm/", "-wm-", "_wm.")):
        score -= 1200

    return score


def _collect_ranked_video_urls(obj, out: list[tuple[int, int, str]], path: tuple[str, ...] = (), depth: int = 0) -> None:
    """只在目前 exact note 物件內找影片 URL，並保留欄位路徑做無水印優先排序。"""
    if depth > 18 or len(out) >= 120:
        return

    if isinstance(obj, str):
        value = base.clean_url(obj)
        if base._allowed_scoped_video(value):
            out.append((_video_url_score(path, value), len(out), value))
        return

    if isinstance(obj, dict):
        for key, value in obj.items():
            if isinstance(value, (dict, list, str)):
                _collect_ranked_video_urls(value, out, path + (str(key),), depth + 1)
        return

    if isinstance(obj, list):
        for index, value in enumerate(obj[:300]):
            if isinstance(value, (dict, list, str)):
                _collect_ranked_video_urls(value, out, path + (str(index),), depth + 1)


def _best_videos_from_target(target) -> list[str]:
    ranked: list[tuple[int, int, str]] = []
    _collect_ranked_video_urls(target, ranked)
    if not ranked:
        return []

    # 同 URL 只保留最高分；masterUrl/h264 會排在展示/分享 URL 前面。
    best_by_url: dict[str, tuple[int, int, str]] = {}
    for row in ranked:
        old = best_by_url.get(row[2])
        if old is None or row[0] > old[0]:
            best_by_url[row[2]] = row
    ordered = sorted(best_by_url.values(), key=lambda x: (-x[0], x[1]))
    return [x[2] for x in ordered]


def _videos_from_exact_note_obj(obj, nid: str) -> list[str]:
    """找到 exact noteId 後，只收集該文章物件自己的影片，masterUrl/h264 優先。"""
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
                return _best_videos_from_target(value)

            note = value.get("note")
            if isinstance(note, dict) and base._obj_note_id(note) == nid:
                return _best_videos_from_target(note)

            direct = value.get(nid)
            if isinstance(direct, dict):
                target = direct.get("note") if isinstance(direct.get("note"), dict) else direct
                if base._obj_note_id(target) in {"", nid}:
                    found = _best_videos_from_target(target)
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
        return []

    return walk(obj)


def _same_url_exact_note_videos(resolved: str) -> tuple[list[str], str]:
    """以網址中的 noteId 鎖定同一文章，再從該物件讀原始 video stream。"""
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
                        return videos, f"same-url-exact-note-masterurl-first-v{variant_index + 1}"

    return [], "same_url_exact_note_video_not_found"


_original_inspect_one_url_only = base.inspect_one_url_only


def _inspect_one_url_only_video_first(input_url: str):
    resolved = base.resolve_url(input_url) or base.normalize_xhs_url(input_url)
    if resolved:
        # 第一順位：網址 → exact noteId → 該文章自己的 masterUrl/h264 原始流。
        videos, reason = _same_url_exact_note_videos(resolved)
        if videos:
            # 只給捷徑第一順位（最佳）影片，避免它抓到後面的展示/水印備援流。
            return resolved, [], [videos[0]], reason

    # 第二順位：既有 yt-dlp 僅作兜底。
    try:
        note = base.inspect_note(input_url)
        if note.get("nt") == "video" and note.get("video"):
            resolved2 = note.get("resolved_url") or resolved or input_url
            return resolved2, [], [str(note["video"])], "yt-dlp-same-url-video-fallback"
    except Exception:
        pass

    # 不是影片時，完整回到已經成功的圖片原圖流程。
    return _original_inspect_one_url_only(input_url)


base.inspect_one_url_only = _inspect_one_url_only_video_first
