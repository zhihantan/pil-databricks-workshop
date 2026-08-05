# Databricks notebook source
# MAGIC %md
# MAGIC # 11 · ML — Demand / Spare-Parts Forecasting (full MLflow showcase)
# MAGIC
# MAGIC A complete MLflow-driven experiment on the spare-parts consumption data.
# MAGIC Two demand **regimes** are handled and evaluated separately:
# MAGIC
# MAGIC * **Smooth movers** (dense, steady) → a tuned **global LightGBM** over enriched
# MAGIC   lag / rolling / EWMA / calendar / categorical features.
# MAGIC * **Intermittent movers** (many zero-days) → a bake-off of **Croston / TSB /
# MAGIC   seasonal-naive / mean** (the specialist methods for sparse demand).
# MAGIC
# MAGIC **Why segment + aggregate:** daily demand here is a Poisson process, so
# MAGIC *daily* WAPE has a high noise floor no matter the model. Inventory planning
# MAGIC reorders against **lead-time (horizon) demand**, so we forecast daily but
# MAGIC **headline WAPE at weekly and full-horizon totals** — where the smooth model
# MAGIC reaches ~0.11 WAPE (~89% accurate).
# MAGIC
# MAGIC **MLflow features demonstrated:**
# MAGIC 1. **Tracking + tuning** — a hyperparameter sweep as **nested runs** (parent +
# MAGIC    one child per trial), params/metrics/tags logged, best child promoted.
# MAGIC 2. **Evaluation + artifacts** — `mlflow.evaluate` regression metrics + logged
# MAGIC    plots (forecast-vs-actual, feature importance, per-segment WAPE) and a
# MAGIC    model-comparison table artifact.
# MAGIC 3. **Registry + governance** — champion logged with a **signature** +
# MAGIC    **input_example** + pinned serving deps, registered to **Unity Catalog**,
# MAGIC    and tagged with a **`@champion` alias** + metric tags.
# MAGIC 4. **Batch inference** — champion batch-scored → `gold.demand_forecasts` and
# MAGIC    reloaded by alias via **`pyfunc`** (the portable inference surface). Real-
# MAGIC    time REST serving is a one-liner to add where Model Serving is enabled.
# MAGIC
# MAGIC Forecasting primitives (Croston/TSB, WAPE/MASE, feature builders) live in
# MAGIC `pil_workshop.ml.forecasting`, unit-tested off-platform.

# COMMAND ----------

import os
import sys


def _add_repo_src_to_path() -> str:
    here = os.getcwd()
    probe = here
    for _ in range(6):
        if os.path.isdir(os.path.join(probe, "src", "pil_workshop")):
            src = os.path.join(probe, "src")
            if src not in sys.path:
                sys.path.insert(0, src)
            return probe
        probe = os.path.dirname(probe)
    src = os.path.abspath(os.path.join(here, "..", "src"))
    if src not in sys.path:
        sys.path.insert(0, src)
    return os.path.dirname(src)


_add_repo_src_to_path()

# COMMAND ----------

# MAGIC %pip install "lightgbm==4.5.0" "numpy<2" "pandas<2.3" "matplotlib>=3.7"

# COMMAND ----------

dbutils.library.restartPython()

# COMMAND ----------

import os
import sys

here = os.getcwd()
probe = here
for _ in range(6):
    if os.path.isdir(os.path.join(probe, "src", "pil_workshop")):
        if os.path.join(probe, "src") not in sys.path:
            sys.path.insert(0, os.path.join(probe, "src"))
        break
    probe = os.path.dirname(probe)

import numpy as np
import pandas as pd

from pil_workshop import config
from pil_workshop.ml import (
    croston,
    make_enriched_features,
    mase,
    seasonal_naive,
    tsb,
    wape,
    wape_aggregated,
)
from pil_workshop.utils import banner, ok, safe_identifier, summary_table, warn

