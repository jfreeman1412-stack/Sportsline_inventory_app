# Sportsline Inventory App

This repository hosts the new inventory management service described in `PRD.MD`. The stack is intentionally lightweight:

- **Backend:** FastAPI 3.11+ server with SQLAlchemy models, Alembic-ready migrations, and HTMX/Jinja templates for the desktop UI.
- **Poller:** Lightweight Python process that queries the existing MySQL schema and pushes JSON to the app via an internal endpoint every 90 seconds.
- **Frontend:** Server-rendered HTML powered by HTMX + Bootstrap 5 for rapid UX iteration without a JavaScript bundle.
- **Deployment:** Docker Compose orchestrates the `app`, `poller`, and (optional) supporting services.

### Getting Started

1. Copy `.env.example` to `.env` and fill in the legacy MySQL (for orders) plus the new app database creds (`APP_MYSQL_*`), ShipStation, SMTP, Twilio, session secret, and internal sync secrets.
2. Build and run with Docker Compose: `docker compose up --build`.
3. The FastAPI app exposes:
   - `GET /`: Dashboard shell (HTMX/Bootstrap ready).
   - `POST /internal-sync`: protected endpoint for the poller.
   - `POST /shipstation/label`: ShipStation webhook to deduct shipping SKUs (requires `X-Internal-Token`).
4. Launch the poller container to keep stock deductions in sync with `ms_orders`.

### How to run locally

1. Install dependencies (e.g., `pip install -r backend/requirements.txt`).
2. Copy `.env.example` to `.env` with your local MySQL and notification credentials.
3. Start the backend (`uvicorn backend.app.main:app --reload --host 0.0.0.0 --port 8000`).
4. The first user created through the admin console/script should be assigned the Owner role. You can seed one via:
   ```bash
   python - <<'PY'
   from sqlalchemy.orm import Session
   from backend.app.database import SessionLocal
   from backend.app.models import RoleEnum, User
   from passlib.hash import bcrypt

   with SessionLocal() as db:
       user = User(
           email="owner@example.com",
           password_hash=bcrypt.hash("secret"),
           role=RoleEnum.owner,
       )
       db.add(user)
       db.commit()
   PY
   ```

### Preparing the App Database

The app requires its own tables in the dedicated `sportsline_inventory` database (see the `APP_MYSQL_*` vars). Because `inventory_app` (and `script_user`) are limited, run the DDL from `db/init_app_schema.sql` with a user who has `CREATE` rights (e.g., the DB admin/root):

```bash
mysql -u root -p sportsline_inventory < db/init_app_schema.sql
```

Once the tables exist, grant `SELECT`, `INSERT`, `UPDATE`, and `DELETE` privileges to both `inventory_app` (the app user) and `script_user` so the FastAPI app and poller can operate normally.  
The SQL script now also creates `products` and `product_recipes`, which mirror the finished goods from the legacy `cart_sku`/`pp_internal_name` values and map each product to its raw material BOM.

If you already have `skus` records from before this change, run the following inside `sportsline_inventory` to add the new vendor metadata columns:

```sql
ALTER TABLE skus
  ADD COLUMN vendor_name VARCHAR(255) NULL AFTER description,
  ADD COLUMN vendor_url VARCHAR(512) NULL AFTER vendor_name;
```

### Raw Materials vs Products

- **Raw Materials**: what used to be called “SKUs” – resins, paper rolls, magnets, tubes, envelopes, etc. Manage them via the **Raw Materials** navigation link. Stock levels, waste %, alerts, and purchase logs still live on these SKUs.
- **Shipping mappings** now capture individual `length`, `width`, and `height` so they match the exact `shipments[0].packages` data returned by ShipStation. Enter each dimension separately (inches). The `package_code` stored with a mapping is optional metadata—it no longer blocks a match but can help you differentiate mappings that share dimensions but represent different presets.

If you already created shipping mappings before these changes, the legacy dimensions_string column must allow NULL values:

