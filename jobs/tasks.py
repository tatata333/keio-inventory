"""Airflow タスク用の分割済みバッチ処理。

設計書 06_batch の DAG 定義( daily_forecast / daily_order / daily_anomaly /
weekly_segment / weekly_stock )に対応する処理をタスク単位で提供する。

各関数は PythonOperator の callable として使う。
在庫データがあれば full モード、無ければ pos_only モードを DB パラメータから判定。
"""
from __future__ import annotations

from datetime import date, timedelta

from keio_inventory.domain.engine import InventoryEngine
from keio_inventory.infra.db.models import (
    ResultAbcXyz, ResultForecast, ResultOrderRecommendation, ResultSafetyStock,
)
from keio_inventory.infra.db.repository import InventoryRepository
from keio_inventory.infra.db.session import SessionLocal


def _target_date(calc_date: date, offset: int) -> date:
    return calc_date + timedelta(days=offset)


def _calc_date(**context) -> date:
    """DAG 実行論理日付を返す。airflow の logical_date / execution_date に追随する。"""
    raw = context.get("logical_date") or context.get("execution_date") or context.get("ds")
    if raw is None:
        return date.today()
    if isinstance(raw, str):
        return date.fromisoformat(raw[:10])
    return raw.date() if hasattr(raw, "date") else date.today()


def _engine_with_inventory() -> InventoryEngine:
    db = SessionLocal()
    try:
        repo = InventoryRepository(db)
        return _resolve_and_build_engine(repo)
    finally:
        db.close()


# --- 在庫データ蓄積に応じたモード自動判定 (full モード切替の自動化) ---
ACCUM_PAIR_COVERAGE_THRESHOLD = 0.8    # 対象(商品x店舗)の80%以上に在庫データが届いたら
ACCUM_FRESHNESS_DAYS = 7               # 最新在庫データが直近7日以内であること
LEAD_TIME_REQUIRED = True              # full モードは実測リードタイムが望ましい(無ければデフォルト使用)


def _resolve_mode(repo: InventoryRepository) -> tuple[bool, dict[tuple[int, int], float]]:
    """在庫データ蓄積状況から実行モードを自動判定し、必要なら DB パラメータを自動更新する。

    Returns (has_inventory, on_hand_map)
      - 蓄積不足 -> pos_only (inventory_enabled=False, on_hand_map 空)
      - 蓄積十分 -> full (inventory_enabled=True, on_hand_map=実在庫)
    """
    status = repo.inventory_accumulation_status(target_days=14)
    coverage = status["coverage"]
    fresh = status["latest_date"] is not None and (status["days_since_latest"] or 0) <= ACCUM_FRESHNESS_DAYS

    should_full = coverage >= ACCUM_PAIR_COVERAGE_THRESHOLD and fresh
    repo.set_param("inventory_enabled", {"value": should_full},
                   "自動判定: 在庫蓄積率 " + str(round(coverage * 100, 1)) + "%")
    repo.session.commit()

    if not should_full:
        return False, {}
    on_hand_map = repo.latest_on_hand_map()
    return True, on_hand_map


def _resolve_and_build_engine(repo: InventoryRepository, end_date=None) -> InventoryEngine:
    """在庫の蓄積状況を見て full/pos_only を自動判定し、エンジンを構築する。

    full モード時は実在庫(on_hand)に加え、入荷履歴から実測リードタイムも注入する。
    end_date を指定すると、その日以前のPOSのみで計算（時系列バックフィル用）。
    """
    has_inv, on_hand = _resolve_mode(repo)
    lead_time_map: dict[int, float] = {}
    if has_inv:
        # 実測リードタイム(purchase_history)があれば商品ごとに反映
        for p in repo.list_products():
            m = repo.measured_lead_time(p.id)
            if m is not None:
                lead_time_map[p.id] = m
    # DB全商品を読み込んで商品個別に計算する（デモでなく実データ）
    return InventoryEngine(has_inventory=has_inv, on_hand_map=on_hand,
                           lead_time_map=lead_time_map, repo=repo, demo=False,
                           end_date=end_date)


