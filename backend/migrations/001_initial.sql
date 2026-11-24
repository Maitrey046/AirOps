-- Initial PostgreSQL schema. Apply once before running against PostgreSQL.
CREATE TABLE IF NOT EXISTS aircraft_observations (
  id BIGSERIAL PRIMARY KEY,
  icao VARCHAR(16) NOT NULL,
  callsign VARCHAR(32),
  registration VARCHAR(32),
  aircraft_type VARCHAR(32),
  operator VARCHAR(128),
  latitude DOUBLE PRECISION NOT NULL CHECK (latitude BETWEEN -90 AND 90),
  longitude DOUBLE PRECISION NOT NULL CHECK (longitude BETWEEN -180 AND 180),
  altitude_ft INTEGER,
  ground_speed_kt INTEGER,
  track_deg INTEGER,
  vertical_rate_fpm INTEGER,
  squawk VARCHAR(8),
  provider VARCHAR(64) NOT NULL,
  observed_at TIMESTAMPTZ NOT NULL,
  received_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
  freshness_seconds INTEGER NOT NULL DEFAULT 0,
  available_fields JSONB NOT NULL DEFAULT '[]'::jsonb,
  missing_fields JSONB NOT NULL DEFAULT '[]'::jsonb,
  evidence_type VARCHAR(32) NOT NULL DEFAULT 'provider-reported'
);
CREATE INDEX IF NOT EXISTS ix_aircraft_observations_icao ON aircraft_observations (icao);
CREATE INDEX IF NOT EXISTS ix_aircraft_observations_observed_at ON aircraft_observations (observed_at);
CREATE INDEX IF NOT EXISTS ix_aircraft_observations_received_at ON aircraft_observations (received_at);
CREATE INDEX IF NOT EXISTS ix_aircraft_obs_icao_observed ON aircraft_observations (icao, observed_at DESC);
