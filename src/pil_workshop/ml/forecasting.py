"""Forecasting primitives: Croston/TSB for intermittent demand, seasonal-naive
baseline, lag/calendar feature engineering, and WAPE/MASE metrics.

Pure NumPy/Python so they unit-test off-platform. LightGBM training itself lives
in notebook 11 (it depends on the runtime), but the features and metrics here
are shared and tested.
"""

from __future__ import annotations

import numpy as np


def croston(demand: np.ndarray, alpha: float = 0.1) -> float:
    """Classic Croston forecast for intermittent demand.

    Separately smooths non-zero demand sizes and inter-arrival intervals;
    returns the per-period forecast (size / interval). Good default for spares
    with many zero-demand days.
    """
    demand = np.asarray(demand, dtype=float)
    nz_idx = np.flatnonzero(demand > 0)
    if nz_idx.size == 0:
        return 0.0
    # Initialize with first non-zero.
    z = demand[nz_idx[0]]          # smoothed size
    x = float(nz_idx[0] + 1)       # smoothed interval
    last = nz_idx[0]
    for i in nz_idx[1:]:
        interval = i - last
        z = alpha * demand[i] + (1 - alpha) * z
        x = alpha * interval + (1 - alpha) * x
        last = i
    return float(z / x) if x > 0 else 0.0


def tsb(demand: np.ndarray, alpha: float = 0.1, beta: float = 0.05) -> float:
    """Teunter-Syntetos-Babai forecast for intermittent demand.

    Updates demand *probability* every period (not just at non-zero events), so
    it handles obsolescence better than Croston. Returns per-period forecast.
    """
    demand = np.asarray(demand, dtype=float)
    if demand.size == 0:
        return 0.0
    nz = demand[demand > 0]
    z = float(nz[0]) if nz.size else 0.0   # smoothed demand size
    p = float((demand > 0).mean())          # smoothed probability
    for d in demand:
        occurred = 1.0 if d > 0 else 0.0
        p = beta * occurred + (1 - beta) * p
        if d > 0:
            z = alpha * d + (1 - alpha) * z
    return float(p * z)


def seasonal_naive(series: np.ndarray, season: int = 7, horizon: int = 1) -> np.ndarray:
    """Seasonal-naive baseline: forecast = value one season ago."""
    series = np.asarray(series, dtype=float)
    if series.size < season:
        fill = series[-1] if series.size else 0.0
        return np.full(horizon, fill)
    return np.array([series[-season + (h % season)] for h in range(horizon)])


def make_lag_features(
    values: np.ndarray, lags: tuple[int, ...] = (1, 7, 14, 28),
    roll_windows: tuple[int, ...] = (7, 28),
) -> dict[str, np.ndarray]:
    """Build lag + rolling-mean features aligned to ``values`` (NaN-padded).

    Returns a dict of feature-name → array (same length as ``values``). Intended
    to feed a global LightGBM model; the notebook adds calendar features.
    """
    values = np.asarray(values, dtype=float)
    n = values.size
    feats: dict[str, np.ndarray] = {}
    for lag in lags:
        arr = np.full(n, np.nan)
        if n > lag:
            arr[lag:] = values[:-lag]
        feats[f"lag_{lag}"] = arr
    for w in roll_windows:
        arr = np.full(n, np.nan)
        for i in range(n):
            if i >= w:
                arr[i] = values[i - w:i].mean()
        feats[f"rollmean_{w}"] = arr
    return feats


