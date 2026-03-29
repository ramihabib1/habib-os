# Daily Briefing Skill

## Objective
Produce a morning briefing for Rami covering yesterday's performance and today's priorities.

## Data to query
1. `v_current_inventory` — FBA + warehouse stock per SKU
2. `v_sales_velocity_7d` — avg daily units sold per SKU (7-day)
3. `v_days_of_stock` — estimated days of stock remaining per SKU
4. `v_ppc_overview_7d` — campaign ACOS, spend, sales, ROAS
5. `profit_daily` WHERE date = yesterday — revenue, gross_profit, margin_pct
6. `approval_requests` WHERE status = 'pending' — outstanding decisions

## Output format (Telegram Markdown)
Send to Rami via `send_to_role("rami", message)`.

```
📊 *Daily Briefing — {date}*

💰 *Yesterday's Revenue*
Total: $X.XX CAD | Profit: $X.XX | Margin: X%

📦 *Inventory Alerts*
🚨 LOW: {sku} — {days} days remaining (FBA: {qty})
✅ OK: {N} SKUs fully stocked

📈 *PPC Summary (7-day)*
Total Spend: $X | ACOS: X% | ROAS: X.X

⏳ *Pending Approvals*
{N} actions waiting for your approval — /pending

🔔 *Action Needed*
• {any replenishment or other urgent item}
```

## Rules
- Only flag SKUs with < 14 days of stock remaining
- If ACOS > 40% on any campaign, flag it
- If no sales yesterday, mention it explicitly
- Keep total message under 1500 characters
- Send at 08:30 Toronto time (UTC-4 in summer, UTC-5 in winter)
