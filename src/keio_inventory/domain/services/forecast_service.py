from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass

import numpy as np


@dataclass(frozen=True)
class ForecastResult:
    product_id: int
    place_id: int
    model_name: str
    target_dates: list[str]
    p50: list[float]
    p80: list[float]
    p95: list[float]


class BaseForecaster(ABC):
    """Forecast engine interface (substitute LightGBM/Prophet in production)."""

    name: str = "base"

    @abstractmethod
    def fit(self, y: np.ndarray): ...

    @abstractmethod
    def predict(self, horizon: int) -> np.ndarray: ...


def _day_of_week_seasonality(series: np.ndarray) -> np.ndarray:
    n = series.size
    if n < 14:
        return np.ones(7)
    dow_means = np.array([
        series[i::7].mean() if (series[i::7].size > 0) else 1.0
        for i in range(7)
    ])
    base = dow_means.mean()
    if base <= 0:
        return np.ones(7)
    return np.minimum(np.maximum(dow_means / base, 0.2), 3.0)


class EwmaForecaster(BaseForecaster):
    """Exponential moving average + day-of-week seasonality + drift.

    Lightweight deterministic baseline that runs with numpy only. It provides
    quantile bands via a heteroscedastic residual model so the API can return
    P50/P80/P95 without heavy ML dependencies.
    """

    name = "ewma_seasonal"

    def __init__(self, alpha: float = 0.15):
        self.alpha = alpha
        self._level = 0.0
        self._dow = np.ones(7)
        self._resid_scale = 0.0
        self._fitted = False

    def fit(self, y: np.ndarray):
        if y.size == 0:
            self._fitted = False
            return self
        self._dow = _day_of_week_seasonality(y)
        idx = np.arange(y.size)
        deseason = y / self._dow[idx % 7]
        deseason = np.where(np.isfinite(deseason), deseason, 0.0)
        level = float(deseason[0])
        for v in deseason[1:]:
            level = self.alpha * v + (1 - self.alpha) * level
        self._level = level
        resid = deseason - level
        self._resid_scale = float(np.percentile(np.abs(resid), 68)) or 1.0
        self._fitted = True
        return self

    def predict(self, horizon: int) -> np.ndarray:
        if not self._fitted:
            return np.zeros(horizon, dtype=float)
        out = np.empty(horizon, dtype=float)
        last_day = 0
        for h in range(horizon):
            out[h] = max(0.0, self._level * self._dow[(last_day + 1 + h) % 7])
        return out

    @property
    def residual_scale(self) -> float:
        return self._resid_scale


def pick_model(segment: str) -> str:
    """Design 4.1.1: choose model family by ABC-XYZ segment."""
    if segment in ("AX", "AY", "BX"):
        return "lightgbm"
    if segment in ("AZ", "CZ"):
        return "prophet"
    if segment in ("CX", "CY"):
        return "ewma"
    return "ewma"


class ForecastService:
    def __init__(self, backend=None):
        self._backend = backend or EwmaForecaster()

    def forecast(
        self,
        product_id: int,
        place_id: int,
        history: list[float],
        horizon: int,
        segment: str = "BX",
        alpha80: float = 1.282,
        alpha95: float = 1.645,
    ) -> ForecastResult:
        series = np.asarray(history, dtype=float)
        backend = self._backend
        backend.fit(series)
        mean = np.maximum(0.0, backend.predict(horizon))
        sigma = float(getattr(backend, "residual_scale", 0.0))
        band80 = alpha80 * (sigma + 0.05 * mean)
        band95 = alpha95 * (sigma + 0.05 * mean)
        return ForecastResult(
            product_id=product_id,
            place_id=place_id,
            model_name=backend.name,
            target_dates=[],
            p50=[float(x) for x in mean],
            p80=[float(x) for x in np.maximum(0.0, mean + band80)],
            p95=[float(x) for x in np.maximum(0.0, mean + band95)],
        )
