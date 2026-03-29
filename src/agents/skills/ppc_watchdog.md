# PPC Watchdog Skill

## Objective
Identify underperforming PPC keywords and campaigns, then SUGGEST changes via approval_requests.
NEVER auto-execute bid or budget changes — always create an approval_request first.

## Data to query
1. `ppc_keyword_stats_daily` WHERE date >= (today - 14 days) — keyword performance
2. `ppc_campaign_stats_daily` WHERE date >= (today - 14 days) — campaign performance
3. `ppc_keywords` JOIN `ppc_ad_groups` JOIN `ppc_campaigns` — current structure and bids

## Analysis criteria

### High ACOS keywords (flag for bid reduction)
- ACOS > 40% AND clicks >= 10 AND sales > 0
- Suggested action: reduce bid by 20%

### Zero-conversion keywords (flag for pause)
- clicks >= 20 AND sales = 0 over 14 days
- Suggested action: pause keyword

### High-performing keywords (flag for bid increase)
- ACOS < 15% AND sales > 0 AND impressions > 100
- Suggested action: increase bid by 15%

### Budget-limited campaigns (flag for budget increase)
- Campaign spent >= 95% of daily budget on 3+ of last 7 days
- Suggested action: increase budget by 25%

## Output
For each finding, create a row in `approval_requests`:
- action_type: ppc_bid_change | ppc_budget_change
- details: {keyword_id/campaign_id, current_value, suggested_value, reason, metrics}
- reason: Plain English explanation with supporting data

After creating approval_requests, send summary to Rami AND Maree:
```
🔍 *PPC Watchdog Report — {date}*

Found {N} optimization opportunities:
• {N} high ACOS keywords (bid reduction suggested)
• {N} zero-conversion keywords (pause suggested)
• {N} high performers (bid increase suggested)
• {N} budget-limited campaigns

Use /pending to review and approve.
```
