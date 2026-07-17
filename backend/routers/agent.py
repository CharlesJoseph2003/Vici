import json
import uuid

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.agent.analyzer_loader import make_loader
from backend.agent.loop import stream_turn
from backend.auth.middleware import get_verified_user
from backend.db.models import Project, User
from backend.db.session import get_db
from backend.schemas import ChatRequest

router = APIRouter(prefix="/projects/{project_id}/chat", tags=["agent"])


@router.post("")
async def chat(
    project_id: uuid.UUID,
    body: ChatRequest,
    user: User = Depends(get_verified_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Server-Sent Events stream. Each event is a JSON object on a `data:` line
    (see backend/agent/loop.py::stream_turn for the event schema).

    Client should parse using EventSource or a manual SSE reader.
    """
    project = (await db.execute(
        select(Project).where(Project.id == project_id, Project.org_id == user.org_id)
    )).scalar_one_or_none()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    loader = make_loader(db, project)

    async def event_source():
        try:
            async for event in stream_turn(db, project, body.message, loader):
                yield f"data: {json.dumps(event, default=str)}\n\n"
        except Exception as exc:
            err = {"type": "error", "message": str(exc)}
            yield f"data: {json.dumps(err)}\n\n"

    return StreamingResponse(
        event_source(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",   # tells nginx not to buffer if proxied
        },
    )
