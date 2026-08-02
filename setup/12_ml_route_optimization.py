# Databricks notebook source
# MAGIC %md
# MAGIC # 12 · ML — Route & Network Optimization (OR-Tools)
# MAGIC
# MAGIC Two classic operations-research problems for a liner:
# MAGIC
# MAGIC 1. **Empty-container repositioning** — a **min-cost flow** across the 60 ports
# MAGIC    given each port's empty-container imbalance (surplus/deficit) and per-lane
# MAGIC    unit repositioning costs → recommended moves + estimated savings →
# MAGIC    `gold.repositioning_plan`.
# MAGIC 2. **Drayage / last-mile** — a small **VRPTW** (vehicle routing with time
# MAGIC    windows) around Singapore, solved with OR-Tools CP-SAT routing.
# MAGIC
# MAGIC Solvers live in `pil_workshop.ml.optimization` (unit-tested off-platform).

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

# MAGIC %pip install ortools

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
from pil_workshop.ml import build_min_cost_flow, solve_min_cost_flow, solve_vrptw
from pil_workshop.utils import banner, ok, safe_identifier, summary_table

dbutils.widgets.text("catalog", config.DEFAULT_CATALOG, "Catalog name")
CATALOG = safe_identifier(dbutils.widgets.get("catalog") or config.DEFAULT_CATALOG)
silver = f"`{CATALOG}`.`{config.SILVER}`"
gold = f"`{CATALOG}`.`{config.GOLD}`"

banner("12 · Route & network optimization (OR-Tools)")

# COMMAND ----------

# MAGIC %md ### Derive empty-container imbalance per port
# MAGIC Net imbalance ≈ inbound laden discharges minus outbound loadings. We derive
# MAGIC a deterministic imbalance from shipments per POD vs POL so the flow problem
# MAGIC is realistic and reproducible.

# COMMAND ----------

imbalance = spark.sql(f"""
    WITH inbound AS (
        SELECT pod_port_id AS port_id, COUNT(*) AS in_cnt
        FROM {silver}.shipments GROUP BY pod_port_id
    ),
    outbound AS (
        SELECT pol_port_id AS port_id, COUNT(*) AS out_cnt
        FROM {silver}.shipments GROUP BY pol_port_id
    )
    SELECT p.port_id, p.port_name, p.region,
           COALESCE(i.in_cnt,0) - COALESCE(o.out_cnt,0) AS net_imbalance
    FROM {silver}.ports p
    LEFT JOIN inbound i ON p.port_id = i.port_id
    LEFT JOIN outbound o ON p.port_id = o.port_id
""").toPandas()

# Scale imbalance down to a tractable number of container "units" to move.
imbalance["units"] = (imbalance["net_imbalance"] / 20).round().astype(int)
surplus = imbalance[imbalance["units"] > 0]
deficit = imbalance[imbalance["units"] < 0]
print(f"Ports with surplus: {len(surplus)}, deficit: {len(deficit)}")
print(f"Total surplus units: {surplus['units'].sum()}, "
      f"deficit units: {deficit['units'].sum()}")

# COMMAND ----------

# MAGIC %md ### Build & solve the min-cost flow
# MAGIC Balance total supply, define per-lane unit cost proportional to distance,
# MAGIC and solve for the cheapest repositioning plan.

# COMMAND ----------

# Balance the net imbalance to sum to zero (add slack to the largest surplus).
supplies = {int(r.port_id): int(r.units) for r in imbalance.itertuples() if r.units != 0}
total = sum(supplies.values())
if total != 0 and supplies:
    # Nudge the single largest-magnitude node to zero-sum.
    k = max(supplies, key=lambda x: abs(supplies[x]))
    supplies[k] -= total

# Unit costs: allow flow from every surplus port to every deficit port, cost by
# great-circle distance (cheap proxy = |lat|+|lon| difference scaled).
coords = {int(r.port_id): (r.region,) for r in imbalance.itertuples()}
ports_pd = spark.table(f"{silver}.ports").select(
    "port_id", "latitude", "longitude").toPandas()
