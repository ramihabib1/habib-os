"""
SP-API FBA inventory queries.

Architecture note (from research):
  The FBA Inventory API returns ALL SKUs that have EVER existed in the FBA
  network, including ghost/zombie SKUs with fulfillableQuantity=0 that are no
  longer active listings. There is no server-side filter to exclude them.

  Correct strategy: only query for SKUs that are confirmed active in our DB
  (is_active=true). Never use the full marketplace scan — it returns ghost SKUs.

  The fulfillableQuantity field is the ONLY customer-facing quantity — it
  represents units available to ship to customers right now.
"""

from __future__ import annotations

from typing import Any

from src.spapi.client import SPAPIClient
from src.utils.logging import get_logger

logger = get_logger(__name__)

_PATH = "/fba/inventory/v1/summaries"
_SKU_BATCH_SIZE = 50   # Amazon API max SKUs per targeted request


async def get_fba_inventory_summaries(
    marketplace_id: str,
    known_skus: list[str],
) -> list[dict[str, Any]]:
    """
    Fetch FBA inventory for a specific list of active SKUs.

    Only queries for skus in known_skus (targeted query). No full marketplace
    scan — that returns ghost SKUs and is unreliable.

    Args:
        marketplace_id: Amazon marketplace ID (e.g. A2EUQ1WTGCTBG2)
        known_skus: Active SKUs from our DB (products.is_active = true).
                    Caller must supply a non-empty list.

    Returns:
        Flat list of inventory summary objects from SP-API.
        Each item has: sellerSku, asin, fnSku, totalQuantity, inventoryDetails
    """
    if not known_skus:
        logger.warning("fba_inventory_no_skus_provided")
        return []

    results: dict[str, dict[str, Any]] = {}

    async with SPAPIClient(marketplace_id=marketplace_id) as client:
        targeted = await _fetch_by_skus(client, marketplace_id, known_skus)
        for s in targeted:
            sku = s.get("sellerSku")
            if sku:
                results[sku] = s

    logger.info(
        "fba_inventory_done",
        requested=len(known_skus),
        returned=len(results),
    )
    return list(results.values())


async def _fetch_by_skus(
    client: SPAPIClient,
    marketplace_id: str,
    skus: list[str],
) -> list[dict[str, Any]]:
    """Fetch inventory for specific SKUs in batches of up to 50."""
    results: list[dict[str, Any]] = []

    for i in range(0, len(skus), _SKU_BATCH_SIZE):
        batch = skus[i: i + _SKU_BATCH_SIZE]
        response = await client.get(_PATH, params={
            "details": "true",
            "granularityType": "Marketplace",
            "granularityId": marketplace_id,
            "marketplaceIds": marketplace_id,
            "sellerSkus": ",".join(batch),
        })
        payload = response.get("payload", {})
        results.extend(payload.get("inventorySummaries", []))

    return results
