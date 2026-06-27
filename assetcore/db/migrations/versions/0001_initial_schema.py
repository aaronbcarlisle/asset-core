"""initial assetcore binding schema (the 5 tables)

Revision ID: 0001
Revises:
Create Date: 2026-06-26

Dialect-correct so the schema matches what the repos expect on each backend:
ids are a real UUID on Postgres (PostgresRepo inserts UUID objects) and a String
on sqlite (SqliteRepo serializes UUID<->str); tags are a TEXT[] on Postgres and
JSON on sqlite, matching each repo's storage. Timestamps carry a server default so
inserts that omit them (e.g. relationship.created_at) succeed.
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def _uuid() -> sa.types.TypeEngine:
    # UUID on Postgres (matches PostgresRepo's register_uuid), String elsewhere (sqlite)
    return sa.String(36).with_variant(postgresql.UUID(), "postgresql")


def _tags() -> sa.types.TypeEngine:
    # TEXT[] on Postgres (PostgresRepo stores a list), JSON on sqlite (json.dumps)
    return sa.JSON().with_variant(postgresql.ARRAY(sa.Text()), "postgresql")


def upgrade() -> None:
    op.create_table(
        "asset",
        sa.Column("id", _uuid(), primary_key=True),
        sa.Column("lifecycle", sa.String(), nullable=False, server_default="provisional"),
        sa.Column("asset_type", sa.String(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
        sa.Column("created_by", sa.String(), nullable=False),
        sa.Column("origin", sa.JSON(), nullable=False, server_default="{}"),
        sa.CheckConstraint("lifecycle IN ('provisional','active','deprecated')",
                           name="ck_asset_lifecycle"),
    )
    op.create_table(
        "facet_identity",
        sa.Column("asset_id", _uuid(), sa.ForeignKey("asset.id", ondelete="CASCADE"),
                  primary_key=True),
        sa.Column("display_name", sa.String()),
        sa.Column("taxonomy", sa.String()),
        sa.Column("status", sa.String()),
        # no server_default: '[]' is a malformed array literal on Postgres TEXT[],
        # and the repos always supply tags, so a column default is unnecessary.
        sa.Column("tags", _tags(), nullable=False),
        sa.Column("attributes", sa.JSON(), nullable=False, server_default="{}"),
    )
    op.create_table(
        "facet_source_version",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("asset_id", _uuid(), sa.ForeignKey("asset.id", ondelete="CASCADE"),
                  nullable=False),
        sa.Column("location_uri", sa.String(), nullable=False),
        sa.Column("tool", sa.String()),
        sa.Column("revision", sa.String()),
        sa.Column("version_num", sa.Integer(), nullable=False),
        sa.Column("is_latest", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
        sa.Column("published_by", sa.String()),
        sa.UniqueConstraint("asset_id", "version_num", name="uq_source_asset_version"),
    )
    op.create_index("one_latest_source", "facet_source_version", ["asset_id"], unique=True,
                    postgresql_where=sa.text("is_latest"), sqlite_where=sa.text("is_latest"))
    op.create_table(
        "facet_runtime_version",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("asset_id", _uuid(), sa.ForeignKey("asset.id", ondelete="CASCADE"),
                  nullable=False),
        sa.Column("location_uri", sa.String(), nullable=False),
        sa.Column("build_id", sa.String()),
        sa.Column("version_num", sa.Integer(), nullable=False),
        sa.Column("is_latest", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("cooked_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
        sa.UniqueConstraint("asset_id", "version_num", name="uq_runtime_asset_version"),
    )
    op.create_index("one_latest_runtime", "facet_runtime_version", ["asset_id"], unique=True,
                    postgresql_where=sa.text("is_latest"), sqlite_where=sa.text("is_latest"))
    op.create_table(
        "relationship",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("from_asset", _uuid(), sa.ForeignKey("asset.id", ondelete="CASCADE"),
                  nullable=False),
        sa.Column("to_asset", _uuid(), sa.ForeignKey("asset.id", ondelete="CASCADE"),
                  nullable=False),
        sa.Column("rel_type", sa.String(), nullable=False),
        sa.Column("binding_mode", sa.String()),
        sa.Column("pinned_version", sa.Integer()),
        sa.Column("attributes", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),   # relate() omits it -> needs a default
        sa.CheckConstraint(
            "rel_type IN ('INSTANCE_OF','DERIVED_FROM','VARIANT_OF','COMPOSED_OF','DEPENDS_ON')",
            name="ck_rel_type"),
        sa.CheckConstraint("binding_mode IS NULL OR binding_mode IN ('float','pin')",
                           name="ck_binding_mode"),
        sa.UniqueConstraint("from_asset", "to_asset", "rel_type", name="uq_edge"),
    )
    op.create_index("rel_from", "relationship", ["from_asset", "rel_type"])
    op.create_index("rel_to", "relationship", ["to_asset", "rel_type"])
    op.create_table(
        "event",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("asset_id", _uuid(), sa.ForeignKey("asset.id", ondelete="SET NULL")),
        sa.Column("event_type", sa.String(), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("actor", sa.String()),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
    )
    op.create_index("event_asset", "event", ["asset_id", "occurred_at"])
    op.create_index("event_type", "event", ["event_type", "occurred_at"])


def downgrade() -> None:
    op.drop_table("event")
    op.drop_index("rel_to", table_name="relationship")
    op.drop_index("rel_from", table_name="relationship")
    op.drop_table("relationship")
    op.drop_index("one_latest_runtime", table_name="facet_runtime_version")
    op.drop_table("facet_runtime_version")
    op.drop_index("one_latest_source", table_name="facet_source_version")
    op.drop_table("facet_source_version")
    op.drop_table("facet_identity")
    op.drop_table("asset")
