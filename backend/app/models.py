from __future__ import annotations

from enum import Enum

from sqlalchemy import (
    Boolean,
    Column,
    Date,
    DateTime,
    Enum as saEnum,
    Float,
    ForeignKey,
    Integer,
    String,
    Table,
    Text,
    func,
)
from sqlalchemy.orm import relationship

from .database import Base


class RoleEnum(str, Enum):
    operator = "operator"
    manager = "manager"
    owner = "owner"


class SKU(Base):
    __tablename__ = "skus"

    sku_id = Column(Integer, primary_key=True)
    sku_code = Column(String(255), unique=True, nullable=False)
    name = Column(String(255), nullable=False)
    description = Column(Text, nullable=True)
    vendor_name = Column(String(255), nullable=True)
    vendor_url = Column(String(512), nullable=True)
    salesman_name = Column(String(255), nullable=True)
    salesman_phone = Column(String(64), nullable=True)
    salesman_email = Column(String(255), nullable=True)
    unit_of_measure = Column(String(20), nullable=False, default="pieces")
    current_stock = Column(Float, nullable=False, default=0.0)
    waste_pct = Column(Float, nullable=False, default=0.0)
    alert_threshold_qty = Column(Float, nullable=True)

    parent_recipes = relationship("SKURecipe", back_populates="parent", foreign_keys="SKURecipe.parent_sku_id")
    child_recipes = relationship("SKURecipe", back_populates="child", foreign_keys="SKURecipe.child_sku_id")
    purchase_logs = relationship("PurchaseLog", back_populates="sku")
    product_recipes = relationship(
        "ProductRecipe",
        back_populates="child",
        foreign_keys="ProductRecipe.child_sku_id",
    )
    tags = relationship("Tag", secondary="sku_tags", back_populates="skus")


class SKURecipe(Base):
    __tablename__ = "sku_recipes"

    id = Column(Integer, primary_key=True)
    parent_sku_id = Column(Integer, ForeignKey("skus.sku_id"), nullable=False)
    child_sku_id = Column(Integer, ForeignKey("skus.sku_id"), nullable=False)
    qty_used = Column(Float, nullable=False, default=0.0)

    parent = relationship("SKU", back_populates="parent_recipes", foreign_keys=[parent_sku_id])
    child = relationship("SKU", back_populates="child_recipes", foreign_keys=[child_sku_id])


class PurchaseLog(Base):
    __tablename__ = "purchase_logs"

    id = Column(Integer, primary_key=True)
    sku_id = Column(Integer, ForeignKey("skus.sku_id"), nullable=False)
    purchase_date = Column(Date, nullable=False)
    quantity = Column(Float, nullable=False)
    price = Column(Float, nullable=False)
    applies_to_stock = Column(Boolean, nullable=False, default=True)
    supplier_name = Column(String(255), nullable=True)
    supplier_url = Column(String(512), nullable=True)
    supplier_code = Column(String(255), nullable=True)
    notes = Column(Text, nullable=True)

    sku = relationship("SKU", back_populates="purchase_logs")


class Product(Base):
    __tablename__ = "products"

    product_id = Column(Integer, primary_key=True)
    product_code = Column(String(255), unique=True, nullable=False)
    name = Column(String(255), nullable=False)
    description = Column(Text, nullable=True)
    price = Column(Float, nullable=True)
    is_active = Column(Boolean, nullable=False, default=True)

    recipes = relationship("ProductRecipe", back_populates="parent")


class ProductRecipe(Base):
    __tablename__ = "product_recipes"

    id = Column(Integer, primary_key=True)
    parent_product_id = Column(Integer, ForeignKey("products.product_id"), nullable=False)
    child_sku_id = Column(Integer, ForeignKey("skus.sku_id"), nullable=False)
    qty_used = Column(Float, nullable=False, default=0.0)

    parent = relationship("Product", back_populates="recipes")
    child = relationship("SKU", back_populates="product_recipes", foreign_keys=[child_sku_id])


class ShippingMapping(Base):
    __tablename__ = "shipping_mapping"

    id = Column(Integer, primary_key=True)
    dimensions_string = Column(String(64), nullable=True, unique=True)
    items_json = Column(Text, nullable=False)
    length = Column(Float, nullable=True)
    width = Column(Float, nullable=True)
    height = Column(Float, nullable=True)
    package_code = Column(String(64), nullable=True)


class SyncLog(Base):
    __tablename__ = "sync_logs"

    id = Column(Integer, primary_key=True)
    internal_order_id = Column(Integer, nullable=False)
    shipstation_order_id = Column(String(128), nullable=True, unique=True)
    timestamp = Column(DateTime(timezone=True), server_default=func.now())
    action = Column(String(64), nullable=False)
    details = Column(Text, nullable=True)


class User(Base):
    __tablename__ = "users"

    user_id = Column(Integer, primary_key=True)
    email = Column(String(255), unique=True, nullable=False)
    password_hash = Column(String(512), nullable=False)
    role = Column(saEnum(RoleEnum), nullable=False, default=RoleEnum.operator)
    receives_stock_alerts = Column(Boolean, nullable=False, default=True)

    def is_operator(self) -> bool:
        return self.role == RoleEnum.operator

    def is_manager(self) -> bool:
        return self.role == RoleEnum.manager

    def is_owner(self) -> bool:
        return self.role == RoleEnum.owner


class AuditLog(Base):
    __tablename__ = "audit_logs"

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, nullable=True)
    order_id = Column(Integer, nullable=True)
    action = Column(String(128), nullable=False)
    child_sku_code = Column(String(255), nullable=True)
    child_sku_name = Column(String(255), nullable=True)
    child_unit = Column(String(64), nullable=True)
    quantity = Column(Float, nullable=True)
    product_code = Column(String(255), nullable=True)
    product_name = Column(String(255), nullable=True)
    product_quantity = Column(Float, nullable=True)
    details = Column(Text, nullable=True)
    timestamp = Column(DateTime(timezone=True), server_default=func.now())


class AppSetting(Base):
    __tablename__ = "app_settings"

    id = Column(Integer, primary_key=True)
    email_alerts_enabled = Column(Boolean, nullable=False, default=True)
    smtp_host = Column(String(255), nullable=True)
    smtp_port = Column(Integer, nullable=True)
    smtp_user = Column(String(255), nullable=True)
    smtp_password = Column(String(512), nullable=True)
    smtp_from = Column(String(255), nullable=True)
    price_spike_pct = Column(Float, nullable=False, default=10.0)
    low_stock_cta = Column(String(512), nullable=True)


class Tag(Base):
    __tablename__ = "tags"

    tag_id = Column(Integer, primary_key=True)
    name = Column(String(64), unique=True, nullable=False)
    color = Column(String(32), nullable=True)
    description = Column(Text, nullable=True)
    skus = relationship("SKU", secondary="sku_tags", back_populates="tags")


sku_tags = Table(
    "sku_tags",
    Base.metadata,
    Column("sku_id", Integer, ForeignKey("skus.sku_id", ondelete="CASCADE"), primary_key=True),
    Column("tag_id", Integer, ForeignKey("tags.tag_id", ondelete="CASCADE"), primary_key=True),
)


class AddOnMapping(Base):
    __tablename__ = "add_on_mappings"

    id = Column(Integer, primary_key=True)
    add_on_name = Column(String(255), nullable=False)
    sku_code = Column(String(255), nullable=False)
    quantity = Column(Float, nullable=False, default=1.0)
    notes = Column(Text, nullable=True)
