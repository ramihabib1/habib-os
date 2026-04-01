"""SP-API Orders API queries."""

from __future__ import annotations

from typing import Any

from src.spapi.client import SPAPIClient
from src.utils.logging import get_logger

logger = get_logger(__name__)


async def get_orders(
    marketplace_id: str,
    last_updated_after: str,
) -> list[dict[str, Any]]:
    """
    Fetch orders updated after a given ISO-8601 timestamp.
    Handles pagination automatically.

    Returns a flat list of order objects.
    """
    async with SPAPIClient(marketplace_id=marketplace_id) as client:
        return await client.paginate(
            path="/orders/v0/orders",
            params={
                "MarketplaceIds": marketplace_id,
                "LastUpdatedAfter": last_updated_after,
            },
            data_key="Orders",
            next_token_key="NextToken",
            next_token_param="NextToken",
        )


async def get_order_items(order_id: str) -> list[dict[str, Any]]:
    """Fetch all items for a given order ID."""
    async with SPAPIClient() as client:
        return await client.paginate(
            path=f"/orders/v0/orders/{order_id}/orderItems",
            params={},
            data_key="OrderItems",
            next_token_key="NextToken",
            next_token_param="NextToken",
        )
