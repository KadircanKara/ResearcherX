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
    """Return True if *role* satisfies the *need* rank.

    `need` is developer-supplied (a literal in a route/service), never data
    read from the database -- a value absent from the table is our bug, and
    must crash loudly rather than silently defaulting to `member`'s rank (0),
    which would let a typo'd `need` quietly admit any member. `role` is a
    stored value on a row a caller does not control the history of, so an
    unrecognized role must never crash a request -- it ranks -1 and satisfies
    nothing, which is what lets a retired role (`editor`/`commenter`/`viewer`)
    sitting in an old client's memory fail safely instead of 500ing.
    """
    if need not in ROLE_RANK:
        raise ValueError(f"unknown need: {need}")
    return ROLE_RANK.get(role, -1) >= ROLE_RANK[need]
