import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, String, Text, func
from sqlalchemy.dialects.postgresql import JSON, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class Organization(Base):
    __tablename__ = "organizations"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    users: Mapped[list["User"]] = relationship(back_populates="organization")
    projects: Mapped[list["Project"]] = relationship(back_populates="organization")


class User(Base):
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)  # matches Supabase auth.users id
    org_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("organizations.id"), nullable=False)
    email: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)
    role: Mapped[str] = mapped_column(String(50), nullable=False, default="member")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    organization: Mapped["Organization"] = relationship(back_populates="users")


class Project(Base):
    __tablename__ = "projects"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    org_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("organizations.id"), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    episodic_memory: Mapped[str | None] = mapped_column(Text)  # markdown log of past sessions
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    organization: Mapped["Organization"] = relationship(back_populates="projects")
    spreadsheets: Mapped[list["Spreadsheet"]] = relationship(back_populates="project")
    runs: Mapped[list["RoiRun"]] = relationship(back_populates="project")
    agent_messages: Mapped[list["AgentMessage"]] = relationship(back_populates="project")


class Spreadsheet(Base):
    __tablename__ = "spreadsheets"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("projects.id"), nullable=False)
    filename: Mapped[str] = mapped_column(String(255), nullable=False)
    storage_path: Mapped[str] = mapped_column(String(512), nullable=False)
    date_min: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    date_max: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    classifications: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    # Per-sheet filters persisted across turns. All computations on this spreadsheet honor them.
    business_units: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    # {"pre_start", "pre_end", "post_start", "post_end", "mode", "window_size"}
    active_context: Mapped[dict | None] = mapped_column(JSON)
    uploaded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    project: Mapped["Project"] = relationship(back_populates="spreadsheets")
    runs: Mapped[list["RoiRun"]] = relationship(back_populates="spreadsheet")


class RoiRun(Base):
    __tablename__ = "roi_runs"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("projects.id"), nullable=False)
    spreadsheet_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("spreadsheets.id"), nullable=False)
    label: Mapped[str] = mapped_column(String(255), nullable=False)
    mode: Mapped[str] = mapped_column(String(50), nullable=False)        # straight | sliding
    window_size: Mapped[str | None] = mapped_column(String(50))          # weekly | biweekly | monthly
    pre_start: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    pre_end: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    post_start: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    post_end: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    metrics: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    llm_summary: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    project: Mapped["Project"] = relationship(back_populates="runs")
    spreadsheet: Mapped["Spreadsheet"] = relationship(back_populates="runs")


class AgentMessage(Base):
    __tablename__ = "agent_messages"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("projects.id"), nullable=False)
    role: Mapped[str] = mapped_column(String(50), nullable=False)        # user | assistant
    content: Mapped[list] = mapped_column(JSON, nullable=False)          # Anthropic message content blocks
    archived: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    project: Mapped["Project"] = relationship(back_populates="agent_messages")
