"""Central aviation observation ingestion API.

The browser consumes this service instead of polling providers when configured.
Every observation records provider and reception time. Provider values are not
enriched with guessed registrations, types, routes, or flight plans.
"""
from __future__ import annotations

import asyncio
import logging
import math
import os
import time
from contextlib import asynccontextmanager, suppress
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import httpx
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, ConfigDict
from sqlalchemy import DateTime, Float, Integer, JSON, String, create_engine, select
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker
from airops.normalization import normalize_airplanes, normalize_opensky

env_file = Path(__file__).resolve().parents[1] / ".env"
if env_file.exists():
    for line in env_file.read_text().splitlines():
        key, sep, value = line.partition("=")
        if sep and key.strip() and not key.lstrip().startswith("#"):
            os.environ.setdefault(key.strip(), value.strip().strip("\"'"))
logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))
log = logging.getLogger("airops")
DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./airops.db")
engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False} if DATABASE_URL.startswith("sqlite") else {}, pool_pre_ping=True)
Session = sessionmaker(engine, expire_on_commit=False)
POLL_SECONDS = max(5, int(os.getenv("POLL_SECONDS", "10")))
PROVIDER = os.getenv("AVIATION_PROVIDER", "airplanes_live").lower()
OPEN_SKY_CLIENT_ID = os.getenv("OPENSKY_CLIENT_ID", "")
OPEN_SKY_CLIENT_SECRET = os.getenv("OPENSKY_CLIENT_SECRET", "")
OPENSKY_WRITTEN_LICENSE = os.getenv("OPENSKY_WRITTEN_LICENSE", "false").lower() == "true"
_opensky_token: str | None = None
_opensky_token_expires = 0.0


class Base(DeclarativeBase):
    pass


class Observation(Base):
    __tablename__ = "aircraft_observations"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    icao: Mapped[str] = mapped_column(String(16), index=True)
    callsign: Mapped[str | None] = mapped_column(String(32), nullable=True)
    registration: Mapped[str | None] = mapped_column(String(32), nullable=True)
    aircraft_type: Mapped[str | None] = mapped_column(String(32), nullable=True)
    operator: Mapped[str | None] = mapped_column(String(128), nullable=True)
    latitude: Mapped[float] = mapped_column(Float)
    longitude: Mapped[float] = mapped_column(Float)
    altitude_ft: Mapped[int | None] = mapped_column(Integer, nullable=True)
    ground_speed_kt: Mapped[int | None] = mapped_column(Integer, nullable=True)
    track_deg: Mapped[int | None] = mapped_column(Integer, nullable=True)
    vertical_rate_fpm: Mapped[int | None] = mapped_column(Integer, nullable=True)
    squawk: Mapped[str | None] = mapped_column(String(8), nullable=True)
    provider: Mapped[str] = mapped_column(String(64))
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    freshness_seconds: Mapped[int] = mapped_column(Integer, default=0)
    available_fields: Mapped[list[str]] = mapped_column(JSON)
    missing_fields: Mapped[list[str]] = mapped_column(JSON)
    evidence_type: Mapped[str] = mapped_column(String(32), default="provider-reported")


Base.metadata.create_all(engine)


