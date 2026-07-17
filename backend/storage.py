"""
Supabase Storage helpers.

Bucket layout: `spreadsheets/{project_id}/{spreadsheet_id}.xlsx`
Uses the service role key so the backend can bypass RLS.
"""
from __future__ import annotations

import httpx

from backend.config import settings

BUCKET = "spreadsheets"
XLSX_CONTENT_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def _url(path: str) -> str:
    return f"{settings.supabase_url}/storage/v1/object/{BUCKET}/{path}"


def _auth_headers() -> dict:
    return {"Authorization": f"Bearer {settings.supabase_secret_key}"}


async def upload_spreadsheet(path: str, data: bytes) -> str:
    async with httpx.AsyncClient(timeout=60.0) as client:
        resp = await client.post(
            _url(path),
            content=data,
            headers={**_auth_headers(), "Content-Type": XLSX_CONTENT_TYPE},
        )
        resp.raise_for_status()
    return path


async def download_spreadsheet(path: str) -> bytes:
    async with httpx.AsyncClient(timeout=60.0) as client:
        resp = await client.get(_url(path), headers=_auth_headers())
        resp.raise_for_status()
        return resp.content
