"""The migration is a data rewrite, so what it must guarantee is a property of
the rows afterwards, not of the DDL."""

import pathlib
import re

MIGRATIONS = pathlib.Path(__file__).resolve().parents[1] / "alembic" / "versions"


def _collapse_migration() -> str:
    matches = [p for p in MIGRATIONS.glob("*.py") if "collapse_project_roles" in p.name]
    assert matches, "the role-collapse migration is missing"
    return matches[0].read_text()


def test_the_migration_rewrites_every_retired_role():
    body = _collapse_migration()
    upgrade = body.split("def upgrade")[1].split("def downgrade")[0]
    for retired in ("editor", "commenter", "viewer"):
        assert retired in upgrade, f"{retired} rows are left behind"
    assert "member" in upgrade


def test_the_downgrade_does_not_pretend_to_restore_them():
    """editor/commenter/viewer are unrecoverable from the collapsed data. The
    downgrade must pick the least-granting of them rather than inventing a
    distinction it cannot know."""
    body = _collapse_migration()
    downgrade = body.split("def downgrade")[1]
    assert "viewer" in downgrade
    assert "editor" not in downgrade
    assert "commenter" not in downgrade


def test_the_loss_is_written_down():
    assert re.search(r"one-way|not recoverable|lossy", _collapse_migration(), re.I)
