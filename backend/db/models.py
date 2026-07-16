import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, Text, func
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
    spreadsheets: Mapped[list["Spreadsheet"]] = relationship(back_populates="organization")


class User(Base):
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)  # matches Supabase auth.users id
    org_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("organizations.id"), nullable=False)
    email: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)
    role: Mapped[str] = mapped_column(String(50), nullable=False, default="member")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    organization: Mapped["Organization"] = relationship(back_populates="users")


class Spreadsheet(Base):
    __tablename__ = "spreadsheets"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    org_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("organizations.id"), nullable=False)
    filename: Mapped[str] = mapped_column(String(255), nullable=False)
    storage_path: Mapped[str] = mapped_column(String(512), nullable=False)
    date_min: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    date_max: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    classifications: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    uploaded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    organization: Mapped["Organization"] = relationship(back_populates="spreadsheets")
    analyses: Mapped[list["RoiAnalysis"]] = relationship(back_populates="spreadsheet")


class RoiAnalysis(Base):
    __tablename__ = "roi_analyses"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    org_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("organizations.id"), nullable=False)
    spreadsheet_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("spreadsheets.id"), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    business_units: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    spreadsheet: Mapped["Spreadsheet"] = relationship(back_populates="analyses")
    runs: Mapped[list["RoiRun"]] = relationship(back_populates="analysis")
    agent_messages: Mapped[list["AgentMessage"]] = relationship(back_populates="analysis")


class RoiRun(Base):
    __tablename__ = "roi_runs"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    analysis_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("roi_analyses.id"), nullable=False)
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

    analysis: Mapped["RoiAnalysis"] = relationship(back_populates="runs")


class AgentMessage(Base):
    __tablename__ = "agent_messages"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    analysis_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("roi_analyses.id"), nullable=False)
    role: Mapped[str] = mapped_column(String(50), nullable=False)        # user | assistant
    content: Mapped[str] = mapped_column(Text, nullable=False)
    tool_calls: Mapped[dict | None] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    analysis: Mapped["RoiAnalysis"] = relationship(back_populates="agent_messages")
