import json
import os
import random
import socket
import ssl
import time
from collections import Counter
from pathlib import Path
from urllib.parse import urlparse

from locust import User, between, constant_throughput, events, task
from locust.contrib.fasthttp import FastHttpUser


ATTACK_MODE = os.getenv("ATTACK_MODE", "measured")
WAIT_TIME_MIN_SECONDS = float(os.getenv("WAIT_TIME_MIN_SECONDS", "0.1"))
WAIT_TIME_MAX_SECONDS = float(os.getenv("WAIT_TIME_MAX_SECONDS", "1.0"))
RESULTS_DIR = os.getenv("RESULTS_DIR")
ENDPOINT_WEIGHTS = {
    "/quick_response": int(os.getenv("QUICK_RESPONSE_WEIGHT", "10")),
    "/long_response": int(os.getenv("LONG_RESPONSE_WEIGHT", "2")),
    "/download_file": int(os.getenv("DOWNLOAD_FILE_WEIGHT", "1")),
    "/healthcheck": int(os.getenv("HEALTHCHECK_WEIGHT", "1")),
}
ENDPOINT_CHOICES = [path for path, weight in ENDPOINT_WEIGHTS.items() for _ in range(max(weight, 0))]
FIRE_AND_FORGET_CONNECT_TIMEOUT_SECONDS = float(os.getenv("FIRE_AND_FORGET_CONNECT_TIMEOUT_SECONDS", "1.0"))
FIRE_AND_FORGET_SEND_TIMEOUT_SECONDS = float(os.getenv("FIRE_AND_FORGET_SEND_TIMEOUT_SECONDS", "1.0"))
FIRE_AND_FORGET_RPS_PER_USER = float(os.getenv("FIRE_AND_FORGET_RPS_PER_USER", "0"))
status_code_counts: Counter[str] = Counter()


@events.quitting.add_listener
def write_source_ip_report(environment, **kwargs) -> None:
    if not RESULTS_DIR:
        return

    report = {
        "attack_mode": ATTACK_MODE,
        "status_code_counts": dict(sorted(status_code_counts.items())),
    }
    Path(RESULTS_DIR, "source_ips.json").write_text(json.dumps(report, indent=2), encoding="utf-8")


def request_path(path: str) -> str:
    return path if path.startswith("/") else f"/{path}"


def host_header(parsed_url) -> str:
    if parsed_url.port:
        return f"{parsed_url.hostname}:{parsed_url.port}"
    return parsed_url.hostname or ""


def fire_and_forget(host: str, path: str) -> None:
    parsed_url = urlparse(host)
    scheme = parsed_url.scheme.lower()
    if scheme not in {"http", "https"}:
        raise ValueError("Only http and https targets are supported")

    port = parsed_url.port or (443 if scheme == "https" else 80)
    sock = socket.create_connection(
        (parsed_url.hostname, port),
        timeout=FIRE_AND_FORGET_CONNECT_TIMEOUT_SECONDS,
    )
    sock.settimeout(FIRE_AND_FORGET_SEND_TIMEOUT_SECONDS)

    if scheme == "https":
        context = ssl.create_default_context()
        sock = context.wrap_socket(sock, server_hostname=parsed_url.hostname)

    headers = [
        f"GET {request_path(path)} HTTP/1.1",
        f"Host: {host_header(parsed_url)}",
        "User-Agent: ddos-bil-locust-fire-and-forget/0.1",
        "Accept: */*",
        "Connection: close",
    ]
    try:
        sock.sendall(("\r\n".join(headers) + "\r\n\r\n").encode("ascii"))
    finally:
        sock.close()


class TestClientUser(FastHttpUser):
    abstract = ATTACK_MODE != "measured"
    wait_time = between(WAIT_TIME_MIN_SECONDS, WAIT_TIME_MAX_SECONDS)

    @task(1)
    def request_endpoint(self) -> None:
        path = random.choice(ENDPOINT_CHOICES or ["/quick_response"])
        self.get(path)

    def get(self, path: str) -> None:
        with self.client.get(path, name=path, catch_response=True) as response:
            status_code_counts[str(response.status_code)] += 1
            if response.status_code >= 400:
                response.failure(f"HTTP {response.status_code}")


class FireAndForgetUser(User):
    abstract = ATTACK_MODE != "fire_and_forget"
    wait_time = (
        constant_throughput(FIRE_AND_FORGET_RPS_PER_USER)
        if FIRE_AND_FORGET_RPS_PER_USER > 0
        else between(WAIT_TIME_MIN_SECONDS, WAIT_TIME_MAX_SECONDS)
    )

    @task(1)
    def request_endpoint(self) -> None:
        path = random.choice(ENDPOINT_CHOICES or ["/long_response"])
        start_time = time.time()
        started_at = time.perf_counter()
        exception = None
        try:
            fire_and_forget(self.host, path)
            status_code_counts["sent_without_response"] += 1
        except Exception as exc:
            exception = exc
            status_code_counts[f"fire_and_forget_error:{type(exc).__name__}"] += 1

        self.environment.events.request.fire(
            request_type="FIRE_FORGET",
            name=path,
            response_time=(time.perf_counter() - started_at) * 1000,
            response_length=0,
            exception=exception,
            context={},
            start_time=start_time,
            url=f"{self.host}{path}",
        )
