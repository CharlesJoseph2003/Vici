import uuid

import pandas as pd
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.agent.analyzer_loader import build_analyzer
from backend.auth.middleware import get_current_user, get_verified_user
from backend.db.models import Project, RoiRun, User
from backend.db.session import get_db
from backend.roi.windows import (
    WindowMode,
    WindowRequest,
    WindowSize,
    group_to_dict,
    run_parallel_requests,
)
from backend.schemas import RunSummary, RunWindowRequest

router = APIRouter(prefix="/projects/{project_id}/runs", tags=["runs"])


@router.post("")
async def create_run(
    project_id: uuid.UUID,
    body: RunWindowRequest,
    user: User = Depends(get_verified_user),
    db: AsyncSession = Depends(get_db),
):
    project = await _load_project(db, project_id, user.org_id)
    analyzer = await build_analyzer(db, project, body.spreadsheet_id)

    try:
        request = WindowRequest(
            label=body.label,
            mode=WindowMode(body.mode),
            pre_start=body.pre_start,
            pre_end=body.pre_end,
            post_start=body.post_start,
            post_end=body.post_end,
            window_size=WindowSize(body.window_size) if body.window_size else None,
            top_n=body.top_n,
        )
        groups = run_parallel_requests(analyzer, [request])
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    metrics = [group_to_dict(g) for g in groups]

    run = RoiRun(
        id=uuid.uuid4(),
        project_id=project.id,
        spreadsheet_id=body.spreadsheet_id,
        label=body.label,
        mode=body.mode,
        window_size=body.window_size,
        pre_start=pd.to_datetime(body.pre_start),
        pre_end=pd.to_datetime(body.pre_end),
        post_start=pd.to_datetime(body.post_start),
        post_end=pd.to_datetime(body.post_end),
        metrics={"results": metrics},
    )
    db.add(run)
    await db.commit()
    await db.refresh(run)

    return {"id": run.id, "results": metrics, "created_at": run.created_at}


@router.get("", response_model=list[RunSummary])
async def list_runs(
    project_id: uuid.UUID,
    current=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    project = await _load_project(db, project_id, current["org_id"])
    result = await db.execute(
        select(RoiRun).where(RoiRun.project_id == project.id).order_by(RoiRun.created_at.desc())
    )
    return [
        RunSummary(
            id=r.id, label=r.label, mode=r.mode, window_size=r.window_size,
            pre_start=r.pre_start, pre_end=r.pre_end,
            post_start=r.post_start, post_end=r.post_end,
            created_at=r.created_at,
        )
        for r in result.scalars()
    ]


@router.get("/{run_id}")
async def get_run(
    project_id: uuid.UUID,
    run_id: uuid.UUID,
    current=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    project = await _load_project(db, project_id, current["org_id"])
    run = (await db.execute(
        select(RoiRun).where(RoiRun.id == run_id, RoiRun.project_id == project.id)
    )).scalar_one_or_none()
    if not run:
        raise HTTPException(status_code=404, detail="Run not found")
    return {
        "id": run.id,
        "label": run.label,
        "mode": run.mode,
        "window_size": run.window_size,
        "pre_start": run.pre_start,
        "pre_end": run.pre_end,
        "post_start": run.post_start,
        "post_end": run.post_end,
        "metrics": run.metrics,
        "llm_summary": run.llm_summary,
        "created_at": run.created_at,
    }


async def _load_project(db: AsyncSession, project_id: uuid.UUID, org_id: uuid.UUID) -> Project:
    project = (await db.execute(
        select(Project).where(Project.id == project_id, Project.org_id == org_id)
    )).scalar_one_or_none()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    return project


