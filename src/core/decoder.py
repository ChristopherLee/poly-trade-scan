"""Transaction decoder for Polymarket orders."""
from typing import Optional

from eth_abi import decode

from src.core.abi import MATCH_ORDERS_ABI_TYPES, MATCH_ORDERS_SELECTOR
from src.core.models import DecodedOrder
from src.utils.logging import get_logger

log = get_logger(__name__)


class TransactionDecoder:
    """Decodes Polymarket transactions using ABI decoding."""

    def __init__(self) -> None:
        log.info("Decoder initialized", selector=MATCH_ORDERS_SELECTOR.hex())

    def decode(self, tx_input: str) -> Optional[list[DecodedOrder]]:
        """Decode transaction input data.

        Returns None if not a Polymarket matchOrders transaction.
        Supports both Fee Module and NegRisk Fee Module 2 contracts.
        """
        try:
            input_bytes = bytes.fromhex(
                tx_input[2:] if tx_input.startswith("0x") else tx_input
            )

            if len(input_bytes) < 4:
                return None

            # Check function selector (first 4 bytes)
            selector = input_bytes[:4]

            # Both Fee Module and NegRisk Fee Module 2 use the same selector
            if selector != MATCH_ORDERS_SELECTOR:
                return None

            decoded = decode(MATCH_ORDERS_ABI_TYPES, input_bytes[4:])

            return self._extract_orders(decoded)
        except Exception:
            # Expected for non-Polymarket transactions with same selector
            return None

    def _extract_orders(self, decoded: tuple) -> list[DecodedOrder]:
        """Extract all orders from decoded parameters."""
        orders = []

        taker_order, maker_orders, taker_fill_amount, taker_receive_amount, maker_fill_amounts, *_ = decoded

        orders.append(
            self._parse_order(
                taker_order,
                fill_maker_amount=int(taker_fill_amount),
                fill_taker_amount=int(taker_receive_amount),
            )
        )
        for idx, order in enumerate(maker_orders):
            maker_fill_amount = int(maker_fill_amounts[idx]) if idx < len(maker_fill_amounts) else 0
            orders.append(
                self._parse_order(
                    order,
                    fill_maker_amount=maker_fill_amount,
                    fill_taker_amount=self._derive_taker_fill(order, maker_fill_amount),
                )
            )

        return orders

    def _parse_order(
        self,
        order_tuple: tuple,
        fill_maker_amount: int = 0,
        fill_taker_amount: int = 0,
    ) -> DecodedOrder:
        """Parse order tuple into DecodedOrder dataclass."""
        return DecodedOrder(
            salt=order_tuple[0],
            maker=order_tuple[1],
            signer=order_tuple[2],
            taker=order_tuple[3],
            token_id=str(order_tuple[4]),
            maker_amount=order_tuple[5],
            taker_amount=order_tuple[6],
            expiration=order_tuple[7],
            nonce=order_tuple[8],
            fee_rate_bps=order_tuple[9],
            side=order_tuple[10],
            signature_type=order_tuple[11],
            signature=order_tuple[12],
            fill_maker_amount=fill_maker_amount,
            fill_taker_amount=fill_taker_amount,
        )

    def _derive_taker_fill(self, order_tuple: tuple, maker_fill_amount: int) -> int:
        """Convert a maker-leg fill amount into the corresponding taker-leg fill amount."""
        order_maker_amount = int(order_tuple[5])
        order_taker_amount = int(order_tuple[6])
        if maker_fill_amount <= 0 or order_maker_amount <= 0 or order_taker_amount <= 0:
            return 0
        return (maker_fill_amount * order_taker_amount) // order_maker_amount