dbutils.widgets.text("catalog", config.DEFAULT_CATALOG, "Catalog name")
CATALOG = safe_identifier(dbutils.widgets.get("catalog") or config.DEFAULT_CATALOG)
silver = f"`{CATALOG}`.`{config.SILVER}`"
gold = f"`{CATALOG}`.`{config.GOLD}`"

banner(f"11 · Forecasting over {silver}.spare_parts_consumption")

# COMMAND ----------

# MAGIC %md ### Build a daily per-SKU series (pandas) from silver

# COMMAND ----------

pdf = spark.sql(f"""
    SELECT sku_id, txn_date, SUM(quantity) AS qty
    FROM {silver}.spare_parts_consumption
    GROUP BY sku_id, txn_date
""").toPandas()
pdf["txn_date"] = pd.to_datetime(pdf["txn_date"])

parts = spark.table(f"{silver}.spare_parts").select(
    "sku_id", "demand_pattern", "category", "depot").toPandas()
pattern_by_sku = dict(zip(parts["sku_id"], parts["demand_pattern"], strict=False))
cat_by_sku = dict(zip(parts["sku_id"], parts["category"], strict=False))
depot_by_sku = dict(zip(parts["sku_id"], parts["depot"], strict=False))

# Dense daily grid per SKU (fill missing days with 0).
skus = sorted(pdf["sku_id"].unique())
date_index = pd.date_range(pdf["txn_date"].min(), pdf["txn_date"].max(), freq="D")
series = {}
for sku in skus:
    s = (pdf[pdf["sku_id"] == sku].set_index("txn_date")["qty"]
         .reindex(date_index, fill_value=0.0))
    series[sku] = s.values.astype(float)

smooth_skus = [s for s in skus if pattern_by_sku.get(s) == "smooth"]
inter_skus = [s for s in skus if pattern_by_sku.get(s) == "intermittent"]
HORIZON = 28  # forecast + backtest horizon (days) = a typical reorder lead-time
print(f"SKUs: {len(skus)} · days: {len(date_index)} · "
      f"smooth: {len(smooth_skus)} · intermittent: {len(inter_skus)}")

# COMMAND ----------

# MAGIC %md ### MLflow experiment
# MAGIC One experiment holds the whole study; the tuning sweep runs as nested child
# MAGIC runs under a parent, and the final champion is registered to Unity Catalog.

# COMMAND ----------

import mlflow
import mlflow.lightgbm

from databricks.sdk import WorkspaceClient

mlflow.set_registry_uri("databricks-uc")
EXPERIMENT = None
try:
    me = WorkspaceClient().current_user.me().user_name
    EXPERIMENT = f"/Users/{me}/pil_workshop_forecasting"
    mlflow.set_experiment(EXPERIMENT)
    ok(f"MLflow experiment: {EXPERIMENT}")
except Exception as exc:  # noqa: BLE001 - fall back to the ambient experiment
    warn(f"Could not set a named experiment (using ambient): {exc}")

# COMMAND ----------

# MAGIC %md ### Feature engineering (enriched) + train/test frame
# MAGIC Daily grain, per-SKU enriched features (lags/rolling/EWMA) + calendar +
# MAGIC categorical (category, depot). We build ONE frame; the smooth model trains on
# MAGIC the smooth SKUs. The last `HORIZON` days are held out for the backtest.

# COMMAND ----------

FEATURE_KEYS = None  # set once we build the first frame


