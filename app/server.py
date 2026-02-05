from fastapi import FastAPI
from app.api.websocket_routes import ws_router
from app.api.rest_routes import rest_router
from app.api.auth_routes import auth_router
from app.DB.pool import init_db_pool, close_db_pool

def create_app() -> FastAPI:
    app = FastAPI()

    # Health check supporting both GET and HEAD
    @app.api_route("/", methods=["GET", "HEAD"])
    async def health_check():
        return {"status": "healthy", "message": "Server is running"}

    app.include_router(ws_router)
    app.include_router(rest_router)
    app.include_router(auth_router)

    # -------------------------------
    # Startup & Shutdown events
    # -------------------------------
    @app.on_event("startup")
    async def startup_event():
        await init_db_pool()  # prints ✅ Neon PostgreSQL DB connected successfully!

    @app.on_event("shutdown")
    async def shutdown_event():
        await close_db_pool()  # prints ℹ️ Neon PostgreSQL DB pool closed

    return app