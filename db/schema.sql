
-- =============================================================
-- 在庫最適化プラットフォーム スキーマ定義 (PostgreSQL)
-- 対応: design/02_data_model.md
-- =============================================================

BEGIN;

-- マスタ系 -------------------------------------------------
CREATE TABLE IF NOT EXISTS product_group (
    id          BIGSERIAL PRIMARY KEY,
    group_code  VARCHAR(64) NOT NULL UNIQUE,
    group_name  VARCHAR(255) NOT NULL,
    hierarchy_level VARCHAR(32),
    created_at  TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE IF NOT EXISTS product (
    id               BIGSERIAL PRIMARY KEY,
    sku_code         VARCHAR(64)  NOT NULL UNIQUE,
    name             VARCHAR(255) NOT NULL,
    product_group_id BIGINT       REFERENCES product_group(id),
    mds              VARCHAR(64),
    category         VARCHAR(128),
    supplier_code    VARCHAR(64),
    lead_time_days   NUMERIC(8,2) DEFAULT 7,
    lead_time_std    NUMERIC(8,2) DEFAULT 1,
    is_active        BOOLEAN      DEFAULT TRUE,
    created_at       TIMESTAMPTZ DEFAULT now(),
    updated_at       TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE IF NOT EXISTS place (
    id         BIGSERIAL PRIMARY KEY,
    place_code VARCHAR(32) NOT NULL UNIQUE,
    place_name VARCHAR(128) NOT NULL,
    place_type VARCHAR(16) NOT NULL CHECK (place_type IN ('store','warehouse'))
);

-- 取引・時系列系（親テーブル + パーティション用）------------
CREATE TABLE IF NOT EXISTS sku_daily_sales (
    sales_date DATE NOT NULL,
    product_id BIGINT NOT NULL REFERENCES product(id),
    place_id   BIGINT NOT NULL REFERENCES place(id),
    qty_sold   NUMERIC NOT NULL DEFAULT 0,
    amount     NUMERIC NOT NULL DEFAULT 0,
    PRIMARY KEY (sales_date, product_id, place_id)
) PARTITION BY RANGE (sales_date);

CREATE TABLE IF NOT EXISTS inventory_daily (
    inventory_date DATE NOT NULL,
    product_id BIGINT NOT NULL REFERENCES product(id),
    place_id   BIGINT NOT NULL REFERENCES place(id),
    on_hand_qty    NUMERIC NOT NULL DEFAULT 0,
    allocated_qty  NUMERIC NOT NULL DEFAULT 0,
    available_qty  NUMERIC NOT NULL DEFAULT 0,
    PRIMARY KEY (inventory_date, product_id, place_id)
) PARTITION BY RANGE (inventory_date);

CREATE TABLE IF NOT EXISTS purchase_history (
    po_date       DATE NOT NULL,
    product_id    BIGINT NOT NULL REFERENCES product(id),
    place_id      BIGINT NOT NULL REFERENCES place(id),
    order_qty     NUMERIC NOT NULL DEFAULT 0,
    received_qty  NUMERIC,
    expected_date DATE,
    PRIMARY KEY (po_date, product_id, place_id)
) PARTITION BY RANGE (po_date);

-- 算出結果系 ---------------------------------------------
CREATE TABLE IF NOT EXISTS result_forecast (
    id           BIGSERIAL PRIMARY KEY,
    product_id   BIGINT NOT NULL REFERENCES product(id),
    place_id     BIGINT NOT NULL REFERENCES place(id),
    forecast_date DATE NOT NULL DEFAULT CURRENT_DATE,
    target_date  DATE NOT NULL,
    forecast_p50 NUMERIC,
    forecast_p80 NUMERIC,
    forecast_p95 NUMERIC,
    actual_qty   NUMERIC,
    model_name   VARCHAR(128),
    created_at   TIMESTAMPTZ DEFAULT now(),
    UNIQUE (product_id, place_id, forecast_date, target_date)
);
CREATE INDEX IF NOT EXISTS idx_forecast_lookup ON result_forecast (target_date, product_id, place_id);

CREATE TABLE IF NOT EXISTS result_safety_stock (
    id              BIGSERIAL PRIMARY KEY,
    product_id      BIGINT NOT NULL REFERENCES product(id),
    place_id        BIGINT NOT NULL REFERENCES place(id),
    calc_date       DATE NOT NULL DEFAULT CURRENT_DATE,
    safety_stock    NUMERIC,
    reorder_point   NUMERIC,
    order_qty       NUMERIC,
    avg_demand      NUMERIC,
    demand_std      NUMERIC,
    lead_time_days  NUMERIC,
    service_level   NUMERIC,
    mode            VARCHAR(16) NOT NULL CHECK (mode IN ('pos_only','full')),
    created_at      TIMESTAMPTZ DEFAULT now(),
    UNIQUE (product_id, place_id, calc_date)
);

CREATE TABLE IF NOT EXISTS result_abc_xyz (
    id           BIGSERIAL PRIMARY KEY,
    product_id   BIGINT NOT NULL REFERENCES product(id),
    abc_class    CHAR(1) CHECK (abc_class IN ('A','B','C')),
    xyz_class    CHAR(1) CHECK (xyz_class IN ('X','Y','Z')),
    calc_date    DATE NOT NULL DEFAULT CURRENT_DATE,
    sales_amount NUMERIC,
    cv           NUMERIC,
    created_at   TIMESTAMPTZ DEFAULT now(),
    UNIQUE (product_id, calc_date)
);

CREATE TABLE IF NOT EXISTS result_order_recommendation (
    id               BIGSERIAL PRIMARY KEY,
    product_id       BIGINT NOT NULL REFERENCES product(id),
    place_id         BIGINT NOT NULL REFERENCES place(id),
    calc_date        DATE NOT NULL DEFAULT CURRENT_DATE,
    forecast_demand  NUMERIC,
    safety_stock     NUMERIC,
    on_hand_qty      NUMERIC,
    recommended_qty  NUMERIC,
    status           VARCHAR(16) DEFAULT 'pending'
                     CHECK (status IN ('pending','adjusted','approved','rejected')),
    created_at       TIMESTAMPTZ DEFAULT now(),
    UNIQUE (product_id, place_id, calc_date)
);

CREATE TABLE IF NOT EXISTS anomaly_alert (
    id                 BIGSERIAL PRIMARY KEY,
    product_id         BIGINT NOT NULL REFERENCES product(id),
    place_id           BIGINT NOT NULL REFERENCES place(id),
    anomaly_type       VARCHAR(32) NOT NULL,
    severity           VARCHAR(16) NOT NULL CHECK (severity IN ('low','medium','high','critical')),
    detected_at        DATE NOT NULL DEFAULT CURRENT_DATE,
    status             VARCHAR(16) DEFAULT 'open' CHECK (status IN ('open','ack','done')),
    detail             JSONB,
    recommended_action VARCHAR(255),
    resolved_at        TIMESTAMPTZ,
    created_at         TIMESTAMPTZ DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_anomaly_status ON anomaly_alert (status, severity, detected_at);

-- 管理系 -----------------------------------------------
CREATE TABLE IF NOT EXISTS m_demand_forecast_param (
    id          BIGSERIAL PRIMARY KEY,
    param_key   VARCHAR(128) NOT NULL UNIQUE,
    value       JSONB NOT NULL,
    description VARCHAR(255),
    updated_at  TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE IF NOT EXISTS sys_user (
    id         BIGSERIAL PRIMARY KEY,
    username   VARCHAR(64) NOT NULL UNIQUE,
    password_hash VARCHAR(255) NOT NULL,
    role       VARCHAR(32) NOT NULL CHECK (role IN ('buyer','inv_ctrl','exec','admin')),
    is_active  BOOLEAN DEFAULT TRUE,
    created_at TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE IF NOT EXISTS audit_log (
    id         BIGSERIAL PRIMARY KEY,
    username   VARCHAR(64),
    action     VARCHAR(128) NOT NULL,
    target     JSONB,
    occurred_at TIMESTAMPTZ DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_audit_time ON audit_log (occurred_at);

-- パーティション自動生成関数 -----------------------------
CREATE OR REPLACE FUNCTION fn_create_partition_monthly(_tbl TEXT, _part_start DATE)
RETURNS void AS $$
DECLARE _part_name TEXT; _part_end DATE;
BEGIN
    _part_name := _tbl || '_' || to_char(_part_start, 'YYYYMM');
    _part_end  := (_part_start + INTERVAL '1 month')::date;
    EXECUTE format(
      'CREATE TABLE IF NOT EXISTS %I PARTITION OF %I FOR VALUES FROM (%L) TO (%L)',
      _part_name, _tbl, _part_start, _part_end
    );
END; $$ LANGUAGE plpgsql;

COMMIT;
