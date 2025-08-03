from fastapi import FastAPI
from app.API.websocket_routes import ws_router

def create_app() -> FastAPI:
    app = FastAPI()
    app.include_router(ws_router)
    return app