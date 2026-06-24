"""Role-based permission helper for project access control."""

ROLE_RANK: dict[str, int] = {
    "viewer": 0,
    "commenter": 1,
    "editor": 2,
    "owner": 3,
}


def can(role: str, need: str) -> bool:
    """Return True if *role* satisfies the *need* rank."""
    return ROLE_RANK.get(role, -1) >= ROLE_RANK.get(need, 0)
