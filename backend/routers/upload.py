import uuid

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.auth.middleware import get_verified_user
from backend.db.models import Project, RoiRun, Spreadsheet, User
from backend.db.session import get_db
from backend.ingestion.parser import parse_from_bytes
from backend.roi.analyzer import RoiAnalyzer
from backend.roi.windows import (
    WindowMode,
    WindowRequest,
    WindowSize,
    group_to_dict,
    run_parallel_requests,
)
from backend.schemas import SpreadsheetResponse, UpdateSpreadsheetResponse
from backend.storage import upload_spreadsheet

router = APIRouter(prefix="/projects/{project_id}/spreadsheets", tags=["spreadsheets"])


@router.post("", response_model=SpreadsheetResponse)
async def upload(
    project_id: uuid.UUID,
    file: UploadFile = File(...),
    user: User = Depends(get_verified_user),
    db: AsyncSession = Depends(get_db),
):
    # Auth scope check: project must belong to the caller's org
    project = (await db.execute(
        select(Project).where(Project.id == project_id, Project.org_id == user.org_id)
    )).scalar_one_or_none()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    if not file.filename.endswith((".xlsx", ".xls")):
        raise HTTPException(status_code=400, detail="Only .xlsx or .xls files are supported")

    data = await file.read()

    try:
        df = parse_from_bytes(data)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    analyzer = RoiAnalyzer(df)
    await analyzer.run_classifications()

    spreadsheet_id = uuid.uuid4()
    storage_path = f"{project.id}/{spreadsheet_id}.xlsx"
    await upload_spreadsheet(storage_path, data)

    date_min, date_max = analyzer.available_date_range()

    spreadsheet = Spreadsheet(
        id=spreadsheet_id,
        project_id=project.id,
        filename=file.filename,
        storage_path=storage_path,
        date_min=date_min.to_pydatetime(),
        date_max=date_max.to_pydatetime(),
        classifications={
            "high_value_diagnostic_types": analyzer.high_value_diagnostic_types,
            "high_value_maintenance_types": analyzer.high_value_maintenance_types,
            "high_value_diagnostic_tags": analyzer.high_value_diagnostic_tags,
            "high_value_maintenance_tags": analyzer.high_value_maintenance_tags,
            "timing_reasons": analyzer.timing_reasons,
        },
        business_units=[],       # empty filter by default — agent asks the user which to apply
        active_context=None,     # date range/window is set by the agent after upload
    )
    db.add(spreadsheet)
    await db.commit()
    await db.refresh(spreadsheet)

    return SpreadsheetResponse(
        id=spreadsheet.id,
        project_id=spreadsheet.project_id,
        filename=spreadsheet.filename,
        date_min=spreadsheet.date_min,
        date_max=spreadsheet.date_max,
        business_units_available=analyzer.available_business_units(),
        business_units=spreadsheet.business_units,
        active_context=spreadsheet.active_context,
        uploaded_at=spreadsheet.uploaded_at,
    )


@router.put("/{spreadsheet_id}", response_model=UpdateSpreadsheetResponse)
async def update_spreadsheet(
    project_id: uuid.UUID,
    spreadsheet_id: uuid.UUID,
    file: UploadFile = File(...),
    user: User = Depends(get_verified_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Replace an existing spreadsheet's data with new bytes, re-run classifications,
    and re-execute every prior RoiRun on this sheet against the new data.

    Order matters for consistency:
      1. Parse + classify the new bytes (fail fast on bad format)
      2. Upload bytes to a NEW storage_path (old file untouched)
      3. Re-run all RoiRuns in memory
      4. Commit DB (spreadsheet row updated to point at new_storage_path + fresh
         classifications; every RoiRun row gets new metrics)

    The df_cache is keyed by storage_path, so the new path is a fresh cache key —
    old DataFrame ages out via LRU. No explicit invalidation needed.
    """
    # Auth scope check
    project = (await db.execute(
        select(Project).where(Project.id == project_id, Project.org_id == user.org_id)
    )).scalar_one_or_none()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    spreadsheet = (await db.execute(
        select(Spreadsheet).where(
            Spreadsheet.id == spreadsheet_id,
            Spreadsheet.project_id == project.id,
        )
    )).scalar_one_or_none()
    if not spreadsheet:
        raise HTTPException(status_code=404, detail="Spreadsheet not found in this project")

    if not file.filename.endswith((".xlsx", ".xls")):
        raise HTTPException(status_code=400, detail="Only .xlsx or .xls files are supported")

    data = await file.read()

    try:
        df = parse_from_bytes(data)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    analyzer = RoiAnalyzer(df)
    await analyzer.run_classifications()

    # Try to preserve the sheet's persisted business_units filter.
    # If any unit no longer exists in the new data, clear it and flag it.
    filter_cleared = False
    if spreadsheet.business_units:
        try:
            analyzer.set_business_units(spreadsheet.business_units)
        except ValueError:
            filter_cleared = True
            spreadsheet.business_units = []

    # Upload bytes to a fresh storage_path — old path stays intact.
    new_storage_path = f"{project.id}/{spreadsheet_id}_{uuid.uuid4().hex}.xlsx"
    await upload_spreadsheet(new_storage_path, data)

    # Re-run every RoiRun on this spreadsheet with its stored params.
    # Individual failures (e.g. dates now out of range) don't abort — old metrics stay.
    prior_runs = list((await db.execute(
        select(RoiRun).where(RoiRun.spreadsheet_id == spreadsheet_id)
    )).scalars())

    runs_updated = 0
    runs_failed: list[dict] = []

    for run in prior_runs:
        try:
            request = WindowRequest(
                label=run.label,
                mode=WindowMode(run.mode),
                pre_start=run.pre_start.date().isoformat(),
                pre_end=run.pre_end.date().isoformat(),
                post_start=run.post_start.date().isoformat(),
                post_end=run.post_end.date().isoformat(),
                window_size=WindowSize(run.window_size) if run.window_size else None,
            )
            groups = run_parallel_requests(analyzer, [request])
            run.metrics = {"results": [group_to_dict(g) for g in groups]}
            db.add(run)
            runs_updated += 1
        except (ValueError, KeyError) as e:
            runs_failed.append({
                "run_id": str(run.id),
                "label": run.label,
                "reason": str(e),
            })

    # Update the spreadsheet row — this is what "activates" the new file for readers.
    date_min, date_max = analyzer.available_date_range()
    spreadsheet.filename = file.filename
    spreadsheet.storage_path = new_storage_path
    spreadsheet.date_min = date_min.to_pydatetime()
    spreadsheet.date_max = date_max.to_pydatetime()
    spreadsheet.classifications = {
        "high_value_diagnostic_types": analyzer.high_value_diagnostic_types,
        "high_value_maintenance_types": analyzer.high_value_maintenance_types,
        "high_value_diagnostic_tags": analyzer.high_value_diagnostic_tags,
        "high_value_maintenance_tags": analyzer.high_value_maintenance_tags,
        "timing_reasons": analyzer.timing_reasons,
    }
    db.add(spreadsheet)

    await db.commit()
    await db.refresh(spreadsheet)

    return UpdateSpreadsheetResponse(
        id=spreadsheet.id,
        project_id=spreadsheet.project_id,
        filename=spreadsheet.filename,
        date_min=spreadsheet.date_min,
        date_max=spreadsheet.date_max,
        business_units_available=analyzer.available_business_units(),
        business_units=spreadsheet.business_units,
        active_context=spreadsheet.active_context,
        uploaded_at=spreadsheet.uploaded_at,
        runs_updated=runs_updated,
        runs_failed=runs_failed,
        filter_cleared=filter_cleared,
    )
