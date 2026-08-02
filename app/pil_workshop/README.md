# Vendored `pil_workshop` (app runtime copy)

**Source of truth:** `/src/pil_workshop`. These files are a mirror.

## Why this exists

Databricks Apps deploy **only the `app/` folder** — there is no pip build step
and the sibling `/src` directory is not shipped. The FastAPI backend imports a
few `pil_workshop` modules at runtime (invoice extraction, container vision,
Lakebase, FMAPI endpoint resolution), so those modules must live inside `app/`.

`uvicorn backend.main:app` runs with `app/` on `sys.path`, so `pil_workshop`
here is importable as a top-level package exactly like `backend`.
`backend/core/config._ensure_pil_workshop_importable()` also adds `app/` (and,
for local checkouts, `../src`) to `sys.path` as a fallback.

## Files (runtime import closure only)

| file              | why it's needed                                        |
| ----------------- | ------------------------------------------------------ |
| `agent_bricks.py` | invoice extraction + container-vision SQL/chat builders |
| `llm.py`          | resolves the governed FMAPI text/vision endpoints       |
| `lakebase.py`     | Lakebase Postgres connection helper                     |
| `dbx_api.py`      | dep of `lakebase`                                       |
| `utils.py`        | dep of `llm`/`dbx_api`                                  |
| `config.py`       | catalog/schema/volume names + palette                   |

`datagen`, `ml`, and the `*_build` notebook helpers are **not** vendored — the
app never imports them.

## Keeping in sync

When you change any of the above modules in `/src/pil_workshop`, re-copy them:

```bash
cd <repo root>
for f in __init__.py utils.py llm.py dbx_api.py lakebase.py agent_bricks.py config.py; do
  cp "src/pil_workshop/$f" "app/pil_workshop/$f"
done
```

Then rebuild is not required (pure Python), but re-sync + redeploy the app.
