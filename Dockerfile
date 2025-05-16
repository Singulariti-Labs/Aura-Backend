FROM python:3.12-slim

WORKDIR /app

# Install dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY ./app ./app
COPY ./websocket ./websocket
COPY main.py .

# Ensure the PYTHONPATH includes both app and websocket directories
ENV PYTHONPATH=/app

# Expose ports for websocket server and metrics
EXPOSE 8765 8000

# Run the application with the new main entry point
CMD ["python", "main.py"]