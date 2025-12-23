# Inventory Application PRD

**Version:** 2.4  
**Date:** December 22, 2025  
**Audience:** AI Agent for Application Development  
**Format:** Markdown

## 1. Introduction & Background

### Problem Statement
The photography studio prints in-house using rolls of paper (tracked by length), consumables (magnets, cards, envelopes – tracked by pieces), and various shipping materials (mailers, tubes, labels, bags). Inventory is currently tracked manually, leading to frequent stockouts, delayed orders, over-ordering, and poor visibility into waste and supplier costs. Estimated annual impact: $50,000–$75,000 in lost efficiency and tied-up capital.

### Objectives
- Reduce stockouts by 50% within six months of launch.
- Reduce over-ordering by 50% within six months of launch.
- Deliver accurate, near-real-time visibility into stock levels, consumption trends, waste, and supplier price changes.

## 2. Target Users & Personas

Total users: 3–5 (desktop-only, no mobile or tablet requirements).

| Persona            | Approx. Count | Key Responsibilities & Needs |
|----------------------------|---------------|-------------------------------------|
| Print Operator             | 2–3           | Views stock levels, receives low-stock alerts, adds basic SKUs. Needs clear, immediate warnings to avoid halting production. |
| Inventory Manager          | 1             | Defines/edits BOM recipes, waste %, thresholds, logs purchases, overrides recipes when needed, manages shipping mapping. |
| Owner/Administrator        | 1             | Analyzes trends, supplier spending, price history. Receives supplier price-spike alerts. Makes strategic purchasing decisions. |

## 3. User Roles & Permissions

| Role               | Key Permissions |
|--------------------|-----------------|
| Print Operator     | View stock & reports, view supplier details, add/edit basic SKUs, receive stock alerts |
| Inventory Manager  | All Operator + edit BOM recipes, waste %, thresholds, manual overrides (logged), manage shipping mapping, log purchases, export data, force sync |
| Owner/Admin        | All permissions + manage users/roles, view full analytics & price graphs, receive price-spike alerts, force sync |

**Authentication:** Email + password. First user to register is automatically assigned Owner role. All stock adjustments, overrides, and purchase logs are audited (user, timestamp, details).

## 4. Key Features (Prioritization)

### Must-Have

- **Product & SKU Management**
  - Add/edit SKUs with: name, description, unit of measure (enum: inches, feet, pieces), current stock (float), waste percentage (float, default 0%).
  - **Bill of Materials (BOM / Recipe)**: Array of child SKUs + quantity/length used per parent unit (e.g., one 8×10 print consumes 10 inches of 8-inch roll + 1 card + 1 envelope).
  - **Details Tab per SKU**: Supplier name, order URL, supplier product code, purchase history log (date, quantity bought, price paid), auto-generated price trend line chart.
  - **Low-Stock Threshold**: Single editable field “Alert when stock falls below X [units]” stored as `alert_threshold_qty` (float, nullable; null = no alert).

- **Inventory Deduction – Print/Production Materials**
  - Triggered by dedicated poller container (see Deployment Notes).
  - Poller runs every 90 seconds, queries for orders where `order_open_status = 40` (printing/production).
  - For each line item: explode BOM, deduct base quantities + (waste % × ordered quantity) from app's `skus` table only.
  - No reversal logic for trashed/cancelled orders.

- **Inventory Deduction – Shipping Materials**
  - Triggered by poller when `order_open_status = 39` (shipped) **or** ShipStation label-created event.
  - Preferred: ShipStation webhook (label created). Fallback: poll ShipStation API every 5 minutes.
  - Payload provides package dimensions (L×W×H).
  - Editable mapping table (UI-managed): dimension string (e.g., "12x9x0.25") → array of shipping SKUs.
    - Examples: `"12x9x0.25"` → `["flat-mailer", "clear-bag", "shipping-label"]`  
      `"12x2x2"` → `["pano-tube", "shipping-label"]`
  - If no rule matches: log event, email all users with details (order ID, dimensions), no deduction.
  - Manual addition of new rules required via UI.
  - Deduct all listed items in one transaction.
  - Deduplication: Persisted `sync_logs` table with unique constraint on `shipstation_order_id`.

- **Manual Sync Trigger**
  - "Force Sync Now" button on dashboard (Manager/Owner only).
  - Immediately runs the same deduction logic as poller.

- **Alerts & Notifications**
  - **Low/Zero Stock**: `current_stock < alert_threshold_qty` → email all users.
  - **Unmapped Shipping Dimensions**: → email all users.
  - **Supplier Price Spike**: New purchase log price > 110% of previous log price for that SKU → email Owner only.
  - Delivery: Email via SMTP (primary). SMS via Twilio only if credentials provided.

