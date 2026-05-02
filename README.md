# ML Tracker 🛒

Rastreador de precios de MercadoLibre con alertas por Telegram y dashboard web.

## Características

- 📊 Dashboard con historial de precios en gráficas
- 🔔 Alertas por Telegram (precio objetivo, nuevo mínimo, caída porcentual, etc.)
- ⏱️ Checks automáticos configurables (1h, 3h, 6h, 12h, 24h)
- 🌐 Interfaz web con tema oscuro

## Requisitos

- Python 3.9+
- Bot de Telegram (crear con [@BotFather](https://t.me/botfather))

## Instalación

```bash
# Clonar el repo
git clone https://github.com/josecd/ml-tracker.git
cd ml-tracker

# Instalar dependencias
pip install -r requirements.txt

# Configurar variables de entorno
cp .env.example .env
# Editar .env con tus credenciales
```

## Configuración

Edita el archivo `.env`:

```env
TELEGRAM_BOT_TOKEN=tu_token_aqui
DEFAULT_CHECK_INTERVAL=1
```

## Uso

```bash
uvicorn main:app --host 0.0.0.0 --port 8000
```

Abre `http://localhost:8000` en tu navegador.

## Tipos de alertas

| Tipo | Descripción |
|------|-------------|
| `target_price` | Avisar cuando el precio baje de X |
| `new_minimum` | Avisar cuando sea el precio más bajo registrado |
| `drop_pct` | Avisar cuando caiga X% desde el último check |
| `sudden_drop` | Avisar ante caídas bruscas |
| `below_7day_avg` | Avisar cuando esté por debajo del promedio de 7 días |

## Stack

- **Backend:** FastAPI + SQLAlchemy + APScheduler
- **Frontend:** Jinja2 + Chart.js
- **Scraping:** httpx + BeautifulSoup4
- **Alertas:** Telegram Bot API
