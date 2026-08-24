import argparse
import csv
import json
import os
import shutil
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from ipaddress import ip_address
from pathlib import Path
from urllib.parse import urlparse

from generate_report import generate_report


BASE_DIR = Path(__file__).resolve().parent


def bool_env(name: str, default: bool = False) -> bool:
    return os.getenv(name, str(default)).lower() in {"1", "true", "yes", "on"}


def load_config(path: Path) -> dict:
    with path.open("r", encoding="utf-8") as file:
        return json.load(file)


def attack_mode(config: dict) -> str:
    return "fire_and_forget" if config.get("fire_and_forget", False) else "measured"


def is_local_or_private_target(url: str) -> bool:
    hostname = urlparse(url).hostname
    if not hostname:
        return False

    if hostname in {"localhost", "127.0.0.1", "::1"}:
        return True

    try:
        addresses = {info[4][0] for info in socket.getaddrinfo(hostname, None)}
    except socket.gaierror:
        return False

    for value in addresses:
        parsed = ip_address(value)
        if parsed.is_loopback or parsed.is_private:
            return True

    return False


def build_results_dir(results_root: Path) -> Path:
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    results_dir = results_root / timestamp
    results_dir.mkdir(parents=True, exist_ok=False)
    return results_dir


def resolve_results_root(config: dict) -> Path:
    configured = Path(config.get("results_dir", "results"))
    if configured.is_absolute():
        return configured
    return BASE_DIR / configured


def fetch_json(url: str | None) -> dict:
    if not url:
        return {}

    try:
        with urllib.request.urlopen(url, timeout=3) as response:
            payload = json.loads(response.read().decode("utf-8"))
            return payload if isinstance(payload, dict) else {}
    except (OSError, urllib.error.URLError, json.JSONDecodeError):
        return {}


def observer_settings() -> dict[str, str | float | bool]:
    return {
        "enabled": bool_env("OBSERVER_ENABLED", False),
        "target_url": os.getenv("OBSERVER_TARGET_URL", ""),
        "interval_seconds": float(os.getenv("OBSERVER_INTERVAL_SECONDS", "1.0")),
        "timeout_seconds": float(os.getenv("OBSERVER_TIMEOUT_SECONDS", "2.0")),
    }


def observer_loop(results_dir: Path, stop_event: threading.Event) -> None:
    settings = observer_settings()
    target_url = str(settings["target_url"])
    if not settings["enabled"] or not target_url:
        return

    interval_seconds = float(settings["interval_seconds"])
    timeout_seconds = float(settings["timeout_seconds"])
    observer_path = results_dir / "observer.csv"
    with observer_path.open("w", encoding="utf-8", newline="") as file:
        writer = csv.DictWriter(
            file,
            fieldnames=["timestamp", "target_url", "ok", "status_code", "response_time_ms", "error"],
        )
        writer.writeheader()
        while not stop_event.is_set():
            started = time.perf_counter()
            status_code = ""
            error = ""
            ok = False
            try:
                request = urllib.request.Request(target_url, headers={"User-Agent": "ddos-bil-observer/0.1"})
                with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
                    status_code = str(response.status)
                    response.read()
                    ok = 200 <= response.status < 400
            except urllib.error.HTTPError as exc:
                status_code = str(exc.code)
                error = type(exc).__name__
            except (OSError, urllib.error.URLError) as exc:
                error = type(exc).__name__

            writer.writerow(
                {
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "target_url": target_url,
                    "ok": str(ok).lower(),
                    "status_code": status_code,
                    "response_time_ms": round((time.perf_counter() - started) * 1000, 3),
                    "error": error,
                }
            )
            file.flush()
            stop_event.wait(interval_seconds)


def start_observer(results_dir: Path) -> tuple[threading.Event, threading.Thread | None]:
    settings = observer_settings()
    stop_event = threading.Event()
    if not settings["enabled"] or not settings["target_url"]:
        return stop_event, None

    thread = threading.Thread(target=observer_loop, args=(results_dir, stop_event), daemon=True)
    thread.start()
    return stop_event, thread


def dict_delta(before: dict, after: dict, key: str) -> dict[str, int]:
    before = before if isinstance(before, dict) else {}
    after = after if isinstance(after, dict) else {}
    before_values = before.get(key, {}) if isinstance(before.get(key, {}), dict) else {}
    after_values = after.get(key, {}) if isinstance(after.get(key, {}), dict) else {}
    result = {}
    for item_key, after_value in after_values.items():
        before_value = before_values.get(item_key, 0)
        if isinstance(after_value, int) and isinstance(before_value, int):
            delta = after_value - before_value
            if delta:
                result[item_key] = delta
    return dict(sorted(result.items()))


def int_delta(before: dict, after: dict, key: str) -> int:
    before = before if isinstance(before, dict) else {}
    after = after if isinstance(after, dict) else {}
    before_value = before.get(key, 0)
    after_value = after.get(key, 0)
    if isinstance(before_value, int) and isinstance(after_value, int):
        return after_value - before_value
    return 0


