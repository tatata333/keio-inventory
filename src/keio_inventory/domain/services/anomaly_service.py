from __future__ import annotations

from dataclasses import dataclass

import numpy as np


@dataclass(frozen=True)
class AnomalyResult:
    product_id: int
    place_id: int
    anomaly_type: str   # slow_mover | demand_spike | demand_drop | abnormal_turnover
    severity: str       # low | medium | high | critical
    detail: dict
    recommended_action: str


def _mad_based_bounds(values: list[float], k: float = 3.0):
    arr = np.asarray(values, dtype=float)
    if arr.size == 0:
        return 0.0, 0.0
    median = float(np.median(arr))
    mad = float(np.median(np.abs(arr - median))) or 1.0
    return median - k * 1.4826 * mad, median + k * 1.4826 * mad


def detect_demand_spike(recent_7d: float, baseline_28d: float, threshold: float = 2.5) -> bool:
    if baseline_28d <= 0:
        return recent_7d > 0
    return recent_7d / baseline_28d >= threshold


def detect_demand_drop(recent_7d: float, baseline_28d: float, threshold: float = 0.4) -> bool:
    if baseline_28d <= 0:
        return recent_7d <= 0 and baseline_28d <= 0 and recent_7d < 0
    return recent_7d / baseline_28d <= threshold


class AnomalyService:
    """Anomaly detection over in-memory daily series (design 4.4)."""

    def __init__(
        self,
        spike_ratio: float = 2.5,
        drop_ratio: float = 0.4,
        slow_turnover_threshold: float = 1.0,   # annual inventory turns
        slow_days_threshold: float = 180.0,     # days on hand
        abnormal_mad_k: float = 3.0,
    ):
        self.spike_ratio = spike_ratio
        self.drop_ratio = drop_ratio
        self.slow_turnover_threshold = slow_turnover_threshold
        self.slow_days_threshold = slow_days_threshold
        self.abnormal_mad_k = abnormal_mad_k

    def run(
        self,
        product_id: int,
        place_id: int,
        daily_demand: list[float],
        on_hand: float | None = None,
        annual_turnover: float | None = None,
        days_on_hand: float | None = None,
        segment: str = "BX",
    ) -> list[AnomalyResult]:
        out: list[AnomalyResult] = []
        arr = np.asarray(daily_demand, dtype=float)

        # --- demand spike / drop (need at least 28 points) ---
        if arr.size >= 28:
            recent = float(arr[-7:].mean())
            baseline = float(arr[-28:].mean()) if arr.size >= 28 else recent
            if detect_demand_spike(recent, baseline, self.spike_ratio):
                sev = "critical" if segment[0] == "A" else "medium"
                out.append(AnomalyResult(
                    product_id, place_id, "demand_spike", sev,
                    {"recent_7d": round(recent, 2), "baseline_28d": round(baseline, 2)},
                    "追加発注・在庫確保",
                ))
            if detect_demand_drop(recent, baseline, self.drop_ratio):
                out.append(AnomalyResult(
                    product_id, place_id, "demand_drop", "medium",
                    {"recent_7d": round(recent, 2), "baseline_28d": round(baseline, 2)},
                    "需要原因調査・販促見直し",
                ))

        # --- slow mover (needs inventory info) ---
        if annual_turnover is not None and annual_turnover < self.slow_turnover_threshold:
            sev = "high" if segment == "CZ" else "medium"
            out.append(AnomalyResult(
                product_id, place_id, "slow_mover", sev,
                {"annual_turnover": annual_turnover, "days_on_hand": days_on_hand},
                "撤退検討・限定販売" if segment == "CZ" else "在庫削減・仕入停止・値引き",
            ))
        elif days_on_hand is not None and days_on_hand > self.slow_days_threshold:
            sev = "medium"
            out.append(AnomalyResult(
                product_id, place_id, "slow_mover", sev,
                {"days_on_hand": days_on_hand},
                "在庫削減・仕入停止・値引き",
            ))

        # --- abnormal turnover (MAD robust bounds) ---
        if on_hand is not None and arr.size >= 14:
            lo, hi = _mad_based_bounds([float(x) for x in arr], self.abnormal_mad_k)
            if on_hand < lo or on_hand > hi:
                out.append(AnomalyResult(
                    product_id, place_id, "abnormal_turnover", "low",
                    {"on_hand": on_hand, "lower": round(lo, 2), "upper": round(hi, 2)},
                    "棚卸確認・運用点検",
                ))
        return out
