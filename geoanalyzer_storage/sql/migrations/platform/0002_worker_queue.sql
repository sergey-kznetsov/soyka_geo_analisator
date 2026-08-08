CREATE TABLE IF NOT EXISTS ga_core.job_queue (
    application_id TEXT NOT NULL,
    analysis_id TEXT NOT NULL,
    compute_class TEXT NOT NULL,
    priority SMALLINT NOT NULL DEFAULT 0,
    attempt INTEGER NOT NULL DEFAULT 0,
    max_attempts INTEGER NOT NULL DEFAULT 3,
    available_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    lease_owner TEXT,
    lease_expires_at TIMESTAMPTZ,
    cancel_requested BOOLEAN NOT NULL DEFAULT FALSE,
    trace_id CHAR(32) NOT NULL,
    last_error JSONB,
    enqueued_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    PRIMARY KEY (application_id, analysis_id),
    FOREIGN KEY (application_id, analysis_id)
        REFERENCES ga_core.jobs(application_id, analysis_id)
        ON DELETE CASCADE,
    CHECK (compute_class IN ('cpu', 'gpu')),
    CHECK (priority BETWEEN -100 AND 100),
    CHECK (attempt >= 0),
    CHECK (max_attempts >= 1),
    CHECK (attempt <= max_attempts),
    CHECK ((lease_owner IS NULL) = (lease_expires_at IS NULL)),
    CHECK (trace_id ~ '^[a-f0-9]{32}$'),
    CHECK (trace_id <> repeat('0', 32)),
    CHECK (last_error IS NULL OR jsonb_typeof(last_error) = 'object')
);

CREATE INDEX IF NOT EXISTS idx_ga_core_job_queue_claim
    ON ga_core.job_queue(
        application_id,
        compute_class,
        priority DESC,
        available_at,
        enqueued_at,
        analysis_id
    )
    WHERE cancel_requested = FALSE;

CREATE INDEX IF NOT EXISTS idx_ga_core_job_queue_lease
    ON ga_core.job_queue(application_id, lease_expires_at)
    WHERE lease_owner IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_ga_core_job_queue_exhausted
    ON ga_core.job_queue(application_id, compute_class, updated_at)
    WHERE attempt >= max_attempts;
