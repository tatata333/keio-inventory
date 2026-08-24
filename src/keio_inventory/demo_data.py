# Generate deterministic dummy data: products, stores, daily POS demand
# 商品定義は demo_catalog.CATALOG を単一 source of truth とする。
import numpy as np

from keio_inventory.demo_catalog import CATALOG, PLACES

NP = 8            # 旧デモ商品数（互換用。実データは CATALOG 全件）
N_DAYS = 180      # days of history


def _demand_series(seed: int, base: float, season_amp: float, noise: float, trend: float,
                 dow_amp: float = 0.4, zero_ratio: float = 0.0):
    rng = np.random.default_rng(seed)
    n = N_DAYS
    dow = np.array([0.9, 0.8, 0.8, 0.9, 1.0, 1.2, 1.3])  # weekend uplift
    t = np.arange(n)
    season = 1 + season_amp * np.sin(2 * np.pi * t / 30.4)   # monthly-ish
    trendline = 1 + trend * t / n
    demand = base * dow[t % 7] * season * trendline * (1 + noise * rng.normal(size=n))
    # intermittent demand: force some days to zero (raises CV -> Z class)
    if zero_ratio > 0:
        mask = rng.random(size=n) < zero_ratio
        demand = np.where(mask, 0.0, demand)
    return np.maximum(0.0, np.round(demand, 2))


def build_products_and_history():
    """商品カタログ(CATALOG)から商品・需要系列・売上金額を構築する。

    商品追加は demo_catalog.CATALOG に1行足すだけで自動反映される。
    """
    products = []
    history = {}
    amounts = []
    for p in CATALOG:
        pid = p["id"]
        series = _demand_series(pid, p["base_demand"], p["season_amp"], p["noise"],
                                p["trend"], zero_ratio=p["zero_ratio"])
        history[pid] = list(series)
        amounts.append(float(series.sum() * p["price"]))
        products.append({
            "id": pid, "name": p["name"], "segment_target": p["segment_target"],
            "lead_time_days": float(p["lead_time_days"]),
        })
    return products, history, amounts
