CREATE SCHEMA IF NOT EXISTS ga_soika;

INSERT INTO ga_meta.applications(application_id, domain_schema)
VALUES ('soika', 'ga_soika')
ON CONFLICT (application_id) DO UPDATE SET
    domain_schema = EXCLUDED.domain_schema,
    active = TRUE,
    updated_at = clock_timestamp();

CREATE TABLE IF NOT EXISTS ga_soika.model_versions (
    application_id TEXT NOT NULL DEFAULT 'soika' CHECK (application_id = 'soika'),
    model_kind TEXT NOT NULL,
    model_id TEXT NOT NULL,
    model_revision TEXT NOT NULL,
    artifact_digest TEXT,
    qualification_digest TEXT,
    metadata JSONB NOT NULL DEFAULT '{}'::JSONB,
    first_seen_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    PRIMARY KEY (application_id, model_kind, model_id, model_revision),
    FOREIGN KEY (application_id)
        REFERENCES ga_meta.applications(application_id),
    CHECK (artifact_digest IS NULL OR artifact_digest ~ '^[a-f0-9]{64}$'),
    CHECK (qualification_digest IS NULL OR qualification_digest ~ '^[a-f0-9]{64}$'),
    CHECK (jsonb_typeof(metadata) = 'object')
);

CREATE TABLE IF NOT EXISTS ga_soika.source_messages (
    application_id TEXT NOT NULL DEFAULT 'soika' CHECK (application_id = 'soika'),
    analysis_id TEXT NOT NULL,
    message_key TEXT NOT NULL,
    source_name TEXT NOT NULL,
    external_id TEXT,
    source_url TEXT,
    raw_text TEXT NOT NULL,
    published_at TIMESTAMPTZ,
    raw_payload JSONB NOT NULL,
    content_digest TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    PRIMARY KEY (application_id, analysis_id, message_key),
    FOREIGN KEY (application_id, analysis_id)
        REFERENCES ga_core.jobs(application_id, analysis_id)
        ON DELETE CASCADE,
    CHECK (content_digest ~ '^[a-f0-9]{64}$')
);

CREATE INDEX IF NOT EXISTS idx_ga_soika_source_messages_external
    ON ga_soika.source_messages(application_id, source_name, external_id)
    WHERE external_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_ga_soika_source_messages_digest
    ON ga_soika.source_messages(application_id, content_digest);

CREATE TABLE IF NOT EXISTS ga_soika.preprocessed_messages (
    application_id TEXT NOT NULL DEFAULT 'soika' CHECK (application_id = 'soika'),
    analysis_id TEXT NOT NULL,
    message_key TEXT NOT NULL,
    original_text TEXT NOT NULL,
    cleaned_text TEXT NOT NULL,
    language TEXT,
    text_digest TEXT NOT NULL,
    duplicate_status TEXT,
    payload JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    PRIMARY KEY (application_id, analysis_id, message_key),
    FOREIGN KEY (application_id, analysis_id, message_key)
        REFERENCES ga_soika.source_messages(application_id, analysis_id, message_key)
        ON DELETE CASCADE,
    CHECK (text_digest ~ '^[a-f0-9]{64}$')
);

CREATE TABLE IF NOT EXISTS ga_soika.classifications (
    application_id TEXT NOT NULL DEFAULT 'soika' CHECK (application_id = 'soika'),
    analysis_id TEXT NOT NULL,
    message_key TEXT NOT NULL,
    classifier_kind TEXT NOT NULL,
    model_id TEXT NOT NULL,
    model_revision TEXT NOT NULL,
    label TEXT,
    confidence DOUBLE PRECISION,
    included BOOLEAN NOT NULL,
    payload JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    PRIMARY KEY (
        application_id,
        analysis_id,
        message_key,
        classifier_kind,
        model_id,
        model_revision
    ),
    FOREIGN KEY (application_id, analysis_id, message_key)
        REFERENCES ga_soika.source_messages(application_id, analysis_id, message_key)
        ON DELETE CASCADE,
    CHECK (confidence IS NULL OR confidence BETWEEN 0.0 AND 1.0)
);

CREATE TABLE IF NOT EXISTS ga_soika.geocoding_results (
    application_id TEXT NOT NULL DEFAULT 'soika' CHECK (application_id = 'soika'),
    analysis_id TEXT NOT NULL,
    message_key TEXT NOT NULL,
    location_kind TEXT,
    selected_candidate_id TEXT,
    confidence DOUBLE PRECISION,
    point geometry(Point, 4326),
    has_exact_geometry BOOLEAN NOT NULL DEFAULT FALSE,
    provider_identity JSONB,
    payload JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    PRIMARY KEY (application_id, analysis_id, message_key),
    FOREIGN KEY (application_id, analysis_id, message_key)
        REFERENCES ga_soika.source_messages(application_id, analysis_id, message_key)
        ON DELETE CASCADE,
    CHECK (confidence IS NULL OR confidence BETWEEN 0.0 AND 1.0)
);

CREATE INDEX IF NOT EXISTS idx_ga_soika_geocoding_results_exact_gist
    ON ga_soika.geocoding_results USING GIST(point)
    WHERE point IS NOT NULL AND has_exact_geometry;
CREATE INDEX IF NOT EXISTS idx_ga_soika_geocoding_results_exact_geography_gist
    ON ga_soika.geocoding_results USING GIST((point::geography))
    WHERE point IS NOT NULL AND has_exact_geometry;

