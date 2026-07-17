from dataclasses import dataclass
from datetime import date, timedelta
from enum import Enum

import pandas as pd

from .analyzer import RoiAnalyzer


class WindowMode(str, Enum):
    SLIDING = "sliding"    # slide a window across both periods and compare each pair
    STRAIGHT = "straight"  # one direct pre vs post comparison, no sliding


class WindowSize(str, Enum):
    WEEKLY = "weekly"
    BIWEEKLY = "biweekly"
    MONTHLY = "monthly"


@dataclass
class WindowRequest:
    label: str
    mode: WindowMode
    pre_start: str
    pre_end: str
    post_start: str
    post_end: str
    # Only used in SLIDING mode
    window_size: WindowSize | None = None
    top_n: int = 5


@dataclass
class WindowResult:
    label: str
    pre_start: str
    pre_end: str
    post_start: str
    post_end: str
    pre_blended_sales_avg_job: float
    post_blended_sales_avg_job: float
    delta_blended_sales_avg_job: float
    pre_blended_sales_avg_opportunity: float
    post_blended_sales_avg_opportunity: float
    delta_blended_sales_avg_opportunity: float
    pre_total_sales: float
    post_total_sales: float
    delta_total_sales: float
    pre_completed_jobs: int
    post_completed_jobs: int
    delta_completed_jobs: int
    pre_completed_opportunities: int
    post_completed_opportunities: int
    delta_completed_opportunities: int
    pre_total_cancellations: int
    post_total_cancellations: int
    delta_total_cancellations: int


@dataclass
class WindowGroupResult:
    request_label: str
    mode: WindowMode
    all_windows: list[WindowResult]
    top_windows: list[WindowResult]  # empty for STRAIGHT mode, ranked by delta BSA for SLIDING


def _generate_pre_windows(size: WindowSize, pre_start: str, pre_end: str) -> list[tuple[date, date]]:
    start = pd.to_datetime(pre_start).date()
    end = pd.to_datetime(pre_end).date()
    windows = []

    if size == WindowSize.WEEKLY:
        cursor = start
        while cursor + timedelta(days=6) <= end:
            windows.append((cursor, cursor + timedelta(days=6)))
            cursor += timedelta(days=7)

    elif size == WindowSize.BIWEEKLY:
        cursor = start
        while cursor + timedelta(days=13) <= end:
            windows.append((cursor, cursor + timedelta(days=13)))
            cursor += timedelta(days=14)

    elif size == WindowSize.MONTHLY:
        cursor = start.replace(day=1)
        while cursor <= end:
            month_end = (cursor.replace(day=28) + timedelta(days=4)).replace(day=1) - timedelta(days=1)
            window_start = max(cursor, start)
            window_end = min(month_end, end)
            if window_start <= window_end:
                windows.append((window_start, window_end))
            cursor = month_end + timedelta(days=1)

    return windows


def _compute_paired_metrics(
    analyzer: RoiAnalyzer,
    pre_start: str,
    pre_end: str,
    post_start: str,
    post_end: str,
    label: str,
) -> WindowResult:
    pre_bsa_job = analyzer.blended_sales_average_job(pre_start, pre_end)
    post_bsa_job = analyzer.blended_sales_average_job(post_start, post_end)

    pre_bsa_opp = analyzer.blended_sales_average_opportunity(pre_start, pre_end)
    post_bsa_opp = analyzer.blended_sales_average_opportunity(post_start, post_end)

    pre_sales = analyzer.total_sales(pre_start, pre_end)
    post_sales = analyzer.total_sales(post_start, post_end)

    pre_jobs = analyzer.completed_jobs(pre_start, pre_end)
    post_jobs = analyzer.completed_jobs(post_start, post_end)

    pre_opps = analyzer.completed_opportunities(pre_start, pre_end)
    post_opps = analyzer.completed_opportunities(post_start, post_end)

    pre_cancels = analyzer.total_cancellations(pre_start, pre_end)
    post_cancels = analyzer.total_cancellations(post_start, post_end)

    return WindowResult(
        label=label,
        pre_start=pre_start,
        pre_end=pre_end,
        post_start=post_start,
        post_end=post_end,
        pre_blended_sales_avg_job=pre_bsa_job,
        post_blended_sales_avg_job=post_bsa_job,
        delta_blended_sales_avg_job=post_bsa_job - pre_bsa_job,
        pre_blended_sales_avg_opportunity=pre_bsa_opp,
        post_blended_sales_avg_opportunity=post_bsa_opp,
        delta_blended_sales_avg_opportunity=post_bsa_opp - pre_bsa_opp,
        pre_total_sales=pre_sales,
        post_total_sales=post_sales,
        delta_total_sales=post_sales - pre_sales,
        pre_completed_jobs=pre_jobs,
        post_completed_jobs=post_jobs,
        delta_completed_jobs=post_jobs - pre_jobs,
        pre_completed_opportunities=pre_opps,
        post_completed_opportunities=post_opps,
        delta_completed_opportunities=post_opps - pre_opps,
        pre_total_cancellations=pre_cancels,
        post_total_cancellations=post_cancels,
        delta_total_cancellations=post_cancels - pre_cancels,
    )


def _run_single_request(analyzer: RoiAnalyzer, request: WindowRequest) -> WindowGroupResult:
    if request.mode == WindowMode.STRAIGHT:
        result = _compute_paired_metrics(
            analyzer,
            request.pre_start,
            request.pre_end,
            request.post_start,
            request.post_end,
            label=request.label,
        )
        return WindowGroupResult(
            request_label=request.label,
            mode=request.mode,
            all_windows=[result],
            top_windows=[],
        )

    # SLIDING mode
    offset = pd.to_datetime(request.post_start) - pd.to_datetime(request.pre_start)
    pre_windows = _generate_pre_windows(request.window_size, request.pre_start, request.pre_end)

    def window_label(pre_s: date) -> str:
        if request.window_size == WindowSize.MONTHLY:
            return pre_s.strftime("%B %Y")
        return f"Week of {pre_s.strftime('%b %d')}"

    results = []
    for pre_s, pre_e in pre_windows:
        post_s = (pd.Timestamp(pre_s) + offset).date()
        post_e = (pd.Timestamp(pre_e) + offset).date()
        try:
            result = _compute_paired_metrics(
                analyzer,
                str(pre_s), str(pre_e),
                str(post_s), str(post_e),
                label=window_label(pre_s),
            )
            results.append(result)
        except ValueError:
            continue

    ranked = sorted(results, key=lambda r: r.delta_blended_sales_avg_job, reverse=True)

    return WindowGroupResult(
        request_label=request.label,
        mode=request.mode,
        all_windows=ranked,
        top_windows=ranked[: request.top_n],
    )


def run_parallel_requests(
    analyzer: RoiAnalyzer,
    requests: list[WindowRequest],
) -> list[WindowGroupResult]:
    return [_run_single_request(analyzer, request) for request in requests]


def group_to_dict(group: WindowGroupResult) -> dict:
    return {
        "request_label": group.request_label,
        "mode": group.mode.value,
        "all_windows": [result_to_dict(r) for r in group.all_windows],
        "top_windows": [result_to_dict(r) for r in group.top_windows],
    }


def result_to_dict(result: WindowResult) -> dict:
    return {k: v for k, v in result.__dict__.items()}
