# Amazon SP-API Technical Research Report
## Implementation-Ready Reference for Python Data Pipeline (Canada Marketplace)

**Context:** Amazon FBA seller, Canada marketplace (`A2EUQ1WTGCTBG2`), ~26 active listings, ~30 SKUs, Python 3.12 async pipeline → PostgreSQL.

---

## 1. How to Get the Seller ID Programmatically

### Direct Answer

There is **no dedicated SP-API endpoint** that returns your `sellerId` as a top-level field. The `getMarketplaceParticipations` endpoint does **not** include `sellerId` in its response. However, there are two reliable methods:

### Method A: Capture at OAuth Authorization (Recommended)

During the SP-API OAuth authorization flow (Step 4), the redirect URL includes the `selling_partner_id` query parameter. This **is** your Seller ID. Store it at authorization time.

```
https://your-redirect-uri.com/callback?selling_partner_id=A1B2C3D4E5F6G7&...
```

### Method B: Extract from Product Fees API (Runtime Fallback)

Call `getMyFeesEstimateForASIN` with any known ASIN:

```
POST /products/fees/v0/items/{Asin}/feesEstimate
```

**Request body:**
```json
{
  "FeesEstimateRequest": {
    "MarketplaceId": "A2EUQ1WTGCTBG2",
    "IsAmazonFulfilled": true,
    "PriceToEstimateFees": {
      "ListingPrice": { "CurrencyCode": "CAD", "Amount": 10 },
      "Shipping": { "CurrencyCode": "CAD", "Amount": 0 }
    },
    "Identifier": "request-1"
  }
}
```

**Response field containing Seller ID:**
```
payload.FeesEstimateResult.FeesEstimateIdentifier.SellerId
```

### Does Seller ID Differ Per Marketplace?

**No.** A single Amazon seller account has one `sellerId` (e.g., `A1B2C3D4E5F6G7`) that is the same across all marketplaces in a region (North America: US, CA, MX, BR). The `sellerId` is a region-scoped account identifier, not marketplace-specific.

### getMarketplaceParticipations — What It Actually Returns

```
GET /sellers/v1/marketplaceParticipations
```

**Parameters:** None required (uses auth context).  
**Rate limit:** 0.016 req/s | Burst: 15

**Response:**
```json
{
  "payload": [
    {
      "marketplace": {
        "id": "A2EUQ1WTGCTBG2",
        "name": "Canada",
        "countryCode": "CA",
        "defaultCurrencyCode": "CAD",
        "defaultLanguageCode": "en",
        "domainName": "www.amazon.ca"
      },
      "participation": {
        "isParticipating": true,
        "hasSuspendedListings": false
      },
      "storeName": "YourStoreName"
    }
  ]
}
```

**Key fields:** `marketplace.id`, `marketplace.countryCode`, `participation.isParticipating`, `storeName`.  
**Missing:** `sellerId` is NOT in this response.

**Required role:** Seller Partner Insights.

---

## 2. Complete List of Active Listings — Synchronous vs Async

### Direct Answer

There is now a **synchronous paginated endpoint**: `searchListingsItems`. However, for a complete dump of all listings, `GET_MERCHANT_LISTINGS_ALL_DATA` remains the most reliable single-source approach.

### Option A: `searchListingsItems` (Synchronous, Paginated) — NEW

```
GET /listings/2021-08-01/items/{sellerId}
```

**Key facts:**
- Added to Listings Items API v2021-08-01.
- Returns multiple listings in a single call with pagination.
- **Requires identifiers** — you must supply SKUs, ASINs, or other product identifiers. You **cannot** call it with zero identifiers to get "all listings."
- Maximum 1,000 results can be paged through (e.g., `pageSize=10` → max 100 pages).
- SKUs containing commas cannot be included in comma-delimited queries (use individual requests for those).
- Rate limit: 5 req/s | Burst: 5

**Supported `includedData` values (same as `getListingsItem`):**

| Value | Description |
|-------|-------------|
| `summaries` | Summary details (ASIN, status, itemName, etc.) |
| `attributes` | Full structured product attributes JSON |
| `issues` | Listing issues/warnings |
| `offers` | Current offer/price details |
| `fulfillmentAvailability` | FBA/MFN fulfillment quantities |
| `procurement` | Vendor procurement details |
| `relationships` | Parent/child variation data |
| `productTypes` | Product type information |

**Verdict for your use case:** Since `searchListingsItems` requires you to already know the identifiers, it does NOT replace the report for initial discovery.

### Option B: `getListingsItem` (Synchronous, One-at-a-Time)

