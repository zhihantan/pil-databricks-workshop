"""PIL Databricks Workshop — shared Python library.

This package is the single source of truth for:

* ``config``   — catalog/schema/volume names, the workshop color palette,
                 KPI target ranges, and data-scale presets.
* ``llm``      — the ONLY place Foundation Model API endpoint names live;
                 every notebook and the app backend import from here.
* ``dbx_api``  — thin, patchable wrappers over Databricks REST/SDK calls
                 whose payloads occasionally change between releases.
* ``utils``    — small helpers (widget/env resolution, logging, retries).
* ``datagen``  — deterministic synthetic-data generators for PIL's business.

Nothing in this package imports ``dbutils`` at module import time so it stays
unit-testable off-platform (``python -m compileall src`` and ``pytest`` pass).
"""

from __future__ import annotations

__version__ = "1.0.0"
__all__ = ["config", "llm", "dbx_api", "utils", "datagen"]
