from __future__ import annotations

from urllib.parse import urlsplit, urlunsplit

from . import compat7 as base

app = base.app


def _raw_same_note_image(value) -> str:
    """把目前文章 imageList 的 CDN 轉換參數移除，保留同一張媒體的原始資源路徑。

    例如：
    .../notes_pre_post/<media-id>!h5_1080jpg
    會改成：
    .../notes_pre_post/<media-id>

    這不是跨文章猜 URL；host/path/media-id 都來自目前網址 exact noteId 的 imageList，
    只移除 CDN 顯示轉換尾碼（h5_1080jpg 等），以取得未套用顯示處理的原始檔。
    """
    if not isinstance(value, str):
        return ""
    value = base.clean_url(value)
    if not base._allowed_scoped_image(value):
        return ""
    try:
        p = urlsplit(value)
        path = p.path
        if "!" in path:
            path = path.split("!", 1)[0]
        # 圖片的 query 若只是顯示轉換也不要帶回去；原始媒體識別仍在 path。
        return urlunsplit((p.scheme, p.netloc, path, "", ""))
    except Exception:
        return value.split("!", 1)[0]


# compat7 的 _item_image_url 會在執行時查詢模組全域 _clean_image，
# 因此在這裡替換即可保留所有「單一網址 + exact noteId」邊界判斷，
# 只把實際下載來源改成同一張圖片的 raw CDN path。
base._clean_image = _raw_same_note_image
