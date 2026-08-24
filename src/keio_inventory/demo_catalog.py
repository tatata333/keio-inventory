"""
デモ用・商品カタログ（単一 source of truth）。

商品を追加するときは、このモジュールの CATALOG リストに1行（dict）追加するだけで、
以下の全てがカタログから導出されて追従する。

  - demo_data.build_products_and_history()  …… エンジン用demoデータ（need/価格/リードタイム）
  - seed_inventory の POS/在庫/仕入生成         …… DB投入用の需要・在庫ベース
  - db/seed.sql の product マスタ INSERT       …… apply_schema がこのカタログから生成する

商品整列は CATALOG の並び順。id は明示指定（AUTO INCREMENT の採番乱れ防止）。
"""
from __future__ import annotations

from typing import Literal

Scenarios = Literal["", "demand_spike", "late_drop", "late_mild_drop"]

# ---------------------------------------------------------------------------
# 商品カタログ（追加はこのリストに1行足すだけ）
# ---------------------------------------------------------------------------
# フィールド:
#   id              : 商品ID（明示指定・他と重複不可）
#   name            : 商品名
#   segment_target  : ABC-XYZ 分類の目標（AX / AY / AZ / BX / BY / BZ / CX / CY / CZ）
#   category        : カテゴリ文字列（表示・ABC分類除外制御用）
#   group_id        : product_group.id（DBマスタ用。1=ヘアケア 2=ボディケア 3=ホーム 4=ビューティ 5=フード）
#   sku_code        : SKUコード
#   supplier_code   : 仕入先コード
#   mds             : MD区分
#   price           : 売価（POS売上算定用）
#   lead_time_days  : リードタイム（日）
#   lead_time_std   : リードタイム標準偏差（供給変動）
#   base_demand     : 日次需要ベース（POS/需要系列の基準値）
#   onhand_base     : 在庫ベース（在庫系列の基準値）
#   scenario        : デモ需要シナリオ（""=通常 / demand_spike=需要急上昇 / late_drop=後半急落 / late_mild_drop=後半ゆるやか低下）
#   season_amp      : 季節変動の振幅（need系列生成用）
#   noise           : ノイズ係数
#   trend           : トレンド係数
#   zero_ratio      : 需要ゼロ日割合（間欠性 → CV上昇で Z 分類）

