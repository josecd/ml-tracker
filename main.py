import os
from contextlib import asynccontextmanager
from datetime import datetime
from fastapi import FastAPI, Request, Depends, Form, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

from config import DEFAULT_CHECK_INTERVAL
from database import create_tables, get_db, Product, PriceHistory, Alert
from scraper import fetch_item_data, extract_ml_id
from scheduler import scheduler, schedule_product, unschedule_product, reschedule_all, check_product_price
from telegram_bot import verify_bot_token, send_message


# ── App lifecycle ─────────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    create_tables()
    db = next(get_db())
    reschedule_all(db)
    db.close()
    scheduler.start()
    yield
    scheduler.shutdown(wait=False)


app = FastAPI(title="ML Price Tracker", lifespan=lifespan)
templates = Jinja2Templates(directory=os.path.join(BASE_DIR, "templates"))


# ── Template helpers ──────────────────────────────────────────────────────────

def _product_card_data(product: Product) -> dict:
    prices = product.prices  # ordered by timestamp asc
    current_price = prices[-1].price if prices else None
    prev_price = prices[-2].price if len(prices) >= 2 else current_price
    min_price = min(p.price for p in prices) if prices else None
    max_price = max(p.price for p in prices) if prices else None
    avg_price = sum(p.price for p in prices) / len(prices) if prices else None

    change_pct = None
    if current_price and prev_price and prev_price != 0:
        change_pct = (current_price - prev_price) / prev_price * 100

    sparkline = [{"t": p.timestamp.isoformat(), "y": p.price} for p in prices[-30:]]

    return {
        "product": product,
        "current_price": current_price,
        "prev_price": prev_price,
        "min_price": min_price,
        "max_price": max_price,
        "avg_price": avg_price,
        "change_pct": change_pct,
        "sparkline": sparkline,
        "alert_count": len([a for a in product.alerts if a.is_active]),
    }


# ── Pages ─────────────────────────────────────────────────────────────────────

@app.get("/", response_class=HTMLResponse)
async def dashboard(request: Request, db: Session = Depends(get_db)):
    products = db.query(Product).order_by(Product.created_at.desc()).all()
    cards = [_product_card_data(p) for p in products]
    return templates.TemplateResponse("dashboard.html", {"request": request, "cards": cards})


@app.get("/product/{product_id}", response_class=HTMLResponse)
async def product_detail(product_id: int, request: Request, db: Session = Depends(get_db)):
    product = db.get(Product, product_id)
    if not product:
        raise HTTPException(status_code=404, detail="Producto no encontrado")
    card = _product_card_data(product)
    history = [{"t": p.timestamp.strftime("%Y-%m-%d %H:%M"), "y": p.price} for p in product.prices]
    return templates.TemplateResponse(
        "product.html",
        {"request": request, "card": card, "history": history, "product": product},
    )


# ── API: Products ─────────────────────────────────────────────────────────────

@app.post("/api/products")
async def add_product(
    url: str = Form(...),
    check_interval: int = Form(DEFAULT_CHECK_INTERVAL),
    db: Session = Depends(get_db),
):
    try:
        ml_id = extract_ml_id(url)
    except ValueError as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=400)

    existing = db.query(Product).filter(Product.ml_item_id == ml_id).first()
    if existing:
        return JSONResponse({"ok": False, "error": "Este producto ya está siendo rastreado."}, status_code=400)

    # Fetch initial data
    try:
        data = await fetch_item_data(url)
    except Exception as e:
        return JSONResponse({"ok": False, "error": f"No se pudo obtener el precio: {e}"}, status_code=400)

    product = Product(
        ml_item_id=data["ml_item_id"],
        title=data["title"],
        url=data.get("url") or url,
        image_url=data.get("image_url"),
        check_interval_hours=check_interval,
        initial_price=data["price"],
    )
    db.add(product)
    db.commit()
    db.refresh(product)

    # Save first price record
    db.add(PriceHistory(product_id=product.id, price=data["price"]))
    db.commit()

    schedule_product(product.id, check_interval)

    return JSONResponse({"ok": True, "product_id": product.id})


