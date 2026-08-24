"""SQLite 版 デモDB構築・データ投入（Render等の公開デモ用）。

- Base.metadata.create_all でスキーマ作成（Alembic不要・PostgreSQL依存を排除）
- demo_catalog からカテゴリ/店舗/商品を投入
- パラメータ（サービスレベル・閾値等）を投入
- jobs.seed_inventory / jobs.run_pipeline_db で POS・在庫・計算結果を投入

使い方: DATABASE_URL=sqlite:///keio_demo.db python -m jobs.setup_sqlite
"""
from __future__ import annotations

import os

from keio_inventory.demo_catalog import CATALOG, PLACES, CATEGORIES
from keio_inventory.infra.db.session import SessionLocal, engine as _engine
from keio_inventory.infra.db.models import Base, ProductGroup, Place, Product, DemandForecastParam

# 確実にスキーマを新規作成（既存は一旦落とす）
import glob as _glob
_url = os.environ.get("DATABASE_URL", "")
_dbpath = _url.replace("sqlite:///", "") if _url.startswith("sqlite") else None
if _dbpath and os.path.exists(_dbpath):
    try:
        os.remove(_dbpath)
        print("[+] removed old sqlite:", _dbpath)
    except Exception as e:
        print("[!] could not remove old db:", e)

Base.metadata.create_all(_engine)
print("[+] schema created (create_all)")

# パラメータ初期値（seed.sql 相当）
PARAMS = [
    ("service_level", '{"value": 0.95}', '目標サービスレベル'),
    ("abc_ratio_a", '{"value": 0.80}', 'ABC分類 A閾値'),
    ("abc_ratio_b", '{"value": 0.95}', 'ABC分類 B閾値'),
    ("xyz_cv_x", '{"value": 0.5}', 'XYZ分類 X境界CV'),
    ("xyz_cv_y", '{"value": 1.0}', 'XYZ分類 Y境界CV'),
    ("inventory_enabled", '{"value": false}', '在庫データモード切替 (pos_only=true / full=false)'),
    ("slow_mover.turnover_threshold", '{"value": 1.0}', '滞留検知 回転率閾値'),
    ("slow_mover.days_threshold", '{"value": 180}', '滞留検知 日数閾値'),
    ("demand_spike.ratio", '{"value": 2.5}', '需要急上昇 倍率閾値'),
    ("demand_drop.ratio", '{"value": 0.4}', '需要急落 倍率閾値'),
]


def main():
    db = SessionLocal()
    try:
        # カテゴリ（商品グループ）
        for i, c in enumerate(CATEGORIES, start=1):
            db.merge(ProductGroup(id=i, group_code=c["code"], group_name=c["name"], hierarchy_level=c["level"]))
        # 店舗
        for pl in PLACES:
            db.merge(Place(id=pl["id"], place_code=pl["code"], place_name=pl["name"], place_type=pl["place_type"]))
        # 商品
        for p in CATALOG:
            db.merge(Product(
                id=p["id"], sku_code=p["sku_code"], name=p["name"], product_group_id=p["group_id"],
                mds=p["mds"], category=p["category"], supplier_code=p["supplier_code"],
                lead_time_days=p["lead_time_days"], lead_time_std=p["lead_time_std"],
            ))
        # パラメータ（id を明示して SQLite の自動採番問題を回避）
        for i, (key, val, desc) in enumerate(PARAMS, start=1):
            db.merge(DemandForecastParam(id=i, param_key=key, value=val, description=desc))
        db.commit()
        print(f"[+] masters: groups={len(CATEGORIES)} places={len(PLACES)} products={len(CATALOG)} params={len(PARAMS)}")
    finally:
        db.close()


if __name__ == "__main__":
    main()
    # POS・在庫・仕入 と 計算結果 を投入（既存シードを再利用）
    from jobs.seed_inventory import main as seed_main
    from jobs.run_pipeline_db import main as run_main
    seed_main()
    run_main()
    print("[+] SQLite デモDB 構築完了")