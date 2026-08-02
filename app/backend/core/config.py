"""App configuration resolved from environment (Databricks Apps sets these).

No secrets live here. Databricks host/token are ambient in the Apps runtime;
Lakebase credentials are minted on demand via the SDK. The app can also make
``pil_workshop`` importable when co-located in the repo.
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass, field
from functools import lru_cache


def _ensure_pil_workshop_importable() -> None:
    """Make ``pil_workshop`` importable in every runtime.

    In the deployed Databricks App only the ``app/`` folder ships (no pip build
    step, no sibling ``src/``), so a vendored copy lives at ``app/pil_workshop``
    next to ``backend``; adding ``app/`` to ``sys.path`` picks it up. Locally
    (repo checkout, notebooks, tests) the source of truth ``src/pil_workshop``
    is used instead. Try both.
    """
    try:
        import pil_workshop  # noqa: F401

        return
    except Exception:  # noqa: BLE001
        here = os.path.dirname(os.path.abspath(__file__))
        # backend/core → backend → app → repo root
        app_dir = os.path.dirname(os.path.dirname(here))
        repo = os.path.dirname(app_dir)
        for candidate in (app_dir, os.path.join(repo, "src")):
            if (
                os.path.isdir(os.path.join(candidate, "pil_workshop"))
                and candidate not in sys.path
            ):
                sys.path.insert(0, candidate)


_ensure_pil_workshop_importable()


@dataclass(frozen=True)
class Settings:
    """Resolved app settings."""

    catalog: str = field(default_factory=lambda: os.environ.get("PIL_CATALOG", "pil_workshop"))
    lakebase_instance: str = field(
        default_factory=lambda: os.environ.get("PIL_LAKEBASE_INSTANCE", "pil-workshop-db")
    )
    # When true, the app runs without Lakebase (reads UC only, in-memory queue).
    uc_only: bool = field(default_factory=lambda: os.environ.get("PIL_UC_ONLY", "") == "1")
    warehouse_id: str = field(default_factory=lambda: os.environ.get("PIL_WAREHOUSE_ID", ""))
    # Static assets (built Vite bundle) served at "/".
    static_dir: str = field(
        default_factory=lambda: os.environ.get(
            "PIL_STATIC_DIR",
            os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "static"),
        )
    )

    @property
    def silver(self) -> str:
        return f"`{self.catalog}`.`silver`"

    @property
    def gold(self) -> str:
        return f"`{self.catalog}`.`gold`"


@lru_cache
def get_settings() -> Settings:
    return Settings()