- **Reporting & Export**
  - All lists/reports exportable to CSV or Markdown.

### Should-Have
- **Analytics Dashboard**
  - Stock trends over time, top 5 slowest-moving items, forecasted run-out dates (average daily consumption), waste trends, supplier price history graphs.
- **Manual Purchase Logging**
  - Form to add stock (quantity, price, date, supplier URL/notes) → updates stock, purchase log, price chart.

### Could-Have (Roadmap)
- Predictive reorder suggestions, PO generation, multi-location support.

## 5. User Stories

- As a Print Operator, I want prominent low-stock alerts on login so I can flag issues before starting a job.
- As an Inventory Manager, I want to edit a SKU’s BOM recipe so deductions match real consumption.
- As an Inventory Manager, I want to override a BOM for a specific order (with reason field) so exceptions are handled and logged.
- As the system, when an order reaches `order_open_status = 40`, I want to automatically deduct all print materials + waste.
- As the system, when an order reaches `order_open_status = 39` or a ShipStation label is created, I want to deduct correct shipping materials (or alert if unmapped).
- As an Owner, I want to view a price history line chart per SKU and receive an email if a new purchase price jumps >10%.
- As a Manager/Owner, I want a "Force Sync Now" button to manually trigger deductions for testing or immediate updates.

## 6. Technical Requirements

### Existing MySQL Schema (Read-Only)
| Table              | Column                  | Type         | Notes |
|--------------------|-------------------------|--------------|-------|
| ms_photo_products  | pp_id                   | INT (PK)     | Product ID |
|                    | pp_name                 | VARCHAR      | Display name |
|                    | pp_internal_name        | VARCHAR      | SKU string – matches cart_sku |
|                    | pp_price                | DECIMAL      | Cost per unit (not used for inventory) |
| ms_orders          | order_id                | INT (PK)     |       |
|                    | order_date              | DATETIME     | Incremental key |
|                    | order_open_status       | INT          | 40 = printing/production, 39 = shipped |
| ms_cart            | cart_order              | INT (FK)     | → ms_orders.order_id |
|                    | cart_sku                | VARCHAR      | Matches pp_internal_name |
|                    | cart_qty                | INT          |       |
|                    | cart_price              | DECIMAL      | Not used |
|                    | cart_product_name       | VARCHAR      | Optional reference |
|                    | cart_date               | DATETIME     | Fallback timestamp |

