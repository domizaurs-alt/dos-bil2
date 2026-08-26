import json
import os
import random
from collections import Counter

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response


TARGET_BASE_URL = os.getenv("TARGET_BASE_URL", "http://127.0.0.1:8080").rstrip("/")
REQUEST_TIMEOUT_SECONDS = float(os.getenv("REQUEST_TIMEOUT_SECONDS", "30.0"))
SIMULATED_IP_HEADER = os.getenv("SIMULATED_IP_HEADER", "X-Simulated-Source-IP")
SOURCE_IP_MODE = os.getenv("SOURCE_IP_MODE", "simulated_header")
RESERVE_FIRST_SOURCE_IP_FOR_OBSERVER = os.getenv("RESERVE_FIRST_SOURCE_IP_FOR_OBSERVER", "true").lower() == "true"
SIMULATED_SOURCE_IPS = [
    value.strip() for value in os.getenv("SIMULATED_SOURCE_IPS", "").split(",") if value.strip()
]
SIMULATED_SOURCE_IP_WEIGHTS = json.loads(os.getenv("SIMULATED_SOURCE_IP_WEIGHTS", "{}"))
REAL_SOURCE_IPS = [
    value.strip() for value in os.getenv("REAL_SOURCE_IPS", "").split(",") if value.strip()
]
REAL_SOURCE_IP_WEIGHTS = json.loads(os.getenv("REAL_SOURCE_IP_WEIGHTS", "{}"))


def weighted_choices(ips: list[str], weights: dict[str, int]) -> list[str]:
    return [ip for ip in ips for _ in range(max(int(weights.get(ip, 1)), 0))]


def observer_source_ip(ips: list[str]) -> str | None:
    if RESERVE_FIRST_SOURCE_IP_FOR_OBSERVER and ips:
        return ips[0]
    return None


def traffic_source_ips(ips: list[str]) -> list[str]:
    if RESERVE_FIRST_SOURCE_IP_FOR_OBSERVER and len(ips) > 1:
        return ips[1:]
    return ips


SIMULATED_TRAFFIC_SOURCE_IPS = traffic_source_ips(SIMULATED_SOURCE_IPS)
REAL_TRAFFIC_SOURCE_IPS = traffic_source_ips(REAL_SOURCE_IPS)
SIMULATED_OBSERVER_SOURCE_IP = observer_source_ip(SIMULATED_SOURCE_IPS)
REAL_OBSERVER_SOURCE_IP = observer_source_ip(REAL_SOURCE_IPS)
SIMULATED_SOURCE_IP_CHOICES = weighted_choices(SIMULATED_TRAFFIC_SOURCE_IPS, SIMULATED_SOURCE_IP_WEIGHTS)
REAL_SOURCE_IP_CHOICES = weighted_choices(REAL_TRAFFIC_SOURCE_IPS, REAL_SOURCE_IP_WEIGHTS)

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

metrics = {
    "forwarded_requests": 0,
    "forward_errors": 0,
    "selected_ip_counts": Counter(),
    "status_code_counts": Counter(),
    "error_counts": Counter(),
}

app = FastAPI(title="Traffic Spreader", version="0.1.0")


def configured_source_ips() -> list[str]:
    if SOURCE_IP_MODE == "real_bind":
        return REAL_TRAFFIC_SOURCE_IPS
    if SOURCE_IP_MODE == "simulated_header":
        return SIMULATED_TRAFFIC_SOURCE_IPS
    return []


def configured_source_ip_weights() -> dict[str, int]:
    if SOURCE_IP_MODE == "real_bind":
        return REAL_SOURCE_IP_WEIGHTS
    if SOURCE_IP_MODE == "simulated_header":
        return SIMULATED_SOURCE_IP_WEIGHTS
    return {}


def selected_source_ip() -> str | None:
    if SOURCE_IP_MODE == "real_bind" and REAL_SOURCE_IP_CHOICES:
        source_ip = random.choice(REAL_SOURCE_IP_CHOICES)
    elif SOURCE_IP_MODE == "simulated_header" and SIMULATED_SOURCE_IP_CHOICES:
        source_ip = random.choice(SIMULATED_SOURCE_IP_CHOICES)
    else:
        return None

    metrics["selected_ip_counts"][source_ip] += 1
    return source_ip


def client_kwargs(source_ip: str | None) -> dict[str, object]:
    kwargs: dict[str, object] = {"timeout": REQUEST_TIMEOUT_SECONDS, "follow_redirects": False}
    if SOURCE_IP_MODE == "real_bind" and source_ip:
        kwargs["transport"] = httpx.AsyncHTTPTransport(local_address=source_ip)
    return kwargs


def filtered_headers(headers: httpx.Headers | dict[str, str]) -> dict[str, str]:
    blocked_headers = HOP_BY_HOP_HEADERS | {SIMULATED_IP_HEADER.lower()}
    return {key: value for key, value in headers.items() if key.lower() not in blocked_headers}


def metrics_payload() -> dict[str, object]:
    reserved_observer_ip = REAL_OBSERVER_SOURCE_IP if SOURCE_IP_MODE == "real_bind" else SIMULATED_OBSERVER_SOURCE_IP
    return {
        "target_base_url": TARGET_BASE_URL,
        "source_ip_mode": SOURCE_IP_MODE,
        "simulated_ip_header": SIMULATED_IP_HEADER,
        "reserve_first_source_ip_for_observer": RESERVE_FIRST_SOURCE_IP_FOR_OBSERVER,
        "reserved_observer_source_ip": reserved_observer_ip,
        "configured_ips": configured_source_ips(),
        "configured_ip_weights": configured_source_ip_weights(),
        "forwarded_requests": metrics["forwarded_requests"],
        "forward_errors": metrics["forward_errors"],
        "selected_ip_counts": dict(sorted(metrics["selected_ip_counts"].items())),
        "status_code_counts": dict(sorted(metrics["status_code_counts"].items())),
        "error_counts": dict(sorted(metrics["error_counts"].items())),
    }


@app.get("/spreader_healthcheck")
async def spreader_healthcheck() -> dict[str, object]:
    return {"status": "ok", **metrics_payload()}


@app.get("/spreader_metrics")
async def spreader_metrics() -> dict[str, object]:
    return metrics_payload()


@app.api_route("/{path:path}", methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"])
async def spread(path: str, request: Request) -> Response:
    body = await request.body()
    source_ip = selected_source_ip()
    target_url = f"{TARGET_BASE_URL}/{path}"
    if request.url.query:
        target_url = f"{target_url}?{request.url.query}"

    headers = filtered_headers(request.headers)
    if SOURCE_IP_MODE == "simulated_header" and source_ip:
        headers[SIMULATED_IP_HEADER] = source_ip

    try:
        async with httpx.AsyncClient(**client_kwargs(source_ip)) as client:
            upstream_response = await client.request(
                method=request.method,
                url=target_url,
                content=body,
                headers=headers,
            )
    except httpx.HTTPError as exc:
        metrics["forward_errors"] += 1
        metrics["error_counts"][type(exc).__name__] += 1
        return JSONResponse(
            status_code=502,
            content={"detail": "traffic spreader upstream error", "error_type": type(exc).__name__},
        )

    metrics["forwarded_requests"] += 1
    metrics["status_code_counts"][str(upstream_response.status_code)] += 1
    response_headers = filtered_headers(upstream_response.headers)
    if source_ip:
        response_headers["x-traffic-spreader-source-ip"] = source_ip
    return Response(
        content=upstream_response.content,
        status_code=upstream_response.status_code,
        headers=response_headers,
        media_type=upstream_response.headers.get("content-type"),
    )
