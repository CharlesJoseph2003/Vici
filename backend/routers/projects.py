import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.auth.middleware import get_current_user, get_verified_user
from backend.db.models import Project, User
from backend.db.session import get_db
from backend.schemas import CreateProjectRequest, ProjectResponse

router = APIRouter(prefix="/projects", tags=["projects"])


@router.post("", response_model=ProjectResponse)
async def create_project(
    body: CreateProjectRequest,
    user: User = Depends(get_verified_user),
    db: AsyncSession = Depends(get_db),
):
    project = Project(
        id=uuid.uuid4(),
        org_id=user.org_id,
        name=body.name,
    )
    db.add(project)
    await db.commit()
    await db.refresh(project)
    return ProjectResponse(id=project.id, name=project.name, created_at=project.created_at)


@router.get("", response_model=list[ProjectResponse])
async def list_projects(
    current=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(Project).where(Project.org_id == current["org_id"]).order_by(Project.created_at.desc())
    )
    return [
        ProjectResponse(id=p.id, name=p.name, created_at=p.created_at)
        for p in result.scalars()
    ]


@router.get("/{project_id}", response_model=ProjectResponse)
async def get_project(
    project_id: uuid.UUID,
    current=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    project = await _load_project(db, project_id, current["org_id"])
    return ProjectResponse(id=project.id, name=project.name, created_at=project.created_at)


async def _load_project(db: AsyncSession, project_id: uuid.UUID, org_id: uuid.UUID) -> Project:
    result = await db.execute(
        select(Project).where(Project.id == project_id, Project.org_id == org_id)
    )
    project = result.scalar_one_or_none()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    return project
