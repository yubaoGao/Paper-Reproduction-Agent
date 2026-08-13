"""SSE formatting and PostgreSQL-backed replay/live tailing."""

from __future__ import annotations

import asyncio
import json


def encode_event(event) -> str:
    data = {
        "event_id": event.event_id,
        "sequence": event.sequence,
        "intake_id": event.intake_id,
        "job_id": event.job_id,
        "type": event.event_type.value,
        "payload": event.payload,
        "created_at": event.created_at.isoformat(),
    }
    return (
        f"id: {event.sequence}\n"
        f"event: {event.event_type.value}\n"
        f"data: {json.dumps(data, separators=(',', ':'), ensure_ascii=False)}\n\n"
    )


async def stream_job_events(request, service, *, job_id: str, principal: str, after_sequence: int, poll_seconds: float = 0.5):
    cursor = after_sequence
    heartbeat = 0
    while not await request.is_disconnected():
        events = await asyncio.to_thread(
            service.events, job_id, principal=principal, after_sequence=cursor,
        )
        if events:
            for event in events:
                cursor = event.sequence
                yield encode_event(event)
            heartbeat = 0
        else:
            heartbeat += 1
            if heartbeat >= max(1, int(15 / poll_seconds)):
                yield ": keep-alive\n\n"
                heartbeat = 0
        await asyncio.sleep(poll_seconds)
