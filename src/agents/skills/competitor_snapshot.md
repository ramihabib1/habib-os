# Competitor Snapshot Skill

## Objective
Analyze the latest competitor_snapshots data and send insights to Maree.

## Data to query
1. `competitor_snapshots` WHERE snapshot_date = today — latest snapshot
2. `competitor_snapshots` WHERE snapshot_date = (today - 7) — last week for comparison
3. `competitors` JOIN `products` — competitor-to-product mapping
4. `v_current_inventory` — our stock levels for context

## Analysis
For each competitor ASIN:
- Price change vs last week (flag if > 5% change)
- BSR change (flag if improved by > 20 positions — competitor gaining momentum)
- Review count change (flag if > 5 new reviews — monitor for quality signals)
- Out-of-stock detection (if available)

## Flags to surface
- 🔴 Competitor dropped price significantly (> 10%)
- 🟡 Competitor BSR improving fast (gaining momentum)
- 🟢 Competitor is out of stock (opportunity to capture sales)
- 📊 Competitor gaining reviews rapidly

## Output
Send to Maree:
```
🔍 *Competitor Update — {date}*

{For each significant finding:}
• [{competitor_name}] vs [{our_product}]: {finding}

Overall: {summary sentence}
```

If no significant changes, send a brief "all clear" message.
