"""
Price check scheduler + alert evaluation.
"""
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from sqlalchemy.orm import Session
from database import SessionLocal, Product, PriceHistory, Alert, AlertLog
from scraper import fetch_item_data
from telegram_bot import send_price_alert, send_message

scheduler = AsyncIOScheduler(timezone="America/Mexico_City")


# ── Alert evaluation ──────────────────────────────────────────────────────────

async def _evaluate_alerts(
    product: Product,
    new_price: float,
    prev_price: float,
    old_min: float | None,
    db: Session,
):
    alerts = [a for a in product.alerts if a.is_active]
    if not alerts:
        return

    now = datetime.now(ZoneInfo("America/Cancun")).replace(tzinfo=None)

    min_price = old_min if old_min is not None else new_price

    # 7-day average (all prices in last 7 days, including new record)
    cutoff = now - timedelta(days=7)
    recent_prices = [
        p.price for p in product.prices
        if p.timestamp >= cutoff
    ]
    avg_7day = sum(recent_prices) / len(recent_prices) if recent_prices else None

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

        elif alert.alert_type == "target_price_high":
            if alert.threshold_value is not None and new_price >= alert.threshold_value:
                triggered = True
                extra = f"(alcanzó ${new_price:,.2f})"

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

        elif alert.alert_type == "price_increase":
            if new_price > prev_price:
                triggered = True
                rise_pct = (new_price - prev_price) / prev_price * 100
                extra = f"(subió {rise_pct:.1f}% desde ${prev_price:,.2f})"

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
                log = AlertLog(
                    alert_id=alert.id,
                    product_id=product.id,
                    alert_type=alert.alert_type,
                    price_at_trigger=new_price,
                    triggered_at=now,
                    extra=extra,
                )
                db.add(log)
                db.commit()


# ── Core check function ───────────────────────────────────────────────────────

async def check_product_price(product_id: int):
    db: Session = SessionLocal()
    try:
        product = db.get(Product, product_id)
        if not product:
            return

        now = datetime.now(ZoneInfo("America/Cancun")).replace(tzinfo=None)

        try:
            data = await fetch_item_data(product.url)
        except Exception as e:
            product.last_checked_at = now
            product.last_check_ok = False
            db.commit()
            # Notify active alert owners that product is unavailable
            chat_ids = {a.telegram_chat_id for a in product.alerts if a.is_active and a.telegram_chat_id}
            for chat_id in chat_ids:
                await send_message(
                    chat_id,
                    f"⚠️ <b>ML Tracker</b> — No se pudo obtener el precio de "
                    f"<b>{product.title}</b>. Es posible que el producto no esté disponible.",
                )
            print(f"[scheduler] Error checking product {product_id}: {e}")
            return

        new_price = data["price"]

        # Update title/image if they changed (e.g., first run)
        if data.get("title") and not product.title:
            product.title = data["title"]
        if data.get("image_url") and not product.image_url:
            product.image_url = data["image_url"]

        product.last_checked_at = now
        product.last_check_ok = True

        # Capture previous price info BEFORE saving the new record
        prev_prices = [ph.price for ph in product.prices]
        prev_price = prev_prices[-1] if prev_prices else new_price
        old_min = min(prev_prices) if prev_prices else None

        # Save price record
        record = PriceHistory(product_id=product_id, price=new_price, timestamp=now)
        db.add(record)
        db.commit()
        db.refresh(product)

        await _evaluate_alerts(product, new_price, prev_price, old_min, db)

    except Exception as e:
        print(f"[scheduler] Error checking product {product_id}: {e}")
    finally:
        db.close()


# ── Cleanup job ───────────────────────────────────────────────────────────────

async def _cleanup_old_history():
    """Delete price_history records older than 90 days."""
    db: Session = SessionLocal()
    try:
        cutoff = datetime.now(ZoneInfo("America/Cancun")).replace(tzinfo=None) - timedelta(days=90)
        deleted = db.query(PriceHistory).filter(PriceHistory.timestamp < cutoff).delete()
        db.commit()
        if deleted:
            print(f"[scheduler] Cleaned {deleted} old price records")
    except Exception as e:
        print(f"[scheduler] Cleanup error: {e}")
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
    # Daily cleanup of old price history (> 90 days)
    if not scheduler.get_job("cleanup_history"):
        scheduler.add_job(
            _cleanup_old_history,
            trigger="interval",
            hours=24,
            id="cleanup_history",
            misfire_grace_time=3600,
        )
