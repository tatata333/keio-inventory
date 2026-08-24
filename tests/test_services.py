from keio_inventory.domain.services.abc_xyz_service import classify_all, classify_one
from keio_inventory.domain.services.safety_stock_service import compute_safety_stock, _z_for, calibrate_k
from keio_inventory.domain.services.forecast_service import ForecastService
from keio_inventory.domain.services.anomaly_service import AnomalyService
from keio_inventory.domain.services.order_service import OrderService
import math


def test_abc_xyz_classification():
    ids = [1, 2, 3]
    amounts = [6000.0, 3000.0, 1000.0]  # total 10000
    demands = [
        [1.0] * 90,
        [round(10 + (i % 5) * 2, 2) for i in range(90)],
        [10.0 if i % 3 == 0 else 0.0 for i in range(90)],
    ]
    res = classify_all(ids, amounts, demands)
    by_id = {r.product_id: r for r in res}
    assert by_id[1].abc_class == "A" and by_id[1].xyz_class == "X"
    assert by_id[2].abc_class == "B"
    assert by_id[3].abc_class == "C"
    assert by_id[3].xyz_class == "Z"  # highest cv
    assert classify_one(99, [1.0] * 30) == "X"


def test_safety_stock_full_vs_pos_only():
    fc = [5.0, 4.0, 6.0, 5.0, 5.0, 4.0, 6.0]
    full = compute_safety_stock(1, 1, fc, lead_time_days=7, lead_time_std=1.0,
                                has_inventory=True, historical_demands=fc, on_hand_qty=10.0)
    assert full.mode == "full"
    assert full.safety_stock > 0
    assert math.isclose(full.reorder_point, full.avg_demand * 7 + full.safety_stock, rel_tol=1e-6)
    # 適正在庫量（目標在庫水準）= 発注点(ROP) = リードタイム需要 + 安全在庫
    assert full.target_inventory == full.reorder_point
    pos = compute_safety_stock(1, 1, fc, lead_time_days=7, lead_time_std=1.0,
                               has_inventory=False, historical_demands=fc)
    assert pos.mode == "pos_only"
    assert 1.64 < _z_for(0.95) < 1.65
    assert 0.95 < calibrate_k(0.05, 0.95) < 1.05


def test_forecast_produces_quantiles():
    svc = ForecastService()
    hist = [5.0 + (i % 7) * 0.5 for i in range(90)]
    fc = svc.forecast(1, 1, hist, horizon=10, segment="AX")
    assert len(fc.p50) == len(fc.p80) == len(fc.p95) == 10
    assert all(a <= b for a, b in zip(fc.p50, fc.p80))
    assert all(b <= c for b, c in zip(fc.p80, fc.p95))


def test_anomaly_detection():
    svc = AnomalyService()
    base = [5.0] * 50
    assert svc.run(1, 1, base, on_hand=5.0, segment="BX") == []
    spiky = [5.0] * 28 + [25.0] * 7
    assert any(a.anomaly_type == "demand_spike" for a in svc.run(1, 1, spiky, segment="AX"))
    assert any(a.anomaly_type == "slow_mover"
               for a in svc.run(1, 1, base, annual_turnover=0.3, segment="CZ"))


def test_order_workflow():
    svc = OrderService()
    rec = svc.recommend(1, 1, forecast_demand=35.0, safety_stock=20.0, on_hand_qty=10.0)
    assert rec.recommended_qty == 35.0 + 20.0 - 10.0
    assert rec.status == "pending"
    adjusted = svc.adjust(rec, 30.0)
    assert adjusted.status == "adjusted" and adjusted.recommended_qty == 30.0
    assert svc.approve(adjusted).status == "approved"
    assert svc.reject(adjusted).status == "rejected"
