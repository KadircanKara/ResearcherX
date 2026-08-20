"""The rank table after the collapse to owner + member."""

import pytest

from app.core.permissions import ROLE_RANK, can


def test_a_member_satisfies_member():
    assert can("member", "member") is True


def test_an_owner_satisfies_member():
    assert can("owner", "member") is True


def test_a_member_does_not_satisfy_owner():
    assert can("member", "owner") is False


def test_a_retired_role_satisfies_nothing():
    """The three old roles are gone. A role absent from the table ranks -1, so
    a stale client's `editor` must not be quietly treated as a member -- that
    would be a role nobody can reason about holding real access."""
    assert can("editor", "member") is False
    assert can("viewer", "member") is False
    assert can("commenter", "member") is False


def test_the_table_holds_exactly_two_roles():
    assert ROLE_RANK == {"member": 0, "owner": 1}


def test_an_unknown_need_raises():
    """`need` is developer-supplied -- a value outside the table is our bug
    and must crash loudly rather than silently rank as `member`."""
    with pytest.raises(ValueError):
        can("member", "editor")


def test_an_unknown_role_returns_false_rather_than_raising():
    """`role` is data read back from the database -- a stale or retired value
    must never crash a request."""
    assert can("editor", "member") is False
