"""Central Foundation Model API (FMAPI) access — the ONLY place model
endpoint names live in this repository.

Every LLM call in the workshop — notebook ``ai_query``/``ai_parse_document``
pipelines, Agent Bricks fallbacks, and the FastAPI app backend — resolves its
endpoint through this module. That guarantees:

* All traffic hits the *same* workspace serving endpoints, so it shows up in
  the same Unity AI Gateway usage tables (dashboard Page 4).
* There are **no external provider SDKs, hosts, or API keys** anywhere — the
  ``openai`` client here is only ever pointed at the workspace serving base
  URL (``https://<host>/serving-endpoints``), never at ``api.openai.com``.
* Region behaviour for ``southeastasia`` is handled in one place: if a
  preferred model is not served in-region we fall back to the best available
  local model and log it loudly (never silently switch).

Design notes
------------
Constants below are the *preferred* Databricks-hosted FMAPI (pay-per-token)
endpoint names. Because availability differs by region/workspace,
:func:`resolve_endpoints` discovers what is actually served and picks the best
match, honouring env/widget overrides. Import the resolved values — do not
hardcode endpoint names elsewhere.

Docs:
* Foundation Model APIs: https://docs.databricks.com/en/machine-learning/foundation-model-apis/index.html
* Query with the OpenAI client: https://docs.databricks.com/en/machine-learning/foundation-models/query-foundation-model-apis.html
* Unity (Mosaic) AI Gateway: https://docs.databricks.com/en/ai-gateway/index.html
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any

from .utils import get_logger

LOG = get_logger("pil_workshop.llm")

# ---------------------------------------------------------------------------
# Preferred pay-per-token FMAPI endpoint names (Databricks-hosted).
# Ordered by preference; resolution picks the first that is actually served.
# Override any of these with PIL_TEXT_ENDPOINT / PIL_VISION_ENDPOINT /
# PIL_EMBEDDING_ENDPOINT (env) or the matching notebook widget.
# ---------------------------------------------------------------------------
# Ordered current models first. FMAPI model names change over time and older
# ones get DEPRECATED while still appearing in the endpoint listing (they return
# HTTP 400 on call), so lead with current families and keep older names only as
# late fallbacks. `resolve_endpoints` additionally skips names known-deprecated.
PREFERRED_TEXT_ENDPOINTS: tuple[str, ...] = (
    "databricks-claude-sonnet-4-5",
    "databricks-claude-sonnet-4-6",
    "databricks-claude-sonnet-5",
    "databricks-llama-4-maverick",
    "databricks-meta-llama-3-3-70b-instruct",
    "databricks-gpt-oss-120b",
    "databricks-claude-sonnet-4",  # legacy fallback (may be deprecated)
)
PREFERRED_VISION_ENDPOINTS: tuple[str, ...] = (
    "databricks-claude-sonnet-4-5",
    "databricks-claude-sonnet-4-6",
    "databricks-claude-sonnet-5",
    "databricks-gemini-2-5-pro",
    "databricks-llama-4-maverick",
    "databricks-claude-sonnet-4",  # legacy fallback (may be deprecated)
)

# Endpoint names that are listed but no longer accept calls on some workspaces.
# resolve_endpoints skips these unless explicitly requested via env override.
KNOWN_DEPRECATED_ENDPOINTS: frozenset[str] = frozenset({
    "databricks-claude-sonnet-4",
    "databricks-claude-3-7-sonnet",
    "databricks-meta-llama-3-1-70b-instruct",
})
PREFERRED_EMBEDDING_ENDPOINTS: tuple[str, ...] = (
    "databricks-gte-large-en",
    "databricks-bge-large-en",
)

# Env/widget override keys.
ENV_TEXT = "PIL_TEXT_ENDPOINT"
ENV_VISION = "PIL_VISION_ENDPOINT"
ENV_EMBEDDING = "PIL_EMBEDDING_ENDPOINT"

# Guardrail: any of these substrings appearing in a resolved base URL means
# something is misconfigured and traffic could leave the governed gateway.
_FORBIDDEN_HOSTS = (
    "api.openai.com",
    "api.anthropic.com",
    "generativelanguage.googleapis.com",
    "api.mistral.ai",
    "api.cohere.ai",
    "api.cohere.com",
)


@dataclass(frozen=True)
class ResolvedEndpoints:
    """The endpoints actually chosen for this workspace/region."""

    text: str
    vision: str
    embedding: str | None
    region: str
    notes: list[str]

    def as_dict(self) -> dict[str, Any]:
        return {
            "text": self.text,
            "vision": self.vision,
            "embedding": self.embedding,
            "region": self.region,
            "notes": list(self.notes),
        }


def _list_served_endpoint_names(client: Any | None) -> set[str]:
    """Return the set of serving-endpoint names available in this workspace."""
    if client is None:
        try:  # pragma: no cover - requires platform auth
            from databricks.sdk import WorkspaceClient

            client = WorkspaceClient()
        except Exception as exc:  # noqa: BLE001
            LOG.warning("Could not create WorkspaceClient to list endpoints: %s", exc)
            return set()
    try:  # pragma: no cover - requires platform auth
        return {e.name for e in client.serving_endpoints.list() if e.name}
    except Exception as exc:  # noqa: BLE001
        LOG.warning("Listing serving endpoints failed: %s", exc)
        return set()


def _pick(preferred: tuple[str, ...], available: set[str], kind: str, notes: list[str]) -> str:
    """Pick the first preferred endpoint that is available, else degrade.

    Skips names in KNOWN_DEPRECATED_ENDPOINTS (listed but 400 on call) unless
    nothing else is available.
    """
    for name in preferred:
        if name in available and name not in KNOWN_DEPRECATED_ENDPOINTS:
            return name
    # Allow a deprecated preferred name only if nothing current is served.
    for name in preferred:
        if name in available:
            notes.append(f"Using possibly-deprecated {kind} endpoint '{name}' "
                         "(no current model served); set PIL_*_ENDPOINT to override.")
            return name
    # Nothing preferred is served. Fall back to any databricks-* endpoint that
    # looks like the right modality, and note the region caveat.
    fallbacks = sorted(n for n in available if n.startswith("databricks-"))
    if fallbacks:
        chosen = fallbacks[0]
        notes.append(
            f"No preferred {kind} model served in-region; fell back to "
            f"'{chosen}'. On Azure {os.environ.get('PIL_REGION', 'southeastasia')} "
            "some FMAPI models require an account admin to enable cross-geography "
            "routing (Account Console → Settings → Feature enablement)."
        )
        return chosen
    # Last resort: return the top preference so callers get a clear 'not found'
    # error at query time rather than a silent None.
    notes.append(
        f"No serving endpoints discovered for {kind}; defaulting to "
        f"'{preferred[0]}'. Verify FMAPI is enabled in this workspace."
    )
    return preferred[0]


def resolve_endpoints(
    client: Any | None = None,
    *,
    region: str = "southeastasia",
    available: set[str] | None = None,
) -> ResolvedEndpoints:
    """Discover and choose the text/vision/embedding endpoints for this env.

    Honours env-var overrides first; otherwise picks the best available
    ``databricks-*`` endpoint per modality. Always returns a value and records
    any degradation in ``notes`` — never silently fails.

    ``available`` can be injected for unit tests; otherwise the workspace is
    queried via the SDK.
    """
    notes: list[str] = []
    served = available if available is not None else _list_served_endpoint_names(client)

    text_override = os.environ.get(ENV_TEXT)
    vision_override = os.environ.get(ENV_VISION)
    emb_override = os.environ.get(ENV_EMBEDDING)

    text = text_override or _pick(PREFERRED_TEXT_ENDPOINTS, served, "text", notes)
    vision = vision_override or _pick(PREFERRED_VISION_ENDPOINTS, served, "vision", notes)

    embedding: str | None
    if emb_override:
        embedding = emb_override
    elif any(n in served for n in PREFERRED_EMBEDDING_ENDPOINTS):
        embedding = _pick(PREFERRED_EMBEDDING_ENDPOINTS, served, "embedding", notes)
    else:
        embedding = None
        notes.append("No embedding endpoint served; embedding features are optional.")

    if text_override:
        notes.append(f"Text endpoint overridden via {ENV_TEXT}={text_override}.")
    if vision_override:
        notes.append(f"Vision endpoint overridden via {ENV_VISION}={vision_override}.")

    return ResolvedEndpoints(
        text=text, vision=vision, embedding=embedding, region=region, notes=notes
    )


# ---------------------------------------------------------------------------
# OpenAI-compatible client, pointed ONLY at the workspace serving endpoints.
# ---------------------------------------------------------------------------
def _workspace_host(client: Any | None) -> str:
    """Return the workspace host URL (no trailing slash).

    Order: DATABRICKS_HOST env → passed client's config → a freshly created
    WorkspaceClient's config (ambient auth inside notebooks/Jobs, where the env
    var is not set but the SDK still resolves the host).
    """
    host = os.environ.get("DATABRICKS_HOST", "").rstrip("/")
    if host:
        return host
    if client is not None:
        cfg = getattr(client, "config", None)
        if cfg is not None and getattr(cfg, "host", None):
            return str(cfg.host).rstrip("/")
    try:  # pragma: no cover - platform-only: resolve host from ambient SDK auth
        from databricks.sdk import WorkspaceClient

        cfg = WorkspaceClient().config
        if getattr(cfg, "host", None):
            return str(cfg.host).rstrip("/")
    except Exception:  # noqa: BLE001
        pass
    raise RuntimeError(
        "Workspace host is unknown. Inside a Databricks notebook this is "
        "ambient; elsewhere set DATABRICKS_HOST."
    )


def openai_base_url(client: Any | None = None) -> str:
    """Build the OpenAI-compatible base URL for this workspace's endpoints."""
    base = f"{_workspace_host(client)}/serving-endpoints"
    _assert_governed(base)
    return base


