"""Build a real Databricks multi-task Job for the workshop setup pipeline.

Instead of running steps in-process with ``dbutils.notebook.run``, this creates
a persistent, schedulable **Job/Workflow**: one task per setup notebook (01–12)
wired into a dependency DAG, running on **serverless** compute, with a **daily**
cron schedule. Creation is idempotent (create-or-update by job name).

Used by ``setup/00_setup_all.py`` (which resolves its own workspace folder) and
by local SDK scripts (which pass an explicit ``notebook_root``).

Docs: https://docs.databricks.com/api/workspace/jobs/create
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .utils import get_logger

LOG = get_logger("pil_workshop.job_builder")

JOB_NAME = "PIL Workshop — Daily Setup"
DEFAULT_TIMEZONE = "Asia/Singapore"  # southeastasia workshop
# 03:00 daily, Quartz format: sec min hour day-of-month month day-of-week
DEFAULT_CRON = "0 0 3 * * ?"

# Serverless notebook tasks share one environment; a few notebooks %pip-install
# their own heavy deps (reportlab/Pillow/lightgbm/ortools), so the base env is
# intentionally light. PyYAML is needed by 06 at import time; openai is the
# OpenAI-compatible client used by 08's governed vision path (llm.chat) and is
# not preinstalled on serverless.
ENVIRONMENT_KEY = "pil_env"
ENVIRONMENT_DEPS = ["PyYAML", "openai"]


@dataclass(frozen=True)
class StepDef:
    """One pipeline step → one Job task."""

    key: str          # task_key (also the skip token)
    notebook: str     # notebook file name (no .py extension in workspace)
    depends_on: tuple[str, ...] = ()
    timeout: int = 0  # 0 = no per-task timeout
    passes_scale: bool = True


# The DAG. Mostly linear (each stage needs the previous), but the two ML
# notebooks (11, 12) can run in parallel after silver/gold exist, and the app
# path (07→08→09→10) is its own chain after gold.
STEPS: tuple[StepDef, ...] = (
    StepDef("01_catalog", "01_create_catalog_schemas"),
    StepDef("01b_gateway", "01b_ai_gateway_setup", ("01_catalog",)),
    StepDef("02_data", "02_generate_synthetic_data", ("01_catalog",), timeout=3600),
    StepDef("03_silver", "03_build_silver", ("02_data",), timeout=3600),
    StepDef("04_gold", "04_build_gold", ("03_silver", "01b_gateway")),
    StepDef("05_dashboard", "05_create_dashboard", ("04_gold",)),
    StepDef("06_genie", "06_create_genie_space", ("04_gold",)),
    StepDef("07_unstructured", "07_generate_invoices_and_images", ("01_catalog",), timeout=2400),
    StepDef("08_agent_bricks", "08_agent_bricks_setup", ("07_unstructured", "04_gold")),
    StepDef("09_lakebase", "09_lakebase_setup", ("08_agent_bricks",)),
    StepDef("10_app", "10_deploy_app", ("09_lakebase",)),
    StepDef("11_forecasting", "11_ml_forecasting", ("03_silver",), timeout=3600),
    StepDef("12_route_opt", "12_ml_route_optimization", ("03_silver",)),
)


def _notebook_path(notebook_root: str, notebook: str) -> str:
    """Workspace path to a setup notebook (no .py suffix in the workspace)."""
    root = notebook_root.rstrip("/")
    return f"{root}/{notebook}"


def build_job_settings(
    notebook_root: str,
    *,
    catalog: str,
    scale: str,
    warehouse_id: str | None = None,
    timezone: str = DEFAULT_TIMEZONE,
    cron: str = DEFAULT_CRON,
    paused: bool = False,
) -> Any:
    """Construct a ``JobSettings`` for the full setup pipeline.

    ``notebook_root`` is the workspace folder holding the setup notebooks
    (e.g. ``/Workspace/Users/me/pil-databricks-workshop/setup``).
    """
    from databricks.sdk.service import compute as c
    from databricks.sdk.service import jobs as j

    base_params = {"catalog": catalog, "scale": scale}
    if warehouse_id:
        base_params["warehouse_id"] = warehouse_id

    tasks: list[Any] = []
    for step in STEPS:
        params = dict(base_params) if step.passes_scale else {"catalog": catalog}
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

    schedule = j.CronSchedule(
        quartz_cron_expression=cron,
        timezone_id=timezone,
        pause_status=j.PauseStatus.PAUSED if paused else j.PauseStatus.UNPAUSED,
    )

    return j.JobSettings(
        name=JOB_NAME,
        tasks=tasks,
        environments=[environment],
        schedule=schedule,
        max_concurrent_runs=1,
        queue=j.QueueSettings(enabled=True),
        tags={"project": "pil_workshop", "managed_by": "00_setup_all"},
    )


def find_job_id(client: Any, name: str = JOB_NAME) -> int | None:
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
    catalog: str,
    scale: str,
    warehouse_id: str | None = None,
    timezone: str = DEFAULT_TIMEZONE,
    cron: str = DEFAULT_CRON,
    paused: bool = False,
) -> int:
    """Create the setup Job (or reset an existing one by name); return job_id."""
    settings = build_job_settings(
        notebook_root, catalog=catalog, scale=scale, warehouse_id=warehouse_id,
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


def job_url(host: str, job_id: int) -> str:
    return f"{host.rstrip('/')}/jobs/{job_id}"
