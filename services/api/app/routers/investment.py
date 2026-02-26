"""Investment analysis and watchlist endpoints."""
from fastapi import APIRouter

router = APIRouter()

# TODO: GET /topics/{topic_id}/analyses — list investment analyses
# TODO: GET /analyses/{analysis_id} — get single analysis
# TODO: POST /topics/{topic_id}/analyses — trigger investment analysis
# TODO: GET /watchlist — list user's watchlist items
# TODO: POST /watchlist — add item to watchlist
# TODO: PUT /watchlist/{item_id} — update watchlist item (notes, targets)
# TODO: DELETE /watchlist/{item_id} — remove from watchlist
# TODO: GET /alerts — list price alerts
# TODO: POST /alerts — create price alert
# TODO: DELETE /alerts/{alert_id} — delete price alert
# TODO: GET /topics/{topic_id}/correlations — list correlation signals
