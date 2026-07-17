"""
Tool registry for the ROI agent.

Tools are grouped into categories. On each turn, we route the user's message to
one or more categories (procedural memory) and only load those schemas — plus a
small always-on set for orientation.

Each tool takes a `spreadsheet_id` and any additional parameters. The dispatcher
resolves the spreadsheet, loads/caches its RoiAnalyzer, and executes the method.
"""
from __future__ import annotations

import json
import uuid
from typing import Any

import pandas as pd
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.db.models import Project, RoiRun, Spreadsheet
from backend.roi.analyzer import RoiAnalyzer
from backend.roi.windows import (
    WindowMode,
    WindowRequest,
    WindowSize,
    group_to_dict,
    run_parallel_requests,
)


# ---------------------------------------------------------------------------
# Tool schemas
# ---------------------------------------------------------------------------

def _sid() -> dict:
    return {"type": "string", "description": "Spreadsheet UUID from list_spreadsheets."}


def _date(desc: str) -> dict:
    return {"type": "string", "description": f"{desc} (YYYY-MM-DD)."}


ALWAYS_ON_TOOLS: list[dict] = [
    {
        "name": "list_spreadsheets",
        "description": "List all spreadsheets in this project with their id, filename, and available date range.",
        "input_schema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "get_run_results",
        "description": (
            "Fetch the full metrics of a previously computed RoiRun by id. Use this to cross-reference "
            "or compare with a fresh analysis. Prior runs are listed in the system context."
        ),
        "input_schema": {
            "type": "object",
            "properties": {"run_id": {"type": "string", "description": "RoiRun UUID."}},
            "required": ["run_id"],
        },
    },
    {
        "name": "get_available_business_units",
        "description": "List every business unit that appears in a spreadsheet.",
        "input_schema": {
            "type": "object",
            "properties": {"spreadsheet_id": _sid()},
            "required": ["spreadsheet_id"],
        },
    },
    {
        "name": "set_business_unit_filter",
        "description": (
            "Set the business unit filter for a SPECIFIC spreadsheet (persists across turns and reloads). "
            "All future metric calls on this spreadsheet honor it. Empty list clears the filter. "
            "After changing, offer to re-run the most recent analysis on this spreadsheet with the new filter."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "spreadsheet_id": _sid(),
                "business_units": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["spreadsheet_id", "business_units"],
        },
    },
    {
        "name": "set_analysis_context",
        "description": (
            "Set the active analysis context for a SPECIFIC spreadsheet (persists across turns and reloads). "
            "This is the default pre/post date range + window mode used when the user says "
            "'run the analysis' without specifying params. Each spreadsheet has its own context. "
            "After changing dates, offer to re-run the most recent analysis with the new range."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "spreadsheet_id": _sid(),
                "pre_start": _date("Pre-period start"),
                "pre_end": _date("Pre-period end"),
                "post_start": _date("Post-period start"),
                "post_end": _date("Post-period end"),
                "mode": {"type": "string", "enum": ["straight", "sliding"]},
                "window_size": {
                    "type": "string",
                    "enum": ["weekly", "biweekly", "monthly"],
                    "description": "Only required for mode='sliding'.",
                },
            },
            "required": ["spreadsheet_id", "pre_start", "pre_end", "post_start", "post_end", "mode"],
        },
    },
]


