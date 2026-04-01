"""SP-API Catalog Items API — BSR, ratings, reviews count."""

from __future__ import annotations

from typing import Any

from src.spapi.client import SPAPIClient
from src.utils.logging import get_logger

logger = get_logger(__name__)


async def get_catalog_item(
    asin: str,
    marketplace_id: str,
) -> dict[str, Any]:
    """
    Fetch catalog item details for an ASIN.
    Returns salesRanks, summaries (rating, reviewCount), etc.
    """
    async with SPAPIClient(marketplace_id=marketplace_id) as client:
        return await client.get(
            f"/catalog/2022-04-01/items/{asin}",
            params={
                "marketplaceIds": marketplace_id,
                "includedData": "salesRanks,summaries",
            },
        )


async def get_reviews_for_asin(
    asin: str,
    marketplace_id: str,
) -> dict[str, Any]:
    """
    Fetch review summary for an ASIN via the catalog API.
    The SP-API does not provide individual review text; that requires
    the Product Reviews API (separate approval). This returns rating + count.
    """
    return await get_catalog_item(asin, marketplace_id)
