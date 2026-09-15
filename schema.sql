-- FCA-Compliant Marketing Generator — schema
-- Safe to re-run: everything is CREATE ... IF NOT EXISTS.

CREATE TABLE IF NOT EXISTS advisors (
    id          SERIAL PRIMARY KEY,
    name        TEXT NOT NULL,
    email       TEXT UNIQUE NOT NULL,
    created_at  TIMESTAMPTZ DEFAULT now()
);

-- One row per FCA Handbook / guidance page we track.
CREATE TABLE IF NOT EXISTS fca_sections (
    id            SERIAL PRIMARY KEY,
    section_key   TEXT UNIQUE NOT NULL,       -- short stable key, e.g. 'COBS_4'
    section_name  TEXT NOT NULL,
    url           TEXT NOT NULL,
    content_text  TEXT,                       -- last-fetched plain text
    content_hash  TEXT,                       -- sha256 of content_text
    last_checked  TIMESTAMPTZ,
    last_changed  TIMESTAMPTZ
);

-- One row per "Generate" click (a request for N posts).
CREATE TABLE IF NOT EXISTS generation_batches (
    id                     SERIAL PRIMARY KEY,
    created_at             TIMESTAMPTZ DEFAULT now(),
    advisor_id             INTEGER REFERENCES advisors(id),
    format_type            TEXT NOT NULL,      -- 'Post' or 'Carousel'
    category               TEXT NOT NULL,      -- Retirement / Investment / ...
    guideline              TEXT,               -- optional free-text guideline
    num_posts              INTEGER NOT NULL,
    -- snapshot of {section_key: content_hash} for every FCA section that
    -- was used to ground the generation prompt. Used later to detect which
    -- batches are affected when a section's hash changes.
    fca_sections_snapshot  JSONB,
    -- human-readable record of the FCA sections/clauses used to ground this
    -- generation so it can be shown in the app history and audit trail.
    used_clauses           JSONB
);

-- One row per generated post/carousel text.
CREATE TABLE IF NOT EXISTS generated_posts (
    id          SERIAL PRIMARY KEY,
    batch_id    INTEGER REFERENCES generation_batches(id) ON DELETE CASCADE,
    post_index  INTEGER NOT NULL,
    post_text   TEXT NOT NULL,
    status      TEXT NOT NULL DEFAULT 'active',  -- active | flagged_for_review
    created_at  TIMESTAMPTZ DEFAULT now()
);

-- One row per detected change to a tracked FCA section.
CREATE TABLE IF NOT EXISTS fca_change_log (
    id           SERIAL PRIMARY KEY,
    section_id   INTEGER REFERENCES fca_sections(id),
    detected_at  TIMESTAMPTZ DEFAULT now(),
    old_hash     TEXT,
    new_hash     TEXT,
    summary      TEXT
);

-- Links a generated_post to the fca_change_log entry that flagged it, so
-- advisers can be notified and the flag can be tracked to resolution.
CREATE TABLE IF NOT EXISTS post_flags (
    id             SERIAL PRIMARY KEY,
    post_id        INTEGER REFERENCES generated_posts(id) ON DELETE CASCADE,
    change_log_id  INTEGER REFERENCES fca_change_log(id) ON DELETE CASCADE,
    flagged_at     TIMESTAMPTZ DEFAULT now(),
    notified       BOOLEAN DEFAULT FALSE,
    notified_at    TIMESTAMPTZ,
    resolved       BOOLEAN DEFAULT FALSE
);

CREATE INDEX IF NOT EXISTS idx_generated_posts_batch ON generated_posts(batch_id);
CREATE INDEX IF NOT EXISTS idx_post_flags_post ON post_flags(post_id);
CREATE INDEX IF NOT EXISTS idx_post_flags_unresolved ON post_flags(resolved) WHERE resolved = FALSE;