```
GET /listings/2021-08-01/items/{sellerId}/{sku}?marketplaceIds=A2EUQ1WTGCTBG2
```

- Returns exactly one SKU per call.
- **Does NOT support listing all SKUs** — you must know the SKU in advance.
- Rate limit: 5 req/s | Burst: 5
- For ~30 SKUs: 30 calls ÷ 5/sec = ~6 seconds total (fast enough for enrichment after initial discovery).

### Option C: `GET_MERCHANT_LISTINGS_ALL_DATA` Report (Async, Complete)

**This remains the ONLY endpoint that returns ALL your listings without knowing SKUs in advance.**

```
POST /reports/2021-06-30/reports
{
  "reportType": "GET_MERCHANT_LISTINGS_ALL_DATA",
  "marketplaceIds": ["A2EUQ1WTGCTBG2"]
}
```

**Workflow:** `createReport` → poll `getReport` → `getReportDocument` → download TSV.  
**Typical latency:** 30-120 seconds.

**Required roles:** Inventory and Order Tracking, Pricing, Direct to Consumer Shipping (Restricted), Product Listing.

### GET_MERCHANT_LISTINGS_ALL_DATA TSV Column Names

| Column | Notes |
|--------|-------|
| `item-name` | Product title |
| `item-description` | Product description |
| `listing-id` | Amazon listing identifier |
| `seller-sku` | Your SKU |
| `price` | Listed price |
| `quantity` | Available quantity |
| `open-date` | Date listing was opened |
| `Deprecated column` | (column 8 — ignore) |
| `item-is-marketplace` | Boolean |
| `product-id-type` | e.g., ASIN |
| `Deprecated column` | (column 11 — ignore) |
| `item-note` | Seller notes |
| `item-condition` | Condition code |
| `Deprecated column` | (column 14 — ignore) |
| `Deprecated column` | (column 15 — ignore) |
| `Deprecated column` | (column 16 — ignore) |
| `asin1` | Primary ASIN |
| `Deprecated column` | (column 18 — ignore) |
| `Deprecated column` | (column 19 — ignore) |
| `will-ship-internationally` | Shipping scope |
| `expedited-shipping` | Expedited eligibility |
| `Deprecated column` | (column 22 — ignore) |
| `product-id` | Product identifier |
| `Deprecated column` | (column 24 — ignore) |
| `add-delete` | Add/delete flag |
| `pending-quantity` | Pending quantity |
| `fulfilment-channel` | `AMAZON_NA` (FBA) or `DEFAULT` (MFN) |
| `optional-payment-type-exclusion` | Payment exclusions |
| `merchant-shipping-group` | Shipping group |
| `status` | `Active`, `Inactive`, etc. |
| `Minimum order quantity` | MOQ if set |
| `Sell remainder` | Sell remainder flag |

**Total: 32 columns** (including 8 deprecated placeholder columns).

### Recommended Approach

1. **Startup/daily full sync:** Run `GET_MERCHANT_LISTINGS_ALL_DATA` to discover all SKUs.
2. **Enrichment:** For each discovered SKU, call `getListingsItem` with `includedData=summaries,offers,fulfillmentAvailability` for detailed status and live pricing.
3. **Intra-day delta:** Use `GET_MERCHANT_LISTINGS_DATA` (active only) or Notifications API (`LISTINGS_ITEM_STATUS_CHANGE`) for real-time updates.

---

## 3. Listings Items API — Correct Usage and 400 Errors

### Endpoint

```
GET /listings/2021-08-01/items/{sellerId}/{sku}?marketplaceIds=A2EUQ1WTGCTBG2&includedData=summaries,offers,fulfillmentAvailability
```

### Required SP-API Roles/Permissions

At least one of:
- **Product Listing** role (primary)

Your developer profile AND app registration must include this role.

### Common Causes of 400 Errors

1. **Missing `marketplaceIds` query parameter** — it is required.
2. **SKU not properly URL-encoded** — special characters in the path segment must be percent-encoded.
3. **Invalid `sellerId`** — using the wrong identifier or a marketplace ID instead.
4. **Invalid `includedData` values** — misspelled or unsupported enum values.
5. **Marketplace mismatch** — the SKU doesn't exist in the specified marketplace.

### Does the SKU Need URL-Encoding?

**Yes, the SKU in the URL path must be URL-encoded.** Hyphens (`-`) are safe in URLs and do not need encoding, so `3I-SHTN-9CKQ` can be used as-is. However, if your SKUs contain characters like `+`, `&`, `%`, `/`, `#`, or spaces, those **must** be percent-encoded.

