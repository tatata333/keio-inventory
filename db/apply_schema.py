"""データベース構築オーケストレーター。

v2: スキーマは Alembic マイグレーションで管理する方式へ移行。
  - DB が無ければ作成
  - alembic upgrade head でスキーマ構築（db/migrations が正）
  - seed.sql でマスタ・パラメータ投入

v3: 商品・店舗・カテゴリのマスタは demo_catalog（単一 source of truth）から
    生成・投入する。seed.sql には商品マスタを書かず、ここでカタログに基づいて投入。

使い方:
    python db/apply_schema.py
"""
from __future__ import annotations

import os
import subprocess
import sys

import psycopg2
from psycopg2.extensions import ISOLATION_LEVEL_AUTOCOMMIT

DEFAULTS = {
    "DB_HOST": "127.0.0.1",
    "DB_PORT": "5432",
    "DB_NAME": "keio_inventory",
    "DB_USER": "postgres",
    "DB_PASSWORD": "postgres",
}

_HERE = os.path.dirname(os.path.abspath(__file__))
_PROJECT = os.path.dirname(_HERE)          # sample/
SEED_SQL = os.path.join(_HERE, "seed.sql")


def _cfg(key: str) -> str:
    return os.environ.get(key, DEFAULTS[key])


def _connect(dbname: str | None = None):
    return psycopg2.connect(
        host=_cfg("DB_HOST"), port=_cfg("DB_PORT"),
        dbname=dbname or _cfg("DB_NAME"), user=_cfg("DB_USER"),
        password=_cfg("DB_PASSWORD"),
    )


def create_database_if_missing() -> None:
    admin = _connect(dbname="postgres")
    admin.set_isolation_level(ISOLATION_LEVEL_AUTOCOMMIT)
    cur = admin.cursor()
    cur.execute("SELECT 1 FROM pg_database WHERE datname = %s", (_cfg("DB_NAME"),))
    if cur.fetchone() is None:
        cur.execute(f'CREATE DATABASE "{_cfg("DB_NAME")}"')
        print(f"[+] database created: {_cfg('DB_NAME')}")
    else:
        print(f"[=] database exists: {_cfg('DB_NAME')}")
    cur.close()
    admin.close()


def alembic_upgrade() -> None:
    """Apply all schema migrations (models.py = source of truth)."""
    env = dict(os.environ)
    env.setdefault("DB_HOST", _cfg("DB_HOST"))
    env.setdefault("DB_PORT", _cfg("DB_PORT"))
    env.setdefault("DB_NAME", _cfg("DB_NAME"))
    env.setdefault("DB_USER", _cfg("DB_USER"))
    env.setdefault("DB_PASSWORD", _cfg("DB_PASSWORD"))
    src = os.path.join(_PROJECT, "src")
    current = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = src + (os.pathsep + current if current else "")
    print("[+] running alembic upgrade head ...")
    result = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=_PROJECT, env=env,
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        print(result.stdout)
        print(result.stderr)
        raise RuntimeError("alembic upgrade head failed")
    print("[+] schema applied (Alembic)")


def apply_catalog_masters(conn) -> None:
    """商品カタログ(demo_catalog)から商品グループ・店舗・商品マスタを投入。

    明示 id で投入し（AUTO INCREMENT の採番乱れ防止）、商品追加は
    demo_catalog.CATALOG / PLACES / CATEGORIES を編集するだけで自動反映する。
    """
    try:
        from keio_inventory.demo_catalog import CATALOG, PLACES, CATEGORIES
    except Exception:
        sys.path.insert(0, os.path.join(_PROJECT, "src"))
        from keio_inventory.demo_catalog import CATALOG, PLACES, CATEGORIES

    cur = conn.cursor()

    # 商品グループ（カテゴリ）
    for i, c in enumerate(CATEGORIES, start=1):
        cur.execute(
            "INSERT INTO product_group (id, group_code, group_name, hierarchy_level) "
            "VALUES (%s, %s, %s, %s) ON CONFLICT (group_code) DO NOTHING",
            (i, c["code"], c["name"], c["level"]),
        )

    # 店舗
    for pl in PLACES:
        cur.execute(
            "INSERT INTO place (id, place_code, place_name, place_type) "
            "VALUES (%s, %s, %s, %s) ON CONFLICT (place_code) DO NOTHING",
            (pl["id"], pl["code"], pl["name"], pl["place_type"]),
        )

    # 商品（明示 id で投入 → AUTO INCREMENT の採番乱れを防止。商品追加は CATALOG の id と一致させる）
    for p in CATALOG:
        cur.execute(
            "INSERT INTO product (id, sku_code, name, product_group_id, mds, category, "
            "supplier_code, lead_time_days, lead_time_std) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s) "
            "ON CONFLICT (sku_code) DO NOTHING",
            (p["id"], p["sku_code"], p["name"], p["group_id"], p["mds"], p["category"],
             p["supplier_code"], p["lead_time_days"], p["lead_time_std"]),
        )

    conn.commit()
    cur.close()
    print(f"[+] catalog masters applied: groups={len(CATEGORIES)} places={len(PLACES)} products={len(CATALOG)}")


def apply_seed() -> None:
    if not os.path.exists(SEED_SQL):
        print("[!] seed.sql not found, skip")
        return
    with open(SEED_SQL, encoding="utf-8") as f:
        seed = f.read()
    conn = _connect()
    cur = conn.cursor()
    cur.execute(seed)
    conn.commit()
    cur.close()
    conn.close()
    print("[+] seed data applied")


if __name__ == "__main__":
    create_database_if_missing()
    alembic_upgrade()
    # カタログ由来のマスタ（商品・店舗・カテゴリ）を投入
    conn = _connect()
    try:
        apply_catalog_masters(conn)
    finally:
        conn.close()
    apply_seed()
    print("Done.")
