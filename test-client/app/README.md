# Test Client Service

FastAPI service used as the local target for Phase 1.

## Endpoints

- `GET /healthcheck` returns service health.
- `GET /quick_response` returns a small response immediately.
- `GET /long_response` delays the response and can allocate memory based on server configuration.
- `GET /download_file` streams a generated binary file based on server configuration.

The attacker sends normal requests. Endpoint behavior is configured on the server side.

## Configuration

Docker uses `test-client/config/client.env`. Edit that file to change default behavior.

Environment variables:

- `LONG_RESPONSE_DELAY_MS` controls the delay in `/long_response`.
- `LONG_RESPONSE_MEMORY_MB` controls memory allocation in `/long_response`.
- `DOWNLOAD_FILE_SIZE_KB` controls the generated file size in `/download_file`.
- `DOWNLOAD_CHUNK_KB` controls the streaming chunk size.

## Local Run

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
LONG_RESPONSE_DELAY_MS=3000 LONG_RESPONSE_MEMORY_MB=16 uvicorn main:app --host 127.0.0.1 --port 8000
```

If you do not set environment variables, the service uses built-in defaults.

Open `http://127.0.0.1:8000/docs` to inspect the API.

## Docker

Build and run through the client Compose file from the repository root:

```bash
docker compose --env-file test-client/config/client.env -f docker-compose.client.yml up --build
```

The service is reachable only inside the client compose network as `http://test-client-app:8000`. The reverse proxy is the host-published entry point.

The Compose service has resource limits by default:

- `APP__CPUS=0.50`
- `APP__MEMORY_LIMIT=256m`
- `APP__MEMORY_SWAP_LIMIT=256m`

The container uses `restart: unless-stopped`, so OOM or process crashes are visible as restarts.

For pressure tests against `/long_response`, configure server-side behavior before starting Compose:

```bash
docker compose --env-file test-client/config/client.env -f docker-compose.client.yml up --build
```

These values are server settings. The attacker still sends a normal `GET /long_response` request.

To make failure scenarios easier to reproduce, combine stricter resource limits with a heavier endpoint profile:

```bash
APP__MEMORY_LIMIT=2500m APP__MEMORY_SWAP_LIMIT=2500m APP__CPUS=2 APP__LONG_RESPONSE_DELAY_MS=20000 APP__LONG_RESPONSE_MEMORY_MB=16 docker compose --env-file test-client/config/client.env -f docker-compose.client.yml up --build
```
