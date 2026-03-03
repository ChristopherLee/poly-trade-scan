"""Application constants."""
import os
from pathlib import Path

from dotenv import load_dotenv

# Project root directory (parent of src/)
PROJECT_ROOT = Path(__file__).parent.parent

# Configuration directory
CONFIG_DIR = PROJECT_ROOT / "config"

# Load environment variables from .env in project root
load_dotenv(PROJECT_ROOT / ".env")

# Polygon RPC endpoint - configurable via POLYGON_WSS_URL env var.
# Default to a public endpoint that supports both websocket and HTTPS derivation.
POLYGON_WSS_URL = os.getenv("POLYGON_WSS_URL", "wss://polygon.drpc.org")

# Default path for wallets file
DEFAULT_WALLETS_FILE = CONFIG_DIR / "wallets.txt"

# RPC retry settings
RPC_MAX_RETRIES = 3
RPC_RETRY_DELAY_SECONDS = 1.0
RPC_TIMEOUT_SECONDS = 2
RPC_POLL_INTERVAL_SECONDS = float(os.getenv("RPC_POLL_INTERVAL_SECONDS", "1.0"))

# Polymarket CLOB market channel
POLYMARKET_CLOB_WS_URL = "wss://ws-subscriptions-clob.polymarket.com/ws/market"
