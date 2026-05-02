"""
Telegram alert sender.
Uses the Bot API — no library needed, just httpx.
"""
from typing import Optional
import httpx
from config import TELEGRAM_BOT_TOKEN

TELEGRAM_API = "https://api.telegram.org"


async def send_message(chat_id: str, text: str) -> bool:
    """Send a message to a Telegram chat. Returns True on success."""
    if not TELEGRAM_BOT_TOKEN:
        return False
    url = f"{TELEGRAM_API}/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": False,
    }
    async with httpx.AsyncClient(timeout=10) as client:
        try:
            resp = await client.post(url, json=payload)
            return resp.status_code == 200
        except Exception:
            return False


def build_alert_message(
    product_title: str,
    current_price: float,
    alert_type: str,
    threshold_value,
    product_url: str,
    extra: str = "",
) -> str:
    icons = {
        "target_price": "🎯",
        "target_price_high": "🎯",
        "new_minimum": "📉",
        "percent_drop_initial": "📉",
        "sudden_drop": "⚡",
        "below_7day_avg": "📊",
        "price_increase": "📈",
    }
    labels = {
        "target_price": f"Alcanzó precio objetivo (${threshold_value:,.0f})" if threshold_value else "Precio objetivo alcanzado",
        "target_price_high": f"Alcanzó precio alto objetivo (${threshold_value:,.0f})" if threshold_value else "Precio alto objetivo alcanzado",
        "new_minimum": "¡Nuevo mínimo histórico!",
        "percent_drop_initial": f"Bajó {threshold_value:.0f}% desde que lo agregaste" if threshold_value else "Bajó % significativo",
        "sudden_drop": f"Caída brusca de {threshold_value:.0f}% en un check" if threshold_value else "Caída brusca detectada",
        "below_7day_avg": "Por debajo del promedio de 7 días",
        "price_increase": "Subida de precio detectada",
    }
    icon = icons.get(alert_type, "🔔")
    label = labels.get(alert_type, "Alerta de precio")
    if extra:
        label += f" {extra}"

    return (
        f"{icon} <b>Alerta ML Tracker</b>\n\n"
        f"<b>{product_title}</b>\n\n"
        f"💰 Precio actual: <b>${current_price:,.2f} MXN</b>\n"
        f"📌 {label}\n\n"
        f'<a href="{product_url}">Ver en MercadoLibre →</a>'
    )


async def send_price_alert(
    chat_id: str,
    product_title: str,
    current_price: float,
    alert_type: str,
    threshold_value,
    product_url: str,
    extra: str = "",
) -> bool:
    msg = build_alert_message(
        product_title, current_price, alert_type, threshold_value, product_url, extra
    )
    return await send_message(chat_id, msg)


async def verify_bot_token() -> Optional[str]:
    """Returns bot username if token is valid, None otherwise."""
    if not TELEGRAM_BOT_TOKEN:
        return None
    url = f"{TELEGRAM_API}/bot{TELEGRAM_BOT_TOKEN}/getMe"
    async with httpx.AsyncClient(timeout=10) as client:
        try:
            resp = await client.get(url)
            if resp.status_code == 200:
                return resp.json()["result"]["username"]
        except Exception:
            pass
    return None
