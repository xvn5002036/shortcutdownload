from __future__ import annotations

import re
from urllib.parse import urlsplit

from . import compat7 as base

app = base.app


def _raw_same_note_image(value) -> str:
    """把目前文章 exact imageList 的展示 URL 轉成同一張圖片的無水印原圖 URL。

    重要：不能只拿最後的 media-id 去組 `ci.xiaohongshu.com/<id>`，
    這類新式 1040... 圖片實際還需要 `notes_pre_post/` 或
    `note_pre_post_uhdr/` 路徑前綴；少了前綴會 502。

    輸入仍然只能來自「目前網址 + exact noteId」自己的 imageList，
    所以不會跨文章。這裡只移除 sns-webpic 的日期/簽名層與
    `!h5_1080jpg` 之類展示轉換，保留原始媒體路徑。
    """
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


# 圖片部分保持原樣，只替換 compat7 的圖片清理函式。
base._clean_image = _raw_same_note_image


# 影片修正：compat7 原本會先看到影片封面 imageList，因而把影片筆記判成 pic。
# 這裡只在「同一個輸入網址」能被既有 yt-dlp/inspect_note 明確判定為 video 時，
# 優先回傳影片；不是 video 時完全回到 compat7 原本的圖片流程，因此不改動
# 已經驗證成功的無水印原圖處理。
_original_inspect_one_url_only = base.inspect_one_url_only


def _inspect_one_url_only_video_first(input_url: str):
    try:
        note = base.inspect_note(input_url)
        if note.get("nt") == "video" and note.get("video"):
            resolved = note.get("resolved_url") or base.resolve_url(input_url) or base.normalize_xhs_url(input_url)
            return resolved, [], [str(note["video"])], "yt-dlp-same-url-video-first"
    except Exception:
        pass

    return _original_inspect_one_url_only(input_url)


base.inspect_one_url_only = _inspect_one_url_only_video_first
