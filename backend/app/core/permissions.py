"""Role-based permission helper for project access control.

Two ranks, not four. Project membership answers one question -- may this
person see the project -- and the finer editor/viewer distinction lives on
individual LaTeX documents (`services/latex_access.py`), where the thing
being shared actually is.

`can` returns False for any role not in this table, which is what makes the
retired `editor`/`commenter`/`viewer` values safe to leave in an old client's
memory: they rank -1 and satisfy nothing.
"""

ROLE_RANK: dict[str, int] = {
    "member": 0,
    "owner": 1,
}


def can(role: str, need: str) -> bool:
    """Return True if *role* satisfies the *need* rank."""
    return ROLE_RANK.get(role, -1) >= ROLE_RANK.get(need, 0)
