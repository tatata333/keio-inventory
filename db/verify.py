from __future__ import annotations

import os
import sys

import psycopg2

DEFAULTS = {
    "DB_HOST": "127.0.0.1",
    "DB_PORT": "5432",
    "DB_NAME": "keio_inventory",
    "DB_USER": "postgres",
    "DB_PASSWORD": "postgres",
}


def _cfg(key):
    return os.environ.get(key, DEFAULTS[key])


def main():
    conn = psycopg2.connect(
        host=_cfg("DB_HOST"), port=_cfg("DB_PORT"), dbname=_cfg("DB_NAME"),
        user=_cfg("DB_USER"), password=_cfg("DB_PASSWORD"),
    )
    cur = conn.cursor()
    cur.execute("""
        SELECT tablename FROM pg_tables
        WHERE schemaname = 'public' AND tablename NOT LIKE 'pg_%'
        ORDER BY tablename;
    """)
    tables = [r[0] for r in cur.fetchall()]
    print("Tables in DB:", len(tables))
    for t in tables:
        print("  -", t)

    for t in ("product", "place", "sku_daily_sales", "result_forecast"):
        try:
            cur.execute(f'SELECT count(*) FROM "{t}"')
            print(f"  {t}: {cur.fetchone()[0]} rows")
        except Exception as e:
            print(f"  {t}: <error {e}>")

    # check partition set on sales
    cur.execute("SELECT count(*) FROM pg_inherits i JOIN pg_class c ON c.oid = i.inhrelid WHERE c.relname='sku_daily_sales'")
    print("sku_daily_sales partitions:", cur.fetchone()[0])
    cur.close()
    conn.close()


if __name__ == "__main__":
    main()