`sql
ALTER TABLE shipping_mapping MODIFY COLUMN dimensions_string VARCHAR(64) NULL;
`

- **Vendor info**: each raw material now captures a `vendor_name` and optional `vendor_url` so you can jump to the supplier listing from the SKU detail page and keep buying links alongside stock data.
- **Sales contacts**: new fields let you record the salesman name, phone, and email for every raw material so you can always reach the rep directly from the SKU detail and form screens.
- **Purchase logs**: the raw-material purchase form (and edit flow) now exposes an “Add to stock” checkbox (checked by default) plus the back-datable `purchase_date` input so you can record historical price points without touching current stock. The `purchase_logs.applies_to_stock` flag drives that behavior—run `ALTER TABLE purchase_logs ADD COLUMN applies_to_stock BOOLEAN NOT NULL DEFAULT TRUE;` if the table already exists before you redeploy the updated code.
- **Email alerts**: the app now sends SMTP emails using `info@sportslinephotography.com` by default. If you need to override it, add `SMTP_FROM=alerts@domain.com` to your `.env`. The rest of the SMTP envs (`SMTP_HOST/PORT/USER/PASSWORD`) remain unchanged.
- **Settings**: `/settings` is now the single place for your account info, user alert toggles, and system alert configuration (email toggles, SMTP overrides, thresholds, CTA). Managers/Owners see the full set of controls together instead of visiting `System Settings` and `My Account` separately.
- **Products**: the finished goods that customers order. Add each product (matching `cart_sku`/`product_code`) with a BOM that pulls from your raw materials. The sync process deducts raw materials by exploding the product recipe; legacy sync still falls back to raw SKU-based BOMs if a product entry is missing.

Use the **Products** UI to keep your catalog, BOMs, and prices up to date so deductions align with actual production consumption.

### Testing the Sync Flow Locally

1. Insert a test order with `order_open_status = 40` in the legacy `ms_orders`/`ms_cart` tables (matching `pp_internal_name`).  
2. Hit the **Force Sync Now** button (Manager/Owner) or wait for the poller to run.  
3. Verify the deduction in the `skus` table and that a row was written to `sync_logs`.  
4. Ensure the new `products` BOMs deduct the intended raw materials: map a `cart_sku` to a `products.product_code`, add child raw materials with `qty_used`, then Force Sync and double-check each BOM line reduces the expected `skus.current_stock` figure.  
5. Shipping deductions now call the ShipStation API for status 39 events, pulling the actual `shipments[0].packages` data and matching those dimensions/package codes against the Shipping Mappings configured in the UI.
5. The sync query now drives off `ms_order_status_logs.update_date` so both the poller and Force Sync pick up status transitions (printing status 40, shipped status 39) instead of the original `ms_orders.order_date`. That keeps the app aligned with the exact moment the order moved into production/shipping.

### Deduction Log

Managers and Owners can now audit raw-material deductions at `/logs/deductions`. The page lists the date/time, order number, action (printed/shipped), employee, and a short summary, with a collapsible detail row showing every SKU and quantity that was deducted. Use the search box to filter by order number when validating specific syncs.

### Add-on SKU Mapping

Add-on options from `ms_cart_options` now deduct their own SKUs. Use the **Add-on SKUs** page (Managers/Owners only) to assign SKUs and quantities to each legacy add-on name so the sync automatically applies those deductions when the option appears on an order line.

### Development Notes

- Backend tests (Pytest) target 80% coverage eventually.
- Linting/formatting: Black + Ruff (run via `black backend` and `ruff check backend`).
- Roles are enforced via FastAPI dependencies; the first registered user becomes Owner and all others default to Operator unless promoted later.
- Shipping mappings, alert preferences, and sync logs are stored in the app database (see `PRD.MD`).

For more, refer to `PRD.MD`, especially the newly appended addendum that captures the clarified schema, poller contract, alerts, and secrets.
