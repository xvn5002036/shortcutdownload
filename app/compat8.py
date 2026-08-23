from __future__ import annotations

from urllib.parse import urlsplit

from . import compat7 as base

app = base.app


def _raw_same_note_image(value) -> str:
    """把目前文章 exact imageList URL 轉成同一張媒體的原始資源 URL。

    sns-webpic 的 `.../<media-id>!h5_1080jpg` 是展示/CDN 轉換版本。
    直接刪掉 `!h5_1080jpg` 在 sns-webpic 會得到 404/502，因此不能這樣做。

    這裡只取「目前文章 imageList 本身提供的 media-id」，轉成
    `https://ci.xiaohongshu.com/<media-id>` 原始資源。media-id 不是猜的，
    仍然來自同一網址、同一 exact noteId 的 imageList，不跨文章。
    """
    if not isinstance(value, str):
        return ""
    value = base.clean_url(value)
    if not base._allowed_scoped_image(value):
        return ""
    try:
        p = urlsplit(value)
        last = (p.path.rsplit("/", 1)[-1] or "").split("!", 1)[0]
        # 小紅書圖片媒體 ID 通常是 1040... / 1o0... 這類非空資源 ID。
        if last and len(last) >= 16 and all(ch.isalnum() or ch in "_-" for ch in last):
            return f"https://ci.xiaohongshu.com/{last}"
    except Exception:
        pass
    return value


# 保留 compat7 的「單一網址 + exact noteId」邊界，只替換實際圖片來源。
base._clean_image = _raw_same_note_image
