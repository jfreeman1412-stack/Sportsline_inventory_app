# Developer Onboarding

## Overview
The Sportsline Inventory App tracks raw materials (paper, magnets, etc.) for the youth sports photography workflow. It syncs orders from the legacy `sportsline` MySQL database, deducts raw materials via BOM recipes, logs activity, and uses a nightly forecasting job (LinearRegression) to predict 30 day usage for proactive reorder alerts.

## Goal
Provide a predictive raw-material cockpit that highlights urgent items, lets operators drill into individual materials (historic usage + forecast), and surfaces reorder recommendations before stockouts or seasonal spikes occur.

## Architecture
- **Backend:** FastAPI + SQLAlchemy + Alembic migrations (models live under `backend/app/models.py`).
- **Frontend:** Server-rendered HTMX/Bootstrap pages with Chart.js for trends.
- **Sync:** Poller container queries the legacy MySQL every 90 seconds, posts orders to `/internal-sync`, and logs deductions/audits.
- **Forecasting:** `forecasting.job.run_nightly_forecast()` uses BOM + legacy history to train LinearRegression models per SKU, stores results in `forecast_results`, and generates `reorder_alerts`.
- **Deployment:** Docker Compose orchestrates `app`, `poller`, and MySQL services.

## Key Features
- Raw-material-focused dashboard (urgency cards, selector/compare, single trend + forecast line, top movers chart).
- Alerts widget + run-forecast button that refreshes the cockpit.
- Audit trail for every deduction, plus forecast results for email/alert consumption.

## Required Database Dumps
1. **Full `sportsline_inventory` dump** (schema + data): `full_app_dump.sql.gz`. Import with:
   ```bash
   gunzip -c full_app_dump.sql.gz | mysql -h HOST -u USER -pPASSWORD sportsline_inventory
   ```
2. **Sanitized legacy data** (`legacy_safe_dump.sql.gz`): contains only the tables the app consumes (`ms_orders`, `ms_order_status_logs`, `ms_cart`, `ms_cart_options`, `ms_photo_products`). Sensitive fields (names, emails, addresses, notes) are replaced with `REDACTED`. Import with:
   ```bash
   gunzip -c legacy_safe_dump.sql.gz | mysql -h HOST -u USER -pPASSWORD sportsline
   ```

## Setup
1. Clone repo and checkout `dev/external-developer-safe`.
2. Copy `.env.example` ? `.env` and set:
   - `APP_MYSQL_*` creds for the imported `sportsline_inventory` database.
   - `MYSQL_*` creds for the sanitized legacy load.
   - SMTP/Twilio credentials if you intend to test alerts (otherwise keep placeholders).
3. Run `docker compose up --build` to start `app`, `poller`, and MySQL.
4. Seed vendors/products via the UI or direct inserts as needed.
5. Manually trigger `Run Forecast Now` (available when `APP_DEBUG=True`) once the poller has fetched legacy orders.
6. Open `http://localhost:8000/dashboard/` to explore the cockpit (filters, cards, compare mode, movers bar).

## Testing and Validation
- Add new products/raw materials and associate them via the SKU/BOM UI.
- Run the forecast job (button or `docker compose exec app python -m backend.app.forecasting.job`) to populate `forecast_results` and `reorder_alerts`.
- Use the filters dropdowns to narrow down by tag/vendor/raw material and observe the chart/mover updates.
- Check logs (`docker compose logs app --tail 200`) for sync/audit/forecast entries.

## Gotchas
- The legacy order sync matches `ms_cart.cart_sku` to active `products.product_code`; unmatched SKUs yield no deduction data.
- Shipping-only raw materials (envelopes, mailers) are forecasted from the app DB (`purchase_logs`, `shipping_mapping`) rather than the legacy database.
- Forecast results rely on BOM definitions (`product_recipes`/`sku_recipes`). Keep them in sync with `products`.

## Contacts
If you need help, reach out to the project owner (Joey) via the internal Slack channel before opening pull requests.
