"""
Price check scheduler + alert evaluation.
"""
from datetime import datetime, timedelta
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from sqlalchemy.orm import Session
from database import SessionLocal, Product, PriceHistory, Alert
from scraper import fetch_item_data
from telegram_bot import send_price_alert

scheduler = AsyncIOScheduler(timezone="America/Mexico_City")


# ── Alert evaluation ──────────────────────────────────────────────────────────

async def _evaluate_alerts(product: Product, new_price: float, db: Session):
    alerts = [a for a in product.alerts if a.is_active]
    if not alerts:
        return

    prices = [p.price for p in product.prices]  # ordered by timestamp asc
    if not prices:
        return

    min_price = min(prices)
    now = datetime.utcnow()

    # 7-day average (prices from last 7 days)
    cutoff = now - timedelta(days=7)
    recent_prices = [
        p.price for p in product.prices
        if p.timestamp >= cutoff
    ]
    avg_7day = sum(recent_prices) / len(recent_prices) if recent_prices else None

    # Previous price (before this check)
    prev_price = prices[-1] if prices else new_price

    for alert in alerts:
        triggered = False
        extra = ""

        if alert.alert_type == "target_price":
            if alert.threshold_value is not None and new_price <= alert.threshold_value:
                triggered = True

        elif alert.alert_type == "new_minimum":
            if new_price < min_price:
                triggered = True
                extra = f"(anterior mínimo: ${min_price:,.2f})"

        elif alert.alert_type == "percent_drop_initial":
            if product.initial_price and alert.threshold_value is not None:
                drop_pct = (product.initial_price - new_price) / product.initial_price * 100
                if drop_pct >= alert.threshold_value:
                    triggered = True
                    extra = f"({drop_pct:.1f}% de caída)"

        elif alert.alert_type == "sudden_drop":
            if alert.threshold_value is not None and prev_price > 0:
                drop_pct = (prev_price - new_price) / prev_price * 100
                if drop_pct >= alert.threshold_value:
                    triggered = True
                    extra = f"(cayó {drop_pct:.1f}% desde ${prev_price:,.2f})"

        elif alert.alert_type == "below_7day_avg":
            if avg_7day is not None and new_price < avg_7day:
                triggered = True
                extra = f"(promedio 7d: ${avg_7day:,.2f})"

        if triggered and alert.telegram_chat_id:
            sent = await send_price_alert(
                chat_id=alert.telegram_chat_id,
                product_title=product.title,
                current_price=new_price,
                alert_type=alert.alert_type,
                threshold_value=alert.threshold_value,
                product_url=product.url,
                extra=extra,
            )
            if sent:
                alert.last_triggered_at = now
                db.commit()


# ── Core check function ───────────────────────────────────────────────────────

async def check_product_price(product_id: int):
    db: Session = SessionLocal()
    try:
        product = db.get(Product, product_id)
        if not product:
            return

        data = await fetch_item_data(product.url)
        new_price = data["price"]

        # Update title/image if they changed (e.g., first run)
        if data.get("title") and not product.title:
            product.title = data["title"]
        if data.get("image_url") and not product.image_url:
            product.image_url = data["image_url"]

        # Save price record
        record = PriceHistory(product_id=product_id, price=new_price, timestamp=datetime.utcnow())
        db.add(record)
        db.commit()
        db.refresh(product)

        await _evaluate_alerts(product, new_price, db)

    except Exception as e:
        print(f"[scheduler] Error checking product {product_id}: {e}")
    finally:
        db.close()


# ── Job management ────────────────────────────────────────────────────────────

def schedule_product(product_id: int, interval_hours: int):
    job_id = f"product_{product_id}"
    if scheduler.get_job(job_id):
        scheduler.remove_job(job_id)
    scheduler.add_job(
        check_product_price,
        trigger="interval",
        hours=interval_hours,
        id=job_id,
        args=[product_id],
        next_run_time=datetime.now(),   # run immediately on add
        misfire_grace_time=3600,
    )


def unschedule_product(product_id: int):
    job_id = f"product_{product_id}"
    if scheduler.get_job(job_id):
        scheduler.remove_job(job_id)


def reschedule_all(db: Session):
    """Called on app startup to restore all scheduled jobs."""
    products = db.query(Product).all()
    for product in products:
        schedule_product(product.id, product.check_interval_hours)
