from __future__ import annotations

import os
from datetime import date, timedelta

import psycopg2

DEFAULTS = {
    "DB_HOST": "127.0.0.1", "DB_PORT": "5432", "DB_NAME": "keio_inventory",
    "DB_USER": "postgres", "DB_PASSWORD": "postgres",
}


def cfg(k):
    return os.environ.get(k, DEFAULTS[k])


def main():
    conn = psycopg2.connect(host=cfg("DB_HOST"), port=cfg("DB_PORT"),
                            dbname=cfg("DB_NAME"), user=cfg("DB_USER"),
                            password=cfg("DB_PASSWORD"))
    cur = conn.cursor()

    # 1) master data exists
    cur.execute("SELECT count(*) FROM product")
    n_products = cur.fetchone()[0]
    cur.execute("SELECT count(*) FROM place")
    n_places = cur.fetchone()[0]
    assert n_products >= 8, n_products
    assert n_places >= 2, n_places

    # 2) insert one sales row to a partition (should work via partition)
    pid = 1
    plid = 1
    d = date(2026, 6, 1)
    cur.execute("""
        INSERT INTO sku_daily_sales (sales_date, product_id, place_id, qty_sold, amount)
        VALUES (%s, %s, %s, %s, %s)
        ON CONFLICT (sales_date, product_id, place_id) DO UPDATE
        SET qty_sold = EXCLUDED.qty_sold, amount = EXCLUDED.amount
    """, (d, pid, plid, 3, 2700))
    conn.commit()
    cur.execute("SELECT qty_sold, amount FROM sku_daily_sales WHERE sales_date=%s AND product_id=%s AND place_id=%s", (d, pid, plid))
    row = cur.fetchone()
    assert row == (3, 2700), row

    # 3) write a safety-stock result with pos_only mode
    cur.execute("""
        INSERT INTO result_safety_stock
            (product_id, place_id, calc_date, safety_stock, reorder_point, order_qty,
             avg_demand, demand_std, lead_time_days, service_level, mode)
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,'pos_only')
        ON CONFLICT (product_id, place_id, calc_date) DO UPDATE
        SET safety_stock=EXCLUDED.safety_stock
    """, (pid, plid, date.today(), 9.6, 44.1, 44.1, 4.93, 1.20, 7.0, 0.95))
    conn.commit()

    # 4) insert anomaly alert with JSONB detail
    cur.execute("""
        INSERT INTO anomaly_alert (product_id, place_id, anomaly_type, severity, status, detail, recommended_action)
        VALUES (%s,%s,'demand_spike','critical','open', %s, '追加発注・在庫確保')
    """, (7, plid, '{"recent_7d": 45, "baseline_28d": 12}'))
    conn.commit()

    # 5) read-back via join
    cur.execute("""
        SELECT p.name, ss.mode, ss.safety_stock FROM result_safety_stock ss
        JOIN product p ON p.id = ss.product_id
        WHERE ss.product_id=%s AND ss.calc_date=CURRENT_DATE
    """, (pid,))
    print("[ok] safety_stock joined:", cur.fetchone())

    # 6) partitions listing
    cur.execute("""
        SELECT child.relname FROM pg_inherits
        JOIN pg_class parent ON parent.oid = pg_inherits.inhparent
        JOIN pg_class child  ON child.oid  = pg_inherits.inhrelid
        WHERE parent.relname='sku_daily_sales' ORDER BY child.relname
    """)
    print("[ok] sku_daily_sales partitions:", [r[0] for r in cur.fetchall()])

    cur.execute("SELECT param_key FROM m_demand_forecast_param ORDER BY 1")
    print("[ok] param keys:", [r[0] for r in cur.fetchall()])

    cur.close(); conn.close()
    print("ALL DB SMOKE TESTS PASSED")


if __name__ == "__main__":
    main()