Python example:
```python
from urllib.parse import quote

sku = "3I-SHTN-9CKQ"
encoded_sku = quote(sku, safe="")  # "3I-SHTN-9CKQ" (hyphens stay)
url = f"/listings/2021-08-01/items/{seller_id}/{encoded_sku}"
```

**Important:** Commas in SKUs cannot be used with `searchListingsItems` (comma-delimited), but are fine with `getListingsItem` if percent-encoded.

### `includedData` Values and What Each Returns

| Value | Returns |
|-------|---------|
| `summaries` | ASIN, productType, conditionType, **status** array, fnSku, itemName, createdDate, lastUpdatedDate, mainImage. **Default if omitted.** |
| `attributes` | Full structured JSON of all product attributes (title, bullet points, description, etc.) keyed by attribute name. |
| `issues` | Array of listing issues with `message`, `severity` (WARNING/ERROR), `attributeName`, `attributeNames`. |
| `offers` | Current offer details: `marketplaceId`, `offerType` (B2C/B2B), `price` (`currency`, `amount`). |
| `fulfillmentAvailability` | `fulfillmentChannelCode` (DEFAULT=MFN, AMAZON_NA=FBA), `quantity`. |
| `procurement` | Vendor-only: procurement details. |

### Field Indicating Active/Buyable Status

**Exact field:** `summaries[0].status` — this is an **array of enum strings**.

**Possible values:**

| Value | Meaning |
|-------|---------|
| `BUYABLE` | Listing is live and purchasable by customers |
| `DISCOVERABLE` | Listing is visible/searchable on Amazon |

An active, live listing will have: `"status": ["BUYABLE", "DISCOVERABLE"]`

A suppressed or inactive listing may have an empty status array or different values.

### Sample JSON Response (`includedData=summaries`)

```json
{
  "sku": "3I-SHTN-9CKQ",
  "summaries": [
    {
      "marketplaceId": "A2EUQ1WTGCTBG2",
      "asin": "B09EXAMPLE1",
      "productType": "HOME_BED_AND_BATH",
      "conditionType": "new_new",
      "status": [
        "BUYABLE",
        "DISCOVERABLE"
      ],
      "fnSku": "X00EXAMPLE",
      "itemName": "Example Product Name - Premium Widget 3-Pack",
      "createdDate": "2023-06-15T10:30:00Z",
      "lastUpdatedDate": "2024-11-20T14:22:00Z",
      "mainImage": {
        "link": "https://m.media-amazon.com/images/I/41exampleImg.jpg",
        "height": 500,
        "width": 500
      }
    }
  ]
}
```

### Rate Limit

| Metric | Value |
|--------|-------|
| Rate (per account-app pair) | 5 req/s |
| Rate (per application) | 100 req/s |
| Burst | 5 |

---

## 4. FBA Inventory API — Filtering and Ghost SKUs

### Endpoint

```
GET /fba/inventory/v1/summaries?details=true&granularityType=Marketplace&granularityId=A2EUQ1WTGCTBG2&marketplaceIds=A2EUQ1WTGCTBG2
```

### Is There a Parameter to Filter to Only Active Listings?

**No.** There is no `status` filter parameter on this endpoint. The FBA Inventory API returns inventory data for **all SKUs that have ever existed in the FBA fulfillment network**, including ghost/zombie SKUs with zero inventory. You must cross-reference with listing status from the Listings Items API or the `GET_MERCHANT_LISTINGS_ALL_DATA` report to filter out inactive SKUs.

### Does Passing `sellerSkus` Prevent Ghost SKUs?

**No.** If you pass `sellerSkus=SKU1,SKU2,...`, the API returns inventory summaries for those specific SKUs **whether or not they are still active listings**. If a ghost SKU with `fulfillableQuantity=0` is in the FBA system, passing it in `sellerSkus` will still return it.

**Strategy to avoid ghosts:** First get the active SKU list from `GET_MERCHANT_LISTINGS_ALL_DATA` (filtered by `status=Active` and `fulfilment-channel=AMAZON_NA`), then pass only those active SKUs as `sellerSkus`. Max 50 SKUs per request.

### Quantity Fields Explained

