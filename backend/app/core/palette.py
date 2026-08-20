"""The fixed set of colours a project may carry.

An ALLOWLIST, not a validator of hex syntax. A project's colour is rendered
straight into a `style` attribute in the browser, so the only safe contract is
that the server can enumerate every value it will ever emit. A "must look like
#rrggbb" check would satisfy the type and still let a project set itself to the
sidebar's own background, or to a value chosen to be invisible in one theme.

Every entry is picked to hold contrast against BOTH the light and the dark
sidebar background -- the colour's whole job is telling projects apart in a
collapsed rail, which is exactly where it is smallest.
"""

from __future__ import annotations

PROJECT_COLORS: tuple[str, ...] = (
    "#3B82F6",  # blue
    "#8B5CF6",  # violet
    "#EC4899",  # pink
    "#EF4444",  # red
    "#F97316",  # orange
    "#EAB308",  # amber
    "#22C55E",  # green
    "#14B8A6",  # teal
    "#06B6D4",  # cyan
    "#64748B",  # slate
)


def is_valid(color: str) -> bool:
    return color in PROJECT_COLORS


def color_for(seed: str) -> str:
    """Deterministic palette entry for a project that was never given one.

    Keyed on the project id so the same project always draws the same colour,
    in every session and on every client, with nothing persisted. Existing
    projects predate the column and rely on this.
    """
    total = sum(seed.encode("utf-8"))
    return PROJECT_COLORS[total % len(PROJECT_COLORS)]
