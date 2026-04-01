# CLAUDE.md — Habib Distribution OS

## System Identity

You are the intelligence layer of **Habib Distribution OS** — a 24/7 autonomous AI operating system that runs an Amazon FBA ecommerce business. The company is **Habib Distribution**, based in Toronto, Canada. It helps Middle Eastern food brands sell on Amazon. The first (and currently only) brand is **Anabtawi Sweets** (حلويات عنبتاوي), a Palestinian sweets manufacturer from Nablus.

The founder is **Rami**, a 22-year-old CS student at Technion. His father handles finance. His brother **Maree** handles marketing. All three interact with the system through Telegram and a dashboard.

## The Golden Rules

1. **NEVER take financial action without Telegram approval.** Every price change, bid change, budget change, reorder trigger, and campaign modification must go through the approval workflow. No exceptions.
2. **Every action must be logged to the audit_log table.** If it happened, it's in the database.
3. **All backend code is Python.** Never TypeScript for the backend. The only TypeScript is the Next.js dashboard (built separately by Claude Code).
4. **Finance agent output is always in Arabic.** Father's language is Arabic. All financial summaries, alerts, and reports sent to him must be in Arabic.
5. **Stay under $20/month total cost.** Use prompt caching aggressively. Minimize API calls. Use Supabase free tier. Use Vercel free tier.
6. **Learn from every task.** Read memory files before every task. Write learnings after every task. The system gets smarter over time.

## Team & Roles

| Name | Role | Language | Telegram Scope | Dashboard Access |
|------|------|----------|----------------|-----------------|
| Rami | Ops (owner) | English | All alerts + approvals | Everything |
| Father | Finance | Arabic | Weekly financial summaries | Sales, fees, profit, inventory, shipments |
| Maree | Marketing | English | PPC alerts, competitor updates | PPC, reviews, competitors, sales |

Only Rami can approve financial actions. Approvals expire after 24 hours.

## Tech Stack

| Layer | Technology |
|-------|------------|
| Agent intelligence | Claude Agent SDK (Python) — `claude-agent-sdk` pip package |
| Model | Claude Sonnet 4.6 via Anthropic API |
| Database | Supabase (PostgreSQL + pgvector for embeddings) |
| Scheduling | Python `schedule` + `asyncio` |
| Process manager | PM2 on Hetzner VPS |
| Approval workflow | Telegram Bot API + PreToolUse hook |
| Dashboard | Next.js + Tailwind + shadcn/ui on Vercel |
| VPS | Hetzner CX22 (Ubuntu 24.04, 2 vCPU, 4GB RAM) — 204.168.188.203 |

## Amazon SP-API Details

- **Seller:** Habib Distribution
- **Marketplace CA ID:** A2EUQ1WTGCTBG2
- **Marketplace US ID:** ATVPDKIKX0DER (future — not active yet)
- **AWS Role ARN:** arn:aws:iam::104981180708:role/habib-spapi-role
- **SP-API roles approved:** Product Listing, Amazon Fulfillment, Selling Partner Insights, Inventory and Order Tracking, Finance and Accounting, Brand Analytics
- **Auth flow:** Login With Amazon (LWA) OAuth → refresh_token → access_token (1hr expiry) + AWS SigV4 signing

## Project Structure

