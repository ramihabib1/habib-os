"""
SP-API Product Pricing API — batch price + BSR retrieval.

Uses getPricing (v0) with ItemType=Sku to fetch:
  - Our current listing price (ListingPrice)
  - Buy Box price (CompetitivePrice where CompetitivePriceId="1")
  - BSR / Sales Rankings (SalesRankings)

All three come from a single API call — no need for separate Catalog API calls.

Rate limits (post-2024 throttle reduction):
  - 0.5 req/s, burst 1
  - Max 20 SKUs per request
  - For 30 SKUs: 2 calls, ~4 seconds total with required delay
"""

from __future__ import annotations

import asyncio
from typing import Any

from src.config.settings import settings
from src.spapi.client import SPAPIClient
from src.utils.logging import get_logger

logger = get_logger(__name__)

_BATCH_SIZE = 20       # Amazon max SKUs per getPricing call
_BATCH_DELAY = 2.5     # seconds between batches (0.5/s rate limit, burst 1)


async def get_prices_batch(
    skus: list[str],
    marketplace_id: str,
) -> dict[str, dict[str, Any]]:
    """
    Fetch listing prices and BSR for a list of SKUs.

    Batches requests at 20 SKUs per call with 2.5s delay between batches
    to respect the 0.5/s rate limit.

    Returns dict of sku → {listing_price, buybox_price, buybox_is_ours,
                            currency, bsr, bsr_category}
    """
    results: dict[str, dict[str, Any]] = {}

    async with SPAPIClient(marketplace_id=marketplace_id) as client:
        for i in range(0, len(skus), _BATCH_SIZE):
            batch = skus[i: i + _BATCH_SIZE]
            batch_results = await _fetch_pricing_batch(client, batch, marketplace_id)
            results.update(batch_results)

            # Rate limit: wait between batches (not after the last one)
            if i + _BATCH_SIZE < len(skus):
                await asyncio.sleep(_BATCH_DELAY)

    logger.info("pricing_batch_done", total=len(results), skus_requested=len(skus))
    return results


async def _fetch_pricing_batch(
    client: SPAPIClient,
    skus: list[str],
    marketplace_id: str,
) -> dict[str, dict[str, Any]]:
    """Fetch pricing for up to 20 SKUs in a single API call."""
    resp = await client.get(
        "/products/pricing/v0/price",
        params={
            "MarketplaceId": marketplace_id,
            "ItemType": "Sku",
            "Skus": ",".join(skus),
        },
    )

    results: dict[str, dict[str, Any]] = {}
    payload = resp.get("payload", resp)  # v0 wraps in payload
    items = payload if isinstance(payload, list) else []

    for item in items:
        if item.get("status") != "Success":
            logger.debug("pricing_item_skip", sku=item.get("SellerSKU"), status=item.get("status"))
            continue

        sku = item.get("SellerSKU", "")
        product = item.get("Product", {})

        listing_price, currency = _extract_listing_price(product)
        buybox_price, buybox_is_ours = _extract_buybox(product)
        bsr, bsr_category = _extract_bsr(product)

        results[sku] = {
            "listing_price": listing_price,
            "buybox_price": buybox_price,
            "buybox_is_ours": buybox_is_ours,
            "currency": currency,
            "bsr": bsr,
            "bsr_category": bsr_category,
        }

    logger.debug("pricing_batch_fetched", count=len(results), batch_size=len(skus))
    return results


def _extract_listing_price(product: dict) -> tuple[float | None, str | None]:
    """
    Extract our listing price from the Offers array.
    Returns (price, currency_code).
    """
    offers = product.get("Offers", [])
    for offer in offers:
        buying_price = offer.get("BuyingPrice", {})
        listing = buying_price.get("ListingPrice", {})
        amount = listing.get("Amount")
        currency = listing.get("CurrencyCode")
        if amount is not None:
            return float(amount), currency
    return None, None


def _extract_buybox(product: dict) -> tuple[float | None, bool]:
    """
    Extract Buy Box price (CompetitivePriceId="1" = New Buy Box).
    Returns (buybox_price, is_ours).
    """
    competitive = product.get("CompetitivePricing", {})
    prices = competitive.get("CompetitivePrices", [])

    for cp in prices:
        if str(cp.get("CompetitivePriceId")) == "1":
            price_obj = cp.get("Price", {})
            landed = price_obj.get("LandedPrice", {})
            amount = landed.get("Amount")
            is_ours = cp.get("belongsToRequester", False)
            if amount is not None:
                return float(amount), bool(is_ours)

    return None, False


def _extract_bsr(product: dict) -> tuple[int | None, str | None]:
    """
    Extract the best (lowest rank number) BSR from SalesRankings.
    Returns (rank, category_name).
    """
    rankings = product.get("SalesRankings", [])
    if not rankings:
        return None, None

    best = min(rankings, key=lambda r: r.get("Rank", 999_999_999))
    rank = best.get("Rank")
    category = best.get("ProductCategoryId")

    return (int(rank) if rank is not None else None), category
