"""
Factory for reconstructing a RoiAnalyzer from a Spreadsheet row.

DataFrame retrieval goes through the module-level LRU in df_cache — so the
expensive download + parse only happens once per worker per spreadsheet.

The analyzer wrapper itself is instantiated fresh every request so the mutable
business_units filter never leaks between concurrent chats.
"""
from __future__ import annotations

import uuid
from typing import Callable

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.agent.df_cache import get_dataframe
from backend.db.models import Project, Spreadsheet
from backend.roi.analyzer import RoiAnalyzer


async def build_analyzer(db: AsyncSession, project: Project, spreadsheet_id: uuid.UUID) -> RoiAnalyzer:
    spreadsheet = (await db.execute(
        select(Spreadsheet).where(
            Spreadsheet.id == spreadsheet_id,
            Spreadsheet.project_id == project.id,
        )
    )).scalar_one_or_none()
    if not spreadsheet:
        raise ValueError(f"Spreadsheet {spreadsheet_id} not found in project {project.id}")

    df = await get_dataframe(spreadsheet.storage_path)

    analyzer = RoiAnalyzer(df)

    c = spreadsheet.classifications or {}
    analyzer.high_value_diagnostic_types = c.get("high_value_diagnostic_types", [])
    analyzer.high_value_maintenance_types = c.get("high_value_maintenance_types", [])
    analyzer.high_value_diagnostic_tags = c.get("high_value_diagnostic_tags", [])
    analyzer.high_value_maintenance_tags = c.get("high_value_maintenance_tags", [])
    analyzer.timing_reasons = c.get("timing_reasons", [])

    if spreadsheet.business_units:
        analyzer.set_business_units(spreadsheet.business_units)

    return analyzer


def make_loader(db: AsyncSession, project: Project) -> Callable:
    """Return an async loader closed over db + project for the agent to use."""
    async def loader(spreadsheet_id: str) -> RoiAnalyzer:
        return await build_analyzer(db, project, uuid.UUID(spreadsheet_id))
    return loader
