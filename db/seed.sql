-- シードデータ投入
-- 注: 商品マスタ・店舗・商品グループは demo_catalog（単一 source of truth）から
--     db/apply_schema.py が生成・投入する（AUTO INCREMENT の採番乱れ防止のため明示 id）。
--     ここには商品マスタを書かない。商品追加は demo_catalog.CATALOG に1行足すだけでよい。

-- パラメータ初期値
INSERT INTO m_demand_forecast_param (param_key, value, description) VALUES
  ('service_level', '{"value": 0.95}', '目標サービスレベル'),
  ('abc_ratio_a',   '{"value": 0.80}', 'ABC分類 A閾値'),
  ('abc_ratio_b',   '{"value": 0.95}', 'ABC分類 B閾値'),
  ('xyz_cv_x',      '{"value": 0.5}',  'XYZ分類 X境界CV'),
  ('xyz_cv_y',      '{"value": 1.0}',  'XYZ分類 Y境界CV'),
  ('inventory_enabled', '{"value": false}', '在庫データモード切替 (pos_only=true / full=false)'),
  ('slow_mover.turnover_threshold', '{"value": 1.0}', '滞留検知 回転率閾値'),
  ('slow_mover.days_threshold',     '{"value": 180}', '滞留検知 日数閾値'),
  ('demand_spike.ratio', '{"value": 2.5}', '需要急上昇 倍率閾値'),
  ('demand_drop.ratio',  '{"value": 0.4}', '需要急落 倍率閾値')
ON CONFLICT (param_key) DO NOTHING;

-- サンプル需要予測結果（冪等化: 既存行は無視）
INSERT INTO result_forecast (product_id, place_id, forecast_date, target_date, forecast_p50, forecast_p80, forecast_p95, model_name)
SELECT p.id, pl.id, CURRENT_DATE, CURRENT_DATE + gs, 10.0, 13.0, 16.0, 'ewma_seasonal'
FROM product p CROSS JOIN place pl
CROSS JOIN generate_series(0, 13) AS gs
ON CONFLICT (product_id, place_id, forecast_date, target_date) DO NOTHING;
