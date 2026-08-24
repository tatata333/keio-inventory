from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class OrderRecommendation:
    product_id: int
    place_id: int
    forecast_demand: float   # forecast demand over reorder/allowed horizon
    safety_stock: float
    on_hand_qty: float
    recommended_qty: float   # max(0, forecast + SS - on_hand)
    status: str              # pending | adjusted | approved | rejected


class OrderService:
    """Recommended order quantity (design 4.5)."""

    def recommend(
        self,
        product_id: int,
        place_id: int,
        forecast_demand: float,
        safety_stock: float,
        on_hand_qty: float,
    ) -> OrderRecommendation:
        recommended = max(0.0, forecast_demand + safety_stock - on_hand_qty)
        return OrderRecommendation(
            product_id=product_id,
            place_id=place_id,
            forecast_demand=forecast_demand,
            safety_stock=safety_stock,
            on_hand_qty=on_hand_qty,
            recommended_qty=recommended,
            status="pending",
        )

    def adjust(self, rec: OrderRecommendation, new_qty: float) -> OrderRecommendation:
        if new_qty < 0:
            raise ValueError("recommended_qty must be >= 0")
        return OrderRecommendation(**{**rec.__dict__, "recommended_qty": new_qty, "status": "adjusted"})

    def approve(self, rec: OrderRecommendation) -> OrderRecommendation:
        return OrderRecommendation(**{**rec.__dict__, "status": "approved"})

    def reject(self, rec: OrderRecommendation) -> OrderRecommendation:
        return OrderRecommendation(**{**rec.__dict__, "status": "rejected"})