| Field | Where Found | Meaning | Customer-facing? |
|-------|-------------|---------|-----------------|
| `fulfillableQuantity` | `inventorySummaries[].inventoryDetails` | Units available to ship to customers RIGHT NOW. Picked, packed, ready. | **YES — this is "how many units customers can buy"** |
| `totalQuantity` | `inventorySummaries[]` | Sum of all inventory in FBA (fulfillable + reserved + inbound + unfulfillable + researching) | No — includes non-purchasable |
| `reservedQuantity.totalReservedQuantity` | Nested in `inventoryDetails.reservedQuantity` | Units currently reserved (being picked/packed/shipped, or in transit between FCs, or sidelined for processing) | No |
| `reservedQuantity.pendingCustomerOrderQuantity` | Nested | Reserved for customer orders already placed | No |
| `reservedQuantity.pendingTransshipmentQuantity` | Nested | Being transferred between fulfillment centers | No |
| `reservedQuantity.fcProcessingQuantity` | Nested | Sidelined at FC for measurement, sampling, or other internal processing | No |
| `inboundWorkingQuantity` | `inventoryDetails` | Units in shipments you've notified Amazon about (not yet shipped) | No |
| `inboundShippedQuantity` | `inventoryDetails` | Units in shipments with tracking (in transit to FC) | No |
| `inboundReceivingQuantity` | `inventoryDetails` | Units partially received at FC (some items in shipment already processed) | No |
| `unfulfillableQuantity.totalUnfulfillableQuantity` | Nested in `inventoryDetails.unfulfillableQuantity` | Total unsellable units (damaged, defective, expired) | No |
| `researchingQuantity.totalResearchingQuantity` | Nested in `inventoryDetails.researchingQuantity` | Units being investigated by Amazon | No |

**For "how many units can customers buy right now":** Use `fulfillableQuantity`. This is the only field that represents purchasable inventory.

### Rate Limit

| Metric | Value |
|--------|-------|
| Rate | 2 req/s |
| Burst | 2 |

### Complete Sample Response (`details=true`)

```json
{
  "payload": {
    "granularity": {
      "granularityType": "Marketplace",
      "granularityId": "A2EUQ1WTGCTBG2"
    },
    "inventorySummaries": [
      {
        "asin": "B09EXAMPLE1",
        "fnSku": "X00EXAMPLE",
        "sellerSku": "3I-SHTN-9CKQ",
        "condition": "NewItem",
        "productName": "Example Product Name - Premium Widget 3-Pack",
        "totalQuantity": 150,
        "lastUpdatedTime": "2024-12-01T08:30:00Z",
        "inventoryDetails": {
          "fulfillableQuantity": 120,
          "inboundWorkingQuantity": 0,
          "inboundShippedQuantity": 20,
          "inboundReceivingQuantity": 0,
          "reservedQuantity": {
            "totalReservedQuantity": 5,
            "pendingCustomerOrderQuantity": 3,
            "pendingTransshipmentQuantity": 1,
            "fcProcessingQuantity": 1
          },
          "researchingQuantity": {
            "totalResearchingQuantity": 0,
            "researchingQuantityBreakdown": []
          },
          "unfulfillableQuantity": {
            "totalUnfulfillableQuantity": 5,
            "customerDamagedQuantity": 2,
            "warehouseDamagedQuantity": 1,
            "distributorDamagedQuantity": 0,
            "carrierDamagedQuantity": 1,
            "defectiveQuantity": 0,
            "expiredQuantity": 1
          }
        }
      }
    ]
  },
  "pagination": {
    "nextToken": "seed=XXXXXX&..."
  }
}
```

---

## 5. Current Listing Prices — Bulk Retrieval

### Direct Answer

Use `GET /products/pricing/v0/price` (the `getPricing` operation) for bulk price retrieval. It accepts up to **20 ASINs or 20 SKUs per request**. For a newer approach with competitive context, the Product Pricing API **v2022-05-01** offers `getCompetitiveSummary`.

### Primary Endpoint: `getPricing`

```
GET /products/pricing/v0/price?MarketplaceId=A2EUQ1WTGCTBG2&ItemType=Sku&Skus=SKU1,SKU2,...
```

**Parameters:**

| Parameter | Required | Description |
|-----------|----------|-------------|
| `MarketplaceId` | Yes | `A2EUQ1WTGCTBG2` for Canada |
| `ItemType` | Yes | `Asin` or `Sku` — determines which identifier list to use |
| `Asins` | Conditional | Up to **20** ASINs (when `ItemType=Asin`) |
| `Skus` | Conditional | Up to **20** SKUs (when `ItemType=Sku`) |
| `ItemCondition` | No | `New`, `Used`, `Collectible`, `Refurbished`, `Club` |
| `OfferType` | No | `B2C` (default) or `B2B` |

**Rate limit (post-2024 throttle adjustment):**

| Metric | Value |
|--------|-------|
| Rate | 0.5 req/s |
| Burst | 1 |

### Difference Between `getPricing` and `getCompetitivePricing`