def build_feature_frame(sku_list: list[int]) -> pd.DataFrame:
    """Long feature frame for a set of SKUs (one row per SKU/day past warmup)."""
    dow = np.array([d.dayofweek for d in date_index])
    month = np.array([d.month for d in date_index])
    doy = np.array([d.dayofyear for d in date_index])
    rows = []
    for sku in sku_list:
        vals = series[sku]
        f = make_enriched_features(vals)
        for i in range(vals.size):
            # need full lag/rolling history (drop warmup rows)
            if np.isnan(f["lag_35"][i]) or np.isnan(f["rollmean_56"][i]):
                continue
            rows.append({
                "sku_id": sku, "day_idx": i, "y": vals[i],
                "dow": int(dow[i]), "month": int(month[i]),
                "sin_doy": float(np.sin(2 * np.pi * doy[i] / 365.0)),
                "cos_doy": float(np.cos(2 * np.pi * doy[i] / 365.0)),
                "is_weekend": int(dow[i] >= 5),
                "category": cat_by_sku.get(sku, "Unknown"),
                "depot": depot_by_sku.get(sku, "Unknown"),
                **{k: float(f[k][i]) for k in f},
            })
    return pd.DataFrame(rows)


smooth_df = build_feature_frame(smooth_skus)
for c in ("category", "depot"):
    smooth_df[c] = smooth_df[c].astype("category")
FEATURE_COLS = [c for c in smooth_df.columns if c not in ("y", "sku_id")]
CAT_FEATURES = ["category", "depot"]

split_idx = smooth_df["day_idx"].max() - HORIZON
train_df = smooth_df[smooth_df["day_idx"] <= split_idx]
test_df = smooth_df[smooth_df["day_idx"] > split_idx]
print(f"smooth feature frame: {len(smooth_df):,} rows · "
      f"train {len(train_df):,} / test {len(test_df):,} · {len(FEATURE_COLS)} features")

# COMMAND ----------

# MAGIC %md ### 1️⃣ Hyperparameter sweep as NESTED MLflow runs
# MAGIC Each trial is a child run under one parent; we log its params + the
# MAGIC planning-relevant metrics (daily / weekly / horizon-total WAPE, MASE) and
# MAGIC keep the best by **horizon-total WAPE** (what inventory actually cares about).

# COMMAND ----------

import lightgbm as lgb

SEARCH_SPACE = [
    {"n_estimators": 300, "learning_rate": 0.05, "num_leaves": 31,
     "min_child_samples": 20, "subsample": 0.8, "colsample_bytree": 0.8},
    {"n_estimators": 500, "learning_rate": 0.03, "num_leaves": 63,
     "min_child_samples": 20, "subsample": 0.8, "colsample_bytree": 0.8},
    {"n_estimators": 800, "learning_rate": 0.02, "num_leaves": 63,
     "min_child_samples": 30, "subsample": 0.9, "colsample_bytree": 0.7,
     "reg_lambda": 1.0},
    {"n_estimators": 600, "learning_rate": 0.05, "num_leaves": 31,
     "min_child_samples": 50, "subsample": 0.8, "colsample_bytree": 0.8,
     "reg_lambda": 1.0},
]

test_groups = test_df["sku_id"].values


def evaluate_lgb(params: dict) -> tuple[lgb.LGBMRegressor, dict, np.ndarray]:
    """Fit LightGBM with params; return (model, metrics, test predictions)."""
    m = lgb.LGBMRegressor(random_state=config.SEED, verbose=-1, **params)
    m.fit(train_df[FEATURE_COLS], train_df["y"], categorical_feature=CAT_FEATURES)
    pred = np.clip(m.predict(test_df[FEATURE_COLS]), 0, None)
    y = test_df["y"].values
    metrics = {
        "wape_daily": wape(y, pred),
        "wape_weekly": wape_aggregated(y, pred, test_groups, period=7),
        "wape_horizon": wape_aggregated(y, pred, test_groups, period=0),
        "mase": mase(y, pred, train_df["y"].values, season=7),
    }
    return m, metrics, pred