def merge_spreader_metrics(results_dir: Path, config: dict, before: dict, after: dict) -> None:
    before = before if isinstance(before, dict) else {}
    after = after if isinstance(after, dict) else {}
    source_ips_path = results_dir / "source_ips.json"
    if source_ips_path.exists():
        with source_ips_path.open("r", encoding="utf-8") as file:
            report = json.load(file)
    else:
        report = {"attack_mode": attack_mode(config)}

    selected_ip_counts = dict_delta(before, after, "selected_ip_counts")
    report.update(
        {
            "traffic_spreader_enabled": bool(after),
            "configured_ips": after.get("configured_ips", []),
            "configured_ip_weights": after.get("configured_ip_weights", {}),
            "used_ip_count": len(selected_ip_counts),
            "total_requests_with_simulated_ip": sum(selected_ip_counts.values()),
            "ip_counts": selected_ip_counts,
            "spreader_status_code_counts": dict_delta(before, after, "status_code_counts"),
            "spreader_error_counts": dict_delta(before, after, "error_counts"),
            "spreader_forwarded_requests": int_delta(before, after, "forwarded_requests"),
            "spreader_forward_errors": int_delta(before, after, "forward_errors"),
        }
    )
    source_ips_path.write_text(json.dumps(report, indent=2), encoding="utf-8")


def run_locust(config: dict, results_dir: Path, args: argparse.Namespace) -> int:
    locust_path = shutil.which("locust")
    if not locust_path:
        raise RuntimeError("Locust executable not found. Install attacker requirements first.")

    env = os.environ.copy()
    env.update(
        {
            "ATTACK_MODE": attack_mode(config),
            "WAIT_TIME_MIN_SECONDS": str(config.get("wait_time_min_seconds", 0.1)),
            "WAIT_TIME_MAX_SECONDS": str(config.get("wait_time_max_seconds", 1.0)),
            "RESULTS_DIR": str(results_dir),
            "QUICK_RESPONSE_WEIGHT": str(config.get("quick_response_weight", 10)),
            "LONG_RESPONSE_WEIGHT": str(config.get("long_response_weight", 2)),
            "DOWNLOAD_FILE_WEIGHT": str(config.get("download_file_weight", 1)),
            "HEALTHCHECK_WEIGHT": str(config.get("healthcheck_weight", 1)),
            "FIRE_AND_FORGET_CONNECT_TIMEOUT_SECONDS": str(config.get("fire_and_forget_connect_timeout_seconds", 1.0)),
            "FIRE_AND_FORGET_SEND_TIMEOUT_SECONDS": str(config.get("fire_and_forget_send_timeout_seconds", 1.0)),
            "FIRE_AND_FORGET_RPS_PER_USER": str(config.get("fire_and_forget_rps_per_user", 0)),
        }
    )

    csv_prefix = results_dir / "locust"
    command = [
        locust_path,
        "-f",
        str(BASE_DIR / "locustfile.py"),
        "--host",
        config["target_url"],
        "--csv",
        str(csv_prefix),
        "--html",
        str(results_dir / "locust-report.html"),
    ]

    if args.web_ui:
        command.extend(
            [
                "--web-host",
                args.web_host,
                "--web-port",
                str(args.web_port),
            ]
        )
        if args.autostart:
            command.extend(
                [
                    "--autostart",
                    "-u",
                    str(config.get("users", 10)),
                    "-r",
                    str(config.get("spawn_rate", 2)),
                    "--run-time",
                    str(config.get("run_time", "30s")),
                ]
            )
            if args.autoquit is not None:
                command.extend(["--autoquit", str(args.autoquit)])
        if args.print_stats:
            command.append("--print-stats")
    else:
        command.extend(
            [
                "--headless",
                "-u",
                str(config.get("users", 10)),
                "-r",
                str(config.get("spawn_rate", 2)),
                "--run-time",
                str(config.get("run_time", "30s")),
                "--only-summary",
            ]
        )

    process = subprocess.run(command, cwd=BASE_DIR, env=env, check=False)
    return process.returncode


def main() -> int:
    parser = argparse.ArgumentParser(description="Run a local Locust load test.")
    parser.add_argument(
        "--config",
        default=str(BASE_DIR / "config" / "attack.json"),
        help="Path to attack configuration JSON.",
    )
    parser.add_argument("--web-ui", action="store_true", help="Run Locust with the web UI instead of headless mode.")
    parser.add_argument("--web-host", default="0.0.0.0", help="Locust web UI bind host.")
    parser.add_argument("--web-port", type=int, default=8089, help="Locust web UI port.")
    parser.add_argument("--autostart", action="store_true", help="Start the configured test automatically when UI starts.")
    parser.add_argument("--autoquit", type=int, help="Quit Locust this many seconds after an autostarted run finishes.")
    parser.add_argument("--print-stats", action="store_true", help="Print periodic stats while running with the UI.")
    args = parser.parse_args()

    config_path = Path(args.config).resolve()
    config = load_config(config_path)
    target_url = config["target_url"]

    if not is_local_or_private_target(target_url) and not config.get("allow_non_local_target", False):
        print(
            "Refusing to run against a non-local target without allow_non_local_target=true.",
            file=sys.stderr,
        )
        return 2

    results_root = resolve_results_root(config).resolve()
    results_dir = build_results_dir(results_root)

    with (results_dir / "config.json").open("w", encoding="utf-8") as file:
        json.dump(config, file, indent=2)

    spreader_metrics_url = config.get("spreader_metrics_url")
    spreader_before = fetch_json(spreader_metrics_url)
    observer_stop, observer_thread = start_observer(results_dir)
    try:
        return_code = run_locust(config, results_dir, args)
    finally:
        observer_stop.set()
        if observer_thread:
            observer_thread.join(timeout=5)
    spreader_after = fetch_json(spreader_metrics_url)
    try:
        merge_spreader_metrics(results_dir, config, spreader_before, spreader_after)
    except Exception as exc:
        print(f"Warning: failed to merge traffic spreader metrics: {type(exc).__name__}: {exc}", file=sys.stderr)
    report_path = generate_report(results_dir)
    print(f"Results directory: {results_dir}")
    print(f"Report: {report_path}")
    return return_code


if __name__ == "__main__":
    raise SystemExit(main())
