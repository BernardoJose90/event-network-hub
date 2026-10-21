"""${message}

Revision ID: ${up_revision}
Revises: ${down_revision | comma,n}
Created: ${create_date}
"""

from alembic import op

revision = ${repr(up_revision)}
down_revision = ${repr(down_revision)}
branch_labels = None
depends_on = None

# Number revisions 0002, 0003, ... (alembic revision --rev-id 0002 -m "...") and bump
# SCHEMA_VERSION in app/db.py. Keep each one backwards compatible with the previous release:
# add columns and tables first, remove what the old code reads only in a later release.


def upgrade() -> None:
    ${upgrades if upgrades else "pass"}


def downgrade() -> None:
    ${downgrades if downgrades else "pass"}
