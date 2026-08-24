from __future__ import annotations

from datetime import date

from sqlalchemy import (
    Integer, Boolean, CheckConstraint, Column, Date, DateTime, ForeignKey,
    JSON, Numeric, String, UniqueConstraint, func, text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import declarative_base, relationship


# SQLAlchemy 1.4 / 2.0 両対応 (Airflow 同梱は 1.4, ホストは 2.0)
Base = declarative_base()


class ProductGroup(Base):
    __tablename__ = "product_group"
    id = Column(Integer, primary_key=True, autoincrement=True)
    group_code = Column(String(64), unique=True, nullable=False)
    group_name = Column(String(255), nullable=False)
    hierarchy_level = Column(String(32))
    created_at = Column(DateTime(timezone=True), server_default=func.now())


class Product(Base):
    __tablename__ = "product"
    id = Column(Integer, primary_key=True, autoincrement=True)
    sku_code = Column(String(64), unique=True, nullable=False)
    name = Column(String(255), nullable=False)
    product_group_id = Column(Integer, ForeignKey("product_group.id"))
    mds = Column(String(64))
    category = Column(String(128))
    supplier_code = Column(String(64))
    lead_time_days = Column(Numeric(8, 2), server_default="7")
    lead_time_std = Column(Numeric(8, 2), server_default="1")
    is_active = Column(Boolean, server_default=text("true"))
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now())
    product_group = relationship("ProductGroup")


class Place(Base):
    __tablename__ = "place"
    id = Column(Integer, primary_key=True, autoincrement=True)
    place_code = Column(String(32), unique=True, nullable=False)
    place_name = Column(String(128), nullable=False)
    place_type = Column(String(16), nullable=False)
    __table_args__ = (CheckConstraint("place_type IN ('store','warehouse')", name="place_place_type_check"),)


class SkuDailySales(Base):
    __tablename__ = "sku_daily_sales"
    sales_date = Column(Date, primary_key=True)
    product_id = Column(Integer, ForeignKey("product.id"), primary_key=True)
    place_id = Column(Integer, ForeignKey("place.id"), primary_key=True)
    qty_sold = Column(Numeric, nullable=False, server_default="0")
    amount = Column(Numeric, nullable=False, server_default="0")


class InventoryDaily(Base):
    __tablename__ = "inventory_daily"
    inventory_date = Column(Date, primary_key=True)
    product_id = Column(Integer, ForeignKey("product.id"), primary_key=True)
    place_id = Column(Integer, ForeignKey("place.id"), primary_key=True)
    on_hand_qty = Column(Numeric, nullable=False, server_default="0")
    allocated_qty = Column(Numeric, nullable=False, server_default="0")
    available_qty = Column(Numeric, nullable=False, server_default="0")


class PurchaseHistory(Base):
    __tablename__ = "purchase_history"
    po_date = Column(Date, primary_key=True)
    product_id = Column(Integer, ForeignKey("product.id"), primary_key=True)
    place_id = Column(Integer, ForeignKey("place.id"), primary_key=True)
    order_qty = Column(Numeric, nullable=False, server_default="0")
    received_qty = Column(Numeric)
    expected_date = Column(Date)


class ResultForecast(Base):
    __tablename__ = "result_forecast"
    id = Column(Integer, primary_key=True, autoincrement=True)
    product_id = Column(Integer, ForeignKey("product.id"), nullable=False)
    place_id = Column(Integer, ForeignKey("place.id"), nullable=False)
    forecast_date = Column(Date, nullable=False, server_default=text("CURRENT_DATE"))
    target_date = Column(Date, nullable=False)
    forecast_p50 = Column(Numeric)
    forecast_p80 = Column(Numeric)
    forecast_p95 = Column(Numeric)
    actual_qty = Column(Numeric)
    model_name = Column(String(128))
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    __table_args__ = (
        UniqueConstraint("product_id", "place_id", "forecast_date", "target_date"),
    )


