"""Polymarket CLOB WebSocket client."""
import asyncio
import json
import ssl
from typing import Any, Callable, Optional

import websockets

from src.constants import POLYMARKET_CLOB_WS_URL
from src.utils.logging import get_logger

log = get_logger(__name__)


class PolymarketWSClient:
    """Manages WebSocket connection to Polymarket CLOB."""

    def __init__(
        self,
        wss_url: str = POLYMARKET_CLOB_WS_URL,
        asset_ids_provider: Optional[Callable[[], list[str]]] = None,
        idle_retry_seconds: int = 60,
    ) -> None:
        self.wss_url = wss_url
        self._ws: Optional[websockets.WebSocketClientProtocol] = None
        self._listen_task: Optional[asyncio.Task] = None
        self._asset_ids_provider = asset_ids_provider
        self._idle_retry_seconds = idle_retry_seconds
        self._callbacks: dict[str, list[Callable]] = {
            "market_resolved": [],
            "error": [],
            "close": [],
        }

    def on(self, event: str, callback: Callable) -> None:
        """Register event callback."""
        if event in self._callbacks:
            self._callbacks[event].append(callback)

    def emit(self, event: str, data: Any) -> None:
        """Emit event to all registered callbacks."""
        for callback in self._callbacks.get(event, []):
            if asyncio.iscoroutinefunction(callback):
                asyncio.create_task(callback(data))
            else:
                callback(data)

    async def start(self) -> None:
        """Start the WebSocket client with auto-reconnection."""
        while True:
            try:
                asset_ids = self._get_asset_ids()
                if not asset_ids:
                    log.info(
                        "Skipping Polymarket market WS connect because no asset ids are currently tracked",
                        retry_in_seconds=self._idle_retry_seconds,
                    )
                    await asyncio.sleep(self._idle_retry_seconds)
                    continue

                await self.connect()
                # Wait for the listen loop to finish (e.g. on disconnect)
                if self._listen_task:
                    await self._listen_task
            except Exception as e:
                log.error("Polymarket WS error, reconnecting in 5s...", error=str(e))
                await asyncio.sleep(5)

    async def connect(self) -> None:
        """Establish WebSocket connection and subscribe to market updates."""
        asset_ids = self._get_asset_ids()
        if not asset_ids:
            raise RuntimeError("No asset ids available for Polymarket market subscription")

        log.info("Connecting to Polymarket CLOB WebSocket", url=self.wss_url)
        ssl_context = ssl.create_default_context()
        self._ws = await websockets.connect(
            self.wss_url,
            ping_interval=30,
            ping_timeout=10,
            ssl=ssl_context,
        )
        
        # Subscribe to tracked tokens on the market channel to get resolution updates.
        subscribe_msg = {
            "type": "market",
            "assets_ids": asset_ids,
            "custom_feature_enabled": True,
        }
        await self._ws.send(json.dumps(subscribe_msg))
        log.info("Subscribed to Polymarket market channel", asset_count=len(asset_ids))

        self._listen_task = asyncio.create_task(self._listen_loop())

    def _get_asset_ids(self) -> list[str]:
        """Return the distinct token ids that should be subscribed on the market channel."""
        if not self._asset_ids_provider:
            return []

        raw_asset_ids = self._asset_ids_provider() or []
        asset_ids: list[str] = []
        seen: set[str] = set()
        for asset_id in raw_asset_ids:
            if not asset_id or asset_id in seen:
                continue
            seen.add(asset_id)
            asset_ids.append(asset_id)
        return asset_ids

    async def _listen_loop(self) -> None:
        """Continuously listen for messages from the WebSocket."""
        if not self._ws:
            return

        try:
            async for message in self._ws:
                try:
                    data = json.loads(message)
                    if isinstance(data, list):
                        log.debug("Received WS batch message", event_count=len(data))
                        for event in data:
                            self._handle_event(event)
                    else:
                        self._handle_event(data)
                except json.JSONDecodeError:
                    log.warning("Received invalid JSON from Polymarket WS")
        except websockets.ConnectionClosed:
            log.info("Polymarket WS connection closed")
            self.emit("close", None)
        except Exception as e:
            log.error("Error in Polymarket WS listen loop", error=str(e))
            self.emit("error", e)

    def _handle_event(self, event: Any) -> None:
        """Distribute events to registered callbacks."""
        if not isinstance(event, dict):
            return

        event_type = event.get("event_type") or event.get("type")
        if event_type:
            log.debug("Polymarket WS event", event_type=event_type)
        if event_type == "market_resolved":
            payload = event.get("data", event)
            log.debug(
                "Forwarding market_resolved event",
                condition_id=payload.get("condition_id") or payload.get("conditionId"),
            )
            self.emit("market_resolved", event)

    async def disconnect(self) -> None:
        """Close WebSocket connection."""
        if self._listen_task:
            self._listen_task.cancel()
            self._listen_task = None
        if self._ws:
            await self._ws.close()
            self._ws = None
        log.info("Polymarket WS disconnected")