METRIC_TOOLS: list[dict] = [
    {
        "name": "get_total_sales",
        "description": "Sum of Jobs Estimate Sales Subtotal for the date range.",
        "input_schema": {
            "type": "object",
            "properties": {
                "spreadsheet_id": _sid(),
                "start": _date("Start date"),
                "end": _date("End date"),
            },
            "required": ["spreadsheet_id", "start", "end"],
        },
    },
    {
        "name": "get_completed_jobs",
        "description": "Count of jobs with a non-null Completion Date.",
        "input_schema": {
            "type": "object",
            "properties": {"spreadsheet_id": _sid(), "start": _date("Start"), "end": _date("End")},
            "required": ["spreadsheet_id", "start", "end"],
        },
    },
    {
        "name": "get_completed_opportunities",
        "description": "Count of completed jobs that were also opportunities.",
        "input_schema": {
            "type": "object",
            "properties": {"spreadsheet_id": _sid(), "start": _date("Start"), "end": _date("End")},
            "required": ["spreadsheet_id", "start", "end"],
        },
    },
    {
        "name": "get_sales_per_job",
        "description": "Total sales divided by completed jobs.",
        "input_schema": {
            "type": "object",
            "properties": {"spreadsheet_id": _sid(), "start": _date("Start"), "end": _date("End")},
            "required": ["spreadsheet_id", "start", "end"],
        },
    },
    {
        "name": "get_sales_per_opportunity",
        "description": "Total sales divided by completed opportunities.",
        "input_schema": {
            "type": "object",
            "properties": {"spreadsheet_id": _sid(), "start": _date("Start"), "end": _date("End")},
            "required": ["spreadsheet_id", "start", "end"],
        },
    },
    {
        "name": "get_blended_sales_avg_job",
        "description": "(tech-generated leads + total sales) / completed jobs. Primary ROI metric.",
        "input_schema": {
            "type": "object",
            "properties": {"spreadsheet_id": _sid(), "start": _date("Start"), "end": _date("End")},
            "required": ["spreadsheet_id", "start", "end"],
        },
    },
    {
        "name": "get_blended_sales_avg_opportunity",
        "description": "(tech-generated leads + total sales) / completed opportunities.",
        "input_schema": {
            "type": "object",
            "properties": {"spreadsheet_id": _sid(), "start": _date("Start"), "end": _date("End")},
            "required": ["spreadsheet_id", "start", "end"],
        },
    },
    {
        "name": "get_tech_generated_leads",
        "description": "Sum of Sales from Leads Created.",
        "input_schema": {
            "type": "object",
            "properties": {"spreadsheet_id": _sid(), "start": _date("Start"), "end": _date("End")},
            "required": ["spreadsheet_id", "start", "end"],
        },
    },
]


HIGH_VALUE_TOOLS: list[dict] = [
    {
        "name": "get_high_value_diagnostic_count",
        "description": (
            "Count of high-value diagnostic jobs in the range. "
            "method='job_type' uses the Job Type column; method='tag' uses the Tags column (fallback signal)."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "spreadsheet_id": _sid(),
                "start": _date("Start"),
                "end": _date("End"),
                "method": {"type": "string", "enum": ["job_type", "tag"]},
            },
            "required": ["spreadsheet_id", "start", "end", "method"],
        },
    },
    {
        "name": "get_high_value_maintenance_count",
        "description": (
            "Count of high-value maintenance jobs in the range. "
            "method='job_type' uses Job Type; method='tag' uses Tags."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "spreadsheet_id": _sid(),
                "start": _date("Start"),
                "end": _date("End"),
                "method": {"type": "string", "enum": ["job_type", "tag"]},
            },
            "required": ["spreadsheet_id", "start", "end", "method"],
        },
    },
]


CANCELLATION_TOOLS: list[dict] = [
    {
        "name": "get_total_cancellations",
        "description": "Count of jobs with a non-null Cancelled Date.",
        "input_schema": {
            "type": "object",
            "properties": {"spreadsheet_id": _sid(), "start": _date("Start"), "end": _date("End")},
            "required": ["spreadsheet_id", "start", "end"],
        },
    },
    {
        "name": "get_timing_cancellations",
        "description": "Count of cancellations classified as timing-related.",
        "input_schema": {
            "type": "object",
            "properties": {"spreadsheet_id": _sid(), "start": _date("Start"), "end": _date("End")},
            "required": ["spreadsheet_id", "start", "end"],
        },
    },
    {
        "name": "get_timing_cancellation_rate",
        "description": "Timing cancellations / total cancellations.",
        "input_schema": {
            "type": "object",
            "properties": {"spreadsheet_id": _sid(), "start": _date("Start"), "end": _date("End")},
            "required": ["spreadsheet_id", "start", "end"],
        },
    },
    {
        "name": "get_high_value_cancellation_rate",
        "description": (
            "Fraction of high-value jobs that were cancelled. "
            "method='job_type' or 'tag' — keep separate because tags are the fallback signal."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "spreadsheet_id": _sid(),
                "start": _date("Start"),
                "end": _date("End"),
                "method": {"type": "string", "enum": ["job_type", "tag"]},
            },
            "required": ["spreadsheet_id", "start", "end", "method"],
        },
    },
]


