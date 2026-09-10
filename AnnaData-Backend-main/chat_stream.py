"""SSE for progress and sentence-checked text, followed by a final result."""
import json

import anyio
from starlette.responses import StreamingResponse


STAGES = {"understanding", "retrieving", "composing", "checking"}


def progress_response(run):
    def frame(event):
        return f"data: {json.dumps(event)}\n\n"

    async def events():
        yield frame({"type": "status", "stage": "connected"})
        sender, receiver = anyio.create_memory_object_stream(16)
        async with sender, receiver, anyio.create_task_group() as tasks:
            def progress(stage):
                if stage in STAGES:
                    anyio.from_thread.run(sender.send, {"type": "status", "stage": stage})

            def text(checked_text):
                if checked_text:
                    anyio.from_thread.run(sender.send, {"type": "delta", "text": checked_text})

            async def work():
                # Do not abandon a still-running provider call on disconnect:
                # admission middleware must retain its slot until work stops.
                try:
                    result = await anyio.to_thread.run_sync(lambda: run(progress, text))
                    await sender.send({"type": "result", **result})
                except Exception:
                    await sender.send({"type": "error", "detail":
                                       "Agent is temporarily unavailable"})

            tasks.start_soon(work)
            while True:
                with anyio.move_on_after(10) as heartbeat:
                    event = await receiver.receive()
                if heartbeat.cancel_called:
                    yield ": keep-alive\n\n"
                    continue
                yield frame(event)
                if event["type"] in {"result", "error"}:
                    break

    return StreamingResponse(events(), media_type="text/event-stream", headers={
        "Cache-Control": "no-cache, no-transform", "X-Accel-Buffering": "no",
    })
