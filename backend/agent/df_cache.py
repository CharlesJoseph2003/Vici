"""
Module-level LRU cache of parsed spreadsheet DataFrames.

Keyed by `storage_path` (not spreadsheet_id) so the cache is self-invalidating
when a spreadsheet is updated in place — every update writes bytes to a fresh
storage_path, which is naturally a fresh cache key. The old path's DataFrame
lingers unused until LRU eviction claims it.

Only the raw DataFrame is cached. The RoiAnalyzer wrapper (with its mutable
business_units filter) is instantiated fresh per request so concurrent chats
never see each other's filter state.

Concurrency: if two coroutines miss on the same key at once, only the first
triggers the download+parse; the rest await the same Future.
"""
from __future__ import annotations

from asyncio import Future, get_running_loop
from collections import OrderedDict

import pandas as pd

from backend.ingestion.parser import parse_from_bytes
from backend.storage import download_spreadsheet

_MAX_ENTRIES = 20
_cache: OrderedDict[str, pd.DataFrame] = OrderedDict()
_inflight: dict[str, Future] = {}


async def get_dataframe(storage_path: str) -> pd.DataFrame:
    if storage_path in _cache:
        _cache.move_to_end(storage_path)
        return _cache[storage_path]

    if storage_path in _inflight:
        return await _inflight[storage_path]

    loop = get_running_loop()
    future: Future = loop.create_future()
    _inflight[storage_path] = future
    try:
        data = await download_spreadsheet(storage_path)
        df = parse_from_bytes(data)
        _cache[storage_path] = df
        _cache.move_to_end(storage_path)
        while len(_cache) > _MAX_ENTRIES:
            _cache.popitem(last=False)
        future.set_result(df)
        return df
    except Exception as e:
        future.set_exception(e)
        raise
    finally:
        _inflight.pop(storage_path, None)


def invalidate(storage_path: str) -> None:
    """Explicit eviction — rarely needed since path changes invalidate naturally."""
    _cache.pop(storage_path, None)


def stats() -> dict:
    return {"size": len(_cache), "in_flight": len(_inflight), "keys": list(_cache.keys())}