latlon = {int(r.port_id): (r.latitude, r.longitude) for r in ports_pd.itertuples()}


def _dist_cost(a: int, b: int) -> float:
    (la, lo), (lb, lob) = latlon[a], latlon[b]
    return 50 + 8 * (abs(la - lb) + abs(lo - lob))  # base + distance proxy


unit_costs = {}
sup_nodes = [n for n, v in supplies.items() if v > 0]
def_nodes = [n for n, v in supplies.items() if v < 0]
for s in sup_nodes:
    for d in def_nodes:
        unit_costs[(s, d)] = _dist_cost(s, d)

if sup_nodes and def_nodes:
    spec = build_min_cost_flow(supplies, unit_costs)
    result = solve_min_cost_flow(spec)
    ok(f"Min-cost flow: {result['status']} · total cost ${result['total_cost']:,}")

    name_by_id = {int(r.port_id): r.port_name for r in imbalance.itertuples()}
    plan_rows = [{
        "from_port_id": u, "from_port": name_by_id.get(u, str(u)),
        "to_port_id": v, "to_port": name_by_id.get(v, str(v)),
        "containers": flow, "cost_usd": cost,
    } for (u, v, flow, cost) in result["flows"]]

    # Estimated savings vs. a naive "ship everything from the single biggest
    # surplus" strategy (a common baseline).
    naive_cost = sum(
        r["containers"] * _dist_cost(sup_nodes[0], r["to_port_id"]) for r in plan_rows
    )
    savings = max(0, naive_cost - result["total_cost"])
    print(f"Estimated savings vs naive single-hub plan: ${savings:,.0f}")

    if plan_rows:
        plan_sdf = spark.createDataFrame(pd.DataFrame(plan_rows))
        plan_sdf.write.format("delta").mode("overwrite").option(
            "overwriteSchema", "true").saveAsTable(f"{gold}.repositioning_plan")
        ok(f"Wrote {len(plan_rows)} moves → gold.repositioning_plan")
        banner("Top repositioning moves", char="-")
        print(summary_table(
            sorted(plan_rows, key=lambda r: -r["containers"])[:8],
            ["from_port", "to_port", "containers", "cost_usd"]))
else:
    ok("Network already balanced — no repositioning needed (rare with this data).")

# COMMAND ----------

# MAGIC %md ### VRPTW drayage demo (Singapore area)
# MAGIC A small vehicle-routing problem with time windows for last-mile container
# MAGIC delivery from the Singapore depot to 6 customer stops.

# COMMAND ----------

# Depot (0) + 6 stops; distances in minutes (symmetric), time windows in minutes.
rng = np.random.default_rng(config.SEED)
n_stops = 7
pts = rng.integers(0, 60, size=(n_stops, 2))
dm = [[int(abs(pts[i][0] - pts[j][0]) + abs(pts[i][1] - pts[j][1])) * 3
       for j in range(n_stops)] for i in range(n_stops)]
time_windows = [(0, 480)] + [(int(rng.integers(0, 180)),
                              int(rng.integers(240, 480))) for _ in range(n_stops - 1)]

vrptw = solve_vrptw(dm, time_windows, num_vehicles=3, depot=0)
ok(f"VRPTW: {vrptw['status']} · {len(vrptw['routes'])} routes · "
   f"total time {vrptw['total_time']} min")
for r in vrptw["routes"]:
    print(f"  Vehicle {r['vehicle']}: stops {r['stops']} ({r['route_time']} min)")

print("\nStretch goal: expose gold.repositioning_plan in the app as a 'Network' "
      "page, and let planners accept/reject recommended moves into Lakebase.")

dbutils.notebook.exit("12 complete · repositioning plan + VRPTW drayage demo")
