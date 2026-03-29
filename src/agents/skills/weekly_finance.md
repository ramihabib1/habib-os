# Weekly Finance Skill

## Objective
Generate a comprehensive weekly financial summary in Arabic for Father.
ALL output MUST be in Arabic. No English text in the Telegram message.

## Data to query (last 7 days vs previous 7 days for comparison)
1. `profit_daily` — revenue, COGS, fees, gross_profit per day
2. `sales_daily` — units sold per SKU
3. `ppc_campaign_stats_daily` — total ad spend
4. `v_product_profitability_30d` — top/bottom performers
5. `v_current_inventory` — inventory value estimate
6. `fees_daily` — fee breakdown

## Calculations
- Total revenue = SUM(revenue) for the week
- Total profit = SUM(gross_profit) for the week
- Net margin = total_profit / total_revenue * 100
- PPC ROAS = SUM(ppc_sales) / SUM(ppc_spend)
- Week-over-week change for revenue and profit
- Inventory value = SUM(fba_fulfillable_qty * landed_cost) per SKU

## Output format (Telegram Markdown — ARABIC)
Send to Father via `send_to_role("father", message)`.

The message must include:
- Date range in Arabic (e.g., "الفترة من X إلى Y")
- Total revenue in CAD
- Total profit in CAD
- Net margin percentage
- Week-over-week comparison with arrows (↑↓)
- Top 3 selling products in Arabic
- PPC spend and ROAS
- Inventory value estimate
- Any important alerts (low stock, fee changes)

Keep message professional and clear for a non-technical business owner.
