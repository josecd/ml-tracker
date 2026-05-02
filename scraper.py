"""
Scraper for MercadoLibre Mexico.
Primary: ML API (if credentials configured).
Fallback: HTML scraping via og: meta tags.
"""
import re
import json
import httpx
from typing import Optional
from urllib.parse import urlparse, parse_qs, unquote
from config import ML_CLIENT_ID, ML_CLIENT_SECRET

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "es-MX,es;q=0.9",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}

_ml_token: Optional[str] = None


def extract_ml_id(url: str) -> str:
    """
    Extract the MercadoLibre item ID (MLMxxxxxxx) from any ML URL.
    Handles: product pages, item pages, cart URLs with wid param.
    """
    parsed = urlparse(url)
    params = parse_qs(parsed.query)

    # wid param (cart / PDP with wid)
    wid = params.get("wid", [None])[0]
    if wid and re.match(r"MLM\d+", wid):
        return wid

    # pdp_filters=item_id%3AMLMxxxxxx
    pdp = unquote(params.get("pdp_filters", [""])[0])
    m = re.search(r"item_id[=:]+(MLM\d+)", pdp)
    if m:
        return m.group(1)

    # /p/MLMxxxxxx  — product page (use product ID for scraping)
    m = re.search(r"/p/(MLM\d+)", parsed.path)
    if m:
        return m.group(1)

    # Item ID at end of path
    m = re.search(r"/(MLM[\d]+)(?:[?#]|$)", url)
    if m:
        return m.group(1)

    # Any MLM id in the URL
    m = re.search(r"(MLM\d+)", url)
    if m:
        return m.group(1)

    raise ValueError(f"No se pudo extraer el ID de MercadoLibre de: {url}")


def build_fetch_url(ml_id: str, original_url: str) -> str:
    """Use the original URL for scraping when it's a ML URL."""
    if "mercadolibre.com.mx" in original_url:
        return original_url.split("#")[0]
    # Fallback: build item URL from ID
    return f"https://articulo.mercadolibre.com.mx/{ml_id.replace('MLM', 'MLM-')}"


async def _get_ml_token() -> Optional[str]:
    global _ml_token
    if not ML_CLIENT_ID or not ML_CLIENT_SECRET:
        return None
    if _ml_token:
        return _ml_token
    async with httpx.AsyncClient() as client:
        try:
            resp = await client.post(
                "https://api.mercadolibre.com/oauth/token",
                data={
                    "grant_type": "client_credentials",
                    "client_id": ML_CLIENT_ID,
                    "client_secret": ML_CLIENT_SECRET,
                },
                timeout=10,
            )
            if resp.status_code == 200:
                _ml_token = resp.json()["access_token"]
                return _ml_token
        except Exception:
            pass
    return None


async def _fetch_via_api(ml_id: str) -> Optional[dict]:
    """Try to get item data from the official ML API."""
    token = await _get_ml_token()
    if not token:
        return None
    async with httpx.AsyncClient() as client:
        try:
            resp = await client.get(
                f"https://api.mercadolibre.com/items/{ml_id}",
                headers={"Authorization": f"Bearer {token}"},
                timeout=10,
            )
            if resp.status_code == 200:
                data = resp.json()
                return {
                    "price": float(data["price"]),
                    "title": data["title"],
                    "image_url": data.get("thumbnail", "").replace("I.jpg", "O.jpg"),
                    "url": data.get("permalink", ""),
                }
        except Exception:
            pass
    return None


async def _fetch_via_scraping(url: str, ml_id: str) -> dict:
    """Scrape ML page using og: meta tags — most reliable without auth."""
    async with httpx.AsyncClient(follow_redirects=True, timeout=15) as client:
        resp = await client.get(url, headers=HEADERS)
        resp.raise_for_status()
        html = resp.text

    # og:title  →  "Title - $ 8,489"
    og_title_m = re.search(r'property="og:title"\s+content="([^"]+)"', html)
    og_title_m = og_title_m or re.search(r'og:title" content="([^"]+)"', html)

    og_image_m = re.search(r'property="og:image"\s+content="([^"]+)"', html)
    og_image_m = og_image_m or re.search(r'og:image" content="([^"]+)"', html)

    og_url_m = re.search(r'property="og:url"\s+content="([^"]+)"', html)
    og_url_m = og_url_m or re.search(r'og:url" content="([^"]+)"', html)

    if not og_title_m:
        raise ValueError(f"No se encontró precio en la página de ML para {ml_id}")

    raw_title = og_title_m.group(1)

    # Extract price from title: "Product name - $ 8,489"
    price_m = re.search(r"-\s*\$\s*([\d,]+(?:\.\d+)?)\s*$", raw_title)
    if not price_m:
        # Fallback: look for "price": NNNN in JSON embedded in page
        json_price_m = re.search(r'"price"\s*:\s*([\d.]+)', html)
        if not json_price_m:
            raise ValueError(f"No se encontró precio en la página para {ml_id}")
        price = float(json_price_m.group(1))
        title = raw_title
    else:
        price = float(price_m.group(1).replace(",", ""))
        title = raw_title[: raw_title.rfind(" - $")].strip()

    return {
        "price": price,
        "title": title,
        "image_url": og_image_m.group(1) if og_image_m else "",
        "url": og_url_m.group(1) if og_url_m else url,
    }


async def fetch_item_data(url: str) -> dict:
    """
    Main entry point. Returns dict with: price, title, image_url, url, ml_item_id.
    Tries ML API first (if configured), falls back to scraping.
    """
    ml_id = extract_ml_id(url)

    # Try official API for item IDs (not product IDs)
    if re.match(r"MLM\d+$", ml_id) and len(ml_id) < 15:
        api_data = await _fetch_via_api(ml_id)
        if api_data:
            api_data["ml_item_id"] = ml_id
            return api_data

    # Scraping fallback
    fetch_url = build_fetch_url(ml_id, url)
    data = await _fetch_via_scraping(fetch_url, ml_id)
    data["ml_item_id"] = ml_id
    return data
