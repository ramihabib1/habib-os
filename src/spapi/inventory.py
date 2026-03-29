"""SP-API FBA inventory queries."""

from __future__ import annotations

from typing import Any

from src.spapi.client import SPAPIClient
from src.utils.logging import get_logger

logger = get_logger(__name__)

_PATH = "/fba/inventory/v1/summaries"


async def get_fba_inventory_summaries(marketplace_id: str) -> list[dict[str, Any]]:
    """
    Fetch all FBA inventory summaries for a marketplace.

    Returns a flat list of inventory summary objects from SP-API.
    Each item contains sellerSku, fulfillableQuantity, reservedQuantity, etc.
    """
    client = SPAPIClient(marketplace_id=marketplace_id)
    return await client.paginate(
        path=_PATH,
        params={
            "details": "true",
            "granularityType": "Marketplace",
            "granularityId": marketplace_id,
            "marketplaceIds": marketplace_id,
        },
        data_key="inventorySummaries",
        next_token_key="nextToken",
        next_token_param="nextToken",
    )
