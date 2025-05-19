# WebSocket Server with RabbitMQ Integration

A robust, production-ready WebSocket server built with asyncio and RabbitMQ for task queuing. The server handles multiple concurrent client connections, authenticates clients using JWT, and supports message routing based on version and channel.

## Features

- **Asynchronous architecture** using asyncio and websockets
- **JWT authentication** for secure client connections
- **Message routing** based on version and channel
- **RabbitMQ integration** for task queuing
- **Rate limiting** to prevent abuse
- **Structured logging** with structlog
- **Metrics collection** using Prometheus
- **Graceful shutdown** and error handling
- **Connection heartbeats** with ping/pong support
- **Docker support** for easy deployment

## Architecture

The server follows a clean, modular architecture:

```
┌───────────┐     ┌────────────┐     ┌───────────┐
│ WebSocket │     │   Message  │     │  RabbitMQ │
│  Clients  │◄────┤   Router   │◄────┤  Queues   │
└───────────┘     └────────────┘     └───────────┘
      ▲                 ▲                  ▲
      │                 │                  │
      │                 │                  │
      │                 │                  │
      ▼                 ▼                  ▼
┌───────────┐     ┌────────────┐     ┌───────────┐
│    JWT    │     │  Metrics & │     │ Automation│
│   Auth    │     │   Logging  │     │  Workers  │
└───────────┘     └────────────┘     └───────────┘
```

1. **Client Connections**: Clients connect via WebSocket and authenticate with JWT
2. **Message Routing**: Messages are routed based on version and channel
3. **Task Queuing**: Automation tasks are queued in RabbitMQ
4. **Result Handling**: Task results are sent back to the appropriate client

## Installation

### Prerequisites

- Python 3.12+
- RabbitMQ
- Docker (optional)

### Setup

1. Clone the repository:

```bash
git clone https://github.com/singulariti-labs/compute-agent.git
cd websocket-server
```

2. Create and activate a virtual environment:

```bash
python -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate
```

3. Install dependencies:

```bash
pip install -r requirements.txt
```

4. Create a `.env` file with your configuration:

```
HOST=0.0.0.0
PORT=8765
LOG_LEVEL=INFO
METRICS_PORT=8000

# RabbitMQ config
RABBITMQ_HOST=localhost
RABBITMQ_PORT=5672
RABBITMQ_USERNAME=guest
RABBITMQ_PASSWORD=guest

# JWT config (for production, use secure keys)
JWT_ISSUER=your-app-issuer
JWT_AUDIENCE=your-app-audience
JWT_PUBLIC_KEY_1=<your public key here>
```

## Running the Server

### Using Python

```bash
python server.py
```

### Using Docker

```bash
docker-compose up -d
```

This will start:
- WebSocket server on port 8765
- Metrics endpoint on port 8000
- RabbitMQ on port 5672 (management UI on 15672)
- Prometheus on port 9090
- Grafana on port 3000

## Message Format

Clients should send messages in the following JSON format:

```json
{
  "version": "v1",
  "channel": "chat | automation",
  "request_id": "uuid",
  "payload": {
    // Channel-specific data
  }
}
```

## Testing

### Using the Sample Client

The repository includes a sample WebSocket client for testing:

```bash
python client.py --token <your-jwt-token> --channel automation
```

For more options:

```bash
python client.py --help
```

### Creating a JWT Token

For testing, you can create a JWT token using the Python `jwt` library:

```python
import jwt
import time
import uuid

payload = {
    "user_id": "123",
    "username": "testuser",
    "iat": int(time.time()),
    "exp": int(time.time()) + 3600,  # 1 hour
    "iss": "your-app-issuer",
    "aud": "your-app-audience"
}

token = jwt.encode(payload, "your-secret-key", algorithm="HS256")
print(token)
```

## Configuration

| Environment Variable | Description | Default |
|----------------------|-------------|---------|
| HOST | Server host | 127.0.0.1 |
| PORT | WebSocket port | 8765 |
| LOG_LEVEL | Logging level | INFO |
| PING_INTERVAL | WebSocket ping interval (seconds) | 30 |
| PING_TIMEOUT | WebSocket ping timeout (seconds) | 10 |
| HEARTBEAT_INTERVAL | Heartbeat check interval (seconds) | 60 |
| RATE_LIMIT_MAX_MESSAGES | Max messages per time window | 10 |
| RATE_LIMIT_WINDOW_SECONDS | Rate limit time window | 1.0 |
| RABBITMQ_HOST | RabbitMQ host | localhost |
| RABBITMQ_PORT | RabbitMQ port | 5672 |
| RABBITMQ_USERNAME | RabbitMQ username | None |
| RABBITMQ_PASSWORD | RabbitMQ password | None |
| JWT_ISSUER | JWT issuer claim | None |
| JWT_AUDIENCE | JWT audience claim | None |
| METRICS_HOST | Metrics server host | 0.0.0.0 |
| METRICS_PORT | Metrics server port | 8000 |