TECHNICIAN_TOOLS: list[dict] = [
    {
        "name": "list_technicians",
        "description": "List unique technicians assigned in the date range.",
        "input_schema": {
            "type": "object",
            "properties": {"spreadsheet_id": _sid(), "start": _date("Start"), "end": _date("End")},
            "required": ["spreadsheet_id", "start", "end"],
        },
    },
    {
        "name": "compare_technician_periods",
        "description": (
            "For each technician present in both pre and post periods, return jobs, high-value rate "
            "(by job_type and by tag), and blended sales average deltas."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "spreadsheet_id": _sid(),
                "pre_start": _date("Pre start"),
                "pre_end": _date("Pre end"),
                "post_start": _date("Post start"),
                "post_end": _date("Post end"),
            },
            "required": ["spreadsheet_id", "pre_start", "pre_end", "post_start", "post_end"],
        },
    },
    {
        "name": "rank_departed_technicians",
        "description": (
            "Return technicians present in the pre period but not in the post period, "
            "ranked by their pre-period blended sales average."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "spreadsheet_id": _sid(),
                "pre_start": _date("Pre start"),
                "pre_end": _date("Pre end"),
                "post_start": _date("Post start"),
                "post_end": _date("Post end"),
            },
            "required": ["spreadsheet_id", "pre_start", "pre_end", "post_start", "post_end"],
        },
    },
]


WINDOW_TOOLS: list[dict] = [
    {
        "name": "run_window_analysis",
        "description": (
            "Run a pre-vs-post comparison. mode='straight' does a single direct comparison; "
            "mode='sliding' slides a window of window_size across the pre range and pairs each with "
            "an offset-matched post window, ranking by BSA delta."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "spreadsheet_id": _sid(),
                "label": {"type": "string"},
                "mode": {"type": "string", "enum": ["straight", "sliding"]},
                "window_size": {
                    "type": "string",
                    "enum": ["weekly", "biweekly", "monthly"],
                    "description": "Required when mode='sliding'.",
                },
                "pre_start": _date("Pre start"),
                "pre_end": _date("Pre end"),
                "post_start": _date("Post start"),
                "post_end": _date("Post end"),
                "top_n": {"type": "integer", "default": 5},
            },
            "required": [
                "spreadsheet_id", "label", "mode",
                "pre_start", "pre_end", "post_start", "post_end",
            ],
        },
    },
]


TOOL_GROUPS: dict[str, list[dict]] = {
    "metrics": METRIC_TOOLS,
    "high_value": HIGH_VALUE_TOOLS,
    "cancellations": CANCELLATION_TOOLS,
    "technicians": TECHNICIAN_TOOLS,
    "windows": WINDOW_TOOLS,
}


# Keyword routing for procedural memory
_GROUP_KEYWORDS: dict[str, list[str]] = {
    "metrics": [
        "sales", "revenue", "job", "opportunit", "average", "avg", "bsa",
        "blended", "lead", "ticket",
    ],
    "high_value": [
        "high value", "high-value", "hv ", "diagnostic", "maintenance",
        "replacement", "upsell",
    ],
    "cancellations": [
        "cancel", "cancelled", "canceled", "cancellation", "timing", "no show",
    ],
    "technicians": [
        "technician", "tech ", "employee", "top performer", "departed",
        "left", "stayed", "who", "rank",
    ],
    "windows": [
        "window", "sliding", "compare", "comparison", "pre vs", "before and after",
        "pre and post", "period", "monthly", "weekly", "biweekly",
    ],
}