```
habib-os/
├── .env                          # All credentials (NEVER commit)
├── .env.example                  # Template with placeholder values
├── .gitignore
├── README.md
├── CLAUDE.md                     # This file
├── requirements.txt
├── pyproject.toml
│
├── .claude/
│   └── memory/
│       ├── ops.md                # Ops agent accumulated learnings
│       ├── finance.md            # Finance agent accumulated learnings
│       ├── marketing.md          # Marketing agent accumulated learnings
│       └── decisions.md          # Rami's strategic decisions with reasoning
│
├── src/
│   ├── __init__.py
│   ├── config/
│   │   ├── __init__.py
│   │   ├── settings.py           # Load .env, all config constants
│   │   └── supabase_client.py    # Supabase client singleton
│   │
│   ├── spapi/
│   │   ├── __init__.py
│   │   ├── auth.py               # LWA token flow (Login With Amazon)
│   │   ├── client.py             # Base SP-API HTTP client with SigV4
│   │   ├── inventory.py          # FBA inventory sync
│   │   ├── orders.py             # Orders sync
│   │   ├── fees.py               # Fee estimation + profit calc
│   │   ├── catalog.py            # Product catalog / BSR / reviews
│   │   └── advertising.py        # PPC / Advertising API
│   │
│   ├── telegram/
│   │   ├── __init__.py
│   │   ├── bot.py                # Telegram bot (polling loop)
│   │   ├── approval.py           # Approval request handler with inline buttons
│   │   └── notifications.py      # Send alerts to specific users
│   │
│   ├── agents/
│   │   ├── __init__.py
│   │   ├── base.py               # BaseAgent class wrapping Claude Agent SDK
│   │   ├── hooks.py              # PreToolUse hook for approval interception
│   │   ├── memory.py             # Read/write memory (markdown + pgvector)
│   │   ├── ops_agent.py          # Operations agent
│   │   ├── finance_agent.py      # Finance agent (Arabic output)
│   │   ├── marketing_agent.py    # Marketing agent
│   │   └── skills/
│   │       ├── daily_briefing.md
│   │       ├── ppc_watchdog.md
│   │       ├── weekly_finance.md
│   │       ├── competitor_snapshot.md
│   │       └── inventory_check.md
│   │
│   ├── jobs/
│   │   ├── __init__.py
│   │   ├── scheduler.py          # Main scheduler entry point
│   │   ├── inventory_sync.py     # Hourly: SP-API → inventory_snapshots
│   │   ├── orders_sync.py        # Hourly: SP-API → orders + sales_daily
│   │   ├── fees_sync.py          # Daily: fees + profit calculation
│   │   ├── ppc_sync.py           # Daily: Advertising API → ppc tables
│   │   ├── reviews_sync.py       # Daily: reviews + product snapshots
│   │   ├── competitor_sync.py    # Weekly: competitor data
│   │   └── expire_approvals.py   # Hourly: expire stale approval requests
│   │
│   └── utils/
│       ├── __init__.py
│       ├── logging.py            # Structured logging with structlog
│       └── audit.py              # Write to audit_log table
│
├── scripts/
│   ├── get_refresh_token.py      # One-time: SP-API OAuth flow
│   ├── seed_competitors.py       # One-time: seed competitor ASINs
│   ├── manual_stock_adjust.py    # CLI: warehouse inventory adjustments
│   └── backfill_sales.py         # One-time: backfill historical sales
│
├── ecosystem.config.js           # PM2 config for VPS
└── deploy.sh                     # Deployment script
```

## Database Schema (Supabase — already deployed)

### Core Business
- **brands** — brand records (currently: Anabtawi Sweets)
- **marketplaces** — Amazon marketplace configs (CA active, US seeded for future)
- **warehouses** — physical warehouses (currently: YYZ-01, Toronto)
- **products** — 30 Anabtawi SKUs with pricing, units_per_box, Arabic names, dual reorder points
- **product_cost_history** — landed cost changes over time

### Inventory (Dual-Warehouse Model)
The supply chain has TWO locations and TWO reorder triggers:
- **Anabtawi (Nablus)** → ships by air/sea → **Toronto warehouse (YYZ-01)** → sends boxes → **Amazon FBA (Canada)**

Tables:
- **inventory_snapshots** — hourly FBA inventory from SP-API (trigger: check_fba_inventory_threshold)
- **warehouse_inventory** — live qty per product at Toronto warehouse (auto-updated by movements)
- **warehouse_stock_movements** — every in/out with full audit trail (trigger: check_warehouse_threshold + apply_stock_movement)
- **inbound_shipments** / **inbound_shipment_items** — warehouse → FBA shipments
- **supplier_shipments** / **supplier_shipment_items** — Anabtawi → warehouse shipments

Trigger 1 (FBA low): FBA fulfillable_qty < fba_reorder_point → suggests whole boxes to send from warehouse → Telegram approval → create FBA shipment via SP-API
Trigger 2 (Warehouse low): warehouse qty < warehouse_reorder_point → suggests whole boxes to order from Anabtawi → Telegram approval → create supplier_shipment

