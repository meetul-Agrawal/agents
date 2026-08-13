"""FastAPI application entry point."""

import logging
from fastapi import FastAPI, WebSocket, Query
from app.config import get_settings
from app.api.routes import router
from app.api.websocket import websocket_endpoint

settings = get_settings()
logging.basicConfig(level=getattr(logging, settings.log_level.upper(), logging.INFO))

app = FastAPI(title="Customer Representative Agent", version="0.1.0")
app.include_router(router, prefix="/api")


@app.websocket("/ws/chat")
async def chat(
    websocket: WebSocket,
    customer_id: str = Query(...),
    session_id: str = Query(default=None),
):
    await websocket_endpoint(websocket, customer_id=customer_id, session_id=session_id)
