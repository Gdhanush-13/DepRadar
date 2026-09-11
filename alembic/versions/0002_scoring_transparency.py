"""Add transparent scoring and per-repository ignore state."""
from alembic import op
from sqlalchemy import Boolean, Column, Float

revision = "0002_scoring_transparency"
down_revision = "0001_initial"


def upgrade():
    op.add_column("dependencies", Column("is_ignored", Boolean(), server_default="false"))
    for name in ("lag_points", "age_points", "archived_points", "cve_points"):
        op.add_column("dependency_snapshots", Column(name, Float(), server_default="0"))


def downgrade():
    for name in ("lag_points", "age_points", "archived_points", "cve_points"):
        op.drop_column("dependency_snapshots", name)
    op.drop_column("dependencies", "is_ignored")
