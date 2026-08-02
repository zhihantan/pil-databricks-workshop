# Databricks notebook source
# MAGIC %md
# MAGIC # 11 · ML — Demand / Spare-Parts Forecasting
# MAGIC
# MAGIC Trains and compares forecasting models on the spare-parts consumption data:
# MAGIC
# MAGIC * **Baselines:** seasonal-naive and **Croston/TSB** (the right default for the
# MAGIC   intermittent spare-parts series — many zero-demand days).
# MAGIC * **Global ML:** a **LightGBM** model over lag/calendar features across all SKUs.
# MAGIC * **Evaluation:** WAPE + MASE on a rolling backtest; champion chosen by WAPE.
# MAGIC * **MLflow:** params/metrics/artifacts logged; champion registered to **Unity
# MAGIC   Catalog** and (optionally) served via a model-serving endpoint.
# MAGIC * Batch forecast → `gold.demand_forecasts`.
# MAGIC
# MAGIC The forecasting primitives (Croston/TSB, WAPE/MASE, features) live in
# MAGIC `pil_workshop.ml.forecasting` so they are unit-tested off-platform.

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

# MAGIC %pip install "lightgbm==4.5.0" "numpy<2" "pandas<2.3"

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
from pil_workshop.ml import croston, make_lag_features, mase, seasonal_naive, tsb, wape
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
    "sku_id", "demand_pattern").toPandas()
pattern_by_sku = dict(zip(parts["sku_id"], parts["demand_pattern"], strict=False))

# Pivot to a dense daily grid per SKU (fill missing days with 0).
skus = pdf["sku_id"].unique()
date_index = pd.date_range(pdf["txn_date"].min(), pdf["txn_date"].max(), freq="D")
print(f"SKUs: {len(skus)} · days: {len(date_index)}")

# COMMAND ----------

# MAGIC %md ### Baselines + LightGBM with a rolling backtest
# MAGIC Hold out the last 28 days. Baselines: seasonal-naive + Croston/TSB (per SKU).
# MAGIC LightGBM: one global model over lag/calendar features.

# COMMAND ----------

HORIZON = 28
frames = []
for sku in skus:
    s = (pdf[pdf["sku_id"] == sku].set_index("txn_date")["qty"]
         .reindex(date_index, fill_value=0.0))
    frames.append((sku, s.values))

# --- Baseline metrics (Croston/TSB/seasonal-naive) ---
baseline_rows = []
sn_wapes, cr_wapes, tsb_wapes = [], [], []
for _sku, series in frames:
    train, test = series[:-HORIZON], series[-HORIZON:]
    if train.sum() == 0:
        continue
    sn = seasonal_naive(train, season=7, horizon=HORIZON)
    cr = np.full(HORIZON, croston(train))
    tb = np.full(HORIZON, tsb(train))
    sn_wapes.append(wape(test, sn))
    cr_wapes.append(wape(test, cr))
    tsb_wapes.append(wape(test, tb))

banner("Baseline WAPE (mean across SKUs)", char="-")
print(f"  seasonal_naive : {np.mean(sn_wapes):.3f}")
print(f"  croston        : {np.mean(cr_wapes):.3f}")
print(f"  tsb            : {np.mean(tsb_wapes):.3f}")

# COMMAND ----------

# MAGIC %md ### Global LightGBM model

# COMMAND ----------

import lightgbm as lgb

# Build a long feature frame: for each SKU/day, lags + calendar features.
rows = []
for sku, series in frames:
    feats = make_lag_features(series)
    dow = np.array([d.dayofweek for d in date_index])
    month = np.array([d.month for d in date_index])
    for i in range(len(series)):
        if np.isnan(feats["lag_28"][i]):
            continue  # need full lag history
        rows.append({
            "sku_id": sku, "day_idx": i, "y": series[i],
            "dow": dow[i], "month": month[i],
            **{k: feats[k][i] for k in feats},
        })
feat_df = pd.DataFrame(rows)
split = feat_df["day_idx"].max() - HORIZON
train_df = feat_df[feat_df["day_idx"] <= split]
test_df = feat_df[feat_df["day_idx"] > split]
feature_cols = [c for c in feat_df.columns if c not in ("y", "sku_id")]

model = lgb.LGBMRegressor(
    n_estimators=300, learning_rate=0.05, num_leaves=31,
    subsample=0.8, colsample_bytree=0.8, random_state=config.SEED,
)
model.fit(train_df[feature_cols], train_df["y"])
pred = np.clip(model.predict(test_df[feature_cols]), 0, None)
lgb_wape = wape(test_df["y"].values, pred)
lgb_mase = mase(test_df["y"].values, pred, train_df["y"].values, season=7)
ok(f"LightGBM WAPE: {lgb_wape:.3f} · MASE: {lgb_mase:.3f}")

# COMMAND ----------

# MAGIC %md ### Champion selection + MLflow + UC registry

# COMMAND ----------

import mlflow
import mlflow.lightgbm