CATALOG: list[dict] = [
    dict(id=1,  name="高級シャンプーA",  segment_target="AX", category="ヘアケア",   group_id=1, sku_code="SKU-0001", supplier_code="SUP-A01", mds="MD-K",
         price=900,  lead_time_days=7,  lead_time_std=1, base_demand=5.0,  onhand_base=40, scenario="",
         season_amp=0.10, noise=0.15, trend=0.05, zero_ratio=0.00),
    dict(id=2,  name="ボディソープB",    segment_target="AY", category="ボディケア", group_id=2, sku_code="SKU-0002", supplier_code="SUP-B02", mds="MD-K",
         price=600,  lead_time_days=5,  lead_time_std=1, base_demand=8.0,  onhand_base=50, scenario="",
         season_amp=0.12, noise=0.22, trend=0.02, zero_ratio=0.00),
    dict(id=3,  name="歯磨き粉C",        segment_target="BX", category="ヘルスケア", group_id=1, sku_code="SKU-0003", supplier_code="SUP-C03", mds="MD-H",
         price=400,  lead_time_days=7,  lead_time_std=1, base_demand=9.0,  onhand_base=60, scenario="",
         season_amp=0.05, noise=0.12, trend=0.01, zero_ratio=0.00),
    dict(id=4,  name="洗剤D",            segment_target="BY", category="ホーム",     group_id=3, sku_code="SKU-0004", supplier_code="SUP-D04", mds="MD-H",
         price=420,  lead_time_days=6,  lead_time_std=2, base_demand=6.0,  onhand_base=30, scenario="late_mild_drop",
         season_amp=0.15, noise=0.25, trend=0.00, zero_ratio=0.15),
    dict(id=5,  name="ティッシュE",      segment_target="CX", category="ホーム",     group_id=3, sku_code="SKU-0005", supplier_code="SUP-E05", mds="MD-H",
         price=300,  lead_time_days=10, lead_time_std=2, base_demand=10.0, onhand_base=80, scenario="",
         season_amp=0.04, noise=0.10, trend=0.00, zero_ratio=0.00),
    dict(id=6,  name="お菓子F",          segment_target="CY", category="フード",     group_id=5, sku_code="SKU-0006", supplier_code="SUP-F06", mds="MD-F",
         price=250,  lead_time_days=4,  lead_time_std=1, base_demand=4.0,  onhand_base=60, scenario="late_mild_drop",
         season_amp=0.20, noise=0.35, trend=0.00, zero_ratio=0.15),
    dict(id=7,  name="限定コスメG",      segment_target="AZ", category="ビューティ", group_id=4, sku_code="SKU-0007", supplier_code="SUP-G07", mds="MD-B",
         price=1500, lead_time_days=14, lead_time_std=3, base_demand=1.0,  onhand_base=15, scenario="demand_spike",
         season_amp=0.30, noise=0.60, trend=0.10, zero_ratio=0.35),
    dict(id=8,  name="特売品H",          segment_target="CZ", category="フード",     group_id=5, sku_code="SKU-0008", supplier_code="SUP-H08", mds="MD-F",
         price=500,  lead_time_days=12, lead_time_std=3, base_demand=2.0,  onhand_base=25, scenario="late_mild_drop",
         season_amp=0.35, noise=0.70, trend=0.00, zero_ratio=0.45),
    dict(id=9,  name="マウスウォッシュI", segment_target="AX", category="ヘルスケア", group_id=1, sku_code="SKU-0009", supplier_code="SUP-I09", mds="MD-H",
         price=450,  lead_time_days=7,  lead_time_std=1, base_demand=7.0,  onhand_base=55, scenario="",
         season_amp=0.06, noise=0.12, trend=0.02, zero_ratio=0.00),
    dict(id=10, name="サプリJ",          segment_target="BZ", category="ヘルスケア", group_id=1, sku_code="SKU-0010", supplier_code="SUP-J10", mds="MD-H",
         price=1200, lead_time_days=9,  lead_time_std=2, base_demand=3.0,  onhand_base=25, scenario="",
         season_amp=0.18, noise=0.30, trend=0.00, zero_ratio=0.20),
    dict(id=11, name="湿布K",            segment_target="CZ", category="ヘルスケア", group_id=1, sku_code="SKU-0011", supplier_code="SUP-K11", mds="MD-H",
         price=700,  lead_time_days=8,  lead_time_std=2, base_demand=2.0,  onhand_base=30, scenario="late_drop",
         season_amp=0.30, noise=0.50, trend=0.00, zero_ratio=0.40),
    dict(id=12, name="スタイリング剤L",  segment_target="BY", category="ヘアケア",   group_id=1, sku_code="SKU-0012", supplier_code="SUP-L12", mds="MD-K",
         price=550,  lead_time_days=5,  lead_time_std=1, base_demand=6.0,  onhand_base=45, scenario="",
         season_amp=0.12, noise=0.22, trend=0.00, zero_ratio=0.10),
    dict(id=13, name="カラー剤M",        segment_target="CY", category="ヘアケア",   group_id=1, sku_code="SKU-0013", supplier_code="SUP-M13", mds="MD-K",
         price=1800, lead_time_days=14, lead_time_std=3, base_demand=2.0,  onhand_base=18, scenario="late_drop",
         season_amp=0.28, noise=0.55, trend=0.00, zero_ratio=0.45),
    dict(id=14, name="ハンドソープN",    segment_target="AX", category="ボディケア", group_id=2, sku_code="SKU-0014", supplier_code="SUP-N14", mds="MD-K",
         price=350,  lead_time_days=5,  lead_time_std=1, base_demand=9.0,  onhand_base=70, scenario="",
         season_amp=0.05, noise=0.12, trend=0.01, zero_ratio=0.00),
    dict(id=15, name="ボディミルクO",    segment_target="BX", category="ボディケア", group_id=2, sku_code="SKU-0015", supplier_code="SUP-O15", mds="MD-K",
         price=650,  lead_time_days=7,  lead_time_std=1, base_demand=4.0,  onhand_base=35, scenario="",
         season_amp=0.14, noise=0.20, trend=0.00, zero_ratio=0.05),
    dict(id=16, name="リップP",          segment_target="AX", category="ビューティ", group_id=4, sku_code="SKU-0016", supplier_code="SUP-P16", mds="MD-B",
         price=800,  lead_time_days=6,  lead_time_std=1, base_demand=5.0,  onhand_base=40, scenario="",
         season_amp=0.08, noise=0.15, trend=0.03, zero_ratio=0.00),
    dict(id=17, name="アイシャドウQ",    segment_target="CZ", category="ビューティ", group_id=4, sku_code="SKU-0017", supplier_code="SUP-Q17", mds="MD-B",
         price=1600, lead_time_days=14, lead_time_std=3, base_demand=1.0,  onhand_base=12, scenario="late_drop",
         season_amp=0.32, noise=0.65, trend=0.00, zero_ratio=0.50),
    dict(id=18, name="美容液R",          segment_target="CY", category="ビューティ", group_id=4, sku_code="SKU-0018", supplier_code="SUP-R18", mds="MD-B",
         price=2200, lead_time_days=10, lead_time_std=2, base_demand=1.0,  onhand_base=10, scenario="",
         season_amp=0.30, noise=0.60, trend=0.05, zero_ratio=0.35),
    dict(id=19, name="トイレットS",      segment_target="AX", category="ホーム",     group_id=3, sku_code="SKU-0019", supplier_code="SUP-S19", mds="MD-H",
         price=280,  lead_time_days=7,  lead_time_std=1, base_demand=11.0, onhand_base=90, scenario="",
         season_amp=0.04, noise=0.10, trend=0.00, zero_ratio=0.00),
    dict(id=20, name="洗濯洗剤T",        segment_target="BY", category="ホーム",     group_id=3, sku_code="SKU-0020", supplier_code="SUP-T20", mds="MD-H",
         price=480,  lead_time_days=8,  lead_time_std=2, base_demand=7.0,  onhand_base=55, scenario="",
         season_amp=0.12, noise=0.22, trend=0.00, zero_ratio=0.10),
    dict(id=21, name="芳香剤U",          segment_target="CY", category="ホーム",     group_id=3, sku_code="SKU-0021", supplier_code="SUP-U21", mds="MD-H",
         price=380,  lead_time_days=6,  lead_time_std=1, base_demand=4.0,  onhand_base=30, scenario="",
         season_amp=0.20, noise=0.35, trend=0.00, zero_ratio=0.15),
    dict(id=22, name="スポンジV",        segment_target="CY", category="ホーム",     group_id=3, sku_code="SKU-0022", supplier_code="SUP-V22", mds="MD-H",
         price=200,  lead_time_days=9,  lead_time_std=1, base_demand=3.0,  onhand_base=28, scenario="",
         season_amp=0.22, noise=0.40, trend=0.00, zero_ratio=0.20),
    dict(id=23, name="即席麺W",          segment_target="BX", category="フード",     group_id=5, sku_code="SKU-0023", supplier_code="SUP-W23", mds="MD-F",
         price=180,  lead_time_days=12, lead_time_std=2, base_demand=8.0,  onhand_base=60, scenario="",
         season_amp=0.10, noise=0.18, trend=0.00, zero_ratio=0.05),
    dict(id=24, name="缶詰X",            segment_target="CY", category="フード",     group_id=5, sku_code="SKU-0024", supplier_code="SUP-X24", mds="MD-F",
         price=260,  lead_time_days=15, lead_time_std=3, base_demand=2.0,  onhand_base=22, scenario="late_drop",
         season_amp=0.28, noise=0.50, trend=0.00, zero_ratio=0.40),
    dict(id=25, name="ハンドクリームY",  segment_target="BZ", category="ボディケア", group_id=2, sku_code="SKU-0025", supplier_code="SUP-Y25", mds="MD-K",
         price=900,  lead_time_days=9,  lead_time_std=2, base_demand=4.0,  onhand_base=35, scenario="",
         season_amp=0.16, noise=0.30, trend=0.00, zero_ratio=0.10),
]