@app.patch("/api/products/{product_id}/interval")
async def update_interval(
    product_id: int,
    check_interval: int = Form(...),
    db: Session = Depends(get_db),
):
    product = db.get(Product, product_id)
    if not product:
        raise HTTPException(status_code=404)
    product.check_interval_hours = check_interval
    db.commit()
    schedule_product(product_id, check_interval)
    return JSONResponse({"ok": True})


@app.delete("/api/products/{product_id}")
async def delete_product(product_id: int, db: Session = Depends(get_db)):
    product = db.get(Product, product_id)
    if not product:
        raise HTTPException(status_code=404)
    unschedule_product(product_id)
    db.delete(product)
    db.commit()
    return JSONResponse({"ok": True})


@app.post("/api/products/{product_id}/check")
async def manual_check(product_id: int, db: Session = Depends(get_db)):
    product = db.get(Product, product_id)
    if not product:
        raise HTTPException(status_code=404)
    await check_product_price(product_id)
    # Return fresh price
    db.expire(product)
    db.refresh(product)
    prices = product.prices
    return JSONResponse({
        "ok": True,
        "price": prices[-1].price if prices else None,
    })


# ── API: Alerts ───────────────────────────────────────────────────────────────

@app.post("/api/alerts")
async def create_alert(
    product_id: int = Form(...),
    alert_type: str = Form(...),
    threshold_value: str = Form(None),
    telegram_chat_id: str = Form(...),
    label: str = Form(""),
    db: Session = Depends(get_db),
):
    product = db.get(Product, product_id)
    if not product:
        raise HTTPException(status_code=404)

    threshold_float = float(threshold_value) if threshold_value and threshold_value.strip() else None

    alert = Alert(
        product_id=product_id,
        alert_type=alert_type,
        threshold_value=threshold_float,
        telegram_chat_id=telegram_chat_id.strip(),
        label=label.strip() or None,
    )
    db.add(alert)
    db.commit()
    db.refresh(alert)
    return JSONResponse({"ok": True, "alert_id": alert.id})


@app.delete("/api/alerts/{alert_id}")
async def delete_alert(alert_id: int, db: Session = Depends(get_db)):
    alert = db.get(Alert, alert_id)
    if not alert:
        raise HTTPException(status_code=404)
    db.delete(alert)
    db.commit()
    return JSONResponse({"ok": True})


@app.patch("/api/alerts/{alert_id}/toggle")
async def toggle_alert(alert_id: int, db: Session = Depends(get_db)):
    alert = db.get(Alert, alert_id)
    if not alert:
        raise HTTPException(status_code=404)
    alert.is_active = not alert.is_active
    db.commit()
    return JSONResponse({"ok": True, "is_active": alert.is_active})


# ── API: Price history (for Chart.js) ────────────────────────────────────────

@app.get("/api/prices/{product_id}")
async def get_prices(product_id: int, db: Session = Depends(get_db)):
    product = db.get(Product, product_id)
    if not product:
        raise HTTPException(status_code=404)
    history = [
        {"t": p.timestamp.strftime("%Y-%m-%d %H:%M"), "y": p.price}
        for p in product.prices
    ]
    return JSONResponse({"ok": True, "prices": history})


# ── API: Telegram test ────────────────────────────────────────────────────────

@app.post("/api/telegram/test")
async def test_telegram(chat_id: str = Form(...)):
    bot_username = await verify_bot_token()
    if not bot_username:
        return JSONResponse({"ok": False, "error": "Token de bot no configurado o inválido."})
    sent = await send_message(chat_id, "✅ <b>ML Tracker</b> — Conexión de Telegram confirmada.")
    return JSONResponse({"ok": sent, "bot": bot_username})