**Sync Query (run by poller)**
```sql
SELECT 
    o.order_id,
    o.order_date,
    o.order_open_status,
    c.cart_sku,
    c.cart_qty
FROM ms_orders o
JOIN ms_cart c ON o.order_id = c.cart_order
WHERE o.order_open_status IN (39, 40)
  AND o.order_date > :last_sync_timestamp
ORDER BY o.order_date ASC;

Application Schema (New Tables – App's Own DB)

### Application Schema (New Tables – App's Own DB)

| Table             | Key Columns & Notes                                                                 |
|-------------------|-------------------------------------------------------------------------------------|
| skus              | sku_id (PK), name, description, unit_of_measure (enum: inches, feet, pieces), current_stock (float), waste_pct (float), alert_threshold_qty (float nullable) |
| sku_recipes       | parent_sku_id (FK), child_sku_id (FK), qty_used (float)                              |
| purchase_logs     | id (PK), sku_id (FK), purchase_date, quantity, price, supplier_name, supplier_url, supplier_code, notes |
| shipping_mapping  | id (PK), dimensions_string (varchar), items_json (text array of sku strings)        |
| sync_logs         | id (PK), internal_order_id, shipstation_order_id (unique nullable), timestamp, action |
| users             | user_id (PK), email, password_hash, role (enum: operator, manager, owner), receives_stock_alerts (bool default true) |
Deployment Notes

Dedicated Poller Container
Lightweight Python/Node container running on same VPS as app and DB.
Loop: every 90 seconds, execute sync query.
If new orders found, POST JSON payload to internal app endpoint (http://inventory-app:3000/internal-sync).
Secure: internal Docker network only, no external exposure.
Restart policy: always.

App exposes /internal-sync (protected by IP whitelist or secret token) to process payload immediately.
Force Sync button calls same endpoint manually.

Environment Variables
textMYSQL_HOST, MYSQL_DATABASE, MYSQL_USER, MYSQL_PASSWORD  # read-only
SHIPSTATION_API_KEY, SHIPSTATION_API_SECRET
SMTP_HOST, SMTP_PORT, SMTP_USER, SMTP_PASSWORD
TWILIO_SID, TWILIO_TOKEN, TWILIO_FROM_NUMBER  # optional
INTERNAL_SYNC_TOKEN=secret  # for poller security
Non-Functional Requirements

Performance: Deductions within ~2 minutes of status change (poller-driven).
Reliability: Poller retry on failure, detailed logging.
Security: Encrypted connections, internal-only sync endpoint, secure env storage.
Scalability: Support 500 SKUs, 100 daily orders.
UI: Clean web app, desktop-focused.

7. Assumptions, Risks & Dependencies
Assumptions

Existing MySQL tables stable and read-accessible.
ShipStation provides reliable label events or pollable API.
VPS Docker environment available for poller container.

Risks & Mitigations

Poller failure → App logs last sync time, alerts on stale data (>10 min).
Unmapped shipping dimensions → Alert all users + easy UI fix.
Duplicate deductions → sync_logs unique constraint.

Dependencies

Read-only MySQL credentials.
ShipStation API credentials.
SMTP configuration (Twilio optional).

8. Definition of Done
Feature complete when:

All related user stories and acceptance criteria met.
End-to-end tested across roles (including manual sync button).
Poller container tested in staging.
Unit + integration tests ≥80% coverage.
Manual testing with real users.
No critical/high-severity bugs.
Documentation (setup, poller Dockerfile, usage) included.

Implementation Order:

SKU/BOM management + app schema
Poller container + internal sync endpoint
Print materials deduction
Shipping integration + mapping
Alerts + manual sync button
Dashboard & purchase logging

# Addendum – Agent Clarifications Resolved
Version: 2.4 (Clarifications Addendum)
Date: December 22, 2025
Audience: AI Agent for Application Development

## 1. Data Schema (App-owned vs. Existing)
- The application has read-only access to the existing `ms_` tables in the legacy MySQL instance. The app manages all inventory data itself.
- **Existing read-only tables**:
  * `ms_photo_products` (`pp_id`, `pp_name`, `pp_internal_name` as SKU key, `pp_price`).
  * `ms_orders` (`order_id`, `order_date`, `order_open_status` with 40=printing, 39=shipped).
  * `ms_cart` (line items: `cart_order` FK → `orders`, `cart_sku` matching `pp_internal_name`, `cart_qty`, `cart_price`, `cart_product_name`, `cart_date`).
  * These tables are only queried by the sync poller; the app never writes to them.
- **New application tables** (full read/write):
  * `skus` (`sku_id`, `sku_code` unique match to `pp_internal_name`, name, description, `unit_of_measure`, `current_stock`, `waste_pct`, `alert_threshold_qty`).
  * `sku_recipes` (`id`, `parent_sku_id`, `child_sku_id`, `qty_used`).
  * `purchase_logs` (`id`, `sku_id`, `purchase_date`, `quantity`, `price`, `supplier_name`, `supplier_url`, `supplier_code`, `notes`).

## 2. Order Sync / Poller
- There are no native `order_confirmed`/`order_trashed` webhooks; the MySQL database cannot push events.
- A dedicated lightweight poller container runs on the same VPS and queries the legacy database every 90 seconds. It posts JSON to the internal `/internal-sync` app endpoint when new data exists.
- Sample payload:
```json
{
  "orders": [
    {
      "order_id": 12345,
      "order_date": "2025-12-22 14:30:00",
      "order_open_status": 40,
      "line_items": [
        {"cart_sku": "8X10-GLOSS", "cart_qty": 5},
        {"cart_sku": "MAGNET-5X7", "cart_qty": 10}
      ]
    }
  ],
  "triggered_at": "2025-12-22T14:31:15Z"
}
```
- The app trusts the poller as the single source of truth; no additional app-side polling is required.

## 3. Shipping Mapping / Deduplication
- If a ShipStation dimension payload does not match an existing `shipping_mapping` entry, no deduction runs and an email alert informs all users with order ID, ShipStation order ID, dimensions, and timestamp. The Inventory Manager then adds the mapping manually.
- Duplicate deductions are prevented solely via the `sync_logs` table. `shipstation_order_id` is unique (nullable for print-only events); the app checks this column before processing a shipping deduction.

## 4. Thresholds, Price Alerts & Secrets
- Waste % and low-stock thresholds live in the `skus` table (`waste_pct`, `alert_threshold_qty`), managed via the SKU editing UI.
- The supplier-price >10% alert compares each new `purchase_logs` entry to the immediately previous log for the same `sku_id`. If the new price exceeds 1.1× the prior price, an email is sent to the Owner role only.
- Credentials/integration secrets come exclusively from env vars:
  * `SMTP_HOST`, `SMTP_PORT`, `SMTP_USER`, `SMTP_PASSWORD`
  * `TWILIO_SID`, `TWILIO_TOKEN`, `TWILIO_FROM_NUMBER`
- Twilio is used only if all three values are set; otherwise email is the sole notification channel.
