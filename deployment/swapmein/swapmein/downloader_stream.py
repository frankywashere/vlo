"""Progress JSON lines followed by a length-checked raw MP4, without base64."""
import json
from queue import Empty, Full, Queue
from threading import Event, Thread
import anyio
from fastapi.responses import StreamingResponse

from . import video_downloader
from .errors import PublicError

MEDIA_TYPE = "application/vnd.recastfx.download-stream"


class DownloadStreamResponse(StreamingResponse):
    def __init__(self, records, account, url):
        self.source = stream(records, account, url)
        super().__init__(self.source, media_type=MEDIA_TYPE,
                         headers={"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"})

    async def __call__(self, scope, receive, send):
        try:
            await super().__call__(scope, receive, send)
        finally:
            with anyio.CancelScope(shield=True):
                await anyio.to_thread.run_sync(self.source.close)


def stream(records, account, url):
    queue = Queue(maxsize=8)
    cancel = Event()
    def publish(value):
        while not cancel.is_set():
            try:
                queue.put(value, timeout=.2)
                return True
            except Full:
                pass
        return False
    def prepare():
        try:
            output, directory = video_downloader.prepare(records, account, url,
                progress=lambda stage: publish(("status", stage)), cancel=cancel)
            if not publish(("ready", (output, directory))):
                video_downloader.cleanup(directory)
        except Exception as error:
            publish(("error", error))
    worker = Thread(target=prepare, daemon=True)
    directory = None
    worker.start()
    try:
        yield b'{"event":"status","stage":"preparing"}\n'
        while True:
            try:
                kind, value = queue.get(timeout=1)
            except Empty:
                yield b'\n'  # Keep the response active while work runs.
                continue
            if kind == "status":
                yield (json.dumps({"event": kind, "stage": value}) + "\n").encode()
            elif kind == "error":
                message = str(value) if isinstance(value, PublicError) else "The download did not finish. Please try again."
                yield (json.dumps({"event": kind, "message": message}) + "\n").encode()
                return
            else:
                output, directory = value
                yield (json.dumps({"event": "ready", "bytes": output.stat().st_size}) + "\n").encode()
                with output.open("rb") as handle:
                    while chunk := handle.read(256 * 1024):
                        yield chunk
                return
    finally:
        cancel.set()
        worker.join(timeout=5)
        if directory is not None:
            video_downloader.cleanup(directory)
        else:
            # A ready result queued immediately before cancellation still owns files.
            while not queue.empty():
                kind, value = queue.get_nowait()
                if kind == "ready": video_downloader.cleanup(value[1])
