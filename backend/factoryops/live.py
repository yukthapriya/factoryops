"""WebSocket fan-out of Postgres NOTIFY messages.

One LISTEN connection per API process feeds every connected WebSocket client. Each client
has a bounded queue; when a slow client's queue is full its oldest message is dropped (and a
`lagged` flag tells the UI to refetch), so one slow browser can't stall the others.
"""

import asyncio
import contextlib
import json
import logging

import psycopg
from fastapi import WebSocket
from sqlalchemy.engine import make_url

from factoryops.config import get_settings
from factoryops.notify import envelope

log = logging.getLogger(__name__)

CLIENT_QUEUE_SIZE = 500
HEARTBEAT_SECONDS = 20


class Client:
    def __init__(self, ws: WebSocket):
        self.ws = ws
        self.queue: asyncio.Queue[str] = asyncio.Queue(maxsize=CLIENT_QUEUE_SIZE)
        self.dropped = 0

    def offer(self, message: str) -> None:
        if self.queue.full():
            with contextlib.suppress(asyncio.QueueEmpty):
                self.queue.get_nowait()
            self.dropped += 1
        self.queue.put_nowait(message)


class LiveHub:
    def __init__(self) -> None:
        self.clients: set[Client] = set()
        self.listening = False
        self._task: asyncio.Task | None = None

    def register(self, ws: WebSocket) -> Client:
        client = Client(ws)
        self.clients.add(client)
        return client

    def unregister(self, client: Client) -> None:
        self.clients.discard(client)

    def broadcast(self, message: str) -> None:
        for client in list(self.clients):
            client.offer(message)

    def status_message(self) -> str:
        return json.dumps(envelope("hello", {"listener_connected": self.listening}))

    async def start(self) -> None:
        self._task = asyncio.create_task(self._listen_forever())

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task

    async def _listen_forever(self) -> None:
        settings = get_settings()
        conninfo = (
            make_url(settings.database_url)
            .set(drivername="postgresql")
            .render_as_string(hide_password=False)
        )
        delay = 1.0
        while True:
            try:
                async with await psycopg.AsyncConnection.connect(
                    conninfo, autocommit=True, connect_timeout=5
                ) as conn:
                    await conn.execute(f'LISTEN "{settings.notify_channel}"')
                    self.listening = True
                    delay = 1.0
                    log.info("live listener connected")
                    self.broadcast(json.dumps(envelope("listener", {"connected": True})))
                    async for notification in conn.notifies():
                        self.broadcast(notification.payload)
            except asyncio.CancelledError:
                self.listening = False
                raise
            except Exception as exc:
                if self.listening:
                    self.broadcast(json.dumps(envelope("listener", {"connected": False})))
                self.listening = False
                log.warning("live listener disconnected", extra={"error": str(exc)[:200]})
                await asyncio.sleep(delay)
                delay = min(delay * 2, 30.0)


hub = LiveHub()


async def serve_client(ws: WebSocket, hub: LiveHub = hub) -> None:
    await ws.accept()
    client = hub.register(ws)
    await ws.send_text(hub.status_message())

    async def receiver() -> None:
        # Drain client frames (pings) so disconnects are detected promptly.
        while True:
            await ws.receive_text()

    recv_task = asyncio.create_task(receiver())
    try:
        while not recv_task.done():
            try:
                message = await asyncio.wait_for(client.queue.get(), timeout=HEARTBEAT_SECONDS)
            except TimeoutError:
                message = json.dumps(envelope("heartbeat", {"listener_connected": hub.listening}))
            if client.dropped:
                await ws.send_text(json.dumps(envelope("lagged", {"dropped": client.dropped})))
                client.dropped = 0
            await ws.send_text(message)
    except Exception:  # client went away
        pass
    finally:
        recv_task.cancel()
        hub.unregister(client)
