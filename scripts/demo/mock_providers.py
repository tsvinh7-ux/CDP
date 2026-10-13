"""MOCK Facebook Graph + Zalo APIs for the demo (the real APIs are not reachable from the demo box).

Records every call so the demo can show exactly what CDP would send:
    python mock_providers.py   → http://localhost:9000, calls at GET /log
"""
import json
import time
from urllib.parse import parse_qs

import uvicorn
from fastapi import FastAPI, Request

app = FastAPI(title="MOCK Facebook/Zalo")
LOG: list[dict] = []
AUDIENCES = {"n": 0}


async def record(request: Request, provider: str) -> dict:
    raw = (await request.body()).decode()
    try:
        body = json.loads(raw)
    except ValueError:  # form-encoded (Graph API style)
        form = {k: v[0] for k, v in parse_qs(raw).items()}
        body = {k: (json.loads(v) if k in ("data", "payload") else ("***" if k in ("access_token", "refresh_token") else v))
                for k, v in form.items()}
    entry = {"at": time.strftime("%H:%M:%S"), "provider": provider, "method": request.method,
             "path": request.url.path, "body": body}
    LOG.append(entry)
    return entry


@app.post("/graph/{version}/{pixel}/events")
async def capi(request: Request, version: str, pixel: str):
    entry = await record(request, "facebook_capi")
    return {"events_received": len(entry["body"].get("data") or []), "fbtrace_id": f"mock-{len(LOG)}"}


@app.post("/graph/{version}/{account}/customaudiences")
async def create_audience(request: Request, version: str, account: str):
    await record(request, "facebook_audience")
    AUDIENCES["n"] += 1
    return {"id": f"mock_audience_{AUDIENCES['n']}"}


@app.api_route("/graph/{version}/{audience}/users", methods=["POST", "DELETE"])
async def audience_users(request: Request, version: str, audience: str):
    entry = await record(request, "facebook_audience")
    return {"audience_id": audience, "num_received": len((entry["body"].get("payload") or {}).get("data") or [])}


@app.post("/zalo/oauth")
async def zalo_oauth(request: Request):
    await record(request, "zalo_oauth")
    return {"access_token": "mock-access", "refresh_token": f"mock-refresh-{len(LOG)}", "expires_in": "90000"}


@app.post("/zalo/zns")
async def zalo_zns(request: Request):
    await record(request, "zalo_zns")
    return {"error": 0, "message": "Success", "data": {"msg_id": f"mock-msg-{len(LOG)}"}}


@app.get("/log")
def log():
    return LOG


if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=9000, log_level="warning")
