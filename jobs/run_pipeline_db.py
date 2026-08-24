"""バッチ相当: エンジン計算結果を PostgreSQL へ永続化する。

設計書 06_batch の daily_forecast / daily_order / daily_anomaly /
weekly_segment / weekly_stock に相当する処理を、この環境用に1スクリプトで
再現する（Airflow導入時は各 dag タスクに分割）。
"""
from __future__ import annotations

from datetime import date

from keio_inventory.infra.db.models import Product, Place
from keio_inventory.infra.db.repository import InventoryRepository
from keio_inventory.infra.db.session import SessionLocal
from keio_inventory.domain.engine import InventoryEngine


def main(calc_date: date | None = None, end_date=None):
    calc_date = calc_date or date.today()

    db = SessionLocal()
    try:
        repo = InventoryRepository(db)
        # 在庫データ蓄積状況に応じてモードを【自動】判定・切替 (pos_only / full)
        # tasks._resolve_and_build_engine が蓄積率+鮮度を見て inventory_enabled を自動更新する
        from jobs.tasks import _resolve_and_build_engine
        engine = _resolve_and_build_engine(repo, end_date=end_date)
        has_inventory = engine.has_inventory
        # 商品数に応じて直列/並列を自動選択（本番15万SKUは並列に）
        engine.run_pipeline_auto()

        products = repo.list_products()
        places = repo.list_places()

        # 冪等化: 当該算定日の result_* を消してから書き直す（設計書 6.4）
        repo.clear_for_calc_date(calc_date)

        # 1) ABC-XYZ を永続化
        abc_items = []
        for s in engine.segments.values():
            name = next((p["name"] for p in engine.products if p["id"] == s.product_id), None)
            abc_items.append({
                "product_id": s.product_id, "abc": s.abc_class, "xyz": s.xyz_class,
                "sales_amount": s.sales_amount, "cv": s.cv,
            })
        repo.save_abc_xyz(calc_date, abc_items)

        # 2) 予測を永続化
        fc_items = []
        for (pid, plid), fc in engine.forecasts.items():
            for i, d in enumerate(fc.p50):
                fc_items.append({
                    "product_id": pid, "place_id": plid,
                    "target_date": _target_date(calc_date, i),
                    "p50": d, "p80": fc.p80[i], "p95": fc.p95[i],
                    "model_name": fc.model_name,
                })
        repo.save_forecast(calc_date, fc_items)

        # 3) 安全在庫を永続化
        ss_items = [{
            "product_id": k[0], "place_id": k[1],
            "safety_stock": v.safety_stock, "reorder_point": v.reorder_point,
            "target_inventory": v.target_inventory,
            "order_qty": v.order_qty, "avg_demand": v.avg_demand,
            "demand_std": v.demand_std, "lead_time_days": v.lead_time_days,
            "service_level": v.service_level, "mode": v.mode,
        } for k, v in engine.safety_stock.items()]
        repo.save_safety_stock(calc_date, ss_items)

        # 4) 推奨発注を永続化
        rec_items = [{
            "product_id": k[0], "place_id": k[1],
            "forecast_demand": v.forecast_demand, "safety_stock": v.safety_stock,
            "on_hand_qty": v.on_hand_qty, "recommended_qty": v.recommended_qty,
            "status": "pending",
        } for k, v in engine.recommendations.items()]
        repo.save_recommendations(calc_date, rec_items)

        # 5) 異常アラートを永続化（差分で上書き）
        repo.clear_alerts()
        for a in engine.alerts:
            repo.save_alert(a)

        db.commit()
        print(f"[+] persisted results for {calc_date}: "
              f"ABC={len(abc_items)}, FC={len(fc_items)}, SS={len(ss_items)}, "
              f"REC={len(rec_items)}, ALERT={len(engine.alerts)}")
    finally:
        db.close()


def _target_date(calc_date: date, offset: int) -> date:
    import datetime
    return calc_date + datetime.timedelta(days=offset)


if __name__ == "__main__":
    main()
