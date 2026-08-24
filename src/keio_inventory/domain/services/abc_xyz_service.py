from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class AbcXyzResult:
    product_id: int
    abc_class: str  # 'A' | 'B' | 'C'
    xyz_class: str  # 'X' | 'Y' | 'Z'
    sales_amount: float
    cv: float  # coefficient of variation
    segment: str  # e.g. 'AX'


# Default thresholds (adjustable via settings)
ABC_RATIO_A = 0.80
ABC_RATIO_B = 0.95
XYZ_CV_X = 0.5   # CV < 0.5  -> X (stable)
XYZ_CV_Y = 1.0   # 0.5 <= CV < 1.0 -> Y
# CV >= 1.0 -> Z


def _abc_class(sales_amount: float, total: float) -> str:
    ratio = (sales_amount / total) if total > 0 else 0.0
    # Note: cumulative ranking handled by caller; here ratio is the item's
    # individual share. We classify by cumulative composition in classify_all.
    return ""


def classify_all(
    product_ids: list[int],
    sales_amounts: list[float],
    daily_demands: list[list[float]],
    abc_ratio_a: float = ABC_RATIO_A,
    abc_ratio_b: float = ABC_RATIO_B,
    xyz_cv_x: float = XYZ_CV_X,
    xyz_cv_y: float = XYZ_CV_Y,
) -> list[AbcXyzResult]:
    """ABC-XYZ classify many products at once.

    parameters
    ----------
    product_ids     : id per product (parallel to sales_amounts)
    sales_amounts   : total sales amount per product over the window
    daily_demands   : list of daily demand series per product
    """
    n = len(product_ids)
    total = float(sum(sales_amounts)) or 1.0

    # Rank by sales amount descending -> cumulative ratio
    order = sorted(range(n), key=lambda i: sales_amounts[i], reverse=True)
    cum = 0.0
    abc_for_idx: dict[int, str] = {}
    for rank in order:
        cum += float(sales_amounts[rank])
        r = cum / total
        if r <= abc_ratio_a:
            abc_for_idx[rank] = "A"
        elif r <= abc_ratio_b:
            abc_for_idx[rank] = "B"
        else:
            abc_for_idx[rank] = "C"

    results: list[AbcXyzResult] = []
    for i in range(n):
        abc_class = abc_for_idx[i]
        # XYZ from coefficient of variation of daily demand
        demand = daily_demands[i]
        cv = _coef_of_variation(demand)
        xyz_class = "Z"
        if cv < xyz_cv_x:
            xyz_class = "X"
        elif cv < xyz_cv_y:
            xyz_class = "Y"
        results.append(
            AbcXyzResult(
                product_id=product_ids[i],
                abc_class=abc_class,
                xyz_class=xyz_class,
                sales_amount=float(sales_amounts[i]),
                cv=cv,
                segment=abc_class + xyz_class,
            )
        )
    return results


def classify_one(product_id: int, daily_demands: list[float]) -> str:
    """XYZ class for a single product (used where ABC is externally given)."""
    cv = _coef_of_variation(daily_demands)
    if cv < XYZ_CV_X:
        return "X"
    if cv < XYZ_CV_Y:
        return "Y"
    return "Z"


def _coef_of_variation(values: list[float]) -> float:
    # 需要が無い/常に0の商品は CV が算出できないため、
    # 大きな有限値（= 不安定 Z 扱い）にして JSON で inf/nan を避ける。
    if not values:
        return 9999.0
    mean = sum(values) / len(values)
    if mean == 0:
        return 9999.0
    var = sum((v - mean) ** 2 for v in values) / len(values)
    return (var ** 0.5) / abs(mean)
