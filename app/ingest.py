"""
WebhookHub - Ingest-only app served on a separate port (8181).
Exposes only POST /webhook/{channel_slug} so the dashboard port (8080)
can be placed behind an authenticated proxy while webhooks remain reachable.
"""

from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.main import receive_webhook, init_db


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    yield


app = FastAPI(title="WebhookHub Ingest", version="1.0.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["POST"],
    allow_headers=["*"],
)

app.add_api_route("/webhook/{channel_slug}", receive_webhook, methods=["POST"])