def route_tools_for_message(message: str) -> list[dict]:
    """Return the tool set to send Claude for a given user message."""
    lower = message.lower()
    selected = list(ALWAYS_ON_TOOLS)
    for group, keywords in _GROUP_KEYWORDS.items():
        if any(k in lower for k in keywords):
            selected.extend(TOOL_GROUPS[group])
    # If nothing matched, err on the side of loading metrics (most common)
    if len(selected) == len(ALWAYS_ON_TOOLS):
        selected.extend(TOOL_GROUPS["metrics"])
    return selected


# ---------------------------------------------------------------------------
# Dispatcher
# ---------------------------------------------------------------------------

class AnalyzerCache:
    """Per-request cache of RoiAnalyzer instances keyed by spreadsheet_id."""

    def __init__(self, loader):
        self._loader = loader  # async fn: (spreadsheet_id: str) -> RoiAnalyzer
        self._cache: dict[str, RoiAnalyzer] = {}

    async def get(self, spreadsheet_id: str) -> RoiAnalyzer:
        if spreadsheet_id not in self._cache:
            self._cache[spreadsheet_id] = await self._loader(spreadsheet_id)
        return self._cache[spreadsheet_id]

    def invalidate(self, spreadsheet_id: str) -> None:
        self._cache.pop(spreadsheet_id, None)


