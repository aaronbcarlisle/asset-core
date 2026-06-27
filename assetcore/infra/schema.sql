-- ============================================================================
-- assetcore : canonical layered schema (PostgreSQL dialect)
-- ----------------------------------------------------------------------------
-- The DDL the layered SQL repos (sqlite_repo, postgres_repo) bootstrap from. It
-- mirrors core/entities.py field-for-field, using the tool-agnostic names the
-- core settled on:  location_uri (not depot_path/engine_path), tool (not dcc),
-- revision:text (not p4_changelist:bigint), origin (not origin_context),
-- pinned_version (not pinned_source_version).
--
-- This is distinct from the prototype's db/schema.sql, which keeps the old
-- column names so the untouched prototype (api.py) stays runnable. sqlite_repo
-- translates this PG dialect to SQLite on the fly (same approach as the
-- prototype's connection.py); postgres_repo uses it as-is.
-- ============================================================================

CREATE EXTENSION IF NOT EXISTS "pgcrypto";   -- gen_random_uuid()

-- 1. ASSET : the immutable identity.
CREATE TABLE IF NOT EXISTS asset (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    lifecycle       TEXT NOT NULL DEFAULT 'provisional'
                    CHECK (lifecycle IN ('provisional','active','deprecated')),
    asset_type      TEXT NOT NULL,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    created_by      TEXT NOT NULL,
    origin          JSONB NOT NULL DEFAULT '{}'::jsonb
);

-- 2a. FACET: IDENTITY (owned by Production).
CREATE TABLE IF NOT EXISTS facet_identity (
    asset_id        UUID PRIMARY KEY REFERENCES asset(id) ON DELETE CASCADE,
    display_name    TEXT,
    taxonomy        TEXT,
    status          TEXT,
    tags            TEXT[] NOT NULL DEFAULT '{}',
    attributes      JSONB NOT NULL DEFAULT '{}'::jsonb
);

-- 2b. FACET: SOURCE (owned by the artist/DCC). Versioned pointer to authored truth.
CREATE TABLE IF NOT EXISTS facet_source_version (
    id              BIGSERIAL PRIMARY KEY,
    asset_id        UUID NOT NULL REFERENCES asset(id) ON DELETE CASCADE,
    location_uri    TEXT NOT NULL,            -- opaque: //depot/...  git://...  s3://...
    tool            TEXT,                     -- 'maya','blender','substance' (a label)
    revision        TEXT,                     -- P4 CL, git sha, ... (opaque string)
    version_num     INT NOT NULL,
    is_latest       BOOLEAN NOT NULL DEFAULT TRUE,
    published_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    published_by    TEXT,
    UNIQUE (asset_id, version_num)
);
CREATE UNIQUE INDEX IF NOT EXISTS one_latest_source
    ON facet_source_version(asset_id) WHERE is_latest;

-- 2c. FACET: RUNTIME (owned by the engine/build). Versioned pointer to cooked form.
CREATE TABLE IF NOT EXISTS facet_runtime_version (
    id              BIGSERIAL PRIMARY KEY,
    asset_id        UUID NOT NULL REFERENCES asset(id) ON DELETE CASCADE,
    location_uri    TEXT NOT NULL,            -- '/Game/...' or any engine address
    build_id        TEXT,
    version_num     INT NOT NULL,
    is_latest       BOOLEAN NOT NULL DEFAULT TRUE,
    cooked_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (asset_id, version_num)
);
CREATE UNIQUE INDEX IF NOT EXISTS one_latest_runtime
    ON facet_runtime_version(asset_id) WHERE is_latest;

-- 3. RELATIONSHIP : typed, directed edges between identities.
CREATE TABLE IF NOT EXISTS relationship (
    id              BIGSERIAL PRIMARY KEY,
    from_asset      UUID NOT NULL REFERENCES asset(id) ON DELETE CASCADE,
    to_asset        UUID NOT NULL REFERENCES asset(id) ON DELETE CASCADE,
    rel_type        TEXT NOT NULL CHECK (rel_type IN (
                        'INSTANCE_OF','DERIVED_FROM','VARIANT_OF',
                        'COMPOSED_OF','DEPENDS_ON')),
    binding_mode    TEXT CHECK (binding_mode IN ('float','pin')),
    pinned_version  INT,
    attributes      JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (from_asset, to_asset, rel_type)
);
CREATE INDEX IF NOT EXISTS rel_from ON relationship(from_asset, rel_type);
CREATE INDEX IF NOT EXISTS rel_to   ON relationship(to_asset, rel_type);

-- 4. EVENT : the append-only spine. Written by an EventSink (Phase 3's notify
--    sink); included here so the canonical schema is the whole data model.
CREATE TABLE IF NOT EXISTS event (
    id              BIGSERIAL PRIMARY KEY,
    asset_id        UUID REFERENCES asset(id) ON DELETE SET NULL,
    event_type      TEXT NOT NULL,
    payload         JSONB NOT NULL DEFAULT '{}'::jsonb,
    actor           TEXT,
    occurred_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS event_asset ON event(asset_id, occurred_at);
CREATE INDEX IF NOT EXISTS event_type  ON event(event_type, occurred_at);