def run_segment(**_context):
    """weekly_segment: ABC-XYZ を計算し result_abc_xyz へ永続化。"""
    cd = _calc_date(**_context)
    db = SessionLocal()
    try:
        repo = InventoryRepository(db)
        engine = _resolve_and_build_engine(repo)
        engine.run_pipeline_auto()
        db.query(ResultAbcXyz).filter(ResultAbcXyz.calc_date == cd).delete()
        items = [
            dict(product_id=s.product_id, abc=s.abc_class, xyz=s.xyz_class,
                 sales_amount=s.sales_amount, cv=s.cv)
            for s in engine.segments.values()
        ]
        repo.save_abc_xyz(cd, items)
        db.commit()
        print(f"[segment] {cd} ABC-XYZ {len(items)} items")
        return len(items)
    finally:
        db.close()


def run_forecast(**_context):
    """daily_forecast: 需要予測を result_forecast へ永続化(冪等: 当日分を消して書直)。"""
    cd = _calc_date(**_context)
    db = SessionLocal()
    try:
        repo = InventoryRepository(db)
        engine = _resolve_and_build_engine(repo)
        engine.run_pipeline_auto()
        db.query(ResultForecast).filter(ResultForecast.forecast_date == cd).delete()
        items = []
        for (pid, plid), fc in engine.forecasts.items():
            for i in range(len(fc.p50)):
                items.append(dict(
                    product_id=pid, place_id=plid, target_date=_target_date(cd, i),
                    p50=fc.p50[i], p80=fc.p80[i], p95=fc.p95[i], model_name=fc.model_name,
                ))
        repo.save_forecast(cd, items)
        db.commit()
        print(f"[forecast] {cd} {len(items)} rows")
        return len(items)
    finally:
        db.close()


def run_safety_stock(**_context):
    """weekly_stock / daily_stock: 安全在庫・発注点を result_safety_stock へ永続化。"""
    cd = _calc_date(**_context)
    db = SessionLocal()
    try:
        repo = InventoryRepository(db)
        engine = _resolve_and_build_engine(repo)
        engine.run_pipeline_auto()
        db.query(ResultSafetyStock).filter(ResultSafetyStock.calc_date == cd).delete()
        items = [
            dict(product_id=k[0], place_id=k[1], safety_stock=v.safety_stock,
                 reorder_point=v.reorder_point, order_qty=v.order_qty,
                 avg_demand=v.avg_demand, demand_std=v.demand_std,
                 lead_time_days=v.lead_time_days, service_level=v.service_level, mode=v.mode)
            for k, v in engine.safety_stock.items()
        ]
        repo.save_safety_stock(cd, items)
        db.commit()
        print(f"[safety_stock] {cd} {len(items)} items")
        return len(items)
    finally:
        db.close()


def run_order_recommendation(**_context):
    """daily_order: 推奨発注量を result_order_recommendation へ永続化。"""
    cd = _calc_date(**_context)
    db = SessionLocal()
    try:
        repo = InventoryRepository(db)
        engine = _resolve_and_build_engine(repo)
        engine.run_pipeline_auto()
        db.query(ResultOrderRecommendation).filter(ResultOrderRecommendation.calc_date == cd).delete()
        items = [
            dict(product_id=k[0], place_id=k[1], forecast_demand=v.forecast_demand,
                 safety_stock=v.safety_stock, on_hand_qty=v.on_hand_qty,
                 recommended_qty=v.recommended_qty, status="pending")
            for k, v in engine.recommendations.items()
        ]
        repo.save_recommendations(cd, items)
        db.commit()
        print(f"[order] {cd} {len(items)} recommendations")
        return len(items)
    finally:
        db.close()


def run_anomaly(**_context):
    """daily_anomaly: 異常検知を anomaly_alert へ永続化(当日分を全上書き)。"""
    cd = _calc_date(**_context)
    db = SessionLocal()
    try:
        repo = InventoryRepository(db)
        engine = _resolve_and_build_engine(repo)
        engine.run_pipeline_auto()
        repo.clear_alerts()
        for a in engine.alerts:
            repo.save_alert(a)
        db.commit()
        print(f"[anomaly] {cd} {len(engine.alerts)} alerts")
        return len(engine.alerts)
    finally:
        db.close()