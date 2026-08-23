from __future__ import annotations

import html
import re
from urllib.request import Request as URLRequest, urlopen

import chompjs

from . import compat9 as locked
from . import compat7 as base

app = locked.app

# 已驗證成功的三條流程全部保留：
# 1) 普通照片原圖
# 2) originVideoKey 無浮水印普通影片 + MP4 remux
# 3) Live Photo 配對
# compat10 只在上述流程「完全沒有取得任何媒體」時，補一條新版高清影片解析。
_locked_inspect = base.inspect_one_url_only


def _clean_hd_video_url(value) -> str:
    if not isinstance(value, str):
        return ""
    value = base.clean_url(value).strip()
    if not value.startswith(("http://", "https://")):
        return ""
    low = value.lower()
    # 明確拒絕分享/浮水印/OG 類 URL；只接受 exact note 的 h264 stream 欄位。
    if any(token in low for token in ("watermark", "water_mark", "wm=", "logo", "share_video", "ogvideo")):
        return ""
    return value if base._allowed_scoped_video(value) else ""


def _hd_streams_from_note_target(target) -> list[str]:
    """只讀 exact note 內新版 video_info_v2/video.media.stream.h264。

    不掃整頁、不用 og:video、不用 gallery-dl/yt-dlp 的展示下載地址。
    依解析度與 bitrate 取最高畫質 H.264，交給現有 /media/video 無轉碼 remux。
    """
    if not isinstance(target, dict):
        return []

    rows: list[tuple[int, int, str]] = []
    seen = set()

    def add_h264_list(arr):
        if not isinstance(arr, list):
            return
        for index, row in enumerate(arr[:50]):
            if not isinstance(row, dict):
                continue
            urls = []
            for key in ("masterUrl", "master_url"):
                u = _clean_hd_video_url(row.get(key))
                if u:
                    urls.append(u)
            for key in ("backupUrls", "backup_urls"):
                val = row.get(key)
                if isinstance(val, list):
                    for x in val[:8]:
                        u = _clean_hd_video_url(x)
                        if u:
                            urls.append(u)
            if not urls:
                continue
            width = row.get("width") or 0
            height = row.get("height") or 0
            bitrate = row.get("videoBitrate") or row.get("video_bitrate") or row.get("bitrate") or 0
            try:
                pixels = int(width) * int(height)
            except Exception:
                pixels = 0
            try:
                br = int(float(bitrate))
            except Exception:
                br = 0
            # masterUrl 排在同品質 backupUrls 前。
            for rank, u in enumerate(urls):
                rows.append((pixels * 10_000_000 + br - rank, index, u))

    def inspect_video_container(obj):
        if not isinstance(obj, dict):
            return
        media = obj.get("media")
        if isinstance(media, dict):
            stream = media.get("stream")
            if isinstance(stream, dict):
                add_h264_list(stream.get("h264"))
                add_h264_list(stream.get("H264"))
        stream = obj.get("stream")
        if isinstance(stream, dict):
            add_h264_list(stream.get("h264"))
            add_h264_list(stream.get("H264"))

    # 只在目前 note object 內找明確的 video 結構。
    for key in ("videoInfoV2", "video_info_v2", "video"):
        inspect_video_container(target.get(key))

    # 某些版本 note.video.media.stream 在更深一層，但仍限定在 exact note object。
    def walk(obj, depth=0):
        if depth > 8:
            return
        oid = id(obj)
        if oid in seen:
            return
        seen.add(oid)
        if isinstance(obj, dict):
            for key, value in obj.items():
                low = str(key).replace("-", "_").lower()
                if low in {"video_info_v2", "videoinfov2", "video"} and isinstance(value, dict):
                    inspect_video_container(value)
                elif isinstance(value, (dict, list)):
                    walk(value, depth + 1)
        elif isinstance(obj, list):
            for value in obj[:100]:
                if isinstance(value, (dict, list)):
                    walk(value, depth + 1)

    walk(target)

    if not rows:
        return []
    rows.sort(key=lambda x: (-x[0], x[1]))
    out = []
    used = set()
    for _, _, u in rows:
        if u not in used:
            used.add(u)
            out.append(u)
    return out


def _find_exact_note_hd_video(obj, nid: str, depth: int = 0, seen=None) -> list[str]:
    if depth > 14:
        return []
    if seen is None:
        seen = set()
    oid = id(obj)
    if oid in seen:
        return []
    seen.add(oid)

    if isinstance(obj, dict):
        if base._obj_note_id(obj) == nid:
            found = _hd_streams_from_note_target(obj)
            if found:
                return found

        note = obj.get("note")
        if isinstance(note, dict) and base._obj_note_id(note) == nid:
            found = _hd_streams_from_note_target(note)
            if found:
                return found

        direct = obj.get(nid)
        if isinstance(direct, dict):
            target = direct.get("note") if isinstance(direct.get("note"), dict) else direct
            if base._obj_note_id(target) in {"", nid}:
                found = _hd_streams_from_note_target(target)
                if found:
                    return found

        for child in obj.values():
            if isinstance(child, (dict, list)):
                found = _find_exact_note_hd_video(child, nid, depth + 1, seen)
                if found:
                    return found

    elif isinstance(obj, list):
        for child in obj[:500]:
            if isinstance(child, (dict, list)):
                found = _find_exact_note_hd_video(child, nid, depth + 1, seen)
                if found:
                    return found
    return []


def _same_url_exact_note_hd_video(input_url: str):
    resolved = base.resolve_url(input_url) or base.normalize_xhs_url(input_url)
    if not resolved:
        return "", [], "hd_url_missing"
    nid = base._note_id_from_url(resolved)
    if not nid:
        return resolved, [], "hd_note_id_missing"

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
        return resolved, [], f"hd_fetch_{type(exc).__name__}"

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
                    videos = _find_exact_note_hd_video(obj, nid)
                    if videos:
                        return resolved, [videos[0]], f"same-url-exact-note-h264-hd-v{variant_index + 1}"

    return resolved, [], "same_url_exact_note_h264_hd_not_found"


def _inspect_with_locked_then_hd(input_url: str):
    # 成功的照片/影片/實況完全不改：只要舊流程有任何媒體，立即原樣返回。
    resolved, images, videos, reason = _locked_inspect(input_url)
    if images or videos:
        return resolved, images, videos, reason

    # 舊流程完全抓不到時，才使用新版 exact-note H.264 高清來源。
    hd_resolved, hd_videos, hd_reason = _same_url_exact_note_hd_video(input_url)
    if hd_videos:
        return hd_resolved, [], hd_videos, hd_reason
    return resolved or hd_resolved, [], [], f"{reason}+{hd_reason}"


base.inspect_one_url_only = _inspect_with_locked_then_hd
