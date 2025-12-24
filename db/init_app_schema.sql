CREATE TABLE IF NOT EXISTS skus (
    sku_id INT AUTO_INCREMENT PRIMARY KEY,
    sku_code VARCHAR(255) NOT NULL UNIQUE,
    name VARCHAR(255) NOT NULL,
    description TEXT,
    salesman_name VARCHAR(255),
    salesman_phone VARCHAR(64),
    salesman_email VARCHAR(255),
    vendor_name VARCHAR(255),
    vendor_url VARCHAR(512),
    unit_of_measure VARCHAR(20) NOT NULL DEFAULT 'pieces',
    current_stock FLOAT NOT NULL DEFAULT 0.0,
    waste_pct FLOAT NOT NULL DEFAULT 0.0,
    alert_threshold_qty FLOAT DEFAULT NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS sku_recipes (
    id INT AUTO_INCREMENT PRIMARY KEY,
    parent_sku_id INT NOT NULL,
    child_sku_id INT NOT NULL,
    qty_used FLOAT NOT NULL DEFAULT 0.0,
    FOREIGN KEY (parent_sku_id) REFERENCES skus(sku_id),
    FOREIGN KEY (child_sku_id) REFERENCES skus(sku_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

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
    FOREIGN KEY (sku_id) REFERENCES skus(sku_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS products (
    product_id INT AUTO_INCREMENT PRIMARY KEY,
    product_code VARCHAR(255) NOT NULL UNIQUE,
    name VARCHAR(255) NOT NULL,
    description TEXT,
    price DECIMAL(10,2),
    is_active BOOLEAN NOT NULL DEFAULT TRUE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

DROP INDEX IF EXISTS idx_product_code ON products;
CREATE INDEX idx_product_code ON products(product_code);

CREATE TABLE IF NOT EXISTS product_recipes (
    id INT AUTO_INCREMENT PRIMARY KEY,
    parent_product_id INT NOT NULL,
    child_sku_id INT NOT NULL,
    qty_used FLOAT NOT NULL DEFAULT 0.0,
    FOREIGN KEY (parent_product_id) REFERENCES products(product_id),
    FOREIGN KEY (child_sku_id) REFERENCES skus(sku_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS shipping_mapping (
    id INT AUTO_INCREMENT PRIMARY KEY,
    dimensions_string VARCHAR(64) UNIQUE,
    items_json TEXT NOT NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

ALTER TABLE shipping_mapping
  ADD COLUMN IF NOT EXISTS length FLOAT DEFAULT NULL,
  ADD COLUMN IF NOT EXISTS width FLOAT DEFAULT NULL,
  ADD COLUMN IF NOT EXISTS height FLOAT DEFAULT NULL,
  ADD COLUMN IF NOT EXISTS package_code VARCHAR(64) DEFAULT NULL;

CREATE TABLE IF NOT EXISTS sync_logs (
    id INT AUTO_INCREMENT PRIMARY KEY,
    internal_order_id INT NOT NULL,
    shipstation_order_id VARCHAR(128),
    timestamp DATETIME(6) DEFAULT CURRENT_TIMESTAMP(6),
    action VARCHAR(64) NOT NULL,
    details TEXT,
    UNIQUE KEY uq_shipstation_order (shipstation_order_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

DROP INDEX IF EXISTS idx_sync_timestamp ON sync_logs;
CREATE INDEX idx_sync_timestamp ON sync_logs(timestamp);

CREATE TABLE IF NOT EXISTS users (
    user_id INT AUTO_INCREMENT PRIMARY KEY,
    email VARCHAR(255) NOT NULL UNIQUE,
    password_hash VARCHAR(512) NOT NULL,
    role ENUM('operator','manager','owner') NOT NULL DEFAULT 'operator',
    receives_stock_alerts BOOLEAN NOT NULL DEFAULT TRUE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS app_settings (
    id INT AUTO_INCREMENT PRIMARY KEY,
    email_alerts_enabled BOOLEAN NOT NULL DEFAULT TRUE,
    smtp_host VARCHAR(255),
    smtp_port INT,
    smtp_user VARCHAR(255),
    smtp_password VARCHAR(512),
    smtp_from VARCHAR(255),
    price_spike_pct FLOAT NOT NULL DEFAULT 10.0,
    low_stock_cta VARCHAR(512)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

INSERT INTO app_settings (email_alerts_enabled, price_spike_pct)
SELECT 1, 10.0 FROM (SELECT 1) AS tmp
WHERE NOT EXISTS (SELECT 1 FROM app_settings);

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
    timestamp DATETIME(6) DEFAULT CURRENT_TIMESTAMP(6)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS add_on_mappings (
    id INT AUTO_INCREMENT PRIMARY KEY,
    add_on_name VARCHAR(255) NOT NULL,
    sku_code VARCHAR(255) NOT NULL,
    quantity FLOAT NOT NULL DEFAULT 1.0,
    notes TEXT
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