| Feature | `getPricing` (`/products/pricing/v0/price`) | `getCompetitivePricing` (`/products/pricing/v0/competitivePrice`) |
|---------|----------------------------------------------|------------------------------------------------------------------|
| Primary use | Your own offer listing price | Competitive prices (Buy Box) |
| Returns your price | Yes — in `Product.Offers[].ListingPrice` | Yes — items where `belongsToRequester=true` |
| Returns Buy Box | Yes — in `Product.CompetitivePricing.CompetitivePrices` where `CompetitivePriceId="1"` | Yes — `CompetitivePriceId="1"` = New Buy Box |
| Returns SalesRankings | Yes | Yes |
| Max items/request | 20 | 20 |
| Rate limit | 0.5/s, burst 1 | 0.5/s, burst 1 |

### Where to Find Your Listing Price

**Path:** `payload[n].Product.Offers[n].BuyingPrice.ListingPrice`

Or from competitive pricing: `payload[n].Product.CompetitivePricing.CompetitivePrices[n].Price.ListingPrice` where `belongsToRequester=true`.

### Where to Find the Buy Box Price

**Path:** `payload[n].Product.CompetitivePricing.CompetitivePrices[n].Price.LandedPrice`

Filter by: `CompetitivePriceId = "1"` (New Buy Box) or `"2"` (Used Buy Box).

**Schema for `CompetitivePriceType`:**
```
CompetitivePriceId: "1"       ← "1" = New Buy Box, "2" = Used Buy Box
condition: "New"
belongsToRequester: true/false ← whether YOU own this Buy Box
Price:
  LandedPrice: { CurrencyCode, Amount }   ← ListingPrice + Shipping - Points
  ListingPrice: { CurrencyCode, Amount }   ← Item price
  Shipping: { CurrencyCode, Amount }       ← Shipping cost
sellerId: "A1B2C3..."                      ← (optional) seller who owns it
```

### Sample Response (getPricing, ItemType=Sku)

```json
{
  "payload": [
    {
      "status": "Success",
      "SellerSKU": "3I-SHTN-9CKQ",
      "ASIN": "B09EXAMPLE1",
      "Product": {
        "Identifiers": {
          "MarketplaceASIN": {
            "MarketplaceId": "A2EUQ1WTGCTBG2",
            "ASIN": "B09EXAMPLE1"
          },
          "SKUIdentifier": {
            "MarketplaceId": "A2EUQ1WTGCTBG2",
            "SellerId": "A1B2C3D4E5F6G7",
            "SellerSKU": "3I-SHTN-9CKQ"
          }
        },
        "CompetitivePricing": {
          "CompetitivePrices": [
            {
              "CompetitivePriceId": "1",
              "condition": "New",
              "belongsToRequester": true,
              "Price": {
                "LandedPrice": { "CurrencyCode": "CAD", "Amount": 29.99 },
                "ListingPrice": { "CurrencyCode": "CAD", "Amount": 29.99 },
                "Shipping": { "CurrencyCode": "CAD", "Amount": 0.00 }
              }
            }
          ],
          "NumberOfOfferListings": [
            { "Count": 1, "condition": "New" }
          ]
        },
        "SalesRankings": [
          { "ProductCategoryId": "home_garden_display_on_website", "Rank": 14523 }
        ],
        "Offers": [
          {
            "BuyingPrice": {
              "LandedPrice": { "CurrencyCode": "CAD", "Amount": 29.99 },
              "ListingPrice": { "CurrencyCode": "CAD", "Amount": 29.99 },
              "Shipping": { "CurrencyCode": "CAD", "Amount": 0.00 }
            },
            "RegularPrice": { "CurrencyCode": "CAD", "Amount": 29.99 },
            "FulfillmentChannel": "AMAZON",
            "ItemCondition": "New",
            "ItemSubCondition": "New",
            "SellerSKU": "3I-SHTN-9CKQ"
          }
        ]
      }
    }
  ]
}
```

**Note:** `Product.Identifiers.SKUIdentifier.SellerId` — this is another way to discover your Seller ID from the Pricing API response.

### Newer Pricing API: v2022-05-01

**Yes, there is a newer version.** Product Pricing API **v2022-05-01** adds:

| Operation | Path | Rate | Burst | Batch Size |
|-----------|------|------|-------|------------|
| `getCompetitiveSummary` | `POST /batches/products/pricing/2022-05-01/items/competitiveSummary` | 0.033/s | 1 | Up to 20 ASINs |
| `getFeaturedOfferExpectedPriceBatch` | `POST /batches/products/pricing/2022-05-01/offer/featuredOfferExpectedPrice` | 0.033/s | 1 | Up to 40 SKUs |

