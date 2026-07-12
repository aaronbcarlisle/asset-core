-- ============================================================================
-- assetcore : identity-first asset management schema (PostgreSQL)
-- ----------------------------------------------------------------------------
-- One idea, expressed as tables:
--   * an asset is an immutable IDENTITY (uuid)
--   * three sovereign FACETS hang off that identity (identity / source / runtime)
--   * relationships are typed EDGES in a graph, not folders in a tree
--   * nothing is inferred; each authority writes only its own facet
-- ============================================================================

CREATE EXTENSION IF NOT EXISTS "pgcrypto";   -- gen_random_uuid()

-- ----------------------------------------------------------------------------
-- 1. ASSET : the immutable identity. This row is born once and never changes
--    except for its lifecycle state. The uuid is the ONLY thing that crosses
--    between Production, the DCC, and the engine.
-- ----------------------------------------------------------------------------
CREATE TABLE asset (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    -- 'provisional' = artist/editor summoned it into existence, Production
    --                 has not yet given it meaning (backfill queue).
    -- 'active'      = Production has claimed and named it.
    -- 'deprecated'  = retired but kept for lineage; never deleted.
    lifecycle       TEXT NOT NULL DEFAULT 'provisional'
                    CHECK (lifecycle IN ('provisional','active','deprecated')),
    asset_type      TEXT NOT NULL,            -- 'prop','anim','material','locomotion_set',...
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    created_by      TEXT NOT NULL,            -- who/what minted it (artist, editor, build)
    -- free-form birth context so a provisional asset isn't a total mystery
    -- to Production later. ("declared while working on pirate_ship")
    origin_context  JSONB NOT NULL DEFAULT '{}'::jsonb
);

-- ----------------------------------------------------------------------------
-- 2a. FACET: IDENTITY  (owned by Production)
--     The human-facing meaning of the asset. Production mutates this freely;
--     a rename here touches NOTHING on disk or in engine.
-- ----------------------------------------------------------------------------
CREATE TABLE facet_identity (
    asset_id        UUID PRIMARY KEY REFERENCES asset(id) ON DELETE CASCADE,
    display_name    TEXT,                     -- "Pirate Barrel, Weathered"
    -- taxonomy as a path-like label, but it's METADATA, not a filesystem.
    -- renaming this is a single UPDATE, never a batch move.
    taxonomy        TEXT,                     -- "props/containers/barrel"
    status          TEXT,                     -- production status: 'wip','review','approved'
    tags            TEXT[] NOT NULL DEFAULT '{}',
    attributes      JSONB NOT NULL DEFAULT '{}'::jsonb,  -- shot assignments, notes, etc.
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_by      TEXT
);

-- ----------------------------------------------------------------------------
-- 2b. FACET: SOURCE  (owned by the artist / DCC)
--     A pointer to authored truth in Perforce. The depot path is just a
--     LOCATION here, not an identity -- artists can p4 move/rename at will and
--     we simply update this row. Versioned so we can pin or float.
-- ----------------------------------------------------------------------------
CREATE TABLE facet_source_version (
    id              BIGSERIAL PRIMARY KEY,
    asset_id        UUID NOT NULL REFERENCES asset(id) ON DELETE CASCADE,
    depot_path      TEXT NOT NULL,            -- "//depot/art/anim/something.ma"
    p4_changelist   BIGINT,                   -- exact P4 revision this version maps to
    dcc             TEXT,                     -- 'maya','substance','houdini'
    version_num     INT NOT NULL,             -- monotonic per asset
    is_latest       BOOLEAN NOT NULL DEFAULT TRUE,
    published_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    published_by    TEXT,
    UNIQUE (asset_id, version_num)
);
-- only one row per asset may be the latest authored version
CREATE UNIQUE INDEX one_latest_source
    ON facet_source_version(asset_id) WHERE is_latest;

-- ----------------------------------------------------------------------------
-- 2c. FACET: RUNTIME  (owned by the engine)
--     Where the asset lives in-editor and what build produced it. Designers
--     rename/move in-editor freely; the reconciliation sync just rewrites
--     engine_path. The uuid lives stamped in the .uasset, so a move is safe.
-- ----------------------------------------------------------------------------
CREATE TABLE facet_runtime_version (
    id              BIGSERIAL PRIMARY KEY,
    asset_id        UUID NOT NULL REFERENCES asset(id) ON DELETE CASCADE,
    engine_path     TEXT NOT NULL,            -- "/Game/Junk/Bob/BP_Barrel_FINAL"
    build_id        TEXT,                     -- which cook/build produced this
    version_num     INT NOT NULL,
    is_latest       BOOLEAN NOT NULL DEFAULT TRUE,
    cooked_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (asset_id, version_num)
);
CREATE UNIQUE INDEX one_latest_runtime
    ON facet_runtime_version(asset_id) WHERE is_latest;

-- ----------------------------------------------------------------------------
-- 3. RELATIONSHIP : the graph. Typed, directed edges between identities.
--    This is what folders can never express: instancing, derivation,
--    composition, dependency -- and crucially, lineage.
-- ----------------------------------------------------------------------------
CREATE TABLE relationship (
    id              BIGSERIAL PRIMARY KEY,
    from_asset      UUID NOT NULL REFERENCES asset(id) ON DELETE CASCADE,
    to_asset        UUID NOT NULL REFERENCES asset(id) ON DELETE CASCADE,
    rel_type        TEXT NOT NULL CHECK (rel_type IN (
                        'INSTANCE_OF',   -- live: same asset, updates propagate
                        'DERIVED_FROM',  -- forked: lineage kept, edits don't propagate
                        'VARIANT_OF',    -- sibling variant under a shared concept
                        'COMPOSED_OF',   -- set/whole contains a part (ship -> props)
                        'DEPENDS_ON'     -- needs another to build (barrel -> wood mat)
                    )),
    -- for DEPENDS_ON: does the consumer FLOAT (always latest) or PIN (locked)?
    -- this is the single knob that kills the materials republish bottleneck.
    binding_mode    TEXT CHECK (binding_mode IN ('float','pin')),
    pinned_source_version  INT,         -- when pinned, which source version
    attributes      JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (from_asset, to_asset, rel_type)
);
CREATE INDEX rel_from ON relationship(from_asset, rel_type);
CREATE INDEX rel_to   ON relationship(to_asset, rel_type);

-- ----------------------------------------------------------------------------
-- 4. EVENT : the append-only spine. Every facet write emits one row.
--    Tools subscribe to this to get the non-blocking "materials updated"
--    nudge. Also your full audit trail / "where has this asset been".
-- ----------------------------------------------------------------------------
CREATE TABLE event (
    id              BIGSERIAL PRIMARY KEY,
    asset_id        UUID REFERENCES asset(id) ON DELETE SET NULL,
    event_type      TEXT NOT NULL,   -- 'declared','source.published','runtime.cooked',
                                     -- 'identity.renamed','relationship.added',...
    payload         JSONB NOT NULL DEFAULT '{}'::jsonb,
    actor           TEXT,
    occurred_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX event_asset ON event(asset_id, occurred_at);
CREATE INDEX event_type  ON event(event_type, occurred_at);
