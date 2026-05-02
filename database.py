from datetime import datetime
from zoneinfo import ZoneInfo
from sqlalchemy import create_engine, Column, Integer, String, Float, DateTime, Boolean, ForeignKey, text
from sqlalchemy.orm import DeclarativeBase, sessionmaker, relationship
from config import DATABASE_URL

engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False})
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


class Base(DeclarativeBase):
    pass


class Product(Base):
    __tablename__ = "products"

    id = Column(Integer, primary_key=True, index=True)
    ml_item_id = Column(String, unique=True, index=True)  # e.g. MLM2746152281
    title = Column(String)
    url = Column(String)
    image_url = Column(String, nullable=True)
    check_interval_hours = Column(Integer, default=6)
    initial_price = Column(Float, nullable=True)
    last_checked_at = Column(DateTime, nullable=True)
    last_check_ok = Column(Boolean, nullable=True)
    created_at = Column(DateTime, default=lambda: datetime.now(ZoneInfo("America/Cancun")).replace(tzinfo=None))

    prices = relationship("PriceHistory", back_populates="product", cascade="all, delete-orphan", order_by="PriceHistory.timestamp")
    alerts = relationship("Alert", back_populates="product", cascade="all, delete-orphan")
    alert_logs = relationship("AlertLog", back_populates="product", cascade="all, delete-orphan", order_by="AlertLog.triggered_at.desc()")


class PriceHistory(Base):
    __tablename__ = "price_history"

    id = Column(Integer, primary_key=True, index=True)
    product_id = Column(Integer, ForeignKey("products.id"))
    price = Column(Float)
    timestamp = Column(DateTime, default=lambda: datetime.now(ZoneInfo("America/Cancun")).replace(tzinfo=None))

    product = relationship("Product", back_populates="prices")


class Alert(Base):
    __tablename__ = "alerts"

    id = Column(Integer, primary_key=True, index=True)
    product_id = Column(Integer, ForeignKey("products.id"))
    # Types: target_price | new_minimum | percent_drop_initial | sudden_drop | below_7day_avg
    alert_type = Column(String)
    threshold_value = Column(Float, nullable=True)
    telegram_chat_id = Column(String)
    label = Column(String, nullable=True)   # User-friendly name for the alert
    is_active = Column(Boolean, default=True)
    last_triggered_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=lambda: datetime.now(ZoneInfo("America/Cancun")).replace(tzinfo=None))

    product = relationship("Product", back_populates="alerts")


class AlertLog(Base):
    __tablename__ = "alert_logs"

    id = Column(Integer, primary_key=True, index=True)
    alert_id = Column(Integer, ForeignKey("alerts.id", ondelete="SET NULL"), nullable=True)
    product_id = Column(Integer, ForeignKey("products.id", ondelete="CASCADE"))
    alert_type = Column(String)
    price_at_trigger = Column(Float)
    triggered_at = Column(DateTime, default=lambda: datetime.now(ZoneInfo("America/Cancun")).replace(tzinfo=None))
    extra = Column(String, nullable=True)

    product = relationship("Product", back_populates="alert_logs")


def create_tables():
    Base.metadata.create_all(bind=engine)
    # Migrate: add columns that may not exist in older DB versions
    _safe_add_column("products", "last_checked_at", "DATETIME")
    _safe_add_column("products", "last_check_ok", "BOOLEAN")


def _safe_add_column(table: str, column: str, col_type: str):
    with engine.connect() as conn:
        try:
            conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {column} {col_type}"))
            conn.commit()
        except Exception:
            pass  # Column already exists


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