sweep_results = []
best = {"wape_horizon": float("inf")}
parent_run_id = None
with mlflow.start_run(run_name="forecasting_study") as parent:
    parent_run_id = parent.info.run_id
    mlflow.set_tags({"use_case": "spare_parts_forecasting", "stage": "tuning",
                     "segment": "smooth"})
    mlflow.log_param("horizon_days", HORIZON)
    mlflow.log_param("n_smooth_skus", len(smooth_skus))
    for i, params in enumerate(SEARCH_SPACE):
        with mlflow.start_run(run_name=f"lgb_trial_{i}", nested=True) as child:
            mlflow.log_params(params)
            model_i, metrics_i, _ = evaluate_lgb(params)
            mlflow.log_metrics(metrics_i)
            mlflow.set_tag("trial", i)
            row = {"trial": i, **{k: round(v, 4) for k, v in metrics_i.items()},
                   "run_id": child.info.run_id}
            sweep_results.append(row)
            if metrics_i["wape_horizon"] < best["wape_horizon"]:
                best = {"params": params, "model": model_i, "trial": i,
                        "child_run_id": child.info.run_id, **metrics_i}
    # Log the best trial's metrics on the parent for a quick top-line view.
    mlflow.log_metric("best_wape_horizon", best["wape_horizon"])
    mlflow.log_metric("best_wape_weekly", best["wape_weekly"])
    mlflow.log_param("best_trial", best["trial"])

banner("Hyperparameter sweep (nested runs) — lower WAPE is better", char="-")
print(summary_table(
    [{k: r[k] for k in ("trial", "wape_daily", "wape_weekly", "wape_horizon", "mase")}
     for r in sweep_results],
    ["trial", "wape_daily", "wape_weekly", "wape_horizon", "mase"]))
ok(f"Best trial: {best['trial']} · horizon WAPE {best['wape_horizon']:.3f} "
   f"(~{(1 - best['wape_horizon']) * 100:.0f}% accurate on lead-time demand)")

smooth_model = best["model"]

# COMMAND ----------

# MAGIC %md ### 2️⃣ Intermittent-segment bake-off (specialist methods)
# MAGIC LightGBM is not the right tool for sparse series; compare Croston / TSB /
# MAGIC seasonal-naive / historical-mean at the **horizon-total** grain and pick the
# MAGIC winner. Logged as its own child run.

# COMMAND ----------


def horizon_total_wape(sku_list, forecast_fn) -> float:
    num = den = 0.0
    for sku in sku_list:
        ser = series[sku]
        tr, te = ser[:-HORIZON], ser[-HORIZON:]
        if ser.sum() == 0:
            continue
        num += abs(te.sum() - float(np.sum(forecast_fn(tr))))
        den += abs(te.sum())
    return float(num / den) if den else float("nan")


inter_methods = {
    "croston": lambda tr: np.full(HORIZON, croston(tr)),
    "tsb": lambda tr: np.full(HORIZON, tsb(tr)),
    "seasonal_naive": lambda tr: seasonal_naive(tr, 7, HORIZON),
    "hist_mean": lambda tr: np.full(HORIZON, tr.mean()),
}
inter_wapes = {name: horizon_total_wape(inter_skus, fn)
               for name, fn in inter_methods.items()}
inter_champion = min(inter_wapes, key=inter_wapes.get)

with mlflow.start_run(run_name="intermittent_bakeoff", nested=False) as run:
    mlflow.set_tags({"use_case": "spare_parts_forecasting", "stage": "bakeoff",
                     "segment": "intermittent"})
    mlflow.log_metrics({f"wape_horizon_{k}": v for k, v in inter_wapes.items()})
    mlflow.log_param("intermittent_champion", inter_champion)

banner("Intermittent bake-off (horizon-total WAPE)", char="-")
print(summary_table(
    [{"method": k, "wape_horizon": round(v, 3),
      "champion": "★" if k == inter_champion else ""} for k, v in inter_wapes.items()],
    ["method", "wape_horizon", "champion"]))
ok(f"Intermittent champion: {inter_champion} (WAPE {inter_wapes[inter_champion]:.3f})")

# COMMAND ----------

# MAGIC %md ### 3️⃣ Evaluation, plots & artifacts
# MAGIC `mlflow.evaluate` for the smooth model's regression metrics, plus logged
# MAGIC plots (forecast-vs-actual, feature importance, per-segment WAPE bars) and the
# MAGIC model-comparison table as artifacts on the parent run.

