"""
Memory assembly for the ROI agent.

Working memory = assembled every turn from:
  - Base agent identity (static)
  - Project context block (structured DB facts)
  - Episodic memory markdown (past sessions)
  - Recent non-archived AgentMessage rows

Compression = when the last response's input_tokens crosses a threshold, we
summarize the current session into episodic memory and mark those messages
archived so future turns don't reload them.
"""
from __future__ import annotations

import anthropic
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from backend.db.models import AgentMessage, Project, RoiRun, Spreadsheet

# Trigger compression when input tokens exceed this. Claude's context is 200k;
# leaving headroom for the response + tool calls in the compressing call itself.
COMPRESSION_THRESHOLD_TOKENS = 150_000

AGENT_SYSTEM_PROMPT = """You are the Vici ROI analyst — an assistant for home services companies exploring what changed between two time periods in their operational data.

You have access to one or more spreadsheets in the current project. Each row is a job with columns like Created Date, Completion Date, Cancelled Date, Job Type, Business Unit, Tags, Assigned Technicians, and sales fields.

Core metrics you can compute:
  - Blended Sales Average (BSA) per job or opportunity — the primary ROI metric
  - High-value job counts and cancellation rates (separately by Job Type and by Tags — tags are the fallback signal when Job Type is ambiguous)
  - Timing cancellation rate — jobs lost to speed/availability issues
  - Technician comparisons: who stayed vs departed, and how their metrics moved

Tool categories available (schemas load on demand based on the question):
  - list_spreadsheets, get_available_business_units, set_business_unit_filter (always available)
  - metrics: total sales, completed jobs, sales per job, BSA
  - high_value: counts by job_type or by tag
  - cancellations: total, timing rate, high-value cancel rate
  - technicians: compare periods, rank departed
  - windows: run pre-vs-post analyses (straight or sliding)

Guidelines:
  - Start by orienting: call list_spreadsheets when the project is new to you.
  - Each spreadsheet has its own persistent business_units filter and active_context (pre/post date range + window mode). Both are shown in the project context below.
  - Business units — NEVER guess:
      * The first time a user references a business unit on a spreadsheet, call get_available_business_units to see the exact strings in that sheet.
      * If the user's phrasing is ambiguous (e.g. they say "HVAC" but the sheet has "HVAC Demand", "HVAC-Georgetown Service", and "HVAC Commercial"), present the actual options to the user and ASK which ones they mean.
      * Only call set_business_unit_filter with exact strings from get_available_business_units. If the tool rejects your input because it's not an exact match, do not retry with a guess — go back to the user.
  - Date ranges — must fall inside the sheet's date_min → date_max. If unsure, ask the user. If a tool rejects the range as out-of-bounds, propose the nearest valid one.
  - When the user says "use business unit X" or "look at period Y", call set_business_unit_filter or set_analysis_context to persist it. After changing either, offer to re-run the most recent analysis on that spreadsheet with the new params.
  - When the user asks about "high value" jobs, run both job_type and tag versions — tags catch cases job types miss.
  - When comparing periods, use run_window_analysis rather than computing metrics one-by-one.
  - Cite specific numbers with dollar formatting and dates in your responses.
"""


def build_project_context(
    project: Project,
    spreadsheets: list[Spreadsheet],
    runs: list[RoiRun],
) -> str:
    """Structured facts pulled directly from the DB — the 'semantic' layer."""
    lines = [
        f"# Project: {project.name}",
        "",
        "## Spreadsheets in this project",
    ]
    for s in spreadsheets:
        lines.append(
            f"- id={s.id} | filename={s.filename} | "
            f"range: {s.date_min.date()} → {s.date_max.date()}"
        )
        lines.append(f"  - Active business unit filter: {s.business_units or 'none (all units)'}")
        if s.active_context:
            ctx = s.active_context
            window = f", window_size={ctx.get('window_size')}" if ctx.get('window_size') else ""
            lines.append(
                f"  - Active analysis context: pre {ctx.get('pre_start')} → {ctx.get('pre_end')}, "
                f"post {ctx.get('post_start')} → {ctx.get('post_end')}, mode={ctx.get('mode')}{window}"
            )
        else:
            lines.append("  - Active analysis context: not set — ask the user for pre/post ranges and mode")
        c = s.classifications or {}
        if c:
            lines.extend([
                f"  - HV diagnostic job types: {c.get('high_value_diagnostic_types', [])}",
                f"  - HV maintenance job types: {c.get('high_value_maintenance_types', [])}",
                f"  - HV diagnostic tags: {c.get('high_value_diagnostic_tags', [])}",
                f"  - HV maintenance tags: {c.get('high_value_maintenance_tags', [])}",
                f"  - Timing cancellation reasons: {c.get('timing_reasons', [])}",
            ])

    if runs:
        lines.append("")
        lines.append("## Prior analyses (use get_run_results to fetch full metrics)")
        for r in runs:
            window = f" | {r.window_size}" if r.window_size else ""
            lines.append(
                f"- id={r.id} | label={r.label!r} | mode={r.mode}{window} | "
                f"pre {r.pre_start.date()} → {r.pre_end.date()} | "
                f"post {r.post_start.date()} → {r.post_end.date()}"
            )
    return "\n".join(lines)


