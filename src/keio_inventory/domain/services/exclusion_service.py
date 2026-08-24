"""販売不振商品の特定・評価（総合スコア）ロジック。

募集要項 目的①「販売不振商品の特定と排除」への対応。
商品ごとに「不振スコア(0-100)」を複合指標から算出し、
  健全 / 要注意 / 撤退候補 に分類してリスト提示する。
"""
from __future__ import annotations

from dataclasses import asdict, dataclass


@dataclass(frozen=True)
class ExclusionResult:
    product_id: int
    name: str
    abcx: str            # ABC-XYZ セグメント（例: CZ）
    sales_count: float
    sales_amount: float
    on_hand: float
    turnover: float      # 在庫回転率（年間近似）
    score: float         # 不振スコア 0-100（高いほど不振）
    risk: str            # 健全 / 要注意 / 撤退候補
    reasons: list[str]   # 不振の理由
    place_id: int | None = None   # 店舗ID（店舗別表示用）
    place_name: str = ""          # 店舗名


# 分類しきい値
RISK_GREEN = 30.0     # これ未満: 健全
RISK_YELLOW = 55.0    # これ以上: 撤退候補
SCORE_UPPER = 100.0


def compute_slow_mover_score(row: dict, with_abc: bool = True) -> ExclusionResult:
    """1商品の不振スコアを算出。

    row は repository.exclusion_product_data が返す項目 + abcx / season_demand を含む想定。
    """
    name = row.get("name", "")
    abcx = row.get("abcx", "")
    sales_count = float(row.get("sales_count", 0.0))
    sales_amount = float(row.get("sales_amount", 0.0))
    on_hand = float(row.get("on_hand", 0.0))
    turnover = float(row.get("turnover", 0.0))

    score = 0.0
    reasons = []

    # 1) 販売低迷（売上数量）
    if sales_count <= 0:
        score += 40
        reasons.append("販売ゼロ")
    elif sales_count < row.get("threshold_low", 10.0):
        score += 20
        reasons.append("販売が少ない")

    # 2) ABC ランク（売上小）
    if with_abc:
        if abcx.startswith("C"):
            score += 15
            reasons.append("売上小(C)")
        elif abcx.startswith("B"):
            score += 5

    # 3) XYZ 不安定
    if with_abc and abcx.endswith("Z"):
        score += 15
        reasons.append("需要不安定(Z)")

    # 4) 在庫回転率の低さ（在庫が滞留）
    if on_hand > 0 and turnover < row.get("threshold_turnover", 2.0):
        score += 15
        reasons.append("在庫回転が低い(滞留)")

    # 5) 滞留（在庫があるのに売れていない）
    if on_hand > 0 and sales_count <= 0:
        score += 15
        reasons.append("在庫滞留(売れず)")

    score = min(score, SCORE_UPPER)
    risk = "撤退候補" if score >= RISK_YELLOW else ("要注意" if score >= RISK_GREEN else "健全")
    return ExclusionResult(
        product_id=row.get("product_id"),
        name=name,
        place_id=row.get("place_id"),
        place_name=row.get("place_name", ""),
        abcx=abcx,
        sales_count=sales_count, sales_amount=sales_amount,
        on_hand=on_hand, turnover=turnover,
        score=round(score, 1), risk=risk, reasons=reasons,
    )
