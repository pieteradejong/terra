# Terra

A real-time, physically-shaded model of Earth in Three.js with infrastructure layers on top:
submarine cables, sea lanes, chokepoints, ports — and, when run locally, live aircraft, vessels and earthquakes.

`index.html` is self-contained (textures and data embedded, ~4.4 MB). Open it directly for the static layers,
or run the feed server for live mode.

## Live mode

```
pip install fastapi uvicorn httpx websockets
export AISSTREAM_KEY=...                                   # free key: https://aisstream.io  (vessels)
export OPENSKY_CLIENT_ID=... OPENSKY_CLIENT_SECRET=...     # optional: https://opensky-network.org (aircraft every 60 s instead of 10 min)
python server.py                                           # → http://localhost:8000
```

## What's rendered

- Day/night terminator from the real UTC clock (solar declination + equation of time); Moon from a low-precision lunar ephemeris, lit by the Sun so its phase is right
- Single-scattering Rayleigh + Mie atmosphere, ray-marched; aerial perspective on the surface; sunlight reddened through the air it crossed
- HDR pipeline: half-float MSAA target → soft-threshold bloom → ACES tonemap
- 4K normal map derived from an elevation model, 4K clouds with shadows, city lights, ocean glint

## Data

- Submarine cables & landing stations: TeleGeography Submarine Cable Map (2021 snapshot via github.com/hadistlm/submarine-cable-map)
- Chokepoint oil/LNG flows: US EIA, World Oil Transit Chokepoints, 1H 2025
- Sea lanes and ports: hand-curated waypoints — illustrative, not a dataset
- Live: OpenSky Network (aircraft), AISStream (vessels), USGS (M2.5+ earthquakes, 24 h)

Textures: NASA Blue Marble / three.js planet set; elevation, water mask and clouds from github.com/turban/webgl-earth.
