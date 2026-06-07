"""Programmatic alembic upgrade, run at app startup.

A fresh deploy (or empty volume) must not depend on a manual `make migrate`.
Two wrinkles handled here:

- alembic's env.py is async and calls ``asyncio.run`` at import, so the
  upgrade is pushed to a worker thread to keep it off the running event loop.
- the Config is built WITHOUT alembic.ini: passing the ini would make env.py
  run ``fileConfig``, which disables uvicorn's already-configured loggers.
  env.py takes the DB URL from app settings, so the ini adds nothing here.
"""

import asyncio
from pathlib import Path

from alembic import command
from alembic.config import Config

from app.core.logging import log

_BACKEND_DIR = Path(__file__).resolve().parents[2]


def _upgrade_to_head() -> None:
    cfg = Config()
    cfg.set_main_option("script_location", str(_BACKEND_DIR / "alembic"))
    command.upgrade(cfg, "head")


async def run_migrations() -> None:
    log.info("migrations_start")
    await asyncio.to_thread(_upgrade_to_head)
    log.info("migrations_done")
