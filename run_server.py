import uvicorn
from app.server import create_app

app = create_app()

if __name__ == "__main__":
    port = int(os.getenv("PORT", 8000))  # Use Render's PORT, fallback to 8000 for local dev
    uvicorn.run(app, host="0.0.0.0", port=8000)