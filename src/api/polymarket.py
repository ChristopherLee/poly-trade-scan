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

    def __init__(self, wss_url: str = POLYMARKET_CLOB_WS_URL) -> None:
        self.wss_url = wss_url
        self._ws: Optional[websockets.WebSocketClientProtocol] = None
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
                await self.connect()
                # Wait for the listen loop to finish (e.g. on disconnect)
                await self._listen_task
            except Exception as e:
                log.error("Polymarket WS error, reconnecting in 5s...", error=str(e))
                await asyncio.sleep(5)

    async def connect(self) -> None:
        """Establish WebSocket connection and subscribe to market updates."""
        log.info("Connecting to Polymarket CLOB WebSocket", url=self.wss_url)
        ssl_context = ssl.create_default_context()
        self._ws = await websockets.connect(
            self.wss_url,
            ping_interval=30,
            ping_timeout=10,
            ssl=ssl_context,
        )
        
        # Subscribe to the 'market' channel to get resolution updates
        subscribe_msg = {
            "type": "subscribe",
            "channels": ["market"],
            "custom_feature_enabled": True
        }
        await self._ws.send(json.dumps(subscribe_msg))
        log.info("Subscribed to Polymarket market channel")
        
        self._listen_task = asyncio.create_task(self._listen_loop())

    async def _listen_loop(self) -> None:
        """Continuously listen for messages from the WebSocket."""
        if not self._ws:
            return

        try:
            async for message in self._ws:
                try:
                    data = json.loads(message)
                    if isinstance(data, list):
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
        if event_type == "market_resolved":
            self.emit("market_resolved", event)

    async def disconnect(self) -> None:
        """Close WebSocket connection."""
        if self._ws:
            await self._ws.close()
            self._ws = None
        log.info("Polymarket WS disconnected")
