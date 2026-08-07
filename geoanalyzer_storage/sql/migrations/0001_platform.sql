CREATE EXTENSION IF NOT EXISTS postgis;

CREATE SCHEMA IF NOT EXISTS ga_core;
CREATE SCHEMA IF NOT EXISTS ga_cache;

CREATE TABLE IF NOT EXISTS ga_meta.applications (
    application_id TEXT PRIMARY KEY,
    domain_schema NAME,
    active BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    CHECK (application_id ~ '^[a-z][a-z0-9_-]{0,62}$')
);

CREATE TABLE IF NOT EXISTS ga_core.jobs (
    application_id TEXT NOT NULL REFERENCES ga_meta.applications(application_id),
    analysis_id TEXT NOT NULL,
    idempotency_key TEXT NOT NULL,
    revision BIGINT NOT NULL CHECK (revision >= 1),
    status TEXT NOT NULL,
    current_stage TEXT,
    progress_percent SMALLINT NOT NULL CHECK (progress_percent BETWEEN 0 AND 100),
    payload JSONB NOT NULL CHECK (jsonb_typeof(payload) = 'object'),
    created_at TIMESTAMPTZ NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL,
    lease_owner TEXT,
    lease_expires_at TIMESTAMPTZ,
    PRIMARY KEY (application_id, analysis_id),
    UNIQUE (application_id, idempotency_key),
    CHECK (payload->>'analysis_id' = analysis_id),
    CHECK ((payload->>'revision')::BIGINT = revision)
);

CREATE INDEX IF NOT EXISTS idx_ga_core_jobs_status_updated
    ON ga_core.jobs(application_id, status, updated_at);
CREATE INDEX IF NOT EXISTS idx_ga_core_jobs_lease
    ON ga_core.jobs(application_id, lease_expires_at)
    WHERE lease_owner IS NOT NULL;

CREATE TABLE IF NOT EXISTS ga_core.stage_checkpoints (
    application_id TEXT NOT NULL,
    analysis_id TEXT NOT NULL,
    stage TEXT NOT NULL,
    state TEXT NOT NULL,
    attempt INTEGER NOT NULL CHECK (attempt >= 0),
    processed_items BIGINT CHECK (processed_items IS NULL OR processed_items >= 0),
    total_items BIGINT CHECK (total_items IS NULL OR total_items >= 0),
    output JSONB NOT NULL DEFAULT '{}'::JSONB,
    warnings JSONB NOT NULL DEFAULT '[]'::JSONB,
    error JSONB,
    started_at TIMESTAMPTZ,
    completed_at TIMESTAMPTZ,
    updated_at TIMESTAMPTZ,
    PRIMARY KEY (application_id, analysis_id, stage),
    FOREIGN KEY (application_id, analysis_id)
        REFERENCES ga_core.jobs(application_id, analysis_id)
        ON DELETE CASCADE,
    CHECK (jsonb_typeof(output) = 'object'),
    CHECK (jsonb_typeof(warnings) = 'array'),
    CHECK (error IS NULL OR jsonb_typeof(error) = 'object'),
    CHECK (processed_items IS NULL OR total_items IS NULL OR processed_items <= total_items)
);

CREATE TABLE IF NOT EXISTS ga_core.artifacts (
    application_id TEXT NOT NULL,
    analysis_id TEXT NOT NULL,
    artifact_type TEXT NOT NULL,
    artifact_key TEXT NOT NULL,
    content_digest TEXT NOT NULL,
    schema_version TEXT,
    producer_version TEXT,
    source_stage TEXT,
    payload JSONB NOT NULL,
    geometry geometry(Geometry, 4326),
    created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    PRIMARY KEY (
        application_id,
        analysis_id,
        artifact_type,
        artifact_key,
        content_digest
    ),
    FOREIGN KEY (application_id, analysis_id)
        REFERENCES ga_core.jobs(application_id, analysis_id)
        ON DELETE CASCADE,
    CHECK (content_digest ~ '^[a-f0-9]{64}$')
);

CREATE INDEX IF NOT EXISTS idx_ga_core_artifacts_lookup
    ON ga_core.artifacts(application_id, analysis_id, artifact_type, artifact_key);
CREATE INDEX IF NOT EXISTS idx_ga_core_artifacts_geometry_gist
    ON ga_core.artifacts USING GIST(geometry)
    WHERE geometry IS NOT NULL;

CREATE TABLE IF NOT EXISTS ga_cache.entries (
    application_id TEXT NOT NULL REFERENCES ga_meta.applications(application_id),
    namespace TEXT NOT NULL,
    key_digest TEXT NOT NULL,
    key_json JSONB,
    value_json JSONB NOT NULL,
    value_digest TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    expires_at TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (application_id, namespace, key_digest),
    CHECK (namespace ~ '^[a-z][a-z0-9_.:-]{0,126}$'),
    CHECK (key_digest ~ '^[a-f0-9]{64}$'),
    CHECK (value_digest ~ '^[a-f0-9]{64}$'),
    CHECK (expires_at > created_at)
);

CREATE INDEX IF NOT EXISTS idx_ga_cache_entries_expiry
    ON ga_cache.entries(expires_at);
