"""Static dashboard: `python -m tracker report` builds dashboard/index.html from runs/."""
from .data import RunInputs, build_report, load_runs
from .render import render

__all__ = ["RunInputs", "build_report", "load_runs", "render"]