class ResultSafetyStock(Base):
    __tablename__ = "result_safety_stock"
    id = Column(Integer, primary_key=True, autoincrement=True)
    product_id = Column(Integer, ForeignKey("product.id"), nullable=False)
    place_id = Column(Integer, ForeignKey("place.id"), nullable=False)
    calc_date = Column(Date, nullable=False, server_default=text("CURRENT_DATE"))
    safety_stock = Column(Numeric)
    reorder_point = Column(Numeric)
    target_inventory = Column(Numeric)  # 適正在庫量（目標在庫水準）= 発注点(ROP) と同値
    order_qty = Column(Numeric)
    avg_demand = Column(Numeric)
    demand_std = Column(Numeric)
    lead_time_days = Column(Numeric)
    service_level = Column(Numeric)
    mode = Column(String(16), nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    __table_args__ = (
        CheckConstraint("mode IN ('pos_only','full')", name="result_safety_stock_mode_check"),
        UniqueConstraint("product_id", "place_id", "calc_date"),
    )


class ResultAbcXyz(Base):
    __tablename__ = "result_abc_xyz"
    id = Column(Integer, primary_key=True, autoincrement=True)
    product_id = Column(Integer, ForeignKey("product.id"), nullable=False)
    abc_class = Column(String(1))
    xyz_class = Column(String(1))
    calc_date = Column(Date, nullable=False, server_default=text("CURRENT_DATE"))
    sales_amount = Column(Numeric)
    cv = Column(Numeric)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    __table_args__ = (
        CheckConstraint("abc_class IN ('A','B','C')", name="result_abc_xyz_abc_class_check"),
        CheckConstraint("xyz_class IN ('X','Y','Z')", name="result_abc_xyz_xyz_class_check"),
        UniqueConstraint("product_id", "calc_date"),
    )


class ResultOrderRecommendation(Base):
    __tablename__ = "result_order_recommendation"
    id = Column(Integer, primary_key=True, autoincrement=True)
    product_id = Column(Integer, ForeignKey("product.id"), nullable=False)
    place_id = Column(Integer, ForeignKey("place.id"), nullable=False)
    calc_date = Column(Date, nullable=False, server_default=text("CURRENT_DATE"))
    forecast_demand = Column(Numeric)
    safety_stock = Column(Numeric)
    on_hand_qty = Column(Numeric)
    recommended_qty = Column(Numeric)
    status = Column(String(16), server_default="pending")
    note = Column(String(255), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    __table_args__ = (
        CheckConstraint("status IN ('pending','adjusted','approved','rejected')",
                        name="result_order_recommendation_status_check"),
        UniqueConstraint("product_id", "place_id", "calc_date"),
    )


class AnomalyAlert(Base):
    __tablename__ = "anomaly_alert"
    id = Column(Integer, primary_key=True, autoincrement=True)
    product_id = Column(Integer, ForeignKey("product.id"), nullable=False)
    place_id = Column(Integer, ForeignKey("place.id"), nullable=False)
    anomaly_type = Column(String(32), nullable=False)
    severity = Column(String(16), nullable=False)
    source = Column(String(32), nullable=True)
    detected_at = Column(Date, nullable=False, server_default=text("CURRENT_DATE"))
    status = Column(String(16), server_default="open")
    detail = Column(JSON)
    recommended_action = Column(String(255))
    resolved_at = Column(DateTime(timezone=True))
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    __table_args__ = (
        CheckConstraint("severity IN ('low','medium','high','critical')",
                        name="anomaly_alert_severity_check"),
        CheckConstraint("status IN ('open','ack','done')", name="anomaly_alert_status_check"),
    )


class DemandForecastParam(Base):
    __tablename__ = "m_demand_forecast_param"
    id = Column(Integer, primary_key=True, autoincrement=True)
    param_key = Column(String(128), unique=True, nullable=False)
    value = Column(JSON, nullable=False)
    description = Column(String(255))
    updated_at = Column(DateTime(timezone=True), server_default=func.now())


class SysUser(Base):
    __tablename__ = "sys_user"
    id = Column(Integer, primary_key=True, autoincrement=True)
    username = Column(String(64), unique=True, nullable=False)
    password_hash = Column(String(255), nullable=False)
    role = Column(String(32), nullable=False)
    is_active = Column(Boolean, server_default=text("true"))
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    __table_args__ = (CheckConstraint("role IN ('buyer','inv_ctrl','exec','admin')",
                                      name="sys_user_role_check"),)


class AuditLog(Base):
    __tablename__ = "audit_log"
    id = Column(Integer, primary_key=True, autoincrement=True)
    username = Column(String(64))
    action = Column(String(128), nullable=False)
    target = Column(JSON)
    occurred_at = Column(DateTime(timezone=True), server_default=func.now())
