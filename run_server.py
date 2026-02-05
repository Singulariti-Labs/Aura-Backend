import uvicorn
import os
import logging
from app.server import create_app   

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)

# Set specific log levels for different modules
logging.getLogger("app.api.websocket_routes").setLevel(logging.INFO)
logging.getLogger("app.api.auth_routes").setLevel(logging.INFO)

# Reduce noise from uvicorn access logs (optional)
logging.getLogger("uvicorn.access").setLevel(logging.WARNING)

app = create_app()

if __name__ == "__main__":
    port = int(os.getenv("PORT", 8000))  # Use Render's PORT, fallback to 8000 for local dev
    uvicorn.run(app, host="0.0.0.0", port=port)