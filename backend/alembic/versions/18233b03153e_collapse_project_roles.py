"""collapse project roles

Revision ID: 18233b03153e
Revises: 66b47d9d45e3
Create Date: 2026-08-20 22:48:01.660390
"""

from typing import Sequence, Union

from alembic import op


revision: str = "18233b03153e"
down_revision: Union[str, None] = "66b47d9d45e3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # A DATA rewrite, not a schema change: the column is String(16) and the
    # role vocabulary lives in Python. Every non-owner becomes a plain member.
    op.execute(
        "UPDATE project_members SET role = 'member' WHERE role IN ('editor', 'commenter', 'viewer')"
    )


def downgrade() -> None:
    # ONE-WAY. Which members had non-viewer roles is not recoverable from the
    # collapsed data, so this restores the LEAST granting role rather than guessing.
    # A downgrade here is lossy, not a round trip.
    #
    # This restores the DATA but not the CODE: 'viewer' is not in the
    # post-collapse Python ROLE_RANK (app/core/permissions.py), so running
    # this downgrade without also reverting the application code leaves
    # every non-owner ranked -1 -- locked out of their own projects, not
    # merely downgraded to viewer.
    op.execute("UPDATE project_members SET role = 'viewer' WHERE role = 'member'")
