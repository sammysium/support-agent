import asyncio
import json
from typing import Awaitable, Callable

from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from src.exceptions import AppException
from src.utils.log import log_me

Emit = Callable[[str], None]


def _event(name: str, data: dict) -> str:
    return f"event: {name}\ndata: {json.dumps(data)}\n\n"


def stream_work(work: Callable[[Emit], Awaitable[BaseModel]], status_code: int = 200) -> StreamingResponse:
    """Server-Sent Events for work that takes a while: a `progress` event ({"message"}) for every emit(...),
    then one `result` event with what work returns, or one `error` event ({"detail"}) if it fails.
    Problems found before the work starts (unknown chat, bad request) should be raised before calling this,
    so they still get their own HTTP status."""

    async def events():
        queue: asyncio.Queue[str | None] = asyncio.Queue()

        def emit(message: str):
            queue.put_nowait(_event("progress", {"message": message}))

        async def run():
            try:
                result = await work(emit)
                queue.put_nowait(_event("result", result.model_dump(mode="json")))
            except AppException as e:
                queue.put_nowait(_event("error", {"detail": e.message}))
            except Exception as e:
                log_me(f"stream failed: {e!r}")
                queue.put_nowait(_event("error", {"detail": "Something went wrong while checking. Please try again."}))
            finally:
                queue.put_nowait(None)

        # the work runs as its own task: an officer closing the app mid-search doesn't cut a stage off half-saved
        task = asyncio.create_task(run())
        while (item := await queue.get()) is not None:
            yield item
        await task

    return StreamingResponse(events(), status_code=status_code, media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})
