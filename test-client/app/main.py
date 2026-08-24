import asyncio
import os
import time
from typing import Iterator

from fastapi import FastAPI
from fastapi.responses import StreamingResponse


def int_env(name: str, default: int, minimum: int, maximum: int) -> int:
    value = int(os.getenv(name, str(default)))
    if value < minimum or value > maximum:
        raise ValueError(f"{name} must be between {minimum} and {maximum}")
    return value


LONG_RESPONSE_DELAY_MS = int_env("LONG_RESPONSE_DELAY_MS", 500, 0, 30_000)
LONG_RESPONSE_MEMORY_MB = int_env("LONG_RESPONSE_MEMORY_MB", 0, 0, 512)
DOWNLOAD_FILE_SIZE_KB = int_env("DOWNLOAD_FILE_SIZE_KB", 256, 1, 102_400)
DOWNLOAD_CHUNK_KB = int_env("DOWNLOAD_CHUNK_KB", 64, 1, 1024)

app = FastAPI(title="Test Client Service", version="0.1.0")


@app.get("/healthcheck")
async def healthcheck() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/quick_response")
async def quick_response() -> dict[str, float | str]:
    return {"status": "ok", "endpoint": "quick_response", "timestamp": time.time()}


@app.get("/long_response")
async def long_response() -> dict[str, int | str]:
    buffer = bytearray(LONG_RESPONSE_MEMORY_MB * 1024 * 1024) if LONG_RESPONSE_MEMORY_MB else None
    if buffer:
        buffer[0] = 1

    await asyncio.sleep(LONG_RESPONSE_DELAY_MS / 1000)

    return {
        "status": "ok",
        "endpoint": "long_response",
        "delay_ms": LONG_RESPONSE_DELAY_MS,
        "memory_mb": LONG_RESPONSE_MEMORY_MB,
    }


@app.get("/download_file")
async def download_file() -> StreamingResponse:
    total_bytes = DOWNLOAD_FILE_SIZE_KB * 1024
    chunk_size = DOWNLOAD_CHUNK_KB * 1024

    def stream() -> Iterator[bytes]:
        remaining = total_bytes
        chunk = b"0" * min(chunk_size, total_bytes)
        while remaining > 0:
            current_size = min(len(chunk), remaining)
            yield chunk[:current_size]
            remaining -= current_size

    headers = {"Content-Disposition": f'attachment; filename="test-{DOWNLOAD_FILE_SIZE_KB}kb.bin"'}
    return StreamingResponse(stream(), media_type="application/octet-stream", headers=headers)