def _assert_governed(base_url: str) -> None:
    """Fail loudly if a base URL points anywhere but the workspace gateway."""
    lowered = base_url.lower()
    for bad in _FORBIDDEN_HOSTS:
        if bad in lowered:
            raise RuntimeError(
                f"Refusing to send model traffic to ungoverned host {bad!r}. "
                "All LLM calls must go through the workspace FMAPI serving "
                "endpoints governed by Unity AI Gateway."
            )


def get_openai_client(token: str | None = None, client: Any | None = None) -> Any:
    """Return an ``openai.OpenAI`` client bound to the workspace endpoints.

    ``token`` defaults to ``DATABRICKS_TOKEN`` (notebook/app ambient auth). The
    base URL is always the workspace ``/serving-endpoints`` route, so this is
    *not* a path to api.openai.com — it is the Databricks-hosted, gateway-
    governed FMAPI surface using an OpenAI-compatible wire format.
    """
    try:
        from openai import OpenAI
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError(
            "The 'openai' package provides the OpenAI-compatible client used to "
            "call Databricks FMAPI. Install it via requirements.txt."
        ) from exc

    api_key = token or os.environ.get("DATABRICKS_TOKEN")
    if not api_key:
        # Inside notebooks/apps the SDK mints a short-lived token. Note that
        # config.token is None under OAuth/SSO (common on serverless Jobs), so
        # fall back to config.authenticate(), which returns an Authorization
        # header for whatever auth the context has (OAuth, PAT, or ambient).
        try:  # pragma: no cover - platform-only
            from databricks.sdk import WorkspaceClient

            wc = client or WorkspaceClient()
            api_key = getattr(wc.config, "token", None)
            if not api_key:
                auth_header = wc.config.authenticate().get("Authorization", "")
                if auth_header.lower().startswith("bearer "):
                    api_key = auth_header.split(" ", 1)[1]
        except Exception as exc:  # noqa: BLE001
            raise RuntimeError(
                "No Databricks token available for FMAPI auth. Set "
                "DATABRICKS_TOKEN or run inside a Databricks context."
            ) from exc
        if not api_key:
            raise RuntimeError(
                "Could not obtain a Databricks token for FMAPI auth (config had "
                "no token and authenticate() returned no bearer)."
            )

    return OpenAI(api_key=api_key, base_url=openai_base_url(client))


