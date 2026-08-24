"""在庫最適化のデモ用シードスクリプト（充実版）

POS(日次需要) / 在庫 / 仕入 を投入し、異常検知・ABC-XYZがデモでも機能するようにする。

- 需要: 各商品に直近 N_DAYS 日分の日次需要（曜日・季節・ノイズ）
- 商品・店舗の定義は demo_catalog を単一 source of truth とする
  （商品追加は demo_catalog.CATALOG に1行足すだけで自動反映される）
- 一部商品は需要急変（急上昇/急落）を仕込み、異常検知を検知可能に
- 在庫・仕入: 蓄積率100%になるよう投入（fullモード切替用）
"""
from __future__ import annotations

from datetime import date, timedelta

import numpy as np

from keio_inventory.demo_catalog import CATALOG, PLACES, LATE_DROP_FACTORS, get_product
from keio_inventory.infra.db.models import (
    InventoryDaily, Product, Place, PurchaseHistory, SkuDailySales,
)
from keio_inventory.infra.db.session import SessionLocal

N_DAYS = 90  # 需要・在庫の日数（ABC-XYZ・異常検知に十分）


def _gen_demand(pid: int, day: int, n: int) -> float:
    """商品別の日次需要を生成（カタログの base_demand / scenario に基づく）。

    - 一部商品（scenario=late_drop / late_mild_drop）は後半で需要が落ちる
    - scenario=demand_spike の商品は需要急上昇スパイクを仕込む
    未定義商品はカタログ既定値で生成。
    """
    p = get_product(pid)
    rng = np.random.default_rng(pid * 1000 + day)
    base = p["base_demand"]
    dow = np.array([0.9, 0.8, 0.8, 0.9, 1.0, 1.2, 1.3])
    season = 1 + 0.1 * np.sin(2 * np.pi * day / 30.4)
    noise = 1 + 0.15 * rng.normal()
    d = base * dow[day % 7] * season * noise
    sc = p.get("scenario", "")
    # 需要急上昇スパイク（demand_spike）
    if sc == "demand_spike" and day in (n - 10, n - 9):
        d *= 5.0
    # 後半の需要急落/ゆるやか低下（late_drop / late_mild_drop）
    factor = LATE_DROP_FACTORS.get(sc)
    if factor is not None and day > n * 0.7:
        d *= factor
    return max(0.0, round(float(d), 2))


def _gen_onhand(pid: int, day: int) -> float:
    p = get_product(pid)
    base = p["onhand_base"]
    r = (pid * 7 + day * 3) % 9
    return float(max(2, base - r * 3))


def main(days: int = N_DAYS):
    db = SessionLocal()
    try:
        products = db.query(Product).all()
        places = db.query(Place).all()
        today = date.today()
        if not products or not places:
            raise RuntimeError("product/place マスタが無い。先に db/apply_schema.py を実行してください。")

        # 既存の取引系データをクリア（冪等に再シード）
        db.query(SkuDailySales).delete()
        db.query(InventoryDaily).delete()
        db.query(PurchaseHistory).delete()
        db.flush()

        # --- POS（日次需要）---
        n_pos = 0
        for pr in products:
            price = get_product(pr.id)["price"]
            for pl in places:
                for d in range(days):
                    date_ = today - timedelta(days=(days - 1 - d))  # 古い順
                    qty = _gen_demand(pr.id, d, days)
                    db.merge(SkuDailySales(
                        sales_date=date_, product_id=pr.id, place_id=pl.id,
                        qty_sold=qty, amount=qty * price,
                    ))
                    n_pos += 1

        # --- inventory_daily ---
        n_inv = 0
        for pr in products:
            for pl in places:
                for d in range(days):
                    db.merge(InventoryDaily(
                        inventory_date=today - timedelta(days=d),
                        product_id=pr.id, place_id=pl.id,
                        on_hand_qty=_gen_onhand(pr.id, d),
                        allocated_qty=0, available_qty=_gen_onhand(pr.id, d),
                    ))
                    n_inv += 1

        # --- purchase_history（実測リードタイム用）---
        n_pur = 0
        for pr in products:
            for pl in places:
                db.merge(PurchaseHistory(
                    po_date=today - timedelta(days=10), product_id=pr.id, place_id=pl.id,
                    order_qty=100, received_qty=100, expected_date=today - timedelta(days=3),
                ))
                n_pur += 1

        db.commit()
        print(f"[+] seeded POS={n_pos} / inventory={n_inv} / purchase={n_pur}")
    finally:
        db.close()


if __name__ == "__main__":
    main()
