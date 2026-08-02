"""Durable independent-source polling and at-least-once delivery primitives."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol
import datetime as dt
import hashlib
import json

from .store import Store


SOURCE_IDS = (
    "org-issues", "org-comments", "product-issues", "product-comments",
    "product-workflow-runs",
)


@dataclass(frozen=True)
class Page:
    items: list[dict[str, Any]]
    next_cursor: str | None
    etag: str | None = None


@dataclass(frozen=True)
class PollResult:
    source_id: str
    complete: bool
    page_count: int
    inserted: int
    watermark: str
    error: str | None = None


class Source(Protocol):
    def fetch_page(self, *, watermark: str, page_cursor: str | None,
                   etag: str | None) -> Page: ...


def event_identity(source_id: str, item: dict[str, Any]) -> tuple[str, str, str]:
    """Return external id, version key, and occurred-at for durable deduplication."""
    updated = str(item.get("updated_at") or item.get("created_at") or "")
    if source_id.endswith("comments"):
        external = str(item["id"])
        return external, f"{external}:{updated}", updated
    if source_id.endswith("workflow-runs"):
        external = str(item["id"])
        attempt = str(item.get("run_attempt") or 1)
        version = ":".join((external, attempt, str(item.get("status") or ""),
                            str(item.get("conclusion") or ""), updated))
        return external, version, updated
    external = str(item["number"])
    return external, f"{external}:{updated}", updated


def query_fingerprint(source_id: str, watermark: str) -> str:
    return hashlib.sha256(f"{source_id}\0{watermark}".encode()).hexdigest()


def overlap_watermark(watermark: str, seconds: int) -> str:
    """Re-read a bounded window so delayed GitHub indexing cannot lose events."""
    if seconds < 0:
        raise ValueError("checkpoint overlap must not be negative")
    try:
        instant = dt.datetime.fromisoformat(watermark.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"invalid ISO-8601 source watermark: {watermark!r}") from exc
    if instant.tzinfo is None:
        raise ValueError("source watermark must include a timezone")
    value = instant - dt.timedelta(seconds=seconds)
    return value.astimezone(dt.timezone.utc).isoformat().replace("+00:00", "Z")


def poll_source(store: Store, source_id: str, source: Source, *, initial_watermark: str) -> PollResult:
    if source_id not in SOURCE_IDS:
        raise ValueError(f"unknown source id: {source_id}")
    checkpoint = store.checkpoint(source_id, initial_watermark=initial_watermark)
    watermark = checkpoint["watermark"]
    query_watermark = overlap_watermark(watermark, int(checkpoint["overlap_seconds"]))
    fingerprint = query_fingerprint(source_id, query_watermark)
    reusable_etag = (checkpoint["etag"] if checkpoint["query_fingerprint"] == fingerprint
                     and checkpoint["last_complete_snapshot_id"] else None)
    snapshot_id = store.begin_snapshot(source_id)
    page_cursor = None
    pages = 0
    maximum = watermark
    final_etag = None
    try:
        while True:
            page = source.fetch_page(watermark=query_watermark, page_cursor=page_cursor,
                                     etag=reusable_etag if pages == 0 else None)
            pages += 1
            final_etag = page.etag or final_etag
            for item in page.items:
                external, version, occurred = event_identity(source_id, item)
                if not occurred:
                    raise ValueError(f"{source_id} event has no occurrence timestamp")
                maximum = max(maximum, occurred)
                store.stage_observation(snapshot_id, source_id, external, version, occurred, item)
            if page.next_cursor is None:
                break
            page_cursor = page.next_cursor
    except Exception as exc:
        store.finish_snapshot(snapshot_id, complete=False, page_count=pages,
                              maximum_event_time=maximum, error=f"{type(exc).__name__}: {exc}")
        return PollResult(source_id, False, pages, 0, watermark, str(exc))
    published = store.finish_snapshot(
        snapshot_id, complete=True, page_count=pages,
        maximum_event_time=maximum, next_watermark=maximum,
        query_fingerprint=query_fingerprint(
            source_id,
            overlap_watermark(maximum, int(checkpoint["overlap_seconds"])),
        ), etag=final_etag)
    return PollResult(source_id, True, pages, published, maximum)


def route_key(role: str, events: list[dict[str, Any]]) -> str:
    identities = sorted(str(event["event_id"]) for event in events)
    return hashlib.sha256(json.dumps([role, identities], separators=(",", ":")).encode()).hexdigest()