Helper functions: `receive_supplier_shipment(shipment_id, warehouse_id)`, `send_to_fba(warehouse_id, fba_shipment_id)`

### Orders & Sales
- **orders** / **order_items** — raw orders from SP-API
- **sales_daily** — aggregated daily sales per SKU (units, revenue, refunds)

### Financial
- **fees_daily** — all Amazon fee components per SKU per day
- **profit_daily** — revenue - COGS - fees = gross_profit per SKU per day

### PPC Advertising (keyword-level granularity)
- **ppc_campaigns** → **ppc_ad_groups** → **ppc_keywords** — campaign structure
- **ppc_keyword_stats_daily** — keyword performance (impressions, clicks, spend, sales, ACOS)
- **ppc_campaign_stats_daily** — rolled-up campaign stats for dashboards

### Reviews & Competitors
- **reviews** — our product reviews with sentiment and themes
- **competitors** — competitor ASINs mapped to our products
- **competitor_snapshots** — weekly price/BSR/rating/stock tracking
- **competitor_reviews** — competitor reviews with weakness flags

### Approval Workflow
- **approval_requests** — every action requiring approval (status: pending/approved/rejected/expired/auto_approved)
  - Action types: ppc_bid_change, ppc_budget_change, price_change, fba_replenishment, supplier_reorder, listing_change, campaign_create, campaign_pause, custom
  - Expires after 24 hours automatically (expire_stale_approvals function)

### System & Observability
- **users** — Rami (ops), Father (finance), Maree (marketing) with telegram_id and language
- **audit_log** — every system action with agent, entity, details
- **notifications** — dashboard alerts with severity, role-based visibility, read/unread
- **sync_log** — SP-API sync history (type, status, records, duration)
- **agent_runs** — agent execution history (tokens, cost, duration, success/error)

### Intelligence & Memory
- **decision_log** — strategic decisions with reasoning, context, outcomes
- **agent_memory** — pgvector 1536-dim embeddings for semantic memory search
  - Search function: `match_memories(query_embedding, match_agent, match_count, match_threshold)`

### Pre-built Views
- **v_current_inventory** — latest FBA + warehouse stock per product
- **v_sales_velocity_7d** / **v_sales_velocity_30d** — average daily sales
- **v_days_of_stock** — days remaining at current velocity (FBA + total)
- **v_ppc_overview_7d** — campaign performance summary
- **v_product_profitability_30d** — profit and margin per SKU
- **v_warehouse_movements_30d** — recent stock movement history
- **v_supplier_shipments_active** — in-progress supplier shipments

### RLS Policy Design
- Python backend uses **service_role key** (bypasses RLS)
- Next.js dashboard uses **anon key + JWT** (RLS enforced by role)
- Ops sees everything, Finance sees financial tables, Marketing sees PPC/reviews/competitors
- Brand owners (future) see only their brand's data via brand_access array

## Product Catalog (30 SKUs)