async def dispatch_tool(
    name: str,
    inputs: dict[str, Any],
    cache: AnalyzerCache,
    project_spreadsheets: list[dict],
    db: AsyncSession,
    project: Project,
) -> str:
    """
    Execute a tool call and return a string result to feed back to Claude.

    project_spreadsheets is the list of {id, filename, date_min, date_max} for
    the current project — used by list_spreadsheets without touching the DB again.
    """
    try:
        # Always-on tools
        if name == "list_spreadsheets":
            return json.dumps(project_spreadsheets, default=str)

        if name == "get_run_results":
            run = (await db.execute(
                select(RoiRun).where(
                    RoiRun.id == uuid.UUID(inputs["run_id"]),
                    RoiRun.project_id == project.id,
                )
            )).scalar_one_or_none()
            if not run:
                return f"Run {inputs['run_id']} not found in this project."
            return json.dumps({
                "id": str(run.id),
                "label": run.label,
                "mode": run.mode,
                "window_size": run.window_size,
                "pre_start": str(run.pre_start.date()),
                "pre_end": str(run.pre_end.date()),
                "post_start": str(run.post_start.date()),
                "post_end": str(run.post_end.date()),
                "metrics": run.metrics,
            }, default=str)

        sid = inputs["spreadsheet_id"]

        # Per-spreadsheet filter mutations — validate before persisting.
        if name == "set_business_unit_filter":
            spreadsheet = await _load_spreadsheet(db, project.id, sid)
            analyzer = await cache.get(sid)
            available = analyzer.available_business_units()
            resolved, problems = _resolve_business_units(inputs["business_units"], available)
            if resolved is None:
                return (
                    "Filter NOT persisted. Resolve the ambiguity with the user first, then call "
                    f"this tool again with exact strings. {' | '.join(problems)}"
                )
            spreadsheet.business_units = resolved
            db.add(spreadsheet)
            await db.flush()
            cache.invalidate(sid)
            display = resolved if resolved else "no filter (all units)"
            return f"Spreadsheet {sid} business unit filter set to: {display}. Persisted."

        if name == "set_analysis_context":
            spreadsheet = await _load_spreadsheet(db, project.id, sid)
            problems = _validate_analysis_context(inputs, spreadsheet)
            if problems:
                return (
                    "Analysis context NOT persisted. Fix the following and try again: "
                    f"{' | '.join(problems)}. "
                    f"Sheet range: {spreadsheet.date_min.date()} → {spreadsheet.date_max.date()}."
                )
            ctx = {
                "pre_start": inputs["pre_start"],
                "pre_end": inputs["pre_end"],
                "post_start": inputs["post_start"],
                "post_end": inputs["post_end"],
                "mode": inputs["mode"],
                "window_size": inputs.get("window_size"),
            }
            spreadsheet.active_context = ctx
            db.add(spreadsheet)
            await db.flush()
            return f"Spreadsheet {sid} analysis context set: {json.dumps(ctx)}. Persisted."

        analyzer = await cache.get(sid)

        if name == "get_available_business_units":
            return json.dumps(analyzer.available_business_units())

        # Metric tools
        start, end = inputs.get("start"), inputs.get("end")

        if name == "get_total_sales":
            return f"${analyzer.total_sales(start, end):,.2f}"
        if name == "get_completed_jobs":
            return str(analyzer.completed_jobs(start, end))
        if name == "get_completed_opportunities":
            return str(analyzer.completed_opportunities(start, end))
        if name == "get_sales_per_job":
            return f"${analyzer.sales_per_job(start, end):,.2f}"
        if name == "get_sales_per_opportunity":
            return f"${analyzer.sales_per_opportunity(start, end):,.2f}"
        if name == "get_blended_sales_avg_job":
            return f"${analyzer.blended_sales_average_job(start, end):,.2f}"
        if name == "get_blended_sales_avg_opportunity":
            return f"${analyzer.blended_sales_average_opportunity(start, end):,.2f}"
        if name == "get_tech_generated_leads":
            return f"${analyzer.tech_generated_leads(start, end):,.2f}"

        # High value
        if name == "get_high_value_diagnostic_count":
            fn = (
                analyzer.total_high_value_diagnostic_jobs_by_job_type
                if inputs["method"] == "job_type"
                else analyzer.total_high_value_diagnostic_jobs_by_tag
            )
            return str(fn(start, end))
        if name == "get_high_value_maintenance_count":
            fn = (
                analyzer.total_high_value_maintenance_jobs_by_job_type
                if inputs["method"] == "job_type"
                else analyzer.total_high_value_maintenance_jobs_by_tag
            )
            return str(fn(start, end))

        # Cancellations
        if name == "get_total_cancellations":
            return str(analyzer.total_cancellations(start, end))
        if name == "get_timing_cancellations":
            return str(analyzer.total_timing_cancellations(start, end))
        if name == "get_timing_cancellation_rate":
            return f"{analyzer.timing_cancellation_rate(start, end):.2%}"
        if name == "get_high_value_cancellation_rate":
            fn = (
                analyzer.high_value_cancellation_rate_by_job_type
                if inputs["method"] == "job_type"
                else analyzer.high_value_cancellation_rate_by_tag
            )
            return f"{fn(start, end):.2%}"

        # Technicians
        if name == "list_technicians":
            return json.dumps(analyzer.technicians(start, end))
        if name == "compare_technician_periods":
            return json.dumps(
                analyzer.compare_periods(
                    inputs["pre_start"], inputs["pre_end"],
                    inputs["post_start"], inputs["post_end"],
                ),
                default=str,
            )
        if name == "rank_departed_technicians":
            return json.dumps(
                analyzer.rank_departed_technicians(
                    inputs["pre_start"], inputs["pre_end"],
                    inputs["post_start"], inputs["post_end"],
                ),
                default=str,
            )

        # Windows
        if name == "run_window_analysis":
            request = WindowRequest(
                label=inputs["label"],
                mode=WindowMode(inputs["mode"]),
                pre_start=inputs["pre_start"],
                pre_end=inputs["pre_end"],
                post_start=inputs["post_start"],
                post_end=inputs["post_end"],
                window_size=WindowSize(inputs["window_size"]) if inputs.get("window_size") else None,
                top_n=inputs.get("top_n", 5),
            )
            groups = run_parallel_requests(analyzer, [request])
            metrics = [group_to_dict(g) for g in groups]

            run = RoiRun(
                id=uuid.uuid4(),
                project_id=project.id,
                spreadsheet_id=uuid.UUID(sid),
                label=inputs["label"],
                mode=inputs["mode"],
                window_size=inputs.get("window_size"),
                pre_start=pd.to_datetime(inputs["pre_start"]),
                pre_end=pd.to_datetime(inputs["pre_end"]),
                post_start=pd.to_datetime(inputs["post_start"]),
                post_end=pd.to_datetime(inputs["post_end"]),
                metrics={"results": metrics},
            )
            db.add(run)
            await db.flush()
            return json.dumps({"run_id": str(run.id), "results": metrics}, default=str)

        return f"Unknown tool: {name}"

    except (ValueError, KeyError) as e:
        return f"Error: {e}"