# 店舗カタログ
PLACES: list[dict] = [
    dict(id=1, code="ST-01", name="新宿本店", place_type="store"),
    dict(id=2, code="ST-02", name="調布店",   place_type="store"),
    dict(id=3, code="ST-03", name="武蔵境店", place_type="store"),
]

# 商品カテゴリ（product_group）マスタ
CATEGORIES: list[dict] = [
    dict(code="CAT-HB", name="ヘアケア",   level="category"),
    dict(code="CAT-BD", name="ボディケア", level="category"),
    dict(code="CAT-HM", name="ホーム",     level="category"),
    dict(code="CAT-BT", name="ビューティ", level="category"),
    dict(code="CAT-FD", name="フード",     level="category"),
]

# 需要スクenario の遅減倍率（後半 n*0.7 以降に乗じる係数）
LATE_DROP_FACTORS: dict[str, float] = {
    "late_mild_drop": 0.15,
    "late_drop": 0.10,
}


def get_product(pid: int) -> dict:
    """商品IDからカタログ辞書を返す（無ければ既定オブジェクト）。"""
    for p in CATALOG:
        if p["id"] == pid:
            return p
    return dict(id=pid, name=f"商品{pid}", segment_target="BX", category="", group_id=1,
                sku_code=f"SKU-{pid:04d}", supplier_code=f"SUP-{pid:04d}", mds="MD-H",
                price=400, lead_time_days=8, lead_time_std=1,
                base_demand=2 + (pid % 6), onhand_base=20 + (pid % 10), scenario="",
                season_amp=0.10, noise=0.20, trend=0.0, zero_ratio=0.0)


def product_ids() -> list[int]:
    return [p["id"] for p in CATALOG]


def sql_product_inserts() -> str:
    """商品マスタの INSERT VALUES 文（seed.sql 用）を生成する。"""
    lines = []
    for p in CATALOG:
        e = lambda v: str(v).replace('"', '""')  # SQL 文字列エスケープ
        lines.append(
            f"  (\'{p['sku_code']}\', \'{p['name']}\', {p['group_id']}, \'{p['mds']}\', "
            f"\'{p['category']}\', \'{p['supplier_code']}\', {p['lead_time_days']}, {p['lead_time_std']})"
        )
    return "\n".join(lines)