def chat(
    messages: list[dict[str, Any]],
    *,
    endpoint: str,
    max_tokens: int = 1024,
    temperature: float = 0.0,
    response_format: dict[str, Any] | None = None,
    token: str | None = None,
    client: Any | None = None,
) -> str:
    """Send a chat completion to a governed FMAPI ``endpoint`` and return text.

    ``endpoint`` must be a workspace serving-endpoint name (e.g. the resolved
    :attr:`ResolvedEndpoints.text`). Supports structured output via
    ``response_format`` (JSON schema) where the model supports it.
    """
    oai = get_openai_client(token=token, client=client)
    kwargs: dict[str, Any] = {
        "model": endpoint,
        "messages": messages,
        "max_tokens": max_tokens,
        "temperature": temperature,
    }
    if response_format is not None:
        kwargs["response_format"] = response_format
    resp = oai.chat.completions.create(**kwargs)
    return resp.choices[0].message.content or ""


def sql_ai_query_snippet(endpoint: str, prompt_col: str = "prompt") -> str:
    """Return a SQL fragment calling ``ai_query`` against a governed endpoint.

    Kept here so SQL pipelines reference the same resolved endpoint name and
    never hardcode it inline. Example::

        SELECT ai_query('databricks-claude-sonnet-4', prompt) AS out FROM ...
    """
    return f"ai_query('{endpoint}', {prompt_col})"


# Convenience module-level cache for notebooks that want a one-liner.
_CACHED: ResolvedEndpoints | None = None


def endpoints(client: Any | None = None, refresh: bool = False) -> ResolvedEndpoints:
    """Return cached resolved endpoints, resolving once per process."""
    global _CACHED
    if _CACHED is None or refresh:
        _CACHED = resolve_endpoints(client)
    return _CACHED
