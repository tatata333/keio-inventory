"""KPIシミュレーションの回帰テスト。

導入後(optimized)が導入前(current)より在庫回転率・欠品率・廃棄ロスで改善することを確認。
"""
from __future__ import annotations

from jobs.kpi_sim import run


def test_kpi_simulation_improves_all_metrics():
    res = run()
    cur, opt = res["current"], res["optimized"]

    # 在庫回転率: 導入後 > 導入前
    assert opt.inventory_turnover > cur.inventory_turnover
    # 欠品率: 導入後 < 導入前
    assert opt.stockout_rate < cur.stockout_rate
    # 廃棄ロス: 導入後 <= 導入前
    assert opt.total_disposal <= cur.total_disposal + 1e-9
    # 販売量: 欠品減少により導入後 >= 導入前
    assert opt.total_sales >= cur.total_sales - 1e-9


def test_kpi_meets_targets():
    """提案KPI(回転率+20%, 欠品率-5%, 廃棄-30%)を満たすことを確認。"""
    res = run()
    cur, opt = res["current"], res["optimized"]

    def pct(a, b):
        return ((a - b) / b * 100) if b else 0.0

    turnover_up = pct(opt.inventory_turnover, cur.inventory_turnover)
    stockout_down = pct(opt.stockout_rate, cur.stockout_rate)
    cd, od = cur.total_disposal, opt.total_disposal
    disposal_down = ((cd - od) / cd * 100) if cd else 0.0

    assert turnover_up >= 20.0, f"在庫回転率改善 {turnover_up:.1f}% < 20%"
    assert stockout_down <= -5.0, f"欠品率改善 {stockout_down:.1f}% > -5%"
    assert disposal_down >= 30.0, f"廃棄ロス改善 {disposal_down:.1f}% < 30%"