**`getCompetitiveSummary`** returns:
- `featuredBuyingOptions` — all featured offers with seller IDs, prices, segments (Prime/Non-Prime)
- `referencePrices` — competitive price threshold, WasPrice (90-day median), CompetitivePrice
- `lowestPricedOffers` — top 20 lowest priced offers

**Recommendation for your use case:** Use `getPricing` v0 (with `ItemType=Sku`) for your daily price sync — it's simpler, returns both your price and the Buy Box in one call, and handles 20 SKUs at a time. Use `getCompetitiveSummary` v2022-05-01 if you later build a repricer.

---

## 6. Orders API — Correct Usage

### Endpoint

```
GET /orders/v0/orders?MarketplaceIds=A2EUQ1WTGCTBG2&LastUpdatedAfter=2024-01-01T00:00:00Z
```

### Correct ISO 8601 Format for `LastUpdatedAfter`

**Use this format exactly:**
```
2024-01-01T00:00:00Z
```

**Rules:**
- Use `Z` for UTC (not `+00:00`, not timezone offsets).
- Do **NOT** include microseconds — `2024-01-01T00:00:00.000000Z` can cause 400 errors.
- Do **NOT** include timezone offsets like `+03:00` — some SP-API endpoints reject these.

**Python code:**
```python
from datetime import datetime, timezone

dt = datetime.now(timezone.utc)
# WRONG: dt.isoformat()  → "2024-12-01T15:30:45.123456+00:00"
# RIGHT:
formatted = dt.strftime("%Y-%m-%dT%H:%M:%SZ")  # "2024-12-01T15:30:45Z"
```

### What Does `OrderStatus=Shipped` Filter Do?

**Yes, it excludes Pending orders.** `OrderStatus` is a list filter. When you specify `OrderStatuses=Shipped`, only orders with status `Shipped` are returned. All other statuses (Pending, Unshipped, PartiallyShipped, Canceled, Unfulfillable, etc.) are excluded.

**All possible `OrderStatus` values:**

| Value | Meaning |
|-------|---------|
| `PendingAvailability` | Pre-order placed, payment not authorized, release date in future |
| `Pending` | Order placed, payment not yet authorized |
| `Unshipped` | Payment authorized, ready to ship |
| `PartiallyShipped` | Some items shipped |
| `Shipped` | All items shipped |
| `InvoiceUnconfirmed` | Shipped, invoice not confirmed to buyer |
| `Canceled` | Order canceled |
| `Unfulfillable` | Cannot be fulfilled (MCF orders only) |

**For revenue sync, use:** `OrderStatuses=Unshipped,PartiallyShipped,Shipped` to capture all orders with authorized payment.

### Revenue: `OrderTotal` vs `ItemPrice`

| Field | What It Contains | Best For |
|-------|-----------------|----------|
| `Order.OrderTotal.Amount` | Grand total charged to buyer (items + shipping + gift wrap - promotions + tax). For FBA CA, this typically includes GST/HST. | Quick order-level total |
| `OrderItems[].ItemPrice.Amount` | Selling price × quantity for one line item. **Excludes** shipping, gift wrap, and tax. | SKU-level revenue attribution |

**Recommendation:** Use `OrderItems[].ItemPrice.Amount - OrderItems[].PromotionDiscount.Amount` summed across all items for accurate per-SKU revenue. `OrderTotal` is convenient for order-level but can't be broken down by SKU and includes shipping/tax.

**Key OrderItem money fields:**
```
ItemPrice.Amount          — item selling price × quantity
PromotionDiscount.Amount  — total promo discount on this item
ShippingPrice.Amount      — shipping charged for this item
ShippingDiscount.Amount   — shipping promo discount
ItemTax.Amount            — tax on item
ShippingTax.Amount        — tax on shipping
```

**Per-item net revenue formula:**
```python
item_revenue = float(item["ItemPrice"]["Amount"]) - float(item["PromotionDiscount"]["Amount"])
```

### Rate Limits

| Operation | Rate | Burst |
|-----------|------|-------|
| `getOrders` | 0.0167 req/s (~1 per 60s) | 20 |
| `getOrder` | 0.5 req/s | 30 |
| `getOrderItems` | 0.5 req/s | 30 |
| `getOrderBuyerInfo` | 0.5 req/s | 30 |

**Important note (2025):** A new Orders API **v2026-01-01** has been announced with a `searchOrders` operation at 0.0056 req/s, burst 20. The v0 `getOrders` remains available and functional.

---

## 7. Rate Limits — Complete Reference Table

All values are **post-2024 throttle adjustments** (the rates lowered in 2024 for pricing and catalog operations).