| SKU | ASIN | Product | Weight | Units/Box | Pack Type | Landed Cost (CAD) | Amazon Price (CAD) | FBA Margin % |
|-----|------|---------|--------|-----------|-----------|-------------------|-------------------|-------------|
| 3I-SHTN-9CKQ | B0FT3HN2XV | Almond Fingers 375g | 375g | 18 | Carton | 7.61 | 24.00 | 26.67% |
| RL-KMFR-SEGS | B0FT3PHRF6 | Cashew Fingers 400g | 400g | 18 | Carton | 8.99 | 26.00 | 31.96% |
| ZK-4NDS-MNA9 | B0FT3L774Y | Walnut Fingers 375g | 375g | 18 | Carton | 8.09 | 24.00 | 24.92% |
| FO-SE3J-T74M | B0FT8GSHMV | Walnut Baklava 350g | 350g | 18 | Carton | 9.48 | 27.00 | 20.85% |
| 5G-ZW6Q-WOZG | B0FT3KLFHK | Cashew Baklava 350g | 350g | 18 | Carton | 9.48 | 27.00 | 21.56% |
| KP-MEL9-XYGW | B0FXXQHDHP | Mix Nuts with Coconuts 500g | 500g | 18 | Carton | 7.96 | 24.00 | 19.96% |
| Y4-Y8EE-VEOD | B0FTM6Y263 | Anabtawi Barazek Special 400g | 400g | 12 | Carton | 11.76 | 30.00 | 23.13% |
| W3-UQRU-PGRR | B0FXXN7HGB | Special Barazek 400g | 400g | 12 | Can | 11.76 | 30.00 | 27.10% |
| 26-JITG-E4FU | B0FXXM1CK8 | Classic Barazek 250g | 250g | 18 | Carton | 10.86 | 28.00 | 27.21% |
| KL-GDUL-HEA1 | B0FTSNBX57 | Holy Land Barazek 250g | 250g | 24 | Carton | 10.86 | 28.00 | 27.21% |
| LE-SUHY-BI89 | B0FT3DDX65 | Kaek Dates 300g | 300g | 18 | Carton | 5.53 | 20.00 | 21.70% |
| OA-26MX-IHV0 | B0FT3DNMJR | Mamoul Dates 300g | 300g | 18 | Carton | 5.53 | 20.00 | 21.70% |
| GG-0DC1-SKHG | B0FTSQ8M46 | Holy Land Mamoul Dates 350g | 350g | 24 | Carton | 15.56 | 36.00 | 21.97% |
| AN-9938-NXOT | B0FTM1JV7N | Special Mamoul Dates 550g | 550g | 12 | Can | 13.49 | 33.00 | 20.36% |
| QF-3CKA-W90D | B0FTG2FJTW | Special Mix Mamoul 550g | 550g | 12 | Can | 20.68 | 44.00 | 20.16% |
| C5-TXQU-Y67R | B0FTM92W43 | Special Mamoul Walnuts 550g | 550g | 12 | Can | 20.06 | 42.00 | 18.38% |
| VH-ZTOC-GW1Q | B0FTM5PBZW | Special Pistachio Mamoul 550g | 550g | 12 | Can | 27.60 | 56.00 | 19.79% |
| BU-6GOS-GW5Q | B0FXX2R3BD | Holy Land Ghraibeh 250g | 250g | 24 | Carton | 8.09 | 24.00 | 21.58% |
| O3-V1B9-CH1H | B0FTMBSVDN | Special Ghraibeh 375g | 375g | 12 | Can | 9.48 | 27.00 | 29.11% |
| 09-AJOP-CS83 | B0FY6PBYZS | Classic Assorted Sweets 250g | 250g | 32 | Carton | 11.07 | 29.00 | 30.52% |
| E3-DSPC-O2UN | B0FY6MFJV5 | Classic Assorted Sweets 500g | 500g | 16 | Carton | 20.06 | 42.00 | 19.02% |
| 9J-ASSK-BVKC | B0FY6NS7MQ | Classic Assorted Sweets 820g | 820g | 8 | Carton | 26.29 | 53.00 | 18.91% |
| EU-Z87B-ZRBZ | B0FY6N6TRH | Classic Assorted Sweets 850g | 850g | 12 | Can | 29.05 | 62.00 | 18.08% |
| H8-PWJ0-3B1Y | B0FY6SX9RP | Special Assorted Sweets 250g | 250g | 32 | Carton | 12.45 | 31.00 | 20.16% |
| SP-AST-500CA | B0FY6M2LHX | Special Assorted Sweets 500g | 500g | 16 | Carton | 21.79 | 45.00 | 18.42% |
| FX-M8MA-MMSA | B0FTSM2HSJ | Holy Land Assorted Sweets 180g | 180g | 40 | Carton | 13.49 | 33.00 | 28.97% |
| 9Z-KUHZ-FU2I | B0FTSMTDGP | Holy Land Assorted Sweets 400g | 400g | 24 | Carton | 26.63 | 53.00 | 18.28% |
| 18-116Z-1R77 | B0FXX46ST8 | Premium Assorted Sweets 180g | 180g | 40 | Carton | 13.63 | 33.00 | 23.24% |
| T8-2W2X-INOK | B0FXX3JVR5 | Premium Assorted Sweets 400g | 400g | 24 | Carton | 26.77 | 53.00 | 18.02% |
| 0C-45D7-6JUB | B0FXX2QVF8 | Premium Assorted Cookies 725g | 725g | 8 | Carton | 34.59 | 69.00 | 19.48% |