async def load_project_runs(db: AsyncSession, project_id) -> list[RoiRun]:
    from sqlalchemy import select
    result = await db.execute(
        select(RoiRun).where(RoiRun.project_id == project_id).order_by(RoiRun.created_at)
    )
    return list(result.scalars())


def build_system_blocks(
    project: Project,
    spreadsheets: list[Spreadsheet],
    runs: list[RoiRun],
) -> list[dict]:
    """
    Return the `system` argument for the Anthropic API — a list of text blocks
    with cache_control on the stable ones.
    """
    blocks = [
        {"type": "text", "text": AGENT_SYSTEM_PROMPT, "cache_control": {"type": "ephemeral"}},
        {"type": "text", "text": build_project_context(project, spreadsheets, runs),
         "cache_control": {"type": "ephemeral"}},
    ]
    if project.episodic_memory:
        blocks.append({
            "type": "text",
            "text": f"# Past session log\n\n{project.episodic_memory}",
        })
    return blocks


async def load_active_messages(db: AsyncSession, project_id) -> list[AgentMessage]:
    result = await db.execute(
        select(AgentMessage)
        .where(AgentMessage.project_id == project_id, AgentMessage.archived == False)  # noqa: E712
        .order_by(AgentMessage.created_at)
    )
    return list(result.scalars())


def to_api_messages(rows: list[AgentMessage]) -> list[dict]:
    return [{"role": r.role, "content": r.content} for r in rows]


# ---------------------------------------------------------------------------
# Compression
# ---------------------------------------------------------------------------

_SUMMARY_PROMPT = """Below is a conversation between a user and an ROI analyst assistant, plus their tool calls and results. Produce a concise markdown summary of THIS session's key findings, decisions, and open threads.

Rules:
  - Preserve concrete numbers (dollar amounts, percentages, dates, technician names).
  - Note which spreadsheet(s) and business units were analyzed.
  - Capture any conclusions the user reached or hypotheses left unresolved.
  - Do NOT include a play-by-play of tool calls — synthesize.
  - Output only the markdown, no preamble.

Conversation:
"""


async def compress_session(
    db: AsyncSession,
    project: Project,
    messages: list[AgentMessage],
) -> str:
    """Summarize the given messages, append to project.episodic_memory, mark rows archived."""
    if not messages:
        return project.episodic_memory or ""

    transcript = _messages_to_transcript(messages)
    client = anthropic.AsyncAnthropic()
    response = await client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=2048,
        messages=[{"role": "user", "content": _SUMMARY_PROMPT + transcript}],
    )
    session_summary = response.content[0].text.strip()

    from datetime import date
    header = f"## Session — {date.today().isoformat()}"
    combined = (project.episodic_memory or "").rstrip()
    new_memory = f"{combined}\n\n{header}\n\n{session_summary}".strip()

    project.episodic_memory = new_memory
    await db.execute(
        update(AgentMessage)
        .where(AgentMessage.id.in_([m.id for m in messages]))
        .values(archived=True)
    )
    await db.commit()
    return new_memory


def _messages_to_transcript(messages: list[AgentMessage]) -> str:
    lines = []
    for m in messages:
        if isinstance(m.content, str):
            lines.append(f"[{m.role}] {m.content}")
            continue
        for block in m.content:
            btype = block.get("type")
            if btype == "text":
                lines.append(f"[{m.role}] {block.get('text', '')}")
            elif btype == "tool_use":
                lines.append(f"[assistant tool_use] {block.get('name')}({block.get('input')})")
            elif btype == "tool_result":
                content = block.get("content")
                if isinstance(content, list):
                    content = " ".join(c.get("text", "") for c in content if c.get("type") == "text")
                lines.append(f"[tool_result] {content}")
    return "\n".join(lines)
