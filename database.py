from datetime import datetime
from sqlalchemy import create_engine, Column, Integer, String, Float, DateTime, Boolean, ForeignKey
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
    created_at = Column(DateTime, default=datetime.utcnow)

    prices = relationship("PriceHistory", back_populates="product", cascade="all, delete-orphan", order_by="PriceHistory.timestamp")
    alerts = relationship("Alert", back_populates="product", cascade="all, delete-orphan")


class PriceHistory(Base):
    __tablename__ = "price_history"

    id = Column(Integer, primary_key=True, index=True)
    product_id = Column(Integer, ForeignKey("products.id"))
    price = Column(Float)
    timestamp = Column(DateTime, default=datetime.utcnow)

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
    created_at = Column(DateTime, default=datetime.utcnow)

    product = relationship("Product", back_populates="alerts")


def create_tables():
    Base.metadata.create_all(bind=engine)


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
