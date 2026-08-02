"""Classic ML helpers for the PIL workshop: intermittent-demand forecasting
(Croston/TSB), forecast accuracy metrics (WAPE/MASE), feature engineering, and
route-optimization model builders (min-cost flow, VRPTW).

Kept in the shared library so the logic is unit-testable off-platform; the
notebooks (11, 12) orchestrate Spark/MLflow/serving around these primitives.
"""

from .forecasting import (
    croston,
    make_lag_features,
    mase,
    seasonal_naive,
    tsb,
    wape,
)
from .optimization import (
    build_min_cost_flow,
    solve_min_cost_flow,
    solve_vrptw,
)

__all__ = [
    "croston",
    "tsb",
    "seasonal_naive",
    "make_lag_features",
    "wape",
    "mase",
    "build_min_cost_flow",
    "solve_min_cost_flow",
    "solve_vrptw",
]
