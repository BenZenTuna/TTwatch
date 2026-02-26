"""Market data endpoints (shared reference data, price history)."""
from fastapi import APIRouter

router = APIRouter()

# TODO: GET /market/quote/{symbol} — get current market data for a symbol
# TODO: GET /market/history/{symbol} — get OHLCV price history
# TODO: GET /market/tickers — search ticker reference database
# TODO: GET /market/themes — list theme-to-ETF mappings