## Implementation Plan — Build Order

Work through these steps in exact order. Each step lists what to build, which files to create, and how to verify.

### Phase 1: Foundation

**Step 1: Project structure + Python environment**
- Create the full directory tree shown above
- `python3 -m venv .venv && source .venv/bin/activate`
- Create requirements.txt and install all dependencies
- Create src/config/settings.py (load .env with python-dotenv, export all config constants)
- Create src/config/supabase_client.py (singleton Supabase client using service_role key)
- Create .env.example with all variable names and placeholder values
- Create .gitignore (include .env, .venv, __pycache__, .claude/memory/*.md)
- Verify: `python -c "from src.config.settings import settings; print(settings.SUPABASE_URL)"` works

**Step 2: Write CLAUDE.md** — this file. Already done.

**Step 3: Create .env** — populate with all real credentials. Variables needed:
- ANTHROPIC_API_KEY
- SUPABASE_URL, SUPABASE_SERVICE_KEY, SUPABASE_ANON_KEY
- SP_API_CLIENT_ID, SP_API_CLIENT_SECRET, SP_API_REFRESH_TOKEN
- SP_API_AWS_ACCESS_KEY, SP_API_AWS_SECRET_KEY, SP_API_ROLE_ARN
- SP_API_MARKETPLACE_CA (A2EUQ1WTGCTBG2)
- ADS_API_CLIENT_ID, ADS_API_CLIENT_SECRET, ADS_API_REFRESH_TOKEN, ADS_API_PROFILE_ID
- TELEGRAM_BOT_TOKEN, TELEGRAM_RAMI_CHAT_ID, TELEGRAM_FATHER_CHAT_ID, TELEGRAM_MAREE_CHAT_ID
- LOG_LEVEL, ENVIRONMENT

**Step 4: Create Telegram bot** — done manually via @BotFather

**Step 5: Hetzner VPS** — already provisioned at 204.168.188.203, user: habib

**Step 6: Telegram approval bot (Python)**
- src/telegram/bot.py — polling loop with command handlers (/status, /inventory, /approve, /reject, /pending)
- src/telegram/approval.py — format approval requests with InlineKeyboardMarkup, handle callbacks, update approval_requests table
- src/telegram/notifications.py — send alerts to specific users based on role/language
- The bot detects new pending approval_requests, sends formatted message to Rami, processes approve/reject callbacks
- Verify: bot responds to /start, manually insert approval_request in Supabase → bot sends to Telegram → tap approve → status updates

### Phase 2: Data Pipeline

**Step 7: SP-API authentication**
- src/spapi/auth.py — LWA token exchange (POST https://api.amazon.com/auth/o2/token with refresh_token), cache access_token (1hr expiry), auto-refresh
- src/spapi/client.py — base HTTP client with AWS SigV4 signing, STS AssumeRole for temp credentials, exponential backoff with jitter, rate limit respect (x-amzn-RateLimit-Limit header)
- scripts/get_refresh_token.py — one-time OAuth authorization flow to get refresh_token
- Verify: test call to GET /sellers/v1/marketplaceParticipations returns data

**Step 8: Inventory sync (hourly)**
- src/jobs/inventory_sync.py
- SP-API: GET /fba/inventory/v1/summaries (granularityType=Marketplace)
- Map each SKU to products.id, insert into inventory_snapshots
- Database trigger check_fba_inventory_threshold fires automatically
- Log to sync_log
- Handle: pagination (nextToken), unknown SKUs (log warning, skip), rate limits

**Step 9: Orders sync (hourly)**
- src/jobs/orders_sync.py
- SP-API: GET /orders/v0/orders (LastUpdatedAfter = 2 hours ago), GET /orders/v0/orders/{id}/orderItems
- Upsert into orders (ON CONFLICT amazon_order_id) and order_items
- Rebuild sales_daily for today: GROUP BY product_id, date → SUM units, revenue → UPSERT
- Rate limit: 1 req/sec for getOrders

**Step 10: Fees + profit calculation (daily)**
- src/jobs/fees_sync.py
- Sources: products table (known fees per SKU), ppc_campaign_stats_daily (PPC spend), sales_daily (revenue, units)
- Calculate: fees_daily (all fee components), profit_daily (revenue - COGS - fees)
- COGS = products.landed_cost × units_sold

**Step 11: PPC data sync (daily) — SEPARATE AUTH from SP-API**
- src/jobs/ppc_sync.py + src/spapi/advertising.py
- Amazon Advertising API (different auth, different endpoints)
- Sync structure: GET campaigns → ad_groups → keywords (upsert all)
- Request keyword-level performance report (POST, async)
- Poll until ready, download gzipped JSON, parse
- Upsert into ppc_keyword_stats_daily, roll up to ppc_campaign_stats_daily
- First-time: GET /v2/profiles to get ADS_API_PROFILE_ID

**Step 12: Product snapshots + reviews (daily)**
- src/jobs/reviews_sync.py
- SP-API: GET /catalog/2022-04-01/items/{asin} for BSR, rating
- Upsert into product_snapshots
- Check for new reviews, insert into reviews table

**Step 13: Scheduler orchestrator**
- src/jobs/scheduler.py — main entry point
- Schedule: inventory_sync hourly :00, orders_sync hourly :15, expire_approvals hourly :30, fees_sync daily 06:00, ppc_sync daily 07:00, reviews_sync daily 08:00, competitor_sync weekly Mon 09:00
- Uses Python `schedule` + `asyncio`, each job in try/except, errors → sync_log + Telegram alert

### Phase 3: Agent Layer

**Step 14: Claude Agent SDK base harness**
- src/agents/base.py — BaseAgent wrapping claude-agent-sdk query() async generator
- Loads CLAUDE.md as system prompt, loads agent memory file, loads task skill file
- Logs to agent_runs (tokens, cost, duration)
- src/agents/hooks.py — PreToolUse hook: checks if tool call is financial → creates approval_request → sends to Telegram → suspends agent loop → polls for response → resumes or cancels

**Step 15: Supabase MCP connection**
- Connect agents to Supabase via official MCP server OR build custom Python tool functions
- Agents can query any table, insert/update records, call RPC functions

**Step 16: Memory system**
- src/agents/memory.py
- Layer 1: Read/write .claude/memory/{agent}.md (dated entries with structured sections)
- Layer 2: Embed learnings into agent_memory table via pgvector, search with match_memories()
- Lifecycle: load markdown → search pgvector → include in context → execute task → generate learnings → append to markdown → embed and store

**Step 17: Ops agent — daily morning briefing (daily 08:30)**
- src/agents/skills/daily_briefing.md
- Queries: v_current_inventory, v_sales_velocity_7d, v_days_of_stock, v_ppc_overview_7d, v_product_profitability_30d
- Output: formatted Telegram message to Rami with yesterday's revenue, inventory status, PPC summary, alerts

**Step 18: PPC watchdog (daily)**
- src/agents/skills/ppc_watchdog.md
- Identifies: high ACOS keywords (>40%), zero-conversion keywords, underperforming campaigns
- SUGGESTS changes via approval_requests, never auto-executes
- Sends findings to Rami + Maree

**Step 19: Finance agent — weekly summary (Sunday 20:00)**
- src/agents/skills/weekly_finance.md
- ALL OUTPUT IN ARABIC
- Revenue, profit, margins, PPC ROAS, inventory value, week-over-week comparison
- Sends to Father's Telegram

**Step 20: Competitor snapshot (weekly Monday 09:00)**
- src/agents/skills/competitor_snapshot.md
- Fetches competitor catalog data, upserts competitor_snapshots
- Flags: price drops >10%, out-of-stock competitors, review spikes
- Sends findings to Maree

### Phase 4: Deploy + Dashboard

**Step 21: Deploy to VPS**
- ecosystem.config.js — PM2 config for scheduler + telegram-bot processes
- deploy.sh — git pull, pip install, pm2 restart
- SSH to habib@204.168.188.203, clone repo, set up venv, copy .env, pm2 start
- Verify: pm2 status shows both processes online, survives reboot

**Step 22: Next.js dashboard**
- Built by Claude Code (TypeScript is OK here only)
- Pages: /, /inventory, /sales, /ppc, /profit, /competitors, /approvals, /logs, /notifications
- Supabase Auth with magic link, RLS enforced
- Real-time notifications via Supabase Realtime
- RTL support for Father's Arabic view
- Deploy to Vercel free tier

## Coding Standards

- **Python 3.12**, type hints on all function signatures
- **async/await** for all I/O operations (SP-API calls, Supabase queries, Telegram)
- **structlog** for structured logging (JSON format in production)
- **tenacity** for retry logic on external API calls
- **httpx** as the async HTTP client (not requests)
- Every function that touches Supabase must handle errors and log to audit_log
- Every sync job must write to sync_log (success/fail, records, duration)
- No hardcoded credentials — everything from .env via settings.py
- Docstrings on all public functions
- Keep files under 300 lines — split if larger

## Environment Variables Reference

```env
# Anthropic
ANTHROPIC_API_KEY=sk-ant-...

# Supabase
SUPABASE_URL=https://xxxxx.supabase.co
SUPABASE_SERVICE_KEY=eyJ...
SUPABASE_ANON_KEY=eyJ...

# Amazon SP-API
SP_API_CLIENT_ID=amzn1.application-oa2-client.xxx
SP_API_CLIENT_SECRET=xxx
SP_API_REFRESH_TOKEN=xxx
SP_API_AWS_ACCESS_KEY=AKIA...
SP_API_AWS_SECRET_KEY=xxx
SP_API_ROLE_ARN=arn:aws:iam::104981180708:role/habib-spapi-role
SP_API_MARKETPLACE_CA=A2EUQ1WTGCTBG2
SP_API_MARKETPLACE_US=ATVPDKIKX0DER

# Amazon Advertising API
ADS_API_CLIENT_ID=amzn1.application-oa2-client.xxx
ADS_API_CLIENT_SECRET=xxx
ADS_API_REFRESH_TOKEN=xxx
ADS_API_PROFILE_ID=xxx

# Telegram
TELEGRAM_BOT_TOKEN=xxx:xxx
TELEGRAM_RAMI_CHAT_ID=xxx
TELEGRAM_FATHER_CHAT_ID=xxx
TELEGRAM_MAREE_CHAT_ID=xxx

# System
LOG_LEVEL=INFO
ENVIRONMENT=development
```

## Memory System Instructions

### For agents reading this file:
1. Before every task, read your memory file at `.claude/memory/{your_role}.md`
2. Search pgvector for relevant past memories using `match_memories()`
3. Use both to inform your analysis and decisions
4. After completing a task, write a "Learnings" section with:
   - Date and task name
   - What you observed (data patterns, anomalies)
   - What you decided and why
   - What you would do differently next time
   - Any rules or thresholds you'd recommend adjusting
5. Append this to your memory file
6. Embed and store in agent_memory table

### For Rami writing decisions:
Edit `.claude/memory/decisions.md` with:
- Date
- Domain (pricing/ppc/inventory/listing/competitor/expansion)
- What you decided
- Why (your reasoning)
- Context (what data you were looking at)
- Expected outcome
- (Fill in later) Actual outcome

## Future Agents (build after core system is stable)

1. **Stockout Prediction Engine** — needs 30+ days of velocity data
2. **Review Intelligence Loop** — mines customer language for listing copy
3. **PPC Self-Optimizer** — evolves from watchdog, gradually gains autonomy
4. **New Brand Onboarding Engine** — when second brand is signed
5. **Diaspora Trend Spotter** — monitors Arab community trends for market intelligence
6. **Agency Watchdog** — monitors for unauthorized Seller Central changes
7. **Competitor Weakness Hunter** — deep competitor review mining
