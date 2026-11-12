"""Static dashboard: `python -m tracker report` builds dashboard/index.html from the Sheet (or runs/)."""
from .data import RunInputs, build_report, load_runs, load_sheet, real_checks
from .render import render

__all__ = ["RunInputs", "build_report", "load_runs", "load_sheet", "real_checks", "render"]
