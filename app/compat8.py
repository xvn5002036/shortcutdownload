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

        # 找 imageList URL 中真正的資源路徑：
        # /.../notes_pre_post/<id>!h5_1080jpg
        # /.../note_pre_post_uhdr/<id>!h5_1080jpg
        m = re.search(r"/(notes_pre_post|note_pre_post_uhdr|spectrum)/([^/?#!]+)", path, flags=re.I)
        if m:
            prefix = m.group(1)
            media_id = m.group(2).split("!", 1)[0]
            if media_id:
                return f"https://ci.xiaohongshu.com/{prefix}/{media_id}?imageView2/format/jpeg"

        # 舊式 URL 有時本身就是 ci.xiaohongshu.com/<traceId>；
        # 這種保留 ID 並只要求 jpeg，不自行改 ID。
        last = (path.rsplit("/", 1)[-1] or "").split("!", 1)[0]
        if last and len(last) >= 16 and all(ch.isalnum() or ch in "_-" for ch in last):
            return f"https://ci.xiaohongshu.com/{last}?imageView2/format/jpeg"
    except Exception:
        pass

    return value


# 保留 compat7 的「單一網址 + exact noteId」邊界，只替換實際圖片來源。
base._clean_image = _raw_same_note_image
