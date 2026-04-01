"""
SP-API Listings module.

Responsibilities:
  1. Seller ID discovery — extracted from getPricing response and cached.
  2. Active listings sync — GET_MERCHANT_LISTINGS_ALL_DATA report (async workflow).
  3. Listing enrichment — getListingsItem per SKU for status, fnSku, offers.

Architecture note:
  Amazon is the source of truth for which SKUs exist and are active.
  This module is the canonical interface between Amazon listings and our DB.
"""

from __future__ import annotations

import asyncio
import csv
import io
import time
from typing import Any

import httpx

from src.config.settings import settings
from src.spapi.client import SPAPIClient
from src.utils.logging import get_logger

logger = get_logger(__name__)

# Module-level seller ID cache
_seller_id: str | None = None
_seller_id_lock = asyncio.Lock()

# Report polling config
_REPORT_POLL_INTERVAL = 10   # seconds between status checks
_REPORT_TIMEOUT = 180        # max seconds to wait for report


async def get_seller_id() -> str:
    """
    Return the Amazon Seller ID for this account.

    Extracted from a getPricing API response on first call, then cached
    for the lifetime of the process. The seller ID is the same across all
    North American marketplaces.
    """
    global _seller_id

    if _seller_id:
        return _seller_id

    async with _seller_id_lock:
        if _seller_id:
            return _seller_id

        logger.info("seller_id_discovery")
        # Pull any one active ASIN from settings to use as the probe
        probe_asin = settings.PROBE_ASIN
        async with SPAPIClient(marketplace_id=settings.SP_API_MARKETPLACE_CA) as client:
            resp = await client.post(
                f"/products/fees/v0/items/{probe_asin}/feesEstimate",
                body={
                    "FeesEstimateRequest": {
                        "MarketplaceId": settings.SP_API_MARKETPLACE_CA,
                        "IsAmazonFulfilled": True,
                        "PriceToEstimateFees": {
                            "ListingPrice": {"CurrencyCode": "CAD", "Amount": 10},
                            "Shipping": {"CurrencyCode": "CAD", "Amount": 0},
                        },
                        "Identifier": "seller-id-probe",
                    }
                },
            )

        try:
            seller_id = (
                resp["payload"]["FeesEstimateResult"]
                ["FeesEstimateIdentifier"]["SellerId"]
            )
            _seller_id = seller_id
            logger.info("seller_id_discovered", seller_id=seller_id)
        except (KeyError, TypeError) as exc:
            raise RuntimeError(
                f"Could not extract SellerId from fees response: {exc}\nResponse: {resp}"
            ) from exc

    return _seller_id


async def sync_active_listings(marketplace_id: str) -> list[dict[str, Any]]:
    """
    Fetch all active listings via GET_MERCHANT_LISTINGS_ALL_DATA report.

    This is the ONLY SP-API endpoint that returns all listings without
    knowing SKUs in advance. Amazon is the source of truth.

    Returns a list of dicts with keys:
        seller_sku, asin, price, status, title, fulfillment_channel, quantity
    """
    async with SPAPIClient(marketplace_id=marketplace_id) as client:
        # Step 1: Request the report
        logger.info("listings_report_requesting")
        resp = await client.post("/reports/2021-06-30/reports", body={
            "reportType": "GET_MERCHANT_LISTINGS_ALL_DATA",
            "marketplaceIds": [marketplace_id],
        })
        report_id = resp.get("reportId")
        if not report_id:
            raise RuntimeError(f"No reportId in response: {resp}")
        logger.info("listings_report_requested", report_id=report_id)

        # Step 2: Poll until DONE
        deadline = time.time() + _REPORT_TIMEOUT
        report_doc_id: str | None = None

        while time.time() < deadline:
            status_resp = await client.get(f"/reports/2021-06-30/reports/{report_id}")
            status = status_resp.get("processingStatus")
            logger.debug("listings_report_status", status=status)

            if status == "DONE":
                report_doc_id = status_resp.get("reportDocumentId")
                break
            if status in ("CANCELLED", "FATAL"):
                raise RuntimeError(f"Report {report_id} failed with status: {status}")

            await asyncio.sleep(_REPORT_POLL_INTERVAL)

        if not report_doc_id:
            raise TimeoutError(
                f"Report {report_id} not ready after {_REPORT_TIMEOUT}s"
            )

        # Step 3: Get download URL
        doc_resp = await client.get(f"/reports/2021-06-30/documents/{report_doc_id}")
        url = doc_resp.get("url")
        compression = doc_resp.get("compressionAlgorithm")
        if not url:
            raise RuntimeError(f"No download URL in document response: {doc_resp}")

    # Step 4: Download (no SigV4 needed — pre-signed S3 URL)
    async with httpx.AsyncClient(timeout=60) as http:
        download = await http.get(url)
        download.raise_for_status()
        content = download.content

    # Step 5: Decompress if needed
    if compression == "GZIP":
        import gzip
        content = gzip.decompress(content)

    # Step 6: Parse TSV and return normalised rows
    text = content.decode("iso-8859-1")
    reader = csv.DictReader(io.StringIO(text), delimiter="\t")
    rows = list(reader)
    logger.info("listings_report_rows", total=len(rows))

    active = []
    for row in rows:
        status = row.get("status", "").strip()
        if status.lower() != "active":
            continue

        price_str = row.get("price", "").strip()
        qty_str = row.get("quantity", "0").strip()

        active.append({
            "seller_sku": row.get("seller-sku", "").strip(),
            "asin": row.get("asin1", "").strip(),
            "title": row.get("item-name", "").strip(),
            "price": float(price_str) if price_str else None,
            "quantity": int(qty_str) if qty_str.isdigit() else 0,
            "status": status,
            "fulfillment_channel": row.get("fulfilment-channel", "").strip(),
        })

    logger.info("listings_report_active", count=len(active))
    return active


