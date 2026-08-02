"""PIL Databricks App — FastAPI backend.

Two modules over one API: invoice review (Lakebase-backed queue + decisions) and
container inspections (governed vision-endpoint analysis + work orders). All
model calls route through the same Unity-AI-Gateway-governed FMAPI endpoints as
the notebooks, imported from ``pil_workshop.llm``.
"""

__version__ = "1.0.0"
