"""
Streaming tool-use loop for the ROI agent.

One `stream_turn` per user message — yields events as they happen so the client
sees the reply typing in real time and gets tool activity signals between
Claude round-trips.

Event schema (each yielded as a dict, encoded as SSE by the router):
  {"type": "text",       "delta": str}                       — streamed assistant text
  {"type": "tool_start", "name": str, "input": dict}         — a tool call is about to run
  {"type": "tool_done",  "name": str}                        — a tool call completed
  {"type": "done",       "message": str}                     — end of turn, full text
  {"type": "error",      "message": str}                     — something failed mid-stream

Per turn:
  1. Load project + spreadsheets + prior runs + active messages from DB
  2. Route tools for the user message (procedural memory)
  3. Assemble system blocks
  4. Iterate Claude ↔ tool calls until stop_reason == "end_turn"
     - Text deltas emitted as they arrive (before the round-trip completes)
     - Tool calls dispatched in parallel via asyncio.gather between rounds
  5. Persist every new message row
  6. If input_tokens crossed the compression threshold, compress
"""
from __future__ import annotations

import asyncio
import uuid
from typing import AsyncIterator, Callable

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


async def stream_turn(
    db: AsyncSession,
    project: Project,
    user_message: str,
    analyzer_loader: Callable,
) -> AsyncIterator[dict]:
    """
    Execute one conversational turn as an async event stream. See module docstring
    for the event schema.
    """
    spreadsheets = list((
        await db.execute(
            select(Spreadsheet).where(Spreadsheet.project_id == project.id)
        )
    ).scalars())
    prior_runs = await load_project_runs(db, project.id)

    active_rows = await load_active_messages(db, project.id)
    history = to_api_messages(active_rows)

    # Persist the incoming user message before hitting Claude — durable even if the stream dies.
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

    final_text_parts: list[str] = []
    last_input_tokens = 0

    for _ in range(MAX_TOOL_ITERATIONS):
        iteration_text_parts: list[str] = []

        async with client.messages.stream(
            model=MODEL_ID,
            max_tokens=MAX_TOKENS,
            system=system_blocks,
            tools=tools,
            messages=history,
        ) as stream:
            async for event in stream:
                # Only surface text deltas to the client — tool_use inputs stream as
                # partial JSON and are only usable once the block completes.
                if (
                    event.type == "content_block_delta"
                    and event.delta.type == "text_delta"
                ):
                    delta_text = event.delta.text
                    iteration_text_parts.append(delta_text)
                    yield {"type": "text", "delta": delta_text}

            final_response = await stream.get_final_message()

        last_input_tokens = final_response.usage.input_tokens

        assistant_content = [_block_to_dict(b) for b in final_response.content]
        assistant_row = AgentMessage(
            id=uuid.uuid4(),
            project_id=project.id,
            role="assistant",
            content=assistant_content,
        )
        db.add(assistant_row)
        await db.flush()

        history.append({"role": "assistant", "content": assistant_content})

        if final_response.stop_reason == "end_turn":
            final_text_parts.extend(iteration_text_parts)
            break

        if final_response.stop_reason != "tool_use":
            # max_tokens, refusal, or something unexpected — surface whatever we got and bail
            final_text_parts.extend(iteration_text_parts)
            break

        # Dispatch tools in parallel, emitting activity signals to the client
        tool_use_blocks = [b for b in final_response.content if b.type == "tool_use"]

        for block in tool_use_blocks:
            yield {"type": "tool_start", "name": block.name, "input": block.input}

        results = await asyncio.gather(*[
            dispatch_tool(b.name, b.input, cache, project_spreadsheets, db, project)
            for b in tool_use_blocks
        ])

        tool_results = []
        for block, result in zip(tool_use_blocks, results):
            yield {"type": "tool_done", "name": block.name}
            tool_results.append({
                "type": "tool_result",
                "tool_use_id": block.id,
                "content": result,
            })

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

    yield {"type": "done", "message": "".join(final_text_parts).strip()}


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
    return block.model_dump()