async def enrich_listing(
    seller_id: str,
    sku: str,
    marketplace_id: str,
) -> dict[str, Any]:
    """
    Fetch detailed listing info for a single SKU via getListingsItem.

    Returns dict with: status (list), fnsku, asin, item_name, listing_price
    Returns empty dict on error (listing may not exist in this marketplace).

    includedData used: summaries, offers, fulfillmentAvailability
    """
    from urllib.parse import quote
    encoded_sku = quote(sku, safe="")

    async with SPAPIClient(marketplace_id=marketplace_id) as client:
        try:
            resp = await client.get(
                f"/listings/2021-08-01/items/{seller_id}/{encoded_sku}",
                params={
                    "marketplaceIds": marketplace_id,
                    "includedData": "summaries,offers,fulfillmentAvailability",
                },
            )
        except Exception as exc:
            logger.warning("enrich_listing_failed", sku=sku, exc=str(exc))
            return {}

    summaries = resp.get("summaries", [])
    if not summaries:
        return {}

    summary = summaries[0]
    status_list: list[str] = summary.get("status", [])

    # Extract listing price from offers
    listing_price: float | None = None
    offers = resp.get("offers", [])
    for offer in offers:
        if offer.get("offerType") == "B2C":
            price_obj = offer.get("price", {})
            amount = price_obj.get("amount")
            if amount is not None:
                listing_price = float(amount)
            break

    # Extract FBA quantity from fulfillmentAvailability
    fba_qty: int | None = None
    for fa in resp.get("fulfillmentAvailability", []):
        if fa.get("fulfillmentChannelCode") == "AMAZON_NA":
            fba_qty = fa.get("quantity")
            break

    return {
        "sku": sku,
        "asin": summary.get("asin"),
        "fnsku": summary.get("fnSku"),
        "item_name": summary.get("itemName"),
        "status": status_list,
        "is_buyable": "BUYABLE" in status_list,
        "listing_price": listing_price,
        "fba_qty": fba_qty,
        "last_updated": summary.get("lastUpdatedDate"),
    }


async def enrich_listings_batch(
    seller_id: str,
    skus: list[str],
    marketplace_id: str,
    delay: float = 0.25,
) -> dict[str, dict[str, Any]]:
    """
    Enrich multiple SKUs via getListingsItem.
    Rate limit: 5/s burst 5 — 0.25s delay is safe.

    Returns dict of sku → enrichment data.
    """
    results: dict[str, dict[str, Any]] = {}
    for sku in skus:
        data = await enrich_listing(seller_id, sku, marketplace_id)
        if data:
            results[sku] = data
        await asyncio.sleep(delay)
    return results