def make_enriched_features(
    values: np.ndarray,
    lags: tuple[int, ...] = (1, 2, 3, 7, 14, 21, 28, 35),
    roll_windows: tuple[int, ...] = (7, 14, 28, 56),
    ewma_spans: tuple[int, ...] = (7, 28),
) -> dict[str, np.ndarray]:
    """Richer feature set than :func:`make_lag_features` for the global LightGBM.

    Adds more lags, more rolling-mean windows, and (leakage-safe, shifted-by-one)
    EWMA levels. All arrays are NaN-padded to align with ``values``; the caller
    drops warmup rows and adds calendar/categorical features. Kept dependency-free
    (NumPy only) so it stays unit-testable off-platform.
    """
    values = np.asarray(values, dtype=float)
    n = values.size
    feats: dict[str, np.ndarray] = {}
    for lag in lags:
        arr = np.full(n, np.nan)
        if n > lag:
            arr[lag:] = values[:-lag]
        feats[f"lag_{lag}"] = arr
    for w in roll_windows:
        arr = np.full(n, np.nan)
        for i in range(n):
            if i >= w:
                arr[i] = values[i - w:i].mean()
        feats[f"rollmean_{w}"] = arr
    for span in ewma_spans:
        # ewm over the raw series, then shift by 1 so row i uses only past data.
        ew = _ewma(values, span)
        arr = np.full(n, np.nan)
        arr[1:] = ew[:-1]
        feats[f"ewma_{span}"] = arr
    return feats


def _ewma(values: np.ndarray, span: int) -> np.ndarray:
    """Exponentially weighted moving average (pandas-free)."""
    values = np.asarray(values, dtype=float)
    alpha = 2.0 / (span + 1.0)
    out = np.empty_like(values)
    if values.size == 0:
        return out
    out[0] = values[0]
    for i in range(1, values.size):
        out[i] = alpha * values[i] + (1 - alpha) * out[i - 1]
    return out


def wape_aggregated(
    actual: np.ndarray, forecast: np.ndarray, groups: np.ndarray, period: int = 0
) -> float:
    """WAPE after aggregating within each group (and optionally into periods).

    Daily intermittent/Poisson demand has a high WAPE floor (day-to-day noise);
    inventory planning cares about demand summed over a lead-time/period. This
    sums actual & forecast per ``group`` (e.g. sku) — and, if ``period`` > 0, into
    consecutive ``period``-length buckets within each group — then computes WAPE
    on those totals. ``period=0`` = one total per group (full-horizon demand).
    """
    actual = np.asarray(actual, dtype=float)
    forecast = np.asarray(forecast, dtype=float)
    groups = np.asarray(groups)
    num = den = 0.0
    for g in np.unique(groups):
        m = groups == g
        a, f = actual[m], forecast[m]
        if period and a.size:
            idx = np.arange(a.size) // period
            a = np.array([a[idx == k].sum() for k in np.unique(idx)])
            f = np.array([f[idx == k].sum() for k in np.unique(idx)])
        else:
            a, f = np.array([a.sum()]), np.array([f.sum()])
        num += np.abs(a - f).sum()
        den += np.abs(a).sum()
    return float(num / den) if den else float("nan")


def wape(actual: np.ndarray, forecast: np.ndarray) -> float:
    """Weighted Absolute Percentage Error = sum|a-f| / sum|a| (as a fraction).

    Robust to intermittent zeros (unlike MAPE). Lower is better.
    """
    actual = np.asarray(actual, dtype=float)
    forecast = np.asarray(forecast, dtype=float)
    denom = np.abs(actual).sum()
    if denom == 0:
        return 0.0 if np.abs(forecast).sum() == 0 else float("inf")
    return float(np.abs(actual - forecast).sum() / denom)


def mase(actual: np.ndarray, forecast: np.ndarray,
         train: np.ndarray, season: int = 1) -> float:
    """Mean Absolute Scaled Error, scaled by the in-sample seasonal-naive MAE."""
    actual = np.asarray(actual, dtype=float)
    forecast = np.asarray(forecast, dtype=float)
    train = np.asarray(train, dtype=float)
    if train.size <= season:
        return float("inf")
    naive_err = np.abs(train[season:] - train[:-season]).mean()
    if naive_err == 0:
        return float("inf")
    return float(np.abs(actual - forecast).mean() / naive_err)