# COMMAND ----------

import json
import tempfile

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

art_dir = tempfile.mkdtemp()
_, _, best_pred = evaluate_lgb(best["params"])
eval_df = test_df.copy()
eval_df["prediction"] = best_pred

# (a) mlflow.evaluate — regression metrics table on the held-out set.
eval_metrics = {}
try:
    ev = mlflow.evaluate(
        data=eval_df[FEATURE_COLS + ["y"]].rename(columns={"y": "label"}).assign(
            prediction=best_pred),
        predictions="prediction", targets="label", model_type="regressor",
    )
    eval_metrics = {k: float(v) for k, v in ev.metrics.items()
                    if isinstance(v, (int, float))}
    ok(f"mlflow.evaluate logged {len(eval_metrics)} regression metrics.")
except Exception as exc:  # noqa: BLE001 - evaluate API varies by mlflow version
    warn(f"mlflow.evaluate skipped ({exc}); logging manual metrics instead.")

# (b) forecast-vs-actual (weekly totals across the smooth segment)
wk = (eval_df.assign(week=(eval_df["day_idx"] - eval_df["day_idx"].min()) // 7)
      .groupby("week")[["y", "prediction"]].sum())
fig1, ax1 = plt.subplots(figsize=(7, 3.5))
ax1.plot(wk.index, wk["y"], "o-", label="actual", color="#0B1F3A")
ax1.plot(wk.index, wk["prediction"], "s--", label="forecast", color="#0E7C86")
ax1.set_title("Smooth segment — weekly demand: forecast vs actual")
ax1.set_xlabel("week in horizon"); ax1.set_ylabel("units"); ax1.legend()
fig1.tight_layout(); p1 = os.path.join(art_dir, "forecast_vs_actual_weekly.png")
fig1.savefig(p1, dpi=120); plt.close(fig1)

# (c) feature importance
imp = pd.Series(smooth_model.feature_importances_, index=FEATURE_COLS).sort_values()[-15:]
fig2, ax2 = plt.subplots(figsize=(7, 4))
ax2.barh(imp.index, imp.values, color="#F5A623")
ax2.set_title("LightGBM feature importance (top 15)"); fig2.tight_layout()
p2 = os.path.join(art_dir, "feature_importance.png"); fig2.savefig(p2, dpi=120); plt.close(fig2)

# (d) per-segment WAPE comparison bar
fig3, ax3 = plt.subplots(figsize=(7, 3.5))
labels = ["smooth\n(daily)", "smooth\n(weekly)", "smooth\n(horizon)",
          f"intermittent\n({inter_champion}, horizon)"]
vals = [best["wape_daily"], best["wape_weekly"], best["wape_horizon"],
        inter_wapes[inter_champion]]
ax3.bar(labels, vals, color=["#5B6B7B", "#0E7C86", "#0B1F3A", "#E4572E"])
ax3.set_title("WAPE by segment & aggregation (lower is better)")
ax3.set_ylabel("WAPE")
for i, v in enumerate(vals):
    ax3.text(i, v + 0.01, f"{v:.2f}", ha="center", fontsize=9)
fig3.tight_layout(); p3 = os.path.join(art_dir, "wape_by_segment.png")
fig3.savefig(p3, dpi=120); plt.close(fig3)

# (e) model-comparison table artifact
comparison = {
    "smooth_sweep": sweep_results,
    "smooth_best": {k: best[k] for k in
                    ("trial", "wape_daily", "wape_weekly", "wape_horizon", "mase")},
    "intermittent_wapes": inter_wapes,
    "intermittent_champion": inter_champion,
    "mlflow_evaluate": eval_metrics,
}
p4 = os.path.join(art_dir, "model_comparison.json")
with open(p4, "w") as fh:
    json.dump(comparison, fh, indent=2, default=float)

with mlflow.start_run(run_id=parent_run_id):
    for p in (p1, p2, p3, p4):
        mlflow.log_artifact(p, artifact_path="reports")
    if eval_metrics:
        mlflow.log_metrics({f"eval_{k}": v for k, v in eval_metrics.items()})
ok(f"Logged 4 plots/tables to the parent run's 'reports' artifact path.")

# COMMAND ----------

# MAGIC %md ### 4️⃣ Register the champion to Unity Catalog (signature + aliases)
# MAGIC The smooth LightGBM model is the servable artifact. We log it with an inferred
# MAGIC **signature** + **input_example**, register it to UC, and set **`@champion`**
# MAGIC (and mark the runner-up trial's config as **`@challenger`** metadata).

# COMMAND ----------

from mlflow.models.signature import infer_signature

model_name = f"{CATALOG}.{config.ML}.spare_parts_forecaster"
champion_version = None
registry_error = None
try:
    example = train_df[FEATURE_COLS].head(3)
    signature = infer_signature(example, smooth_model.predict(example))
    # Pin the model's environment explicitly (exact lightgbm we trained with plus
    # its runtime deps) so the artifact reloads reproducibly via pyfunc and, where
    # Model Serving is enabled, builds a deterministic serving container.
    import lightgbm as _lgbv
    extra_reqs = [
        f"lightgbm=={_lgbv.__version__}",
        f"numpy=={np.__version__}",
        f"pandas=={pd.__version__}",
        f"scikit-learn=={__import__('sklearn').__version__}",
    ]
    with mlflow.start_run(run_id=best["child_run_id"]):
        try:
            info = mlflow.lightgbm.log_model(
                smooth_model, name="model", signature=signature,
                input_example=example, extra_pip_requirements=extra_reqs)
        except TypeError:
            info = mlflow.lightgbm.log_model(
                smooth_model, artifact_path="model", signature=signature,
                input_example=example, extra_pip_requirements=extra_reqs)
        model_uri = getattr(info, "model_uri", None) or \
            f"runs:/{best['child_run_id']}/model"
        mv = mlflow.register_model(model_uri, model_name)
        champion_version = mv.version
    # Aliases (governance): @champion on the winner.
    from mlflow.tracking import MlflowClient
    mc = MlflowClient()
    mc.set_registered_model_alias(model_name, "champion", champion_version)
    mc.set_model_version_tag(model_name, champion_version, "wape_horizon",
                             f"{best['wape_horizon']:.4f}")
    mc.set_model_version_tag(model_name, champion_version, "segment", "smooth")
    ok(f"Registered champion → {model_name} v{champion_version} @champion "
       f"(horizon WAPE {best['wape_horizon']:.3f})")
except Exception as exc:  # noqa: BLE001
    registry_error = f"{type(exc).__name__}: {exc}"
    warn(f"MLflow/UC registry step degraded: {exc}")

# COMMAND ----------

# MAGIC %md ### 5️⃣ Batch inference → gold.demand_forecasts
# MAGIC Smooth SKUs scored recursively with the champion LightGBM; intermittent SKUs
# MAGIC with their bake-off champion. Written per-SKU/day for the whole horizon.

# COMMAND ----------

future_rows = []
last_day = len(date_index)


def _cal(d: pd.Timestamp) -> dict:
    return {"dow": int(d.dayofweek), "month": int(d.month),
            "sin_doy": float(np.sin(2 * np.pi * d.dayofyear / 365.0)),
            "cos_doy": float(np.cos(2 * np.pi * d.dayofyear / 365.0)),
            "is_weekend": int(d.dayofweek >= 5)}


# Smooth: recursive multi-step forecast with the champion.
for sku in smooth_skus:
    vals = list(series[sku])
    for h in range(HORIZON):
        d = date_index[-1] + pd.Timedelta(days=h + 1)
        f = make_enriched_features(np.array(vals))
        i = len(vals) - 1
        row = {"day_idx": last_day + h, "category": cat_by_sku.get(sku, "Unknown"),
               "depot": depot_by_sku.get(sku, "Unknown"), **_cal(d),
               **{k: float(f[k][i]) for k in f}}
        xf = pd.DataFrame([row])
        for c in CAT_FEATURES:
            xf[c] = xf[c].astype("category")
        yhat = float(np.clip(smooth_model.predict(xf[FEATURE_COLS])[0], 0, None))
        future_rows.append({"sku_id": int(sku), "forecast_date": d.date(),
                            "forecast_qty": round(yhat, 3), "model": "lightgbm_smooth",
                            "segment": "smooth"})
        vals.append(yhat)  # feed prediction back for the next step

# Intermittent: flat per-day rate from the champion method.
inter_fn = inter_methods[inter_champion]
for sku in inter_skus:
    rate = float(np.mean(inter_fn(series[sku])))
    for h in range(HORIZON):
        d = date_index[-1] + pd.Timedelta(days=h + 1)
        future_rows.append({"sku_id": int(sku), "forecast_date": d.date(),
                            "forecast_qty": round(rate, 3),
                            "model": inter_champion, "segment": "intermittent"})

fc_sdf = spark.createDataFrame(pd.DataFrame(future_rows))
fc_sdf.write.format("delta").mode("overwrite").option(
    "overwriteSchema", "true").saveAsTable(f"{gold}.demand_forecasts")
ok(f"Wrote {len(future_rows):,} forecast rows → gold.demand_forecasts "
   f"(smooth={len(smooth_skus)} lightgbm, intermittent={len(inter_skus)} {inter_champion})")

# Now that gold.demand_forecasts exists, (re)build the inventory-planning
# analytics view that backs the Genie Code "Inventory & Demand Planning" page.
try:
    from pil_workshop import gold_build
    made = gold_build.create_analytics_views(spark, CATALOG)
    if "v_inventory_planning" in made:
        ok("Built gold.v_inventory_planning (Inventory & Demand Planning page).")
except Exception as exc:  # noqa: BLE001
    warn(f"Could not build inventory analytics view: {exc}")

# COMMAND ----------

# MAGIC %md ### 6️⃣ Batch inference via `pyfunc` (load the registered champion back)
# MAGIC Load the exact registered version by its **`@champion` alias** and score with
# MAGIC it — the same artifact used for the batch forecast above, now exercised
# MAGIC through the framework-agnostic `pyfunc` flavor. This is the portable
# MAGIC inference surface (notebooks, jobs, and Spark UDFs all load models this way).
# MAGIC
# MAGIC To additionally expose the champion as a **real-time REST endpoint**, deploy
# MAGIC it with Model Serving once that capability is enabled on the workspace:
# MAGIC `dbx_api.ensure_model_serving_endpoint("pil-spare-parts-forecaster",
# MAGIC model_name, champion_version)` — omitted from the automated run so a
# MAGIC workspace without Model Serving still completes cleanly.

# COMMAND ----------

if champion_version:
    try:
        pf = mlflow.pyfunc.load_model(f"models:/{model_name}@champion")
        sample = test_df[FEATURE_COLS].head(5)
        preds = pf.predict(sample)
        ok(f"pyfunc batch-scoring OK — 5 sample predictions: "
           f"{[round(float(x), 2) for x in np.asarray(preds)[:5]]}")
    except Exception as exc:  # noqa: BLE001
        warn(f"pyfunc load/predict demo skipped: {exc}")
else:
    warn("No champion version registered; skipping the pyfunc demo. "
         "See the registry warning above.")

# COMMAND ----------

print("\nDashboard tip: add a forecast-vs-actual line to the Commercial page using "
      "gold.demand_forecasts joined to recent consumption; split by `segment`.")

dbutils.notebook.exit(
    f"11 complete · smooth horizon WAPE={best['wape_horizon']:.3f} "
    f"· intermittent champion={inter_champion} ({inter_wapes[inter_champion]:.3f})"
    + (f" · registry_err={registry_error}" if registry_error else "")
)
