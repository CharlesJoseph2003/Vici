"""
Tool-use loop for the ROI agent.

One `run_turn` per user message:
  1. Load project + spreadsheets + active messages from DB
  2. Route tools for the user message (procedural memory)
  3. Assemble system blocks (project context + episodic memory)
  4. Iterate Claude ↔ tool calls until stop_reason == "end_turn"
  5. Persist every new message row
  6. If input_tokens crossed the compression threshold, compress
"""
from __future__ import annotations

import asyncio
import uuid
from typing import Callable

import anthropic
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.agent.memory import (
    COMPRESSION_THRESHOLD_TOKENS,
    build_system_blocks,
    compress_session,
    load_active_messages,
    load_project_runs,
    to_api_messages,
)
from backend.agent.tools import AnalyzerCache, dispatch_tool, route_tools_for_message
from backend.db.models import AgentMessage, Project, Spreadsheet

MODEL_ID = "claude-opus-4-7"
MAX_TOKENS = 4096
MAX_TOOL_ITERATIONS = 12


async def run_turn(
    db: AsyncSession,
    project: Project,
    user_message: str,
    analyzer_loader: Callable,
) -> str:
    """
    Execute one conversational turn. Returns the final assistant text.
    `analyzer_loader` is an async callable (spreadsheet_id: str) -> RoiAnalyzer.
    """
    spreadsheets = list((
        await db.execute(
            select(Spreadsheet).where(Spreadsheet.project_id == project.id)
        )
    ).scalars())
    prior_runs = await load_project_runs(db, project.id)

    active_rows = await load_active_messages(db, project.id)
    history = to_api_messages(active_rows)

    # Persist the incoming user message
    user_row = AgentMessage(
        id=uuid.uuid4(),
        project_id=project.id,
        role="user",
        content=[{"type": "text", "text": user_message}],
    )
    db.add(user_row)
    await db.flush()

    history.append({"role": "user", "content": [{"type": "text", "text": user_message}]})

    system_blocks = build_system_blocks(project, spreadsheets, prior_runs)
    tools = route_tools_for_message(user_message)
    project_spreadsheets = [
        {
            "id": str(s.id),
            "filename": s.filename,
            "date_min": s.date_min.date().isoformat(),
            "date_max": s.date_max.date().isoformat(),
        }
        for s in spreadsheets
    ]

    cache = AnalyzerCache(analyzer_loader)
    client = anthropic.AsyncAnthropic()

    final_text = ""
    last_input_tokens = 0

    for _ in range(MAX_TOOL_ITERATIONS):
        response = await client.messages.create(
            model=MODEL_ID,
            max_tokens=MAX_TOKENS,
            system=system_blocks,
            tools=tools,
            messages=history,
        )
        last_input_tokens = response.usage.input_tokens

        assistant_content = [_block_to_dict(b) for b in response.content]
        assistant_row = AgentMessage(
            id=uuid.uuid4(),
            project_id=project.id,
            role="assistant",
            content=assistant_content,
        )
        db.add(assistant_row)
        await db.flush()

        history.append({"role": "assistant", "content": assistant_content})

        if response.stop_reason == "end_turn":
            final_text = _extract_text(response.content)
            break

        if response.stop_reason != "tool_use":
            final_text = _extract_text(response.content) or "(no response)"
            break

        # Dispatch every tool_use block in parallel
        tool_use_blocks = [b for b in response.content if b.type == "tool_use"]
        results = await asyncio.gather(*[
            dispatch_tool(b.name, b.input, cache, project_spreadsheets, db, project)
            for b in tool_use_blocks
        ])
        tool_results = [
            {"type": "tool_result", "tool_use_id": b.id, "content": r}
            for b, r in zip(tool_use_blocks, results)
        ]

        tool_row = AgentMessage(
            id=uuid.uuid4(),
            project_id=project.id,
            role="user",
            content=tool_results,
        )
        db.add(tool_row)
        await db.flush()
        history.append({"role": "user", "content": tool_results})

    await db.commit()

    if last_input_tokens > COMPRESSION_THRESHOLD_TOKENS:
        session_rows = await load_active_messages(db, project.id)
        await compress_session(db, project, session_rows)

    return final_text


def _block_to_dict(block) -> dict:
    if block.type == "text":
        return {"type": "text", "text": block.text}
    if block.type == "tool_use":
        return {
            "type": "tool_use",
            "id": block.id,
            "name": block.name,
            "input": block.input,
        }
    # Fallback for future block types
    return block.model_dump()


def _extract_text(content_blocks) -> str:
    return "\n".join(b.text for b in content_blocks if b.type == "text").strip()
