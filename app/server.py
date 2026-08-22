from dotenv import load_dotenv, find_dotenv
load_dotenv(find_dotenv(), override=True)

from fastapi import FastAPI
from app.api.websocket_routes import ws_router
from app.api.rest_routes import rest_router
from app.api.auth_routes import auth_router
from app.STT.realtime_routes import realtime_stt_router
from app.Subscription.routes import subscription_router
from app.Promotions.routes import promotion_router
from app.DB.Queries.subscription import sync_plan_stripe_ids_from_env
from app.DB.migrations import run_db_migrations
from app.DB.pool import init_db_pool, close_db_pool
from app.RateLimit.rate_limit_service import drain_token_usage_updates

def create_app() -> FastAPI:
    app = FastAPI()

    # Health check supporting both GET and HEAD
    @app.api_route("/", methods=["GET", "HEAD"])
    async def health_check():
        return {"status": "healthy", "message": "Server is running"}

    app.include_router(ws_router)
    app.include_router(rest_router)
    app.include_router(auth_router)
    app.include_router(realtime_stt_router)
    app.include_router(subscription_router)
    app.include_router(promotion_router)

    # -------------------------------
    # Startup & Shutdown events
    # -------------------------------
    @app.on_event("startup")
    async def startup_event():
        pool = await init_db_pool()  # prints ✅ Neon PostgreSQL DB connected successfully!
        await run_db_migrations(pool)
        await sync_plan_stripe_ids_from_env(pool)

    @app.on_event("shutdown")
    async def shutdown_event():
        await drain_token_usage_updates()
        await close_db_pool()  # prints ℹ️ Neon PostgreSQL DB pool closed

    return app