candidates = {
    "seasonal_naive": float(np.mean(sn_wapes)),
    "croston": float(np.mean(cr_wapes)),
    "tsb": float(np.mean(tsb_wapes)),
    "lightgbm_global": float(lgb_wape),
}
champion = min(candidates, key=candidates.get)
rows = [{"model": k, "wape": round(v, 3),
         "champion": "★" if k == champion else ""} for k, v in candidates.items()]
banner("Model comparison (WAPE, lower is better)", char="-")
print(summary_table(rows, ["model", "wape", "champion"]))

mlflow.set_registry_uri("databricks-uc")
model_name = f"{CATALOG}.{config.ML}.spare_parts_forecaster"
champion_version = None
try:
    # Ensure a valid experiment exists (ambient in interactive notebooks, but a
    # Job task may not have one set, which makes start_run fail).
    try:
        me = WorkspaceClient().current_user.me().user_name
        mlflow.set_experiment(f"/Users/{me}/pil_workshop_forecasting")
    except Exception:  # noqa: BLE001 - fall back to the ambient experiment
        pass

    with mlflow.start_run(run_name="pil_spare_parts_forecast") as run:
        mlflow.log_metrics({f"wape_{k}": v for k, v in candidates.items()})
        mlflow.log_metric("champion_wape", candidates[champion])
        mlflow.log_param("champion", champion)
        mlflow.log_param("horizon_days", HORIZON)
        if champion == "lightgbm_global":
            # Two-step: log to the run, then explicitly register + capture the
            # version. The one-step `registered_model_name=` can create the
            # model container without an attached version on UC in a Job.
            info = mlflow.lightgbm.log_model(
                model, name="model", input_example=train_df[feature_cols].head(2)
            )
            mv = mlflow.register_model(info.model_uri, model_name)
            champion_version = mv.version
            ok(f"Registered LightGBM champion to UC: {model_name} v{mv.version}")
        else:
            warn(f"Champion is a baseline ({champion}); logging metrics only. "
                 "LightGBM is registered when it wins on WAPE.")
except Exception as exc:  # noqa: BLE001
    warn(f"MLflow/UC registry step degraded: {exc}")

# COMMAND ----------

# MAGIC %md ### Batch forecast → gold.demand_forecasts

# COMMAND ----------

# Forecast the next HORIZON days per SKU using the champion (LightGBM path shown;
# baselines produce a flat per-SKU forecast).
future_rows = []
last_day = len(date_index)
for sku, series in frames:
    if champion == "lightgbm_global":
        feats = make_lag_features(series)
        i = len(series) - 1
        base = {k: feats[k][i] for k in feats}
        for h in range(HORIZON):
            d = date_index[-1] + pd.Timedelta(days=h + 1)
            x = pd.DataFrame([{**base, "day_idx": last_day + h,
                               "dow": d.dayofweek, "month": d.month}])[feature_cols]
            yhat = float(np.clip(model.predict(x)[0], 0, None))
            future_rows.append({"sku_id": int(sku), "forecast_date": d.date(),
                                "forecast_qty": round(yhat, 2), "model": champion})
    else:
        val = croston(series) if champion == "croston" else (
            tsb(series) if champion == "tsb" else float(seasonal_naive(series, 7, 1)[0]))
        for h in range(HORIZON):
            d = date_index[-1] + pd.Timedelta(days=h + 1)
            future_rows.append({"sku_id": int(sku), "forecast_date": d.date(),
                                "forecast_qty": round(float(val), 2), "model": champion})

fc_sdf = spark.createDataFrame(pd.DataFrame(future_rows))
fc_sdf.write.format("delta").mode("overwrite").option(
    "overwriteSchema", "true").saveAsTable(f"{gold}.demand_forecasts")
ok(f"Wrote {len(future_rows):,} forecast rows → gold.demand_forecasts "
   f"(champion={champion})")

# COMMAND ----------

# MAGIC %md ### (Optional) serve the champion
# MAGIC If LightGBM won, expose it via a serving endpoint. Skipped for baselines.

# COMMAND ----------

if champion == "lightgbm_global" and champion_version:
    try:
        from databricks.sdk import WorkspaceClient

        from pil_workshop import dbx_api
        wc = WorkspaceClient()
        # Serve the exact version we just registered (not a hardcoded "1").
        dbx_api.ensure_model_serving_endpoint(
            "pil-spare-parts-forecaster", model_name, str(champion_version),
            client=wc)
        ok(f"Serving endpoint requested: pil-spare-parts-forecaster "
           f"(model v{champion_version})")
    except Exception as exc:  # noqa: BLE001
        warn(f"Serving endpoint step skipped: {exc}")
elif champion == "lightgbm_global":
    warn("LightGBM won but no model version was registered; skipping serving. "
         "See the MLflow/UC registry warning above.")

print("\nDashboard tip: add a forecast-vs-actual line to the Commercial page "
      "using gold.demand_forecasts joined to recent consumption.")

dbutils.notebook.exit(f"11 complete · champion={champion} wape={candidates[champion]:.3f}")
