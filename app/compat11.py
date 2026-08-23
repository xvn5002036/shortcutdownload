from __future__ import annotations

import html
import json
import re
from urllib.request import Request as URLRequest, urlopen

import chompjs
from fastapi import Query
from fastapi.responses import JSONResponse

from . import compat10 as locked
from . import compat7 as gate

app = locked.app

# 鎖定並沿用目前已驗證成功的四條媒體流程：
# 1) 普通照片原圖
# 2) 普通影片無浮水印 + MP4 remux
# 3) Live Photo
# 4) 高清保存 eigl
# compat11 只補「筆記保存」需要的 title / desc / author，不改任何媒體 URL、分類或下載流程。
_locked_gate = gate.xhszshq_gate

# 移除原 /xhszshq，改由 wrapper 先取得完全相同的既有 payload，再只補 metadata。
app.router.routes[:] = [r for r in app.router.routes if getattr(r, "path", None) != "/xhszshq"]


def _text(value) -> str:
    if value is None:
        return ""
    if isinstance(value, (str, int, float)):
        return html.unescape(str(value)).strip()
    return ""


def _author_from_note(note: dict) -> str:
    if not isinstance(note, dict):
        return ""
    for key in ("author", "user", "userInfo", "user_info", "owner"):
        obj = note.get(key)
        if isinstance(obj, str) and obj.strip():
            return obj.strip()
        if isinstance(obj, dict):
            for name_key in ("nickname", "nickName", "nick_name", "name", "userName", "user_name"):
                value = _text(obj.get(name_key))
                if value:
                    return value
    for key in ("nickname", "nickName", "nick_name", "userName", "user_name"):
        value = _text(note.get(key))
        if value:
            return value
    return ""


def _metadata_from_note(note: dict) -> dict[str, str]:
    if not isinstance(note, dict):
        return {"title": "", "desc": "", "author": ""}

    title = ""
    for key in ("title", "noteTitle", "note_title", "displayTitle", "display_title"):
        title = _text(note.get(key))
        if title:
            break

    desc = ""
    for key in ("desc", "description", "content", "noteDesc", "note_desc", "text"):
        value = note.get(key)
        if isinstance(value, str):
            desc = html.unescape(value).strip()
            if desc:
                break

    return {"title": title, "desc": desc, "author": _author_from_note(note)}


def _find_exact_note_metadata(obj, nid: str, depth: int = 0, seen=None) -> dict[str, str]:
    if depth > 14:
        return {"title": "", "desc": "", "author": ""}
    if seen is None:
        seen = set()
    oid = id(obj)
    if oid in seen:
        return {"title": "", "desc": "", "author": ""}
    seen.add(oid)

    if isinstance(obj, dict):
        if gate._obj_note_id(obj) == nid:
            return _metadata_from_note(obj)

        note = obj.get("note")
        if isinstance(note, dict) and gate._obj_note_id(note) == nid:
            return _metadata_from_note(note)

        direct = obj.get(nid)
        if isinstance(direct, dict):
            target = direct.get("note") if isinstance(direct.get("note"), dict) else direct
            if gate._obj_note_id(target) in {"", nid}:
                data = _metadata_from_note(target)
                if any(data.values()):
                    return data

        for child in obj.values():
            if isinstance(child, (dict, list)):
                data = _find_exact_note_metadata(child, nid, depth + 1, seen)
                if any(data.values()):
                    return data

    elif isinstance(obj, list):
        for child in obj[:500]:
            if isinstance(child, (dict, list)):
                data = _find_exact_note_metadata(child, nid, depth + 1, seen)
                if any(data.values()):
                    return data

    return {"title": "", "desc": "", "author": ""}


def _same_url_metadata(input_url: str) -> dict[str, str]:
    resolved = gate.resolve_url(input_url) or gate.normalize_xhs_url(input_url)
    nid = gate._note_id_from_url(resolved)
    if not resolved or not nid:
        return {"title": "", "desc": "", "author": ""}

    try:
        req = URLRequest(resolved, headers={
            "User-Agent": gate.UA,
            "Accept": "text/html,application/xhtml+xml,application/json;q=0.9,*/*;q=0.8",
            "Accept-Language": "zh-TW,zh;q=0.9,en;q=0.8",
            "Referer": "https://www.xiaohongshu.com/",
        })
        with urlopen(req, timeout=25) as resp:
            raw = resp.read(12 * 1024 * 1024).decode("utf-8", errors="ignore")
    except Exception:
        return {"title": "", "desc": "", "author": ""}

    variants = [
        raw,
        html.unescape(raw).replace("\\u002F", "/").replace("\\/", "/").replace("\\u0026", "&"),
    ]

    for text in variants:
        positions = [m.start() for m in re.finditer(re.escape(nid), text, flags=re.I)]
        for pos in positions[:30]:
            left = max(0, pos - 180_000)
            starts = [m.start() for m in re.finditer(r"\{", text[left:pos])]
            for rel_start in reversed(starts[-500:]):
                start = left + rel_start
                chunk = gate._balanced_object(text, start)
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
                    data = _find_exact_note_metadata(obj, nid)
                    if any(data.values()):
                        return data

    return {"title": "", "desc": "", "author": ""}


@app.get("/xhszshq")
def xhszshq_gate_note_save(
    a: str = Query(default=""),
    b: str = Query(default="ios"),
    c: str = Query(default=""),
    device_id: str = Query(default=""),
):
    # 先完整執行鎖定版 gate，媒體結果完全不變。
    response = _locked_gate(a=a, b=b, c=c, device_id=device_id)

    # 驗證失敗等非 JSON 回應直接原樣返回。
    body = getattr(response, "body", b"")
    try:
        payload = json.loads(body.decode("utf-8")) if body else None
    except Exception:
        return response
    if not isinstance(payload, dict) or payload.get("error"):
        return response

    # 只補筆記保存使用的三個欄位。
    metadata = _same_url_metadata(c)
    if metadata.get("title"):
        payload["title"] = metadata["title"]
    payload["desc"] = metadata.get("desc") or payload.get("desc") or ""
    payload["description"] = payload["desc"]
    if metadata.get("author"):
        payload["author"] = metadata["author"]

    # 兼容捷徑可能使用的別名；不影響原本媒體欄位。
    payload["note_title"] = payload.get("title") or ""
    payload["note_desc"] = payload.get("desc") or ""
    payload["nickname"] = payload.get("author") or ""
    payload["message_note_save"] = "ok-note-save-metadata-v1"
    return JSONResponse(payload)