| Endpoint | Method | Rate (req/s) | Burst | Notes |
|----------|--------|-------------|-------|-------|
| `/fba/inventory/v1/summaries` | GET | 2 | 2 | Per account-app pair. Up to 50 SKUs per request. |
| `/orders/v0/orders` | GET | 0.0167 | 20 | ~1 request per 60s sustained. Use `NextToken` for pagination. |
| `/orders/v0/orders/{id}/orderItems` | GET | 0.5 | 30 | Per account-app pair. |
| `/catalog/2022-04-01/items/{asin}` | GET | 2 | 2 | Per account-app pair; 250/s per application. |
| `/catalog/2022-04-01/items` (search) | GET | 2 | 2 | Per account-app pair; 500/s per application. Keyword searches: 50/s per app. |
| `/reports/2021-06-30/reports` | POST | 0.0167 | 15 | `createReport` — one per ~60s sustained. |
| `/reports/2021-06-30/reports/{reportId}` | GET | 2.0 | 15 | `getReport` — poll status. Generous rate. |
| `/reports/2021-06-30/documents/{documentId}` | GET | 0.0167 | 15 | `getReportDocument` — gets download URL. |
| `/listings/2021-08-01/items/{sellerId}/{sku}` | GET | 5 | 5 | `getListingsItem` — per account-app pair; 100/s per application. |
| `/listings/2021-08-01/items/{sellerId}` (search) | GET | 5 | 5 | `searchListingsItems` — per account-app pair; 100/s per application. |
| `/products/pricing/v0/price` | GET | 0.5 | 1 | `getPricing` — up to 20 items per call. **Lowered from 10/20 in 2024.** |
| `/products/pricing/v0/competitivePrice` | GET | 0.5 | 1 | `getCompetitivePricing` — up to 20 items. **Lowered from 10/20.** |
| `/products/pricing/v0/items/{Asin}/offers` | GET | 0.5 | 1 | `getItemOffers` — single ASIN. **Lowered from 5/10.** |
| `/batches/products/pricing/v0/itemOffers` | POST | 0.5 | 1 | `getItemOffersBatch` — up to 20 ASINs. |
| `/batches/products/pricing/v0/listingOffers` | POST | 0.5 | 1 | `getListingOffersBatch` — up to 20 SKUs. |
| `/sellers/v1/marketplaceParticipations` | GET | 0.016 | 15 | Call once at startup, cache result. |
| `/batches/products/pricing/2022-05-01/items/competitiveSummary` | POST | 0.033 | 1 | v2022-05-01. Up to 20 ASINs. |
| `/batches/products/pricing/2022-05-01/offer/featuredOfferExpectedPrice` | POST | 0.033 | 1 | v2022-05-01. Up to 40 SKUs. |

### How Rate Limits Work (Token Bucket Algorithm)

- **Rate** = tokens added per second (sustained throughput).
- **Burst** = maximum bucket size (max concurrent requests).
- At startup, the bucket is full (equal to burst).
- Each request removes 1 token; tokens refill at the rate.
- When empty → 429 response with `x-amzn-RateLimit-Limit` header.

**For your ~30 SKU account:**
- Pricing: 30 SKUs ÷ 20 per call = 2 calls. At 0.5/s burst 1, takes ~4 seconds.
- Inventory: 30 SKUs ÷ 50 per call = 1 call. Instant.
- Listings: 30 calls at 5/s = ~6 seconds.
- Orders: Burst of 20, then 1 per 60s. First page instant; paginate slowly.

---

## 8. Recommended Architecture: DB vs Amazon as Source of Truth

### Amazon Should Be the Source of Truth

**Amazon is the canonical source.** Your PostgreSQL database should be a **read-only mirror/cache** that is refreshed from Amazon on a schedule. Reasons:

1. Amazon can change listing status, suppress listings, or update prices without your action.
2. FBA inventory changes constantly (customer purchases, returns, FC transfers).
3. Orders originate on Amazon — they're always the authoritative record.
4. Any manual or Seller Central changes bypass your local DB.

### When a SKU Exists in Amazon But Not in Local DB

**Auto-insert it.** During your daily sync of `GET_MERCHANT_LISTINGS_ALL_DATA`, for any SKU present in the report that's missing from your `products` table:

1. Insert a new row with all available report fields.
2. Enrich immediately via `getListingsItem` with `includedData=summaries,offers,fulfillmentAvailability`.
3. Flag it as `source=amazon_discovered` for review.
4. Log the discovery event for audit.

### When a SKU Exists in Local DB But Is No Longer Active on Amazon

**Do NOT delete it.** Instead:

