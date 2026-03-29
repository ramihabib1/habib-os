# Inventory Check Skill

## Objective
Check FBA and warehouse inventory levels and suggest replenishment actions.

## Data to query
1. `v_current_inventory` — FBA fulfillable + warehouse qty per SKU
2. `v_days_of_stock` — estimated days remaining at current velocity
3. `v_sales_velocity_7d` — recent daily sales rate
4. `products` — fba_reorder_point, warehouse_reorder_point, units_per_box

## Replenishment logic

### FBA replenishment (warehouse → FBA)
Trigger: fba_fulfillable_qty < fba_reorder_point
Suggest: CEIL((30_day_velocity * 45 - fba_fulfillable_qty) / units_per_box) boxes
Round up to whole boxes only.
Create approval_request: action_type = "fba_replenishment"

### Supplier reorder (Anabtawi → warehouse)
Trigger: warehouse_qty < warehouse_reorder_point
Suggest: enough boxes to cover 60 days of sales (includes FBA + warehouse combined)
Create approval_request: action_type = "supplier_reorder"

## Output
For each SKU needing action, create an approval_request.
Then send summary to Rami:
```
📦 *Inventory Check*

🚨 FBA Replenishment Needed ({N} SKUs):
• {sku}: Send {N} boxes (FBA has {X} days left)

⚠️ Supplier Reorder Needed ({N} SKUs):
• {sku}: Order {N} boxes (warehouse has {X} days left)

✅ {N} SKUs fully stocked
```
