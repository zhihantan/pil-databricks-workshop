"""Build the workshop's Databricks Jobs (Workflows) from the setup notebooks.

The pipeline is split into **two** persistent Jobs, both on serverless compute,
created idempotently (create-or-update by name):

* **PIL Workshop — Data Setup** — the recurring data + assets pipeline
  (01,01b,02,03,04,07,08,11,12). Scheduled **every 12 hours**; each run refreshes
  the medallion and appends an incremental slice (see notebook 02), so the
  dataset grows over time.
* **PIL Workshop — Consumables Setup** — the **one-time** deploy/serve surfaces
  (05 dashboard, 06 Genie space, 09 Lakebase, 10 app). Created **unscheduled
  (paused)**: run it once after Data Setup has produced the gold layer.

Used by ``setup/00_setup_all.py`` (which resolves its own workspace folder) and
by local SDK scripts (which pass an explicit ``notebook_root``).

Docs: https://docs.databricks.com/api/workspace/jobs/create
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .utils import get_logger

LOG = get_logger("pil_workshop.job_builder")

DATA_JOB_NAME = "PIL Workshop — Data Setup"
CONSUMABLES_JOB_NAME = "PIL Workshop — Consumables Setup"
# Back-compat alias (older scripts / the previous single-job name).
JOB_NAME = DATA_JOB_NAME
DEFAULT_TIMEZONE = "Asia/Singapore"  # southeastasia workshop
# EVERY 12 HOURS (00:00 and 12:00), Quartz: sec min hour day-of-month month
# day-of-week. The Data Setup job refreshes the pipeline and appends a new
# incremental slice (see notebook 02), so the dataset grows over time. Set to
# "0 0 3 * * ?" for once-daily, or "0 0 * * * ?" for hourly.
DEFAULT_CRON = "0 0 0/12 * * ?"

# Serverless notebook tasks share one environment; a few notebooks %pip-install
# their own heavy deps (reportlab/Pillow/lightgbm/ortools), so the base env is
# intentionally light. PyYAML is needed by 06 at import time; openai is the
# OpenAI-compatible client used by 08's governed vision path (llm.chat) and is
# not preinstalled on serverless.
ENVIRONMENT_KEY = "pil_env"
# The serverless base runtime ships older databricks-sdk (~0.49, no
# genie.create_space) and mlflow (~2.21). Pin newer ones so notebook 06 (Genie
# space creation) and 11 (UC model registration/serving) have the needed APIs.
ENVIRONMENT_DEPS = [
    "PyYAML",
    "openai",
    "databricks-sdk>=0.86",
    "mlflow>=2.16",
    "matplotlib>=3.7",  # notebooks 11/12 log plots as MLflow artifacts
]


@dataclass(frozen=True)
class StepDef:
    """One pipeline step → one Job task."""

    key: str          # task_key (also the skip token)
    notebook: str     # notebook file name (no .py extension in workspace)
    depends_on: tuple[str, ...] = ()
    timeout: int = 0  # 0 = no per-task timeout
    passes_scale: bool = True


# --- Job 1: DATA SETUP (recurring, 12-hourly) ------------------------------
# The data + assets pipeline. Linear where each stage needs the previous; the
# unstructured/agent chain (07→08) and the two ML notebooks (11,12) branch off
# after their inputs (01/03/04) exist.
DATA_STEPS: tuple[StepDef, ...] = (
    StepDef("01_catalog", "01_create_catalog_schemas"),
    StepDef("01b_gateway", "01b_ai_gateway_setup", ("01_catalog",)),
    StepDef("02_data", "02_generate_synthetic_data", ("01_catalog",), timeout=3600),
    StepDef("03_silver", "03_build_silver", ("02_data",), timeout=3600),
    StepDef("04_gold", "04_build_gold", ("03_silver", "01b_gateway")),
    StepDef("07_unstructured", "07_generate_invoices_and_images", ("01_catalog",), timeout=2400),
    StepDef("08_agent_bricks", "08_agent_bricks_setup", ("07_unstructured", "04_gold")),
    StepDef("11_forecasting", "11_ml_forecasting", ("03_silver",), timeout=3600),
    StepDef("12_route_opt", "12_ml_route_optimization", ("03_silver",)),
)

# --- Job 2: CONSUMABLES SETUP (one-time, unscheduled) ----------------------
# The deploy/serve-once surfaces. These read the gold layer produced by the Data
# Setup job, so run Data Setup FIRST. depends_on here references only tasks
# WITHIN this job (a Databricks task cannot depend on another job's task): the
# cross-job edges 05/06→04_gold and 09→08_agent_bricks are intentionally dropped
# — the notebooks are idempotent and fail-soft if an input isn't ready yet.
CONSUMABLES_STEPS: tuple[StepDef, ...] = (
    StepDef("05_dashboard", "05_create_dashboard", passes_scale=False),
    StepDef("06_genie", "06_create_genie_space", passes_scale=False),
    StepDef("09_lakebase", "09_lakebase_setup", passes_scale=False),
    StepDef("10_app", "10_deploy_app", ("09_lakebase",), passes_scale=False),
)

# Back-compat: some callers imported STEPS (the old single pipeline).
STEPS: tuple[StepDef, ...] = DATA_STEPS + CONSUMABLES_STEPS


def _notebook_path(notebook_root: str, notebook: str) -> str:
    """Workspace path to a setup notebook (no .py suffix in the workspace)."""
    root = notebook_root.rstrip("/")
    return f"{root}/{notebook}"


def build_job_settings(
    notebook_root: str,
    *,
    name: str,
    steps: tuple[StepDef, ...],
    catalog: str,
    scale: str,
    warehouse_id: str | None = None,
    managed_location: str | None = None,
    increment_days: float | None = None,
    timezone: str = DEFAULT_TIMEZONE,
    cron: str | None = DEFAULT_CRON,
    paused: bool = False,
) -> Any:
    """Construct a ``JobSettings`` for a set of pipeline ``steps``.

    ``notebook_root`` is the workspace folder holding the setup notebooks
    (e.g. ``/Workspace/Users/me/pil-databricks-workshop/setup``). ``name`` is the
    job's display name; ``steps`` is the ordered task list (``DATA_STEPS`` or
    ``CONSUMABLES_STEPS``). ``cron=None`` builds an **unscheduled** job (used for
    the one-time Consumables job).

    ``managed_location`` (optional) is passed to the catalog task only; set it
    for Azure "Default Storage" metastores that have no storage root, where a
    plain ``CREATE CATALOG`` fails and a ``MANAGED LOCATION`` is required.

    ``increment_days`` (optional) is passed to the data-generation task only; it
    sizes the incremental slice appended on each recurring run (see notebook 02).
    ``None`` leaves the notebook's own default in effect.
    """
    from databricks.sdk.service import compute as c
    from databricks.sdk.service import jobs as j

    base_params = {"catalog": catalog, "scale": scale}
    if warehouse_id:
        base_params["warehouse_id"] = warehouse_id

    tasks: list[Any] = []
    for step in steps:
        params = dict(base_params) if step.passes_scale else {"catalog": catalog}
        # Only the catalog-creation task understands managed_location.
        if managed_location and step.key == "01_catalog":
            params["managed_location"] = managed_location
        # Only the data-generation task understands increment_days.
        if increment_days is not None and step.key == "02_data":
            params["increment_days"] = str(increment_days)
        task = j.Task(
            task_key=step.key,
            description=f"PIL setup: {step.notebook}",
            depends_on=[j.TaskDependency(task_key=d) for d in step.depends_on] or None,
            environment_key=ENVIRONMENT_KEY,
            notebook_task=j.NotebookTask(
                notebook_path=_notebook_path(notebook_root, step.notebook),
                base_parameters=params,
                source=j.Source.WORKSPACE,
            ),
            timeout_seconds=step.timeout or None,
            max_retries=1,
            min_retry_interval_millis=30_000,
        )
        tasks.append(task)

    environment = j.JobEnvironment(
        environment_key=ENVIRONMENT_KEY,
        spec=c.Environment(
            environment_version="3", dependencies=list(ENVIRONMENT_DEPS)
        ),
    )

    schedule = None
    if cron:
        schedule = j.CronSchedule(
            quartz_cron_expression=cron,
            timezone_id=timezone,
            pause_status=j.PauseStatus.PAUSED if paused else j.PauseStatus.UNPAUSED,
        )

    return j.JobSettings(
        name=name,
        tasks=tasks,
        environments=[environment],
        schedule=schedule,
        max_concurrent_runs=1,
        queue=j.QueueSettings(enabled=True),
        tags={"project": "pil_workshop", "managed_by": "00_setup_all"},
    )


def find_job_id(client: Any, name: str = DATA_JOB_NAME) -> int | None:
    """Return the id of an existing job with ``name``, or None."""
    try:
        for job in client.jobs.list(name=name):
            if job.settings and job.settings.name == name:
                return job.job_id
    except Exception as exc:  # noqa: BLE001
        LOG.warning("Listing jobs failed: %s", exc)
    return None


def create_or_update_job(
    client: Any,
    notebook_root: str,
    *,
    name: str,
    steps: tuple[StepDef, ...],
    catalog: str,
    scale: str,
    warehouse_id: str | None = None,
    managed_location: str | None = None,
    increment_days: float | None = None,
    timezone: str = DEFAULT_TIMEZONE,
    cron: str | None = DEFAULT_CRON,
    paused: bool = False,
) -> int:
    """Create a Job (or reset an existing one by ``name``); return job_id."""
    settings = build_job_settings(
        notebook_root, name=name, steps=steps, catalog=catalog, scale=scale,
        warehouse_id=warehouse_id, managed_location=managed_location,
        increment_days=increment_days,
        timezone=timezone, cron=cron, paused=paused,
    )
    existing = find_job_id(client, settings.name)
    if existing is not None:
        # Full reset so re-runs pick up notebook/DAG changes.
        client.jobs.reset(job_id=existing, new_settings=settings)
        LOG.info("Reset existing job %s (%s).", existing, settings.name)
        return existing

    created = client.jobs.create(
        name=settings.name,
        tasks=settings.tasks,
        environments=settings.environments,
        schedule=settings.schedule,
        max_concurrent_runs=settings.max_concurrent_runs,
        queue=settings.queue,
        tags=settings.tags,
    )
    LOG.info("Created job %s (%s).", created.job_id, settings.name)
    return created.job_id


def ensure_schedule_unpaused(client: Any, job_id: int) -> bool:
    """Verify job ``job_id``'s schedule is UNPAUSED; self-heal if it is paused.

    ``create_or_update_job`` already writes the schedule as UNPAUSED, but this
    reads the job BACK to *prove* it (and re-unpauses if someone manually paused
    it between setup runs). Returns ``True`` when the schedule is confirmed
    active, ``False`` if the job has no schedule to unpause.
    """
    from databricks.sdk.service import jobs as j

    sched = getattr(client.jobs.get(job_id=job_id).settings, "schedule", None)
    if sched is None:
        LOG.warning("Job %s has no schedule to unpause.", job_id)
        return False
    if sched.pause_status != j.PauseStatus.UNPAUSED:
        LOG.info("Job %s schedule was %s — unpausing.", job_id, sched.pause_status)
        client.jobs.update(
            job_id=job_id,
            new_settings=j.JobSettings(
                schedule=j.CronSchedule(
                    quartz_cron_expression=sched.quartz_cron_expression,
                    timezone_id=sched.timezone_id,
                    pause_status=j.PauseStatus.UNPAUSED,
                )
            ),
        )
        sched = client.jobs.get(job_id=job_id).settings.schedule
    return sched.pause_status == j.PauseStatus.UNPAUSED


def create_or_update_data_job(
    client: Any,
    notebook_root: str,
    *,
    catalog: str,
    scale: str,
    warehouse_id: str | None = None,
    managed_location: str | None = None,
    increment_days: float | None = None,
    timezone: str = DEFAULT_TIMEZONE,
    cron: str = DEFAULT_CRON,
    paused: bool = False,
) -> int:
    """Create/reset the recurring **Data Setup** job (scheduled 12-hourly).

    When ``paused`` is False (the default) the schedule is created UNPAUSED and
    then verified/enforced via :func:`ensure_schedule_unpaused`, so the job is
    live and running on its cron the moment setup finishes.

    ``increment_days`` (optional) sizes the incremental slice the data-generation
    task appends on each recurring run; ``None`` uses the notebook default.
    """
    job_id = create_or_update_job(
        client, notebook_root, name=DATA_JOB_NAME, steps=DATA_STEPS,
        catalog=catalog, scale=scale, warehouse_id=warehouse_id,
        managed_location=managed_location, increment_days=increment_days,
        timezone=timezone, cron=cron, paused=paused,
    )
    if cron and not paused:
        ensure_schedule_unpaused(client, job_id)
    return job_id


def create_or_update_consumables_job(
    client: Any,
    notebook_root: str,
    *,
    catalog: str,
    scale: str,
    warehouse_id: str | None = None,
    timezone: str = DEFAULT_TIMEZONE,
) -> int:
    """Create/reset the one-time **Consumables Setup** job (unscheduled).

    Built with ``cron=None`` so it has no schedule — run it once on demand after
    the Data Setup job has produced the gold layer it consumes.
    """
    return create_or_update_job(
        client, notebook_root, name=CONSUMABLES_JOB_NAME, steps=CONSUMABLES_STEPS,
        catalog=catalog, scale=scale, warehouse_id=warehouse_id,
        managed_location=None, timezone=timezone, cron=None, paused=False,
    )


def job_url(host: str, job_id: int) -> str:
    return f"{host.rstrip('/')}/jobs/{job_id}"