CREATE TABLE IF NOT EXISTS ga_soika.geocoding_candidates (
    application_id TEXT NOT NULL DEFAULT 'soika' CHECK (application_id = 'soika'),
    analysis_id TEXT NOT NULL,
    message_key TEXT NOT NULL,
    candidate_id TEXT NOT NULL,
    candidate_rank INTEGER NOT NULL CHECK (candidate_rank >= 1),
    confidence DOUBLE PRECISION NOT NULL CHECK (confidence BETWEEN 0.0 AND 1.0),
    point geometry(Point, 4326),
    payload JSONB NOT NULL,
    PRIMARY KEY (application_id, analysis_id, message_key, candidate_id),
    UNIQUE (application_id, analysis_id, message_key, candidate_rank),
    FOREIGN KEY (application_id, analysis_id, message_key)
        REFERENCES ga_soika.geocoding_results(application_id, analysis_id, message_key)
        ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS ga_soika.events (
    application_id TEXT NOT NULL DEFAULT 'soika' CHECK (application_id = 'soika'),
    analysis_id TEXT NOT NULL,
    event_id TEXT NOT NULL,
    level TEXT NOT NULL,
    object_id TEXT NOT NULL,
    category TEXT,
    topic TEXT,
    started_at TIMESTAMPTZ,
    ended_at TIMESTAMPTZ,
    centroid geometry(Point, 4326),
    algorithm_version TEXT NOT NULL,
    payload JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    PRIMARY KEY (application_id, analysis_id, event_id),
    FOREIGN KEY (application_id, analysis_id)
        REFERENCES ga_core.jobs(application_id, analysis_id)
        ON DELETE CASCADE,
    CHECK (ended_at IS NULL OR started_at IS NULL OR ended_at >= started_at)
);

CREATE INDEX IF NOT EXISTS idx_ga_soika_events_centroid_gist
    ON ga_soika.events USING GIST(centroid)
    WHERE centroid IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_ga_soika_events_time
    ON ga_soika.events(application_id, analysis_id, started_at, ended_at);

CREATE TABLE IF NOT EXISTS ga_soika.event_members (
    application_id TEXT NOT NULL DEFAULT 'soika' CHECK (application_id = 'soika'),
    analysis_id TEXT NOT NULL,
    event_id TEXT NOT NULL,
    message_key TEXT NOT NULL,
    PRIMARY KEY (application_id, analysis_id, event_id, message_key),
    FOREIGN KEY (application_id, analysis_id, event_id)
        REFERENCES ga_soika.events(application_id, analysis_id, event_id)
        ON DELETE CASCADE,
    FOREIGN KEY (application_id, analysis_id, message_key)
        REFERENCES ga_soika.source_messages(application_id, analysis_id, message_key)
        ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_ga_soika_event_members_message
    ON ga_soika.event_members(application_id, analysis_id, message_key);

CREATE TABLE IF NOT EXISTS ga_soika.event_connections (
    application_id TEXT NOT NULL DEFAULT 'soika' CHECK (application_id = 'soika'),
    analysis_id TEXT NOT NULL,
    source_event_id TEXT NOT NULL,
    target_event_id TEXT NOT NULL,
    connection_kind TEXT NOT NULL,
    jaccard DOUBLE PRECISION NOT NULL CHECK (jaccard > 0.0 AND jaccard <= 1.0),
    distance_m DOUBLE PRECISION CHECK (distance_m IS NULL OR distance_m >= 0.0),
    geometry geometry(Geometry, 4326),
    payload JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    PRIMARY KEY (application_id, analysis_id, source_event_id, target_event_id),
    FOREIGN KEY (application_id, analysis_id, source_event_id)
        REFERENCES ga_soika.events(application_id, analysis_id, event_id)
        ON DELETE CASCADE,
    FOREIGN KEY (application_id, analysis_id, target_event_id)
        REFERENCES ga_soika.events(application_id, analysis_id, event_id)
        ON DELETE CASCADE,
    CHECK (source_event_id < target_event_id),
    CHECK ((geometry IS NULL) = (distance_m IS NULL))
);

CREATE INDEX IF NOT EXISTS idx_ga_soika_event_connections_geometry_gist
    ON ga_soika.event_connections USING GIST(geometry)
    WHERE geometry IS NOT NULL;

CREATE TABLE IF NOT EXISTS ga_soika.risk_history (
    application_id TEXT NOT NULL DEFAULT 'soika' CHECK (application_id = 'soika'),
    analysis_id TEXT NOT NULL,
    event_id TEXT NOT NULL,
    formula_version TEXT NOT NULL,
    config_digest TEXT NOT NULL,
    output_digest TEXT NOT NULL,
    score DOUBLE PRECISION,
    risk_band TEXT NOT NULL,
    decision_use_approved BOOLEAN NOT NULL,
    payload JSONB NOT NULL,
    recorded_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    PRIMARY KEY (
        application_id,
        analysis_id,
        event_id,
        formula_version,
        config_digest,
        output_digest
    ),
    FOREIGN KEY (application_id, analysis_id, event_id)
        REFERENCES ga_soika.events(application_id, analysis_id, event_id)
        ON DELETE CASCADE,
    CHECK (config_digest ~ '^[a-f0-9]{64}$'),
    CHECK (output_digest ~ '^[a-f0-9]{64}$'),
    CHECK (score IS NULL OR score BETWEEN 0.0 AND 1.0)
);

CREATE INDEX IF NOT EXISTS idx_ga_soika_risk_history_event_time
    ON ga_soika.risk_history(application_id, analysis_id, event_id, recorded_at DESC);
