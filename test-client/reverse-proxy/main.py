import os
import time
from collections import defaultdict, deque
from ipaddress import ip_address
from typing import Deque

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response


TARGET_BASE_URL = os.getenv("TARGET_BASE_URL", "http://127.0.0.1:8000").rstrip("/")
DDOS_PROTECTION_ENABLED = os.getenv("DDOS_PROTECTION_ENABLED", "true").lower() == "true"
TRUST_SIMULATED_IP = os.getenv("TRUST_SIMULATED_IP", "true").lower() == "true"
TRUST_FORWARDED_FOR = os.getenv("TRUST_FORWARDED_FOR", "false").lower() == "true"
RATE_LIMIT_REQUESTS = int(os.getenv("RATE_LIMIT_REQUESTS", "20"))
RATE_LIMIT_WINDOW_SECONDS = float(os.getenv("RATE_LIMIT_WINDOW_SECONDS", "1.0"))
BLOCK_DURATION_SECONDS = float(os.getenv("BLOCK_DURATION_SECONDS", "30.0"))
REQUEST_TIMEOUT_SECONDS = float(os.getenv("REQUEST_TIMEOUT_SECONDS", "30.0"))
SIMULATED_IP_HEADER = "x-simulated-source-ip"

HOP_BY_HOP_HEADERS = {
    "connection",
    "keep-alive",
    "proxy-authenticate",
    "proxy-authorization",
    "te",
    "trailers",
    "transfer-encoding",
    "upgrade",
    "host",
}


app = FastAPI(title="Test Client Reverse Proxy", version="0.1.0")
request_windows: defaultdict[str, Deque[float]] = defaultdict(deque)
blocked_until: dict[str, float] = {}
metrics = {"forwarded_requests": 0, "blocked_requests": 0, "forward_errors": 0}


def source_ip(request: Request) -> str:
    simulated_ip = request.headers.get(SIMULATED_IP_HEADER)
    if TRUST_SIMULATED_IP and simulated_ip:
        try:
            return str(ip_address(simulated_ip))
        except ValueError:
            return "invalid-simulated-ip"

    forwarded_for = request.headers.get("x-forwarded-for")
    if TRUST_FORWARDED_FOR and forwarded_for:
        forwarded_ip = forwarded_for.split(",", 1)[0].strip()
        try:
            return str(ip_address(forwarded_ip))
        except ValueError:
            return "invalid-forwarded-for-ip"

    return request.client.host if request.client else "unknown"


def is_blocked(ip: str, now: float) -> bool:
    return blocked_until.get(ip, 0) > now


def should_block(ip: str, now: float) -> bool:
    window = request_windows[ip]
    while window and now - window[0] > RATE_LIMIT_WINDOW_SECONDS:
        window.popleft()

    window.append(now)
    if len(window) > RATE_LIMIT_REQUESTS:
        blocked_until[ip] = now + BLOCK_DURATION_SECONDS
        return True

    return False


def filtered_headers(headers: httpx.Headers | dict[str, str]) -> dict[str, str]:
    return {key: value for key, value in headers.items() if key.lower() not in HOP_BY_HOP_HEADERS}


@app.get("/proxy_healthcheck")
async def proxy_healthcheck() -> dict[str, str | int | float | bool]:
    return {
        "status": "ok",
        "target_base_url": TARGET_BASE_URL,
        "ddos_protection_enabled": DDOS_PROTECTION_ENABLED,
        "rate_limit_requests": RATE_LIMIT_REQUESTS,
        "rate_limit_window_seconds": RATE_LIMIT_WINDOW_SECONDS,
        "block_duration_seconds": BLOCK_DURATION_SECONDS,
        "trust_simulated_ip": TRUST_SIMULATED_IP,
        "trust_forwarded_for": TRUST_FORWARDED_FOR,
    }


@app.get("/proxy_metrics")
async def proxy_metrics() -> dict[str, object]:
    now = time.monotonic()
    active_blocks = {ip: round(until - now, 3) for ip, until in blocked_until.items() if until > now}
    return {**metrics, "active_blocks": active_blocks}


@app.api_route("/{path:path}", methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"])
async def proxy(path: str, request: Request) -> Response:
    ip = source_ip(request)
    now = time.monotonic()

    if DDOS_PROTECTION_ENABLED and (is_blocked(ip, now) or should_block(ip, now)):
        metrics["blocked_requests"] += 1
        retry_after = max(0, int(blocked_until.get(ip, now) - now))
        return JSONResponse(
            status_code=429,
            content={"detail": "source IP rate limit exceeded", "source_ip": ip, "retry_after": retry_after},
            headers={"Retry-After": str(retry_after)},
        )

    body = await request.body()
    target_url = f"{TARGET_BASE_URL}/{path}"
    if request.url.query:
        target_url = f"{target_url}?{request.url.query}"

    try:
        async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT_SECONDS, follow_redirects=False) as client:
            upstream_response = await client.request(
                method=request.method,
                url=target_url,
                content=body,
                headers=filtered_headers(request.headers),
            )
    except httpx.HTTPError as exc:
        metrics["forward_errors"] += 1
        return JSONResponse(
            status_code=502,
            content={
                "detail": "reverse proxy upstream error",
                "upstream": TARGET_BASE_URL,
                "error_type": type(exc).__name__,
            },
        )

    metrics["forwarded_requests"] += 1
    response_headers = filtered_headers(upstream_response.headers)
    response_headers["x-proxy-source-ip"] = ip
    return Response(
        content=upstream_response.content,
        status_code=upstream_response.status_code,
        headers=response_headers,
        media_type=upstream_response.headers.get("content-type"),
    )
