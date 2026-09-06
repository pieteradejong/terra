#!/usr/bin/env python3
"""
Terra live-feed server — serves index.html and a merged /api/snapshot of
aircraft (OpenSky), vessels (AISStream) and earthquakes (USGS).

    pip install fastapi uvicorn httpx websockets
    export AISSTREAM_KEY=...            # free key from https://aisstream.io (optional; no key → no ships)
    export OPENSKY_CLIENT_ID=... OPENSKY_CLIENT_SECRET=...   # optional; anonymous works but only every 10 min
    python server.py                    # then open http://localhost:8000

Snapshot rows are compact arrays so the page can stream them into GPU buffers:
    flights: [lon, lat, alt_m, heading_deg, callsign]
    ships:   [lon, lat, cog_deg, name]
    quakes:  [lon, lat, magnitude, depth_km, place, time_ms]
"""
import asyncio, json, os, sys, time
from pathlib import Path

import httpx
from fastapi import FastAPI
from fastapi.responses import FileResponse, JSONResponse
import uvicorn

HERE = Path(__file__).parent
PAGE = HERE / "index.html"
AIS_KEY = os.environ.get("AISSTREAM_KEY")
OSK_ID, OSK_SECRET = os.environ.get("OPENSKY_CLIENT_ID"), os.environ.get("OPENSKY_CLIENT_SECRET")
OPENSKY_INTERVAL = 60 if OSK_ID else 600          # anonymous OpenSky: 400 credits/day, 4 per world query

state = {"flights": [], "ships": {}, "quakes": [], "updated": {}, "errors": {}}
app = FastAPI()


@app.get("/")
def index():
    return FileResponse(PAGE)


@app.get("/api/snapshot")
def snapshot():
    cutoff = time.time() - 1800
    ships = [v[:4] for v in state["ships"].values() if v[4] > cutoff]
    sources = [s for s, ok in (("opensky", bool(state["flights"])), ("aisstream", bool(ships)), ("usgs", bool(state["quakes"]))) if ok]
    return JSONResponse({"flights": state["flights"], "ships": ships, "quakes": state["quakes"], "updated": state["updated"], "sources": sources})


# ---------------- OpenSky ----------------
async def opensky_token(client):
    if not OSK_ID:
        return None
    r = await client.post("https://auth.opensky-network.org/auth/realms/opensky-network/protocol/openid-connect/token",
                          data={"grant_type": "client_credentials", "client_id": OSK_ID, "client_secret": OSK_SECRET})
    r.raise_for_status()
    return r.json()["access_token"]


async def poll_opensky():
    async with httpx.AsyncClient(timeout=30) as client:
        while True:
            try:
                headers = {}
                tok = await opensky_token(client)
                if tok:
                    headers["Authorization"] = f"Bearer {tok}"
                r = await client.get("https://opensky-network.org/api/states/all", headers=headers)
                r.raise_for_status()
                rows = []
                for s in r.json().get("states") or []:
                    # state vector: 0 icao24, 1 callsign, 5 lon, 6 lat, 7 baro_alt, 8 on_ground, 10 heading
                    if s[5] is None or s[6] is None or s[8]:
                        continue
                    rows.append([round(s[5], 3), round(s[6], 3), int(s[7] or 0), round(s[10] or 0), (s[1] or "").strip()])
                state["flights"] = rows
                state["updated"]["flights"] = time.time()
                state["errors"].pop("opensky", None)
            except Exception as e:  # rate limits are common when anonymous — keep going
                state["errors"]["opensky"] = str(e)[:80]
            await asyncio.sleep(OPENSKY_INTERVAL)


# ---------------- USGS earthquakes ----------------
async def poll_usgs():
    async with httpx.AsyncClient(timeout=30) as client:
        while True:
            try:
                r = await client.get("https://earthquake.usgs.gov/earthquakes/feed/v1.0/summary/2.5_day.geojson")
                r.raise_for_status()
                rows = []
                for f in r.json()["features"]:
                    lon, lat, depth = f["geometry"]["coordinates"]
                    p = f["properties"]
                    rows.append([round(lon, 3), round(lat, 3), p.get("mag") or 0, round(depth or 0), p.get("place") or "", p.get("time")])
                state["quakes"] = rows
                state["updated"]["quakes"] = time.time()
                state["errors"].pop("usgs", None)
            except Exception as e:
                state["errors"]["usgs"] = str(e)[:80]
            await asyncio.sleep(300)


# ---------------- AISStream (websocket) ----------------
async def stream_ais():
    if not AIS_KEY:
        state["errors"]["aisstream"] = "AISSTREAM_KEY not set"
        return
    import websockets
    sub = {"APIKey": AIS_KEY, "BoundingBoxes": [[[-90, -180], [90, 180]]], "FilterMessageTypes": ["PositionReport"]}
    while True:
        try:
            async with websockets.connect("wss://stream.aisstream.io/v0/stream", max_size=2**22) as ws:
                await ws.send(json.dumps(sub))
                state["errors"].pop("aisstream", None)
                async for raw in ws:
                    m = json.loads(raw)
                    meta = m.get("MetaData") or {}
                    pr = (m.get("Message") or {}).get("PositionReport") or {}
                    mmsi = meta.get("MMSI")
                    if mmsi is None:
                        continue
                    state["ships"][mmsi] = [round(meta.get("longitude", 0), 3), round(meta.get("latitude", 0), 3),
                                            round(pr.get("Cog") or 0), (meta.get("ShipName") or "").strip(), time.time()]
                    state["updated"]["ships"] = time.time()
        except Exception as e:
            state["errors"]["aisstream"] = str(e)[:80]
            await asyncio.sleep(10)


# ---------------- terminal status line ----------------
async def status_line():
    spin = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"
    i = 0
    while True:
        i += 1
        now = time.time()
        age = lambda k: f"{int(now - state['updated'][k])}s" if k in state["updated"] else "—"
        ships = sum(1 for v in state["ships"].values() if v[4] > now - 1800)
        errs = "  ".join(f"⚠ {k}: {v}" for k, v in state["errors"].items())
        line = (f"\r{spin[i % len(spin)]} ✈ {len(state['flights']):>6,} aircraft ({age('flights')})   "
                f"🚢 {ships:>6,} vessels ({age('ships')})   🌍 {len(state['quakes']):>3} quakes ({age('quakes')})   {errs}")
        sys.stdout.write(line[:200].ljust(200))
        sys.stdout.flush()
        await asyncio.sleep(0.5)


@app.on_event("startup")
async def startup():
    for coro in (poll_opensky(), poll_usgs(), stream_ais(), status_line()):
        asyncio.create_task(coro)
    print(f"Terra → http://localhost:8000   (OpenSky every {OPENSKY_INTERVAL}s, AIS {'on' if AIS_KEY else 'off — set AISSTREAM_KEY'})")


if __name__ == "__main__":
    if not PAGE.exists():
        sys.exit("index.html not found next to server.py")
    uvicorn.run(app, host="127.0.0.1", port=8000, log_level="warning")