1. Set `status = 'INACTIVE'` (or `'ARCHIVED'`).
2. Set `last_seen_active = <timestamp>`.
3. Keep historical data (orders, revenue, inventory history) intact.
4. After a configurable grace period (e.g., 90 days), flag for manual review.
5. Ghost SKUs in FBA Inventory with `fulfillableQuantity=0` and no active listing should be tracked but filtered from active dashboards.

### Recommended Sync Frequencies

| Data Type | Frequency | API/Method | Rationale |
|-----------|-----------|------------|-----------|
| **Listing status** (active/inactive) | Every 6-12 hours | `GET_MERCHANT_LISTINGS_ALL_DATA` report | SKU discovery, status changes; low-frequency API. |
| **Listing details** (title, ASIN, images) | Daily | `getListingsItem` for each active SKU | Enrichment. Stable data, changes rarely. |
| **FBA inventory quantities** | Every 1-2 hours | `GET /fba/inventory/v1/summaries` | Inventory changes with sales. For 30 SKUs: 1 API call. |
| **Orders** | Every 15-30 minutes | `GET /orders/v0/orders` with `LastUpdatedAfter` | Revenue tracking. Burst of 20 allows fast catch-up. |
| **Pricing (your price + Buy Box)** | Every 2-4 hours | `GET /products/pricing/v0/price` | Price drift detection. For 30 SKUs: 2 calls. |
| **BSR / Sales Rank** | Daily | Comes with `getPricing` response in `SalesRankings` | Changes gradually. Already included in pricing sync. |
| **Ratings/Reviews** | Not available via SP-API | Catalog Items API has limited data; use Product Advertising API or scraping alternative | SP-API does not expose review text or star ratings. |

### The Single Most Important Endpoint to Call First on Startup

**`GET /sellers/v1/marketplaceParticipations`**

Call this first because:

1. **Validates your auth is working** — if this fails, nothing else will work.
2. **Discovers which marketplaces you participate in** — confirms `A2EUQ1WTGCTBG2` is active.
3. **Returns your store name** — useful for logging and multi-account setups.
4. **Has a generous burst of 15** — won't throttle even on cold start.
5. **Zero parameters required** — simplest possible API call to verify connectivity.

### Recommended Startup Sequence

```
1. GET /sellers/v1/marketplaceParticipations    → Validate auth, get marketplace list
2. POST /products/fees/v0/items/{asin}/feesEstimate → Extract sellerId (if not cached)
3. POST /reports/2021-06-30/reports              → Request GET_MERCHANT_LISTINGS_ALL_DATA
4. (poll getReport until DONE)
5. GET /reports/2021-06-30/documents/{id}        → Download full listing TSV
6. Parse TSV → upsert into products table
7. For each active FBA SKU:
   GET /listings/2021-08-01/items/{sellerId}/{sku}  → Enrich with status, offers
8. GET /fba/inventory/v1/summaries               → Sync inventory
9. GET /products/pricing/v0/price (batches of 20) → Sync prices
10. GET /orders/v0/orders?LastUpdatedAfter=...    → Sync recent orders
```

---

## Appendix: 2024-2025 API Changes to Be Aware Of

| Change | Date | Impact |
|--------|------|--------|
| Pricing API rate limits reduced | 2024 | `getPricing`, `getCompetitivePricing`: 10/20 → 0.5/1. `getItemOffers`: 5/10 → 0.5/1. Use batch endpoints. |
| Catalog Items rate limits reduced | 2024 | `getCatalogItem`, `searchCatalogItems`: 5/5 → 2/2. |
| `searchListingsItems` added | 2024 | New paginated search for Listings Items API. Requires identifiers. Max 1,000 results. |
| Product Pricing v2022-05-01 `getCompetitiveSummary` enhanced | May 2024 | Added `lowestPricedOffers` and `referencePrices` (including `WasPrice`). |
| Orders API v2026-01-01 announced | 2025 | New `searchOrders` operation. v0 not deprecated yet. |
| Listings Items API rate limit pages added | Nov 2024 | Per-application rate limits now documented separately from per-account-app pair limits. |
| Catalog Items v0 deprecated | Sep 2022 | `GET /catalog/v0/items` removed. Use v2022-04-01. |

---

## Appendix: Key SP-API Hosts by Region

| Region | Host |
|--------|------|
| North America (US, CA, MX, BR) | `sellingpartnerapi-na.amazon.com` |
| Europe (UK, FR, DE, IT, ES, NL, etc.) | `sellingpartnerapi-eu.amazon.com` |
| Far East (JP, AU, SG, IN) | `sellingpartnerapi-fe.amazon.com` |

For Canada marketplace: use `sellingpartnerapi-na.amazon.com` with `marketplaceId=A2EUQ1WTGCTBG2`.
