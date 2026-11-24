"""Pure provider-field normalization helpers, with no network or DB dependencies."""
import math
from typing import Any


def normalize_airplanes(row: dict[str, Any]) -> dict[str, Any] | None:
    try:
        lat, lon = float(row["lat"]), float(row["lon"])
        if not math.isfinite(lat) or not math.isfinite(lon) or not (-90 <= lat <= 90 and -180 <= lon <= 180):
            return None
        alt = row.get("alt_baro")
        return {"icao": str(row.get("hex", "")).strip().lower(), "callsign": (row.get("flight") or "").strip() or None,
                "registration": row.get("r"), "aircraft_type": row.get("t"), "operator": row.get("ownOp"),
                "latitude": lat, "longitude": lon, "altitude_ft": 0 if alt == "ground" else int(alt) if isinstance(alt, (int, float)) else None,
                "ground_speed_kt": round(row["gs"]) if isinstance(row.get("gs"), (int, float)) else None,
                "track_deg": round(row["track"]) if isinstance(row.get("track"), (int, float)) else None,
                "vertical_rate_fpm": round(row["baro_rate"]) if isinstance(row.get("baro_rate"), (int, float)) else None,
                "squawk": row.get("squawk"), "_seen_seconds": row.get("seen_pos", row.get("seen"))}
    except (ValueError, TypeError, KeyError):
        return None


def normalize_opensky(row: list[Any]) -> dict[str, Any] | None:
    try:
        lat, lon = float(row[6]), float(row[5])
        if not math.isfinite(lat) or not math.isfinite(lon) or not (-90 <= lat <= 90 and -180 <= lon <= 180):
            return None
        return {"icao": str(row[0]).strip().lower(), "callsign": (row[1] or "").strip() or None,
                "registration": None, "aircraft_type": None, "operator": row[2] or None, "latitude": lat, "longitude": lon,
                "altitude_ft": round((row[7] or 0) * 3.28084), "ground_speed_kt": round((row[9] or 0) * 1.94384),
                "track_deg": round(row[10] or 0), "vertical_rate_fpm": round((row[11] or 0) * 196.85), "squawk": row[14] or None,
                "_observed_epoch": row[3]}
    except (ValueError, TypeError, IndexError):
        return None
