"""PIL Databricks Workshop — shared library (vendored subset for the app).

VENDORED COPY. The source of truth is ``/src/pil_workshop``; these files are
mirrored here so the Databricks App is self-contained. Databricks Apps deploy
only the ``app/`` folder (no pip build step and no access to the sibling
``src/``), so the modules the backend imports at runtime
(``llm``, ``agent_bricks``, ``lakebase`` and their intra-package deps
``utils``, ``dbx_api``, ``config``) live alongside ``backend`` here. Keep them
in sync with ``src/pil_workshop`` — see ``app/pil_workshop/README.md``.

Nothing in this package imports ``dbutils`` at module import time so it stays
unit-testable off-platform.
"""

from __future__ import annotations

__version__ = "1.0.0"
__all__ = ["config", "llm", "dbx_api", "utils", "agent_bricks", "lakebase"]