def _resolve_business_units(
    requested: list[str], available: list[str]
) -> tuple[list[str] | None, list[str]]:
    """
    Every requested business unit must EXACTLY match an available one. We allow
    case-insensitive canonicalization ('hvac demand' → 'HVAC Demand') as a courtesy,
    but we deliberately do NOT do fuzzy/semantic matching — 'HVAC' could plausibly
    mean 'HVAC Demand', 'HVAC-Georgetown Service', or 'HVAC Commercial', and only
    the user can disambiguate. The agent must call get_available_business_units,
    show the options to the user, and confirm before persisting.

    Returns (resolved | None, problems). resolved is None on any failure.
    Empty input → ([], []) which cleanly clears the filter.
    """
    if not requested:
        return [], []

    canonical: list[str] = []
    unmatched: list[str] = []
    lower_map = {a.lower(): a for a in available}

    for req in requested:
        if req in available:
            canonical.append(req)
        elif req.lower() in lower_map:
            canonical.append(lower_map[req.lower()])
        else:
            unmatched.append(req)

    if unmatched:
        return None, [
            f"No exact match for: {unmatched}. "
            f"Available business units: {available}. "
            "Do NOT guess — present these options to the user and ask which one(s) "
            "they mean, then call this tool again with exact strings."
        ]
    return canonical, []


def _validate_analysis_context(inputs: dict, spreadsheet: Spreadsheet) -> list[str]:
    """Check every date parses and falls within the sheet's range; check ordering + mode."""
    sheet_min = spreadsheet.date_min.date()
    sheet_max = spreadsheet.date_max.date()
    problems: list[str] = []
    parsed: dict[str, Any] = {}

    for field in ("pre_start", "pre_end", "post_start", "post_end"):
        val = inputs.get(field)
        try:
            d = pd.to_datetime(val).date()
        except (ValueError, TypeError):
            problems.append(f"{field}='{val}' is not a valid date (expected YYYY-MM-DD)")
            continue
        if d < sheet_min or d > sheet_max:
            problems.append(f"{field}={val} is outside the sheet's range")
            continue
        parsed[field] = d

    if "pre_start" in parsed and "pre_end" in parsed and parsed["pre_start"] > parsed["pre_end"]:
        problems.append(f"pre_start ({parsed['pre_start']}) must be before pre_end ({parsed['pre_end']})")
    if "post_start" in parsed and "post_end" in parsed and parsed["post_start"] > parsed["post_end"]:
        problems.append(f"post_start ({parsed['post_start']}) must be before post_end ({parsed['post_end']})")

    if inputs.get("mode") == "sliding" and not inputs.get("window_size"):
        problems.append("window_size is required when mode='sliding'")

    return problems


async def _load_spreadsheet(db: AsyncSession, project_id, spreadsheet_id: str) -> Spreadsheet:
    result = await db.execute(
        select(Spreadsheet).where(
            Spreadsheet.id == uuid.UUID(spreadsheet_id),
            Spreadsheet.project_id == project_id,
        )
    )
    spreadsheet = result.scalar_one_or_none()
    if not spreadsheet:
        raise ValueError(f"Spreadsheet {spreadsheet_id} not found in this project.")
    return spreadsheet