class Aircraft(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    icao: str
    callsign: str | None = None
    registration: str | None = None
    aircraft_type: str | None = None
    operator: str | None = None
    latitude: float
    longitude: float
    altitude_ft: int | None = None
    ground_speed_kt: int | None = None
    track_deg: int | None = None
    vertical_rate_fpm: int | None = None
    squawk: str | None = None
    provider: str
    observed_at: datetime
    received_at: datetime
    freshness_seconds: int
    available_fields: list[str]
    missing_fields: list[str]
    evidence_type: str


class Hub:
    def __init__(self) -> None:
        self.clients: set[WebSocket] = set()
        self.state: dict[str, Any] = {"provider": PROVIDER, "status": "starting", "last_success": None, "last_error": None, "count": 0}
        self.lock = asyncio.Lock()

    async def broadcast(self, payload: dict[str, Any]) -> None:
        stale = []
        for client in tuple(self.clients):
            try:
                await client.send_json(payload)
            except Exception:
                stale.append(client)
        for client in stale:
            self.clients.discard(client)


hub = Hub()


async def collect() -> None:
    """One broad-area poll; production deployments should partition by configured regions."""
    now = datetime.now(timezone.utc)
    async with httpx.AsyncClient(timeout=12) as client:
        if PROVIDER == "opensky":
            if not OPENSKY_WRITTEN_LICENSE:
                raise RuntimeError("OpenSky REST ingestion requires a written license; see OpenSky terms")
            if not OPEN_SKY_CLIENT_ID or not OPEN_SKY_CLIENT_SECRET:
                raise RuntimeError("OpenSky client credentials are required")
            token = await opensky_access_token(client)
            lat = float(os.getenv("OBSERVE_LAT", "18.52"))
            lon = float(os.getenv("OBSERVE_LON", "73.86"))
            radius_deg = min(250, max(1, int(os.getenv("OBSERVE_RADIUS_NM", "100")))) / 60
            lon_delta = radius_deg / max(0.01, abs(math.cos(math.radians(lat))))
            response = await client.get("https://opensky-network.org/api/states/all", params={
                "lamin": max(-90, lat - radius_deg), "lamax": min(90, lat + radius_deg),
                "lomin": max(-180, lon - lon_delta), "lomax": min(180, lon + lon_delta)},
                headers={"Authorization": f"Bearer {token}"})
            response.raise_for_status()
            rows = [normalize_opensky(s) for s in response.json().get("states", [])]
        else:
            # Airplanes.Live point endpoint has bounded radius; default to Pune/100 km.
            lat, lon, radius = float(os.getenv("OBSERVE_LAT", "18.52")), float(os.getenv("OBSERVE_LON", "73.86")), min(250, max(1, int(os.getenv("OBSERVE_RADIUS_NM", "100"))))
            response = await client.get(f"https://api.airplanes.live/v2/point/{lat}/{lon}/{radius}")
            response.raise_for_status()
            rows = [normalize_airplanes(a) for a in response.json().get("ac", [])]
    valid = [r for r in rows if r and r["icao"]]
    # Deduplicate provider responses by ICAO within each poll, keeping the final record.
    by_icao = {r["icao"]: r for r in valid}
    received = datetime.now(timezone.utc)
    objects = []
    for r in by_icao.values():
        seen_seconds = r.pop("_seen_seconds", None)
        observed_epoch = r.pop("_observed_epoch", None)
        try:
            observed = datetime.fromtimestamp(float(observed_epoch), timezone.utc) if observed_epoch else now - timedelta(seconds=max(0, float(seen_seconds))) if seen_seconds is not None else now
        except (ValueError, TypeError, OverflowError, OSError):
            observed = now
        known = {k: v for k, v in r.items() if k not in ("icao",) and v is not None}
        all_fields = ["callsign", "registration", "aircraft_type", "operator", "latitude", "longitude", "altitude_ft", "ground_speed_kt", "track_deg", "vertical_rate_fpm", "squawk"]
        objects.append(Observation(**r, provider=PROVIDER, observed_at=observed, received_at=received,
                                   freshness_seconds=max(0, int((received - observed).total_seconds())),
                                   available_fields=sorted(known), missing_fields=[f for f in all_fields if f not in known],
                                   evidence_type="provider-reported"))
    db = Session()
    try:
        db.add_all(objects)
        db.commit()
    finally:
        db.close()
    current = [Aircraft.model_validate(row).model_dump(mode="json") for row in objects]
    hub.state.update(status="online", provider=PROVIDER, last_success=now.isoformat(), last_error=None, count=len(current))
    await hub.broadcast({"type": "aircraft", "provider": PROVIDER, "observed_at": now.isoformat(), "aircraft": current})


async def opensky_access_token(client: httpx.AsyncClient) -> str:
    """Get and cache an OpenSky OAuth2 client-credentials token."""
    global _opensky_token, _opensky_token_expires
    if _opensky_token and time.monotonic() < _opensky_token_expires - 60:
        return _opensky_token
    response = await client.post("https://auth.opensky-network.org/auth/realms/opensky-network/protocol/openid-connect/token",
                                 data={"grant_type": "client_credentials", "client_id": OPEN_SKY_CLIENT_ID,
                                       "client_secret": OPEN_SKY_CLIENT_SECRET})
    response.raise_for_status()
    payload = response.json()
    _opensky_token = payload["access_token"]
    _opensky_token_expires = time.monotonic() + float(payload.get("expires_in", 1800))
    return _opensky_token


async def ingestion_loop() -> None:
    while True:
        try:
            await collect()
        except Exception as exc:
            log.exception("Aviation provider poll failed")
            hub.state.update(status="degraded" if hub.state["last_success"] else "offline", last_error=type(exc).__name__)
        await asyncio.sleep(POLL_SECONDS)


@asynccontextmanager
async def lifespan(_: FastAPI):
    task = asyncio.create_task(ingestion_loop())
    yield
    task.cancel()
    with suppress(asyncio.CancelledError):
        await task


app = FastAPI(title="AirOps API", version="0.1.0", lifespan=lifespan)
origins = [x.strip() for x in os.getenv("CORS_ORIGINS", "http://localhost:8000").split(",") if x.strip()]
app.add_middleware(CORSMiddleware, allow_origins=origins, allow_credentials=False, allow_methods=["GET"], allow_headers=["*"])


@app.get("/", include_in_schema=False)
def home() -> FileResponse:
    return FileResponse(Path(__file__).resolve().parents[2] / "index.html")


@app.get("/api/status")
def status() -> dict[str, Any]:
    try:
        with engine.connect() as connection:
            connection.exec_driver_sql("SELECT 1")
        db_state = "online"
    except Exception:
        db_state = "offline"
    return {**hub.state, "poll_seconds": POLL_SECONDS, "database": db_state, "history": "observations retained in database"}


@app.get("/api/aircraft", response_model=list[Aircraft])
def aircraft() -> list[Aircraft]:
    """Latest stored observation for each ICAO address (within the last 30 minutes)."""
    cutoff = datetime.now(timezone.utc) - timedelta(minutes=30)
    with Session() as db:
        rows = db.scalars(select(Observation).where(Observation.received_at >= cutoff).order_by(Observation.received_at.desc())).all()
    seen: set[str] = set()
    result = []
    for row in rows:
        if row.icao not in seen:
            result.append(Aircraft.model_validate(row))
            seen.add(row.icao)
    return result


@app.get("/api/history/{icao}", response_model=list[Aircraft])
def history(icao: str, limit: int = 1000) -> list[Aircraft]:
    """Historical observations actually archived by this service."""
    with Session() as db:
        rows = db.scalars(select(Observation).where(Observation.icao == icao.lower()).order_by(Observation.observed_at.desc()).limit(min(max(limit, 1), 5000))).all()
    return [Aircraft.model_validate(row) for row in reversed(rows)]


@app.websocket("/ws")
async def websocket_feed(socket: WebSocket) -> None:
    await socket.accept()
    hub.clients.add(socket)
    try:
        await socket.send_json({"type": "status", **hub.state})
        while True:
            await socket.receive_text()
    except WebSocketDisconnect:
        hub.clients.discard(socket)
