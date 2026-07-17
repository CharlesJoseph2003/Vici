import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.agent.analyzer_loader import make_loader
from backend.agent.loop import run_turn
from backend.auth.middleware import get_verified_user
from backend.db.models import Project, User
from backend.db.session import get_db
from backend.schemas import ChatRequest, ChatResponse

router = APIRouter(prefix="/projects/{project_id}/chat", tags=["agent"])


@router.post("", response_model=ChatResponse)
async def chat(
    project_id: uuid.UUID,
    body: ChatRequest,
    user: User = Depends(get_verified_user),
    db: AsyncSession = Depends(get_db),
):
    project = (await db.execute(
        select(Project).where(Project.id == project_id, Project.org_id == user.org_id)
    )).scalar_one_or_none()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    loader = make_loader(db, project)
    reply = await run_turn(db, project, body.message, loader)
    return ChatResponse(message=reply)
