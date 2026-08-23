from __future__ import annotations

from fastapi import Query
from fastapi.responses import JSONResponse, PlainTextResponse

from .main import app
from .db import log_request, verify_and_bind

# Remove the phase-1 plain-text compatibility route and replace it with a
# dictionary response. The Shortcut has been observed reading GATE.notetype,
# so a successful response must be a dictionary/JSON object rather than "1".
app.router.routes[:] = [
    route for route in app.router.routes
    if getattr(route, "path", None) != "/xhszshq"
]


@app.get("/xhszshq")
def xhszshq_gate(
    a: str = Query(default=""),
    b: str = Query(default="ios"),
    c: str = Query(default=""),
    device_id: str = Query(default=""),
):
    ok, reason = verify_and_bind(a, device_id, b)
    log_request(a, device_id, b, reason, c)

    # Preserve the verified failure behavior used by the original Shortcut.
    if not ok:
        return PlainTextResponse("0")

    # Diagnostic compatibility payload. Confirmed keys so far: notetype, nt.
    # Extra empty media fields keep subsequent dictionary lookups type-safe
    # while we map the remaining original v4.7 keys action-by-action.
    payload = {
        "status": 1,
        "gate": 1,
        "notetype": "",
        "nt": "",
        "url": c,
        "title": "",
        "author": "",
        "images": [],
        "image": [],
        "video": "",
        "videos": [],
        "live": [],
        "livephoto": [],
        "message": "gate-json-compat",
    }
    return JSONResponse(payload)
