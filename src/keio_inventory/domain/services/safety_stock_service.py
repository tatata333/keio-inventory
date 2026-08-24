from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np


# Standard normal quantile table (z = Phi^-1(SL))
_SERVICE_LEVEL_Z: dict[float, float] = {
    0.90: 1.2816,
    0.95: 1.6449,
    0.975: 1.9600,
    0.990: 2.3263,
}


def _z_for(service_level: float) -> float:
    """Return standard-normal z for a given service level (interpolates)."""
    levels = sorted(_SERVICE_LEVEL_Z)
    if service_level <= levels[0]:
        return _SERVICE_LEVEL_Z[levels[0]]
    if service_level >= levels[-1]:
        return _SERVICE_LEVEL_Z[levels[-1]]
    # linear interpolation between table entries
    lower, upper = None, None
    for lv in levels:
        if lv >= service_level:
            upper = lv
            break
        lower = lv
    zl = _SERVICE_LEVEL_Z[lower]  # type: ignore[arg-type]
    zu = _SERVICE_LEVEL_Z[upper]  # type: ignore[arg-type]
    t = (service_level - lower) / (upper - lower)  # type: ignore[operator]
    return zl + t * (zu - zl)


@dataclass(frozen=True)
class SafetyStockResult:
    product_id: int
    place_id: int
    mode: str          # 'pos_only' | 'full'
    avg_demand: float  # per-day mean demand
    demand_std: float  # per-day demand std dev
    lead_time_days: float
    lead_time_std: float
    service_level: float
    z: float
    safety_stock: float
    reorder_point: float      # ROP = demand*LT + SS
    order_qty: float          # recommended order quantity
    target_inventory: float   # 適正在庫量（目標在庫水準）= demand*LT + SS = reorder_point と同値


def compute_safety_stock(
    product_id: int,
    place_id: int,
    daily_forecasts: list[float],   # daily demand forecast (mean) series over horizon
    lead_time_days: float,
    lead_time_std: float = 1.0,
    service_level: float = 0.95,
    has_inventory: bool = False,    # False -> pos_only mode
    historical_demands: list[float] | None = None,  # for demand std (pos_only)
    on_hand_qty: float | None = None,
    calibration_k: float = 1.0,
) -> SafetyStockResult:
    """Dynamic safety stock.

    full  mode : SS = z * sqrt(LT*sigma_d^2 + d^2*sigma_LT^2)
    pos_only   : inventory not yet available -> SS estimated from POS demand
                 distribution; reorder point & order qty use on_hand=0 default.

    order_qty = max(0, forecastLeadTime + SS - onHand)

    target_inventory(適正在庫量) = reorder_point = demand*LT + SS:
    在庫をこの水準で維持すべき目標在庫水準（発注点と同値）。
    """
    # Mean daily demand from the forecast series (over one lead-time horizon)
    horizon = len(daily_forecasts) or 1
    d = float(np.mean(daily_forecasts)) if daily_forecasts else 0.0
    sigma_d = float(np.std(daily_forecasts)) if len(daily_forecasts) > 1 else 0.0

    z = _z_for(service_level)

    if has_inventory:
        # full mode
        lt = lead_time_days
        lt_std = lead_time_std
        ss = z * math.sqrt(lt * sigma_d**2 + d**2 * lt_std**2)
        mode = "full"
    else:
        # pos_only mode: inventory data not accumulated yet.
        # Estimate demand std from historical POS demand if provided.
        if historical_demands and len(historical_demands) > 1:
            sigma_d = max(float(np.std(historical_demands)), sigma_d)
        lt = lead_time_days
        lt_std = lead_time_std  # default CV applied
        cv_lt = (lt_std / lt) if lt > 0 else 0.0
        # SS = z * sqrt(LT*sigma_d^2 + (d*LT*CV_lt)^2)
        ss = z * math.sqrt(lt * sigma_d**2 + (d * lt * cv_lt) ** 2)
        mode = "pos_only"

    ss *= calibration_k
    rop = d * lt + ss
    # 適正在庫量（目標在庫水準）: 平均リードタイム需要 + 安全在庫 = 発注点(ROP) と同値
    target_inventory = rop

    on_hand = on_hand_qty if (has_inventory and on_hand_qty is not None) else 0.0
    forecast_lead_time = d * lt
    order_qty = max(0.0, forecast_lead_time + ss - on_hand)

    return SafetyStockResult(
        product_id=product_id,
        place_id=place_id,
        mode=mode,
        avg_demand=d,
        demand_std=sigma_d,
        lead_time_days=lt,
        lead_time_std=lt_std,
        service_level=service_level,
        z=z,
        safety_stock=ss,
        reorder_point=rop,
        order_qty=order_qty,
        target_inventory=target_inventory,
    )


def calibrate_k(actual_stockout_rate: float, target_service_level: float) -> float:
    """Capability correction factor.

    z_target = Phi^-1(target_service_level)
    z_actual = Phi^-1(1 - actual_stockout_rate)
    k = z_target / z_actual   (clamped to a sane band)
    """
    if actual_stockout_rate >= 1.0:
        return 2.0
    z_target = _z_for(target_service_level)
    z_actual = _z_for(1 - actual_stockout_rate)
    if z_actual <= 0:
        return 2.0
    k = z_target / z_actual
    return min(max(k, 0.5), 2.0)
