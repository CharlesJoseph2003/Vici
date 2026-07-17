import uuid
from datetime import datetime

from pydantic import BaseModel


class CreateProjectRequest(BaseModel):
    name: str


class ProjectResponse(BaseModel):
    id: uuid.UUID
    name: str
    created_at: datetime


class SpreadsheetResponse(BaseModel):
    id: uuid.UUID
    project_id: uuid.UUID
    filename: str
    date_min: datetime
    date_max: datetime
    business_units_available: list[str]
    business_units: list[str]                # currently selected filter
    active_context: dict | None              # pre/post dates + mode + window_size
    uploaded_at: datetime


class UpdateSpreadsheetResponse(SpreadsheetResponse):
    runs_updated: int                        # how many RoiRuns got fresh metrics
    runs_failed: list[dict]                  # [{run_id, label, reason}] — skipped, kept old metrics
    filter_cleared: bool                     # true if the old business_units filter no longer exists in the new data


class RunWindowRequest(BaseModel):
    spreadsheet_id: uuid.UUID
    label: str
    mode: str                          # "straight" | "sliding"
    window_size: str | None = None     # "weekly" | "biweekly" | "monthly"
    pre_start: str
    pre_end: str
    post_start: str
    post_end: str
    top_n: int = 5


class RunSummary(BaseModel):
    id: uuid.UUID
    label: str
    mode: str
    window_size: str | None
    pre_start: datetime
    pre_end: datetime
    post_start: datetime
    post_end: datetime
    created_at: datetime


class ChatRequest(BaseModel):
    message: str


class ChatResponse(BaseModel):
    message: str
