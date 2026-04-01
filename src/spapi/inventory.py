"""SP-API FBA inventory queries."""

from __future__ import annotations

from typing import Any

from src.spapi.client import SPAPIClient
from src.utils.logging import get_logger

logger = get_logger(__name__)

_PATH = "/fba/inventory/v1/summaries"
# Amazon API max SKUs per targeted request
_SKU_BATCH_SIZE = 50


async def get_fba_inventory_summaries(
    marketplace_id: str,
    known_skus: list[str] | None = None,
) -> list[dict[str, Any]]:
    """
    Fetch all FBA inventory summaries for a marketplace.

    Strategy (two-phase):
    1. Targeted query for all known_skus (reliable, avoids pagination issues).
       This is the primary source — ensures every product in our DB is covered.
    2. Full marketplace query (single page, no pagination) to detect unknown/new
       SKUs not yet in our products table. These are merged in by SKU.

    The full marketplace query silently omits some active SKUs (Amazon API quirk),
    so the targeted query is the authoritative source for known products.

    Returns a flat list of inventory summary objects from SP-API.
    Each item contains sellerSku, fulfillableQuantity, reservedQuantity, etc.
    """
    results: dict[str, dict[str, Any]] = {}  # keyed by sellerSku, deduplicates

    async with SPAPIClient(marketplace_id=marketplace_id) as client:
        # Phase 1: targeted query for all known SKUs (primary, authoritative)
        if known_skus:
            targeted = await _fetch_by_skus(client, marketplace_id, known_skus)
            for s in targeted:
                results[s["sellerSku"]] = s
            logger.info("fba_inventory_targeted_query", known=len(known_skus), returned=len(targeted))

        # Phase 2: single-page full marketplace query to catch unknown SKUs
        response = await client.get(_PATH, params={
            "details": "true",
            "granularityType": "Marketplace",
            "granularityId": marketplace_id,
            "marketplaceIds": marketplace_id,
        })
        payload = response.get("payload", {})
        full_page = payload.get("inventorySummaries", [])
        new_unknown = 0
        for s in full_page:
            sku = s.get("sellerSku")
            if sku and sku not in results:
                results[sku] = s
                new_unknown += 1

    if new_unknown:
        logger.info("fba_inventory_unknown_skus_found", count=new_unknown)

    logger.info("fba_inventory_total", total=len(results))
    return list(results.values())


async def _fetch_by_skus(
    client: SPAPIClient,
    marketplace_id: str,
    skus: list[str],
) -> list[dict[str, Any]]:
    """Fetch inventory for specific SKUs in batches of up to 50."""
    results: list[dict[str, Any]] = []

    for i in range(0, len(skus), _SKU_BATCH_SIZE):
        batch = skus[i : i + _SKU_BATCH_SIZE]
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
