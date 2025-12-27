-- init_app_schema.sql
-- Create tables for the Inventory App (run against new sportsline_inventory database)

CREATE TABLE IF NOT EXISTS users (
    user_id INT AUTO_INCREMENT PRIMARY KEY,
    email VARCHAR(255) UNIQUE NOT NULL,
    password_hash VARCHAR(255) NOT NULL,
    role ENUM('operator', 'manager', 'owner') NOT NULL DEFAULT 'operator',
    receives_stock_alerts BOOL DEFAULT TRUE
);

CREATE TABLE IF NOT EXISTS app_settings (
    id INT AUTO_INCREMENT PRIMARY KEY,
    email_alerts_enabled BOOLEAN NOT NULL DEFAULT TRUE,
    smtp_host VARCHAR(255),
    smtp_port INT,
    smtp_user VARCHAR(255),
    smtp_password VARCHAR(512),
    smtp_from VARCHAR(255),
    price_spike_pct FLOAT NOT NULL DEFAULT 10.0,
    low_stock_cta VARCHAR(512),
    deduction_window_days INT NOT NULL DEFAULT 30
);

CREATE TABLE IF NOT EXISTS tags (
    tag_id INT AUTO_INCREMENT PRIMARY KEY,
    name VARCHAR(64) UNIQUE NOT NULL,
    color VARCHAR(32),
    description TEXT
);

CREATE TABLE IF NOT EXISTS sku_tags (
    sku_id INT NOT NULL,
    tag_id INT NOT NULL,
    PRIMARY KEY(sku_id, tag_id),
    FOREIGN KEY (sku_id) REFERENCES skus(sku_id) ON DELETE CASCADE,
    FOREIGN KEY (tag_id) REFERENCES tags(tag_id) ON DELETE CASCADE
);

INSERT INTO app_settings (email_alerts_enabled, price_spike_pct)
SELECT 1, 10.0 FROM (SELECT 1) AS tmp
WHERE NOT EXISTS (SELECT 1 FROM app_settings);

CREATE TABLE IF NOT EXISTS skus (
    sku_id INT AUTO_INCREMENT PRIMARY KEY,
    sku_code VARCHAR(255) UNIQUE NOT NULL,  -- Matches legacy pp_internal_name/cart_sku
    name VARCHAR(255) NOT NULL,
    description TEXT,
    salesman_name VARCHAR(255),
    salesman_phone VARCHAR(64),
    salesman_email VARCHAR(255),
    unit_of_measure ENUM('inches', 'feet', 'pieces') NOT NULL DEFAULT 'pieces',
    current_stock FLOAT NOT NULL DEFAULT 0.0,
    waste_pct FLOAT NOT NULL DEFAULT 0.0,
    alert_threshold_qty FLOAT
);

CREATE TABLE IF NOT EXISTS sku_recipes (
    id INT AUTO_INCREMENT PRIMARY KEY,
    parent_sku_id INT NOT NULL,
    child_sku_id INT NOT NULL,
    qty_used FLOAT NOT NULL,
    FOREIGN KEY (parent_sku_id) REFERENCES skus(sku_id) ON DELETE CASCADE,
    FOREIGN KEY (child_sku_id) REFERENCES skus(sku_id) ON DELETE CASCADE,
    UNIQUE KEY unique_recipe (parent_sku_id, child_sku_id)
);

CREATE TABLE IF NOT EXISTS purchase_logs (
    id INT AUTO_INCREMENT PRIMARY KEY,
    sku_id INT NOT NULL,
    purchase_date DATE NOT NULL,
    quantity FLOAT NOT NULL,
    price DECIMAL(18,6) NOT NULL,
    applies_to_stock BOOLEAN NOT NULL DEFAULT TRUE,
    supplier_name VARCHAR(255),
    supplier_url VARCHAR(512),
    supplier_code VARCHAR(255),
    notes TEXT,
    FOREIGN KEY (sku_id) REFERENCES skus(sku_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS shipping_mapping (
    id INT AUTO_INCREMENT PRIMARY KEY,
    dimensions_string VARCHAR(64) UNIQUE,
    items_json TEXT NOT NULL,  -- JSON array of sku_codes, e.g. '["pano-tube", "shipping-label"]'
    length FLOAT,
    width FLOAT,
    height FLOAT,
    package_code VARCHAR(64)
);

CREATE TABLE IF NOT EXISTS sync_logs (
    id INT AUTO_INCREMENT PRIMARY KEY,
    internal_order_id INT,
    shipstation_order_id VARCHAR(255) UNIQUE,
    timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
    action VARCHAR(50) NOT NULL,  -- e.g., 'print_deduct', 'shipping_deduct'
    details TEXT
);

-- Indexes for performance
CREATE INDEX idx_sync_timestamp ON sync_logs(timestamp);
CREATE INDEX idx_purchase_sku_date ON purchase_logs(sku_id, purchase_date);

CREATE TABLE IF NOT EXISTS audit_logs (
    id INT AUTO_INCREMENT PRIMARY KEY,
    order_id INT,
    user_id INT,
    child_sku_code VARCHAR(255),
    child_sku_name VARCHAR(255),
    child_unit VARCHAR(64),
    quantity FLOAT,
    product_code VARCHAR(255),
    product_name VARCHAR(255),
    product_quantity FLOAT,
    action VARCHAR(128) NOT NULL,
    details TEXT,
    timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS add_on_mappings (
    id INT AUTO_INCREMENT PRIMARY KEY,
    add_on_name VARCHAR(255) NOT NULL,
    sku_code VARCHAR(255) NOT NULL,
    quantity FLOAT NOT NULL DEFAULT 1.0,
    notes TEXT
);

CREATE TABLE IF NOT EXISTS forecast_results (
    forecast_id INT AUTO_INCREMENT PRIMARY KEY,
    sku_id INT NOT NULL,
    forecast_date DATE NOT NULL,
    predicted_quantity FLOAT NOT NULL,
    model_version VARCHAR(64),
    model_score FLOAT,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (sku_id) REFERENCES skus(sku_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS reorder_alerts (
    alert_id INT AUTO_INCREMENT PRIMARY KEY,
    sku_id INT NOT NULL,
    alert_level VARCHAR(32) NOT NULL,
    message TEXT NOT NULL,
    generated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    active BOOLEAN NOT NULL DEFAULT TRUE,
    FOREIGN KEY (sku_id) REFERENCES skus(sku_id) ON DELETE CASCADE
);
