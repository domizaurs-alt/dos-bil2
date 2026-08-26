import argparse
import base64
import csv
import html
import json
import os
from datetime import datetime, timezone
from pathlib import Path


BASE_DIR = Path(__file__).resolve().parent
PLOTLY_CDN_URL = "https://cdn.plot.ly/plotly-3.7.0.min.js"
TARGET_LABEL = "Target site"
REPORT_DATE_TEXT = "report_date"
LOGO_PATH = BASE_DIR / "logo-website-file-globe-icon-svg-wikimedia-commons-21.png"


def read_csv(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []

    with path.open("r", encoding="utf-8", newline="") as file:
        return list(csv.DictReader(file))


def read_json(path: Path) -> dict:
    if not path.exists():
        return {}

    with path.open("r", encoding="utf-8") as file:
        payload = json.load(file)
        return payload if isinstance(payload, dict) else {}


def find_latest_results_dir(results_root: Path | None = None) -> Path:
    root = results_root or BASE_DIR / "results"
    candidates: list[Path] = []
    if root.exists():
        for path in root.iterdir():
            if not path.is_dir() or path.name == "generated-reports":
                continue
            has_direct_results = (path / "locust_stats.csv").exists()
            has_manifest = (path / "manifest.json").exists()
            has_scenario_results = any(child.is_dir() and (child / "locust_stats.csv").exists() for child in path.iterdir())
            if has_direct_results or has_manifest or has_scenario_results:
                candidates.append(path)

    if not candidates:
        raise FileNotFoundError(f"No result directories found under {root}")

    return max(candidates, key=lambda path: path.name)


def report_output_path(results_path: Path, filename: str) -> Path:
    target = results_path / filename
    if (target.exists() and os.access(target, os.W_OK)) or (not target.exists() and os.access(results_path, os.W_OK)):
        return target

    fallback_dir = results_path.parent / "generated-reports"
    fallback_dir.mkdir(exist_ok=True)
    return fallback_dir / f"{results_path.name}-{filename}"


def logo_html() -> str:
    if not LOGO_PATH.exists():
        return ""

    encoded = base64.b64encode(LOGO_PATH.read_bytes()).decode("ascii")
    return f'<img class="report-logo" src="data:image/png;base64,{encoded}" alt="Report logo">'


def escape(value: object) -> str:
    return html.escape(str(value), quote=True)


def value(row: dict[str, str], key: str, default: str = "n/a") -> str:
    return row.get(key) or default


def integer_value(row: dict[str, str], key: str) -> int:
    try:
        return int(float(row.get(key, "0") or "0"))
    except (TypeError, ValueError):
        return 0


def numeric_value(row: dict[str, str], key: str) -> float | None:
    raw = row.get(key, "")
    if raw in ("", "N/A", None):
        return None

    try:
        return float(raw)
    except (TypeError, ValueError):
        return None


def parse_epoch(raw: str) -> float | None:
    try:
        return float(raw)
    except (TypeError, ValueError):
        return None


def parse_iso_datetime(raw: str) -> datetime | None:
    if not raw:
        return None

    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None


def format_metric(metric: float | int | None, digits: int = 1, suffix: str = "") -> str:
    if metric is None:
        return "n/a"

    number = float(metric)
    if number.is_integer():
        text = f"{number:,.0f}"
    else:
        text = f"{number:,.{digits}f}"
    return f"{text}{suffix}"


def format_percent(metric: float | None) -> str:
    return format_metric(metric, digits=2, suffix="%")


def percent(part: int | float, total: int | float) -> float | None:
    if not total:
        return None
    return float(part) / float(total) * 100


def format_elapsed(seconds: float | int | None) -> str:
    if seconds is None:
        return "n/a"

    total = int(round(float(seconds)))
    minutes, remaining = divmod(total, 60)
    if minutes:
        return f"{minutes}m {remaining}s"
    return f"{remaining}s"


def scenario_name(name: str) -> str:
    cleaned = name.strip("/").replace("_", " ").strip()
    return cleaned.title() if cleaned else name


def error_label(error: str) -> str:
    if not error:
        return "n/a"
    lowered = error.lower()
    if "HTTP " in error:
        code = error.split("HTTP ", 1)[1].split("'", 1)[0].split(")", 1)[0]
        return status_label(code)
    labels = {
        "HTTPError": "HTTP error",
        "URLError": "Connection error",
        "TimeoutError": "Timeout",
        "ReadTimeout": "Read timeout",
        "ConnectTimeout": "Connection timeout",
    }
    if error in labels:
        return labels[error]
    if "timed out" in lowered or "timeout" in lowered or "retriesexceeded" in lowered:
        return "Timeout"
    return error.replace("_", " ")


def status_label(status: str) -> str:
    labels = {
        "0": "No response",
        "sent_without_response": "Sent without response wait",
        "200": "HTTP 200 OK",
        "201": "HTTP 201 Created",
        "202": "HTTP 202 Accepted",
        "204": "HTTP 204 No Content",
        "301": "HTTP 301 Moved Permanently",
        "302": "HTTP 302 Found",
        "304": "HTTP 304 Not Modified",
        "400": "HTTP 400 Bad Request",
        "401": "HTTP 401 Unauthorized",
        "403": "HTTP 403 Forbidden",
        "404": "HTTP 404 Not Found",
        "408": "HTTP 408 Request Timeout",
        "429": "HTTP 429 Too Many Requests",
        "500": "HTTP 500 Internal Server Error",
        "502": "HTTP 502 Bad Gateway",
        "503": "HTTP 503 Service Unavailable",
        "504": "HTTP 504 Gateway Timeout",
    }
    if status.startswith("fire_and_forget_error:"):
        return error_label(status.split(":", 1)[1])
    return labels.get(status, status)


def status_code_from_label(label: str) -> int | None:
    parts = label.split()
    for part in parts:
        if part.isdigit():
            return int(part)
    return None


def is_error_outcome(label: str) -> bool:
    code = status_code_from_label(label)
    return (code is not None and code >= 400) or "timeout" in label.lower() or label == "No response"


def response_outcome_counts(scenario: dict) -> dict[str, int]:
    outcomes: dict[str, int] = {}
    for status, count in scenario["serviceStatusCounts"].items():
        outcomes[status_label(str(status))] = outcomes.get(status_label(str(status)), 0) + int(count)

    if not outcomes:
        for status, count in scenario["statusCounts"].items():
            outcomes[status_label(str(status))] = outcomes.get(status_label(str(status)), 0) + int(count)

    for error, count in scenario["deliveryErrors"].items():
        label = error_label(str(error))
        outcomes[label] = outcomes.get(label, 0) + int(count)

    return outcomes


def issue_chart_rows(scenario: dict) -> list[dict[str, object]]:
    if scenario["failures"]:
        return [
            {
                "name": scenario_name(row.get("Name", "")),
                "error": error_label(row.get("Error", "")),
                "occurrences": integer_value(row, "Occurrences"),
            }
            for row in scenario["failures"]
        ]

    return [
        {
            "name": scenario_name(row.get("Name", "")),
            "error": scenario["issueBasis"],
            "occurrences": integer_value(row, "Failure Count"),
        }
        for row in scenario["endpoints"]
    ]


def find_aggregate(stats: list[dict[str, str]]) -> dict[str, str]:
    for row in stats:
        if row.get("Name") == "Aggregated":
            return row
    return stats[-1] if stats else {}


def endpoint_rows(stats: list[dict[str, str]]) -> list[dict[str, str]]:
    return [row for row in stats if row.get("Name") != "Aggregated"]


def blocked_request_count(failures: list[dict[str, str]]) -> int:
    total = 0
    for row in failures:
        if "429" in value(row, "Error"):
            total += integer_value(row, "Occurrences")
    return total


def is_send_only_mode(config: dict, source_ips: dict, manifest_entry: dict) -> bool:
    mode = source_ips.get("attack_mode") or manifest_entry.get("mode")
    return bool(config.get("fire_and_forget")) or mode == "fire_and_forget"


def execution_mode_label(send_only: bool) -> str:
    return "Send-only pressure" if send_only else "Measured response"


def metric_basis(send_only: bool) -> str:
    return "Send attempts" if send_only else "Requests"


def issue_basis(send_only: bool) -> str:
    return "Send errors" if send_only else "Failed responses"


def time_basis(send_only: bool) -> str:
    return "Send duration" if send_only else "Response time"


def source_rows(source_counts: dict, configured_weights: dict) -> list[dict[str, object]]:
    rows = []
    for index, source in enumerate(sorted(source_counts), start=1):
        rows.append(
            {
                "label": f"Source {index}",
                "requests": source_counts[source],
                "weight": configured_weights.get(source, "n/a"),
            }
        )
    return rows


def history_rows(history: list[dict[str, str]]) -> list[dict[str, float | None]]:
    aggregate_rows = [row for row in history if row.get("Name") == "Aggregated"]
    timestamps = [parse_epoch(row.get("Timestamp", "")) for row in aggregate_rows]
    valid_timestamps = [timestamp for timestamp in timestamps if timestamp is not None]
    start = min(valid_timestamps) if valid_timestamps else None
    rows = []
    for row, timestamp in zip(aggregate_rows, timestamps):
        elapsed = timestamp - start if timestamp is not None and start is not None else None
        rows.append(
            {
                "elapsedSeconds": elapsed,
                "users": numeric_value(row, "User Count"),
                "rps": numeric_value(row, "Requests/s"),
                "failuresPerSecond": numeric_value(row, "Failures/s"),
                "p50": numeric_value(row, "50%"),
                "p95": numeric_value(row, "95%"),
                "p99": numeric_value(row, "99%"),
                "avg": numeric_value(row, "Total Average Response Time"),
                "totalRequests": numeric_value(row, "Total Request Count"),
                "totalFailures": numeric_value(row, "Total Failure Count"),
            }
        )
    return rows


def duration_from_history(history: list[dict[str, float | None]]) -> float | None:
    values = [row["elapsedSeconds"] for row in history if row.get("elapsedSeconds") is not None]
    if not values:
        return None
    return max(float(value) for value in values)


def max_history_metric(history: list[dict[str, float | None]], key: str) -> float | None:
    values = [row.get(key) for row in history if row.get(key) is not None]
    if not values:
        return None
    return max(float(value) for value in values)


def observer_rows(records: list[dict[str, str]]) -> list[dict[str, object]]:
    timestamps = [parse_iso_datetime(row.get("timestamp", "")) for row in records]
    valid_timestamps = [timestamp for timestamp in timestamps if timestamp is not None]
    start = min(valid_timestamps) if valid_timestamps else None
    rows = []
    for row, timestamp in zip(records, timestamps):
        elapsed = (timestamp - start).total_seconds() if timestamp is not None and start is not None else None
        ok = str(row.get("ok", "")).lower() == "true"
        rows.append(
            {
                "elapsedSeconds": elapsed,
                "healthy": 1 if ok else 0,
                "failed": 0 if ok else 1,
                "status": row.get("status_code") or "n/a",
                "latencyMs": numeric_value(row, "response_time_ms"),
                "error": error_label(row.get("error_detail") or row.get("error", "")),
            }
        )
    return rows


def observer_summary(rows: list[dict[str, object]]) -> dict[str, object]:
    total = len(rows)
    healthy = sum(1 for row in rows if row.get("healthy") == 1)
    failed = total - healthy
    latencies = [float(row["latencyMs"]) for row in rows if row.get("latencyMs") is not None]
    first_failure = next((row for row in rows if row.get("failed") == 1), None)
    return {
        "target": TARGET_LABEL,
        "probes": total,
        "healthy": healthy,
        "failed": failed,
        "availability": percent(healthy, total),
        "averageLatencyMs": sum(latencies) / len(latencies) if latencies else None,
        "firstFailureAfterSeconds": first_failure.get("elapsedSeconds") if first_failure else None,
        "firstFailureStatus": first_failure.get("status") if first_failure else "n/a",
        "firstFailureError": first_failure.get("error") if first_failure else "n/a",
    }


def read_text_file(path: Path, max_lines: int = 80) -> list[str]:
    if not path.exists():
        return []

    try:
        return path.read_text(encoding="utf-8", errors="replace").splitlines()[:max_lines]
    except OSError:
        return []


def execution_log_rows(scenario_path: Path, exceptions: list[dict[str, str]]) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for row in exceptions:
        message = row.get("Message") or row.get("Traceback") or "Exception recorded"
        rows.append(
            {
                "source": "Locust exception",
                "count": row.get("Count") or "1",
                "message": message.replace("\n", " ").strip(),
            }
        )

    for name in ("run.log", "attack.log", "locust.log", "stderr.log", "stdout.log"):
        for line in read_text_file(scenario_path / name):
            cleaned = line.strip()
            if cleaned:
                rows.append({"source": name, "count": "", "message": cleaned})

    return rows[:80]


def test_date_from_history(history: list[dict[str, str]]) -> str | None:
    for row in history:
        timestamp = parse_epoch(row.get("Timestamp", ""))
        if timestamp is not None:
            return datetime.fromtimestamp(timestamp, tz=timezone.utc).date().isoformat()
    return None


def table_html(headers: list[str], rows: list[list[object]], empty_message: str = "No data recorded.") -> str:
    if not rows:
        return f'<p class="muted">{escape(empty_message)}</p>'

    header_html = "".join(f"<th>{escape(header)}</th>" for header in headers)
    rows_html = []
    for row in rows:
        cells = "".join(f"<td>{escape(cell)}</td>" for cell in row)
        rows_html.append(f"<tr>{cells}</tr>")
    return (
        '<div class="table-wrap"><table>'
        f"<thead><tr>{header_html}</tr></thead>"
        f"<tbody>{''.join(rows_html)}</tbody>"
        "</table></div>"
    )


def metric_card(title: str, value_text: str, detail: str) -> str:
    return (
        '<article class="metric-card">'
        f"<span>{escape(title)}</span>"
        f"<strong>{escape(value_text)}</strong>"
        f"<small>{escape(detail)}</small>"
        "</article>"
    )


def section_block(title: str, body: str, extra_class: str = "") -> str:
    class_name = f"section-block {extra_class}".strip()
    return f'<section class="{class_name}"><h2>{escape(title)}</h2>{body}</section>'


def variant_count_label(count: int) -> str:
    return "1 scenario" if count == 1 else f"{count} consecutive scenarios"


def scope_html(scenario_count: int) -> str:
    return (
        "<p>The assessment focused on the observable behavior of the service during the simulation, including traffic generation, service availability, HTTP response behavior, error conditions, workload distribution and the point at which service degradation became visible.</p>"
        "<p>The results are intended to identify resilience gaps within the tested scenario and to provide recommendations for further investigation, remediation and subsequent validation.</p>"
    )


def scenario_config_rows(config: dict, send_only: bool) -> list[list[object]]:
    rows = [
        ["Execution mode", execution_mode_label(send_only)],
        ["Ramp-up rate", f"{config.get('spawn_rate', 'n/a')} per second"],
        ["Configured duration", config.get("run_time", "n/a")],
        ["Minimum wait", f"{config.get('wait_time_min_seconds', 'n/a')} s"],
        ["Maximum wait", f"{config.get('wait_time_max_seconds', 'n/a')} s"],
        ["Long response weight", config.get("long_response_weight", "n/a")],
        ["Short response weight", config.get("quick_response_weight", "n/a")],
        ["Health check weight", config.get("healthcheck_weight", "n/a")],
        ["Download weight", config.get("download_file_weight", "n/a")],
    ]
    if send_only and float(config.get("fire_and_forget_rps_per_user", 0) or 0) > 0:
        users = float(config.get("users", 0) or 0)
        rate = float(config.get("fire_and_forget_rps_per_user", 0) or 0)
        rows.append(["Approximate target send rate", format_metric(users * rate, digits=2)])
    return rows


def discover_scenarios(results_path: Path) -> tuple[dict, list[tuple[Path, dict]]]:
    manifest = read_json(results_path / "manifest.json")
    scenarios = []
    if isinstance(manifest.get("scenarios"), list) and manifest["scenarios"]:
        for entry in manifest["scenarios"]:
            if not isinstance(entry, dict):
                continue
            directory = entry.get("directory")
            if not directory:
                continue
            scenario_path = results_path / str(directory)
            if scenario_path.exists():
                scenarios.append((scenario_path, entry))
    else:
        child_scenarios = [path for path in results_path.iterdir() if path.is_dir() and (path / "locust_stats.csv").exists()] if results_path.exists() else []
        if child_scenarios:
            for index, scenario_path in enumerate(sorted(child_scenarios), start=1):
                scenarios.append((scenario_path, {"order": index, "label": f"Scenario {index}", "directory": scenario_path.name}))
        elif (results_path / "locust_stats.csv").exists():
            scenarios.append((results_path, {"order": 1, "label": "Scenario 1", "directory": "."}))

    return manifest, scenarios


def load_scenario(scenario_path: Path, manifest_entry: dict) -> dict:
    stats = read_csv(scenario_path / "locust_stats.csv")
    history_csv = read_csv(scenario_path / "locust_stats_history.csv")
    failures = read_csv(scenario_path / "locust_failures.csv")
    exceptions = read_csv(scenario_path / "locust_exceptions.csv")
    source_ips = read_json(scenario_path / "source_ips.json")
    config = read_json(scenario_path / "config.json")
    observer = observer_rows(read_csv(scenario_path / "observer.csv"))
    aggregate = find_aggregate(stats)
    endpoints = endpoint_rows(stats)
    send_only = is_send_only_mode(config, source_ips, manifest_entry)
    source_counts = source_ips.get("ip_counts", {}) if isinstance(source_ips.get("ip_counts", {}), dict) else {}
    source_weights = source_ips.get("configured_ip_weights", {}) if isinstance(source_ips.get("configured_ip_weights", {}), dict) else {}
    status_counts = source_ips.get("status_code_counts", {}) if isinstance(source_ips.get("status_code_counts", {}), dict) else {}
    service_status_counts = source_ips.get("spreader_status_code_counts", {}) if isinstance(source_ips.get("spreader_status_code_counts", {}), dict) else {}
    delivery_errors = source_ips.get("spreader_error_counts", {}) if isinstance(source_ips.get("spreader_error_counts", {}), dict) else {}
    history = history_rows(history_csv)
    total_events = integer_value(aggregate, "Request Count")
    total_issues = integer_value(aggregate, "Failure Count")
    scenario_sources = source_rows(source_counts, source_weights)
    protection_429 = blocked_request_count(failures) + int(service_status_counts.get("429", 0) or 0)
    order = int(manifest_entry.get("order") or config.get("scenario_order") or 1)
    scenario_id = str(manifest_entry.get("id") or config.get("id") or f"scenario_{order}")

    return {
        "id": scenario_id,
        "safeId": "".join(character if character.isalnum() else "-" for character in scenario_id.lower()).strip("-") or f"scenario-{order}",
        "order": order,
        "label": manifest_entry.get("label") or config.get("label") or f"Scenario {order}: {execution_mode_label(send_only)}",
        "path": scenario_path,
        "config": config,
        "sendOnly": send_only,
        "modeLabel": execution_mode_label(send_only),
        "metricBasis": metric_basis(send_only),
        "issueBasis": issue_basis(send_only),
        "timeBasis": time_basis(send_only),
        "stats": stats,
        "aggregate": aggregate,
        "endpoints": endpoints,
        "failures": failures,
        "executionLogRows": execution_log_rows(scenario_path, exceptions),
        "sourceIps": source_ips,
        "sourceRows": scenario_sources,
        "statusCounts": status_counts,
        "serviceStatusCounts": service_status_counts,
        "deliveryErrors": delivery_errors,
        "history": history,
        "observerRows": observer,
        "observerSummary": observer_summary(observer),
        "totalEvents": total_events,
        "totalIssues": total_issues,
        "issueRate": percent(total_issues, total_events),
        "protection429": protection_429,
        "durationSeconds": duration_from_history(history),
        "testDate": test_date_from_history(history_csv),
    }


def combined_summary(scenarios: list[dict]) -> dict[str, object]:
    service_counts: dict[str, int] = {}
    for scenario in scenarios:
        for status, count in response_outcome_counts(scenario).items():
            service_counts[status] = service_counts.get(status, 0) + int(count)
    error_statuses = {
        status: count
        for status, count in service_counts.items()
        if is_error_outcome(status)
    }
    dominant_error = max(error_statuses.items(), key=lambda item: item[1], default=("n/a", 0))
    availability_values = [scenario["observerSummary"]["availability"] for scenario in scenarios if scenario["observerSummary"].get("availability") is not None]
    return {
        "totalEvents": sum(scenario["totalEvents"] for scenario in scenarios),
        "totalIssues": sum(scenario["totalIssues"] for scenario in scenarios),
        "lowestAvailability": min(availability_values) if availability_values else None,
        "dominantServiceError": dominant_error,
        "sourceCount": max((len(scenario["sourceRows"]) for scenario in scenarios), default=0),
        "serviceCounts": service_counts,
    }


def scenario_chart_data(scenario: dict) -> dict:
    return {
        "id": scenario["safeId"],
        "label": scenario["label"],
        "modeLabel": scenario["modeLabel"],
        "sendOnly": scenario["sendOnly"],
        "metricBasis": scenario["metricBasis"],
        "issueBasis": scenario["issueBasis"],
        "timeBasis": scenario["timeBasis"],
        "history": scenario["history"],
        "observer": scenario["observerRows"],
        "endpoints": [
            {
                "name": scenario_name(row.get("Name", "")),
                "events": integer_value(row, "Request Count"),
                "issues": integer_value(row, "Failure Count"),
                "avg": numeric_value(row, "Average Response Time"),
                "p95": numeric_value(row, "95%"),
                "max": numeric_value(row, "Max Response Time"),
            }
            for row in scenario["endpoints"]
        ],
        "statusCounts": {status_label(str(key)): value for key, value in scenario["statusCounts"].items()},
        "serviceStatusCounts": {str(key): value for key, value in scenario["serviceStatusCounts"].items()},
        "responseOutcomeCounts": response_outcome_counts(scenario),
        "sources": scenario["sourceRows"],
        "failures": issue_chart_rows(scenario),
    }


def comparison_chart_data(scenarios: list[dict]) -> dict:
    statuses = sorted({status for scenario in scenarios for status in response_outcome_counts(scenario)})
    return {
        "labels": [scenario["label"] for scenario in scenarios],
        "events": [scenario["totalEvents"] for scenario in scenarios],
        "issues": [scenario["totalIssues"] for scenario in scenarios],
        "availability": [scenario["observerSummary"].get("availability") for scenario in scenarios],
        "statuses": statuses,
        "serviceStatusSeries": [
            {
                "status": status,
                "values": [int(response_outcome_counts(scenario).get(status, 0) or 0) for scenario in scenarios],
            }
            for status in statuses
        ],
    }


def observer_table_rows(summary: dict[str, object]) -> list[list[object]]:
    return [
        ["Target", summary.get("target", TARGET_LABEL)],
        ["Availability probes", format_metric(summary.get("probes"))],
        ["Healthy probes", format_metric(summary.get("healthy"))],
        ["Failed probes", format_metric(summary.get("failed"))],
        ["Availability", format_percent(summary.get("availability"))],
        ["Average probe latency", format_metric(summary.get("averageLatencyMs"), suffix=" ms")],
        ["First failure after", format_elapsed(summary.get("firstFailureAfterSeconds"))],
        ["First failure status", summary.get("firstFailureStatus", "n/a")],
        ["First failure error", summary.get("firstFailureError", "n/a")],
    ]


def observer_interpretation(summary: dict[str, object]) -> str:
    probes = int(summary.get("probes") or 0)
    failed = int(summary.get("failed") or 0)
    if probes == 0:
        return "No availability observer data was recorded for this scenario."
    if failed == 0:
        return "All availability probes succeeded during the recorded scenario window."

    first_failure = summary.get("firstFailureAfterSeconds")
    if first_failure is not None and float(first_failure) <= 1:
        return "The first availability probe failed at the beginning of the recorded scenario window. This indicates that Target site was already unavailable or unreachable from the observer path before measurable load progression could be established."

    return f"The first availability failure was observed after {format_elapsed(first_failure)} from the start of the recorded scenario window."


def scenario_tables_html(scenario: dict) -> str:
    endpoint_rows_html = [
        [
            scenario_name(row.get("Name", "")),
            format_metric(integer_value(row, "Request Count")),
            format_metric(integer_value(row, "Failure Count")),
            format_metric(numeric_value(row, "Average Response Time"), suffix=" ms"),
            format_metric(numeric_value(row, "95%"), suffix=" ms"),
            format_metric(numeric_value(row, "Max Response Time"), suffix=" ms"),
        ]
        for row in scenario["endpoints"]
    ]
    failure_rows_html = [
        [scenario_name(row.get("Name", "")), error_label(row.get("Error", "")), format_metric(integer_value(row, "Occurrences"))]
        for row in scenario["failures"]
    ]
    source_rows_html = [[row["label"], row["weight"], format_metric(row["requests"])] for row in scenario["sourceRows"]]
    status_rows_html = [[status_label(str(status)), format_metric(count)] for status, count in scenario["statusCounts"].items()]
    response_outcome_rows_html = [[status, format_metric(count)] for status, count in response_outcome_counts(scenario).items()]
    delivery_error_rows_html = [[error_label(str(error)), format_metric(count)] for error, count in scenario["deliveryErrors"].items()]
    execution_log_rows_html = [
        [row["source"], row["count"], row["message"]]
        for row in scenario["executionLogRows"]
    ]
    config_rows_html = scenario_config_rows(scenario["config"], scenario["sendOnly"])
    issue_empty_message = f"No {scenario['issueBasis'].lower()} recorded."

    if scenario["sendOnly"]:
        return "".join(
            [
                f'<article class="panel full"><h3>Scenario Parameters</h3>{table_html(["Scenario", "Send attempts", "Send errors", "Avg", "P95", "Max"], endpoint_rows_html)}</article>',
                f'<article class="panel"><h3>Source Usage</h3>{table_html(["Source", "Weight", "Requests"], source_rows_html, "No source distribution data recorded.")}</article>',
                f'<article class="panel"><h3>Transmission Results</h3>{table_html(["Result", "Count"], status_rows_html, "No transmission result data recorded.")}</article>',
                f'<article class="panel"><h3>Delivery Result Summary</h3>{table_html(["Result", "Count"], response_outcome_rows_html, "No delivery result data recorded.")}</article>',
                f'<article class="panel"><h3>Delivery Errors</h3>{table_html(["Error", "Count"], delivery_error_rows_html, "No delivery errors recorded.")}</article>',
                f'<article class="panel full"><h3>Availability Observer</h3>{table_html(["Metric", "Value"], observer_table_rows(scenario["observerSummary"]))}</article>',
                f'<article class="panel full"><h3>Execution Log</h3>{table_html(["Source", "Count", "Message"], execution_log_rows_html, "No execution log entries or Locust exceptions recorded.")}</article>',
                f'<article class="panel full"><h3>Scenario Configuration</h3>{table_html(["Parameter", "Value"], config_rows_html)}</article>',
            ]
        )

    return "".join(
        [
            f'<article class="panel full"><h3>Scenario Parameters</h3>{table_html(["Scenario", scenario["metricBasis"], scenario["issueBasis"], "Avg", "P95", "Max"], endpoint_rows_html)}</article>',
            f'<article class="panel full"><h3>{escape(scenario["issueBasis"])}</h3>{table_html(["Scenario", "Error", "Occurrences"], failure_rows_html, issue_empty_message)}</article>',
            f'<article class="panel"><h3>Source Usage</h3>{table_html(["Source", "Weight", "Requests"], source_rows_html, "No source distribution data recorded.")}</article>',
            f'<article class="panel"><h3>Response Results</h3>{table_html(["Result", "Count"], response_outcome_rows_html, "No response result data recorded.")}</article>',
            f'<article class="panel"><h3>Delivery Errors</h3>{table_html(["Error", "Count"], delivery_error_rows_html, "No delivery errors recorded.")}</article>',
            f'<article class="panel full"><h3>Availability Observer</h3>{table_html(["Metric", "Value"], observer_table_rows(scenario["observerSummary"]))}</article>',
            f'<article class="panel full"><h3>Execution Log</h3>{table_html(["Source", "Count", "Message"], execution_log_rows_html, "No execution log entries or Locust exceptions recorded.")}</article>',
            f'<article class="panel full"><h3>Scenario Configuration</h3>{table_html(["Parameter", "Value"], config_rows_html)}</article>',
        ]
    )


def scenario_html(scenario: dict) -> str:
    send_only = scenario["sendOnly"]
    mode_note = (
        "This variant records transmission behavior and service availability independently. It is useful for pressure generation, while application health is interpreted from the observer and service response data."
        if send_only
        else "This variant waits for application responses, so throughput, failures and response-time percentiles can be interpreted as end-to-end request behavior."
    )
    observer = scenario["observerSummary"]
    cards = "".join(
        [
            metric_card(scenario["metricBasis"], format_metric(scenario["totalEvents"]), "Total event volume"),
            metric_card(scenario["issueBasis"], format_metric(scenario["totalIssues"]), format_percent(scenario["issueRate"])),
            metric_card("Average RPS", format_metric(numeric_value(scenario["aggregate"], "Requests/s"), digits=2), "Requests per second"),
            metric_card("Peak RPS", format_metric(max_history_metric(scenario["history"], "rps"), digits=2), "Highest observed second"),
            metric_card(f"Average {scenario['timeBasis'].lower()}", format_metric(numeric_value(scenario["aggregate"], "Average Response Time"), suffix=" ms"), "Recorded percentile basis"),
            metric_card(f"P95 {scenario['timeBasis'].lower()}", format_metric(numeric_value(scenario["aggregate"], "95%"), suffix=" ms"), "95th percentile"),
            metric_card("Observed availability", format_percent(observer.get("availability")), f"{format_metric(observer.get('failed'))} failed probes"),
            metric_card("Duration", format_elapsed(scenario["durationSeconds"]), "Observed reporting window"),
            metric_card("Protection 429", format_metric(scenario["protection429"]), "Observed protection responses"),
        ]
    )
    safe_id = scenario["safeId"]
    if send_only:
        panels = f"""
        <article class="panel full"><h3>Send Pressure Over Time</h3><p class="chart-note">Shows generated send attempts per second and sender-side send errors. It validates the pressure profile only; it does not represent application response health.</p><div id="traffic-{escape(safe_id)}" class="chart"></div></article>
        <article class="panel full"><h3>Send Duration Percentiles</h3><p class="chart-note">Shows how long it took to transmit requests from the sender side. These are transmission timings, not application response times.</p><div id="latency-{escape(safe_id)}" class="chart"></div></article>
        <article class="panel full"><h3>Availability Observer</h3><p class="chart-note">Shows independent availability probes against {TARGET_LABEL}. State 1 means available, state 0 means unavailable; the yellow line shows probe latency.</p><div id="observer-{escape(safe_id)}" class="chart"></div></article>
        <article class="panel"><h3>Send Volume by Scenario</h3><p class="chart-note">Shows which workload paths generated the transmission pressure during the send-only variant.</p><div id="volume-{escape(safe_id)}" class="chart"></div></article>
        <article class="panel"><h3>Delivery Result Summary</h3><p class="chart-note">Shows transmission results and delivery-path errors observed outside the sender. HTTP status-code analysis is not used for this send-only variant.</p><div id="service-status-{escape(safe_id)}" class="chart"></div></article>
        <article class="panel"><h3>Source Distribution</h3><p class="chart-note">Shows whether traffic source distribution followed the configured source weights.</p><div id="sources-{escape(safe_id)}" class="chart"></div></article>
        """
    else:
        panels = f"""
        <article class="panel full"><h3>Request Rate and Failed Responses</h3><p class="chart-note">Shows request throughput and failed responses per second during the measured-response variant.</p><div id="traffic-{escape(safe_id)}" class="chart"></div></article>
        <article class="panel full"><h3>Response Time Percentiles</h3><p class="chart-note">Shows P50, average, P95 and P99 end-to-end response times because this variant waits for application responses.</p><div id="latency-{escape(safe_id)}" class="chart"></div></article>
        <article class="panel full"><h3>Availability Observer</h3><p class="chart-note">Shows independent availability probes against {TARGET_LABEL}. State 1 means available, state 0 means unavailable; the yellow line shows probe latency.</p><div id="observer-{escape(safe_id)}" class="chart"></div></article>
        <article class="panel"><h3>Request Volume by Scenario</h3><p class="chart-note">Shows request volume and failed responses by workload path.</p><div id="volume-{escape(safe_id)}" class="chart"></div></article>
        <article class="panel"><h3>Response Results</h3><p class="chart-note">Shows response outcomes recorded while waiting for application responses, including missing responses and delivery errors where applicable.</p><div id="service-status-{escape(safe_id)}" class="chart"></div></article>
        <article class="panel"><h3>Source Distribution</h3><p class="chart-note">Shows whether traffic source distribution followed the configured source weights.</p><div id="sources-{escape(safe_id)}" class="chart"></div></article>
        <article class="panel"><h3>Failed Responses by Scenario</h3><p class="chart-note">Shows failed measured responses grouped by scenario and normalized error label.</p><div id="failures-{escape(safe_id)}" class="chart"></div></article>
        """
    return f"""
    <section class="section-block scenario-block" id="{escape(safe_id)}">
      <div class="scenario-heading">
        <span class="eyebrow">Scenario {scenario['order']}</span>
        <h2>{escape(scenario['label'])}</h2>
        <p>{escape(mode_note)}</p>
        <p class="callout">{escape(observer_interpretation(observer))}</p>
      </div>
      <section class="metrics">{cards}</section>
      <section class="grid">
        {panels}
        {scenario_tables_html(scenario)}
      </section>
    </section>
    """


def overview_cards_html(summary: dict[str, object], scenario_count: int) -> str:
    dominant_status, dominant_count = summary["dominantServiceError"]
    return "".join(
        [
            metric_card("Scenarios", format_metric(scenario_count), "Executed consecutively"),
            metric_card("Total traffic events", format_metric(summary["totalEvents"]), "Combined across variants"),
            metric_card("Total issues", format_metric(summary["totalIssues"]), "Send errors or failed responses"),
            metric_card("Lowest availability", format_percent(summary["lowestAvailability"]), "Availability observer"),
            metric_card("Dominant service error", str(dominant_status), f"{format_metric(dominant_count)} occurrences"),
            metric_card("Traffic sources", format_metric(summary["sourceCount"]), "Source labels"),
        ]
    )


def generate_html_report(results_path: Path, manifest: dict, scenarios: list[dict]) -> Path:
    summary = combined_summary(scenarios)
    chart_data = {
        "scenarios": [scenario_chart_data(scenario) for scenario in scenarios],
        "comparison": comparison_chart_data(scenarios),
    }
    scenario_sections = "".join(scenario_html(scenario) for scenario in scenarios)

    template = """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>DDoS Resilience Assessment Report</title>
  <script src="__PLOTLY_CDN_URL__" charset="utf-8"></script>
  <style>
    :root {
      color-scheme: dark;
      --bg: #0a1020;
      --panel: rgba(17, 25, 45, 0.86);
      --line: rgba(148, 163, 184, 0.22);
      --text: #e5edf8;
      --muted: #94a3b8;
      --accent: #38bdf8;
      --accent-2: #a78bfa;
      --danger: #fb7185;
      --ok: #34d399;
      font-family: Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
    }
    * { box-sizing: border-box; }
    body {
      margin: 0;
      color: var(--text);
      background:
        radial-gradient(circle at top left, rgba(56, 189, 248, 0.22), transparent 30rem),
        radial-gradient(circle at top right, rgba(167, 139, 250, 0.20), transparent 28rem),
        linear-gradient(135deg, #080d1a 0%, var(--bg) 45%, #10172a 100%);
      min-height: 100vh;
    }
    main { width: min(1180px, calc(100vw - 32px)); margin: 0 auto; padding: 44px 0 64px; }
    .hero { display: grid; grid-template-columns: 1.4fr 0.8fr; gap: 24px; align-items: end; margin-bottom: 28px; }
    h1 { font-size: clamp(2rem, 5vw, 4.7rem); line-height: 0.94; letter-spacing: -0.07em; margin: 0 0 18px; }
    h2 { margin: 0 0 14px; font-size: 1.4rem; }
    h3 { margin: 0 0 14px; font-size: 1.03rem; }
    p { color: var(--muted); line-height: 1.6; }
    .eyebrow { color: var(--accent); font-weight: 700; text-transform: uppercase; letter-spacing: 0.18em; font-size: 0.75rem; }
    .summary, .section-block, .panel, .metric-card {
      border: 1px solid var(--line);
      background: var(--panel);
      backdrop-filter: blur(18px);
      border-radius: 24px;
      box-shadow: 0 18px 50px rgba(0, 0, 0, 0.22);
    }
    .summary { padding: 22px; border-radius: 28px; background: linear-gradient(160deg, rgba(17, 25, 45, 0.94), rgba(17, 25, 45, 0.62)); }
    .report-logo { display: block; width: 72px; height: 72px; object-fit: contain; margin: 0 0 18px auto; filter: drop-shadow(0 10px 24px rgba(56,189,248,0.22)); }
    .summary dl { display: grid; grid-template-columns: 1fr 1fr; gap: 12px 18px; margin: 0; }
    .summary dt { color: var(--muted); font-size: 0.75rem; text-transform: uppercase; letter-spacing: 0.08em; }
    .summary dd { margin: 3px 0 0; font-weight: 700; word-break: break-word; }
    .section-block { margin: 22px 0; padding: 24px; }
    .section-block ul { margin: 10px 0 0 20px; color: var(--muted); line-height: 1.65; }
    .metrics { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 14px; margin: 20px 0; }
    .metric-card { padding: 18px; min-height: 120px; display: flex; flex-direction: column; justify-content: space-between; }
    .metric-card span, .metric-card small, .muted, .chart-note { color: var(--muted); }
    .metric-card strong { display: block; font-size: clamp(1.35rem, 3vw, 2.1rem); letter-spacing: -0.05em; margin: 10px 0; }
    .grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 18px; }
    .panel { padding: 20px; overflow: hidden; }
    .panel.full { grid-column: 1 / -1; }
    .chart { width: 100%; min-height: 370px; }
    .chart-note { margin: -4px 0 12px; font-size: 0.88rem; line-height: 1.55; }
    .callout { margin: 14px 0 0; padding: 14px 16px; border-left: 3px solid var(--accent); background: rgba(56,189,248,0.07); color: #cbd5e1; border-radius: 8px 16px 16px 8px; }
    .table-wrap { overflow-x: auto; border-radius: 18px; border: 1px solid var(--line); }
    table { width: 100%; border-collapse: collapse; min-width: 620px; background: rgba(2, 6, 23, 0.28); }
    th, td { padding: 13px 14px; text-align: left; border-bottom: 1px solid var(--line); font-size: 0.92rem; }
    th { color: #cbd5e1; background: rgba(148, 163, 184, 0.08); font-size: 0.76rem; text-transform: uppercase; letter-spacing: 0.08em; }
    tr:last-child td { border-bottom: 0; }
    .comparison-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 18px; }
    .placeholder { border-style: dashed; }
    .placeholder li { color: #cbd5e1; }
    .footer { margin-top: 24px; color: var(--muted); font-size: 0.9rem; }
    @media (max-width: 900px) {
      main { width: min(100vw - 20px, 760px); padding-top: 28px; }
      .hero, .grid, .comparison-grid { grid-template-columns: 1fr; }
      .metrics { grid-template-columns: repeat(2, minmax(0, 1fr)); }
      .summary dl { grid-template-columns: 1fr; }
    }
    @media (max-width: 560px) {
      .metrics { grid-template-columns: 1fr; }
      .chart { min-height: 320px; }
    }
  </style>
</head>
<body>
  <main>
    <section class="hero">
      <div>
        <h1>DDoS Resilience Assessment Report</h1>
        <p>This report summarizes the application-layer DDoS simulation results. Traffic pressure and response behavior are shown separately to make the findings easier to review.</p>
      </div>
      <aside class="summary">
        __LOGO_HTML__
        <dl>
          <div><dt>Report date</dt><dd>__REPORT_DATE__</dd></div>
          <div><dt>Target</dt><dd>Target site</dd></div>
          <div><dt>Assessment</dt><dd>Application-layer DDoS simulation</dd></div>
          <div><dt>Variants</dt><dd>__VARIANT_COUNT_LABEL__</dd></div>
        </dl>
      </aside>
    </section>

    <section class="section-block">
      <h2>Assessment Objectives and Scope:</h2>
      __SCOPE_HTML__
    </section>

    <section class="metrics">__OVERVIEW_CARDS__</section>

    <section class="section-block">
      <h2>Variant Comparison</h2>
      <p>The comparison uses shared metrics only: total traffic events, issue counts, observer availability and outcome categories. Detailed charts differ by scenario because send-only pressure and measured-response execution answer different questions.</p>
      <div class="comparison-grid">
        <article class="panel"><h3>Traffic Events and Issues</h3><p class="chart-note">Compares total generated events with scenario-specific issues: send errors for send-only pressure and failed responses for measured response.</p><div id="comparison-events" class="chart"></div></article>
        <article class="panel"><h3>Observed Availability</h3><p class="chart-note">Compares independent observer availability for each variant. This is the common user-facing availability signal across both execution modes.</p><div id="comparison-availability" class="chart"></div></article>
        <article class="panel full"><h3>Outcome Summary</h3><p class="chart-note">Combines meaningful outcomes per variant. Send-only pressure uses transmission/delivery outcomes; measured response uses response outcomes and delivery errors.</p><div id="comparison-service-status" class="chart"></div></article>
      </div>
    </section>

    __SCENARIO_SECTIONS__

    <p class="footer">Prepared from the DDoS assessment dataset.</p>
  </main>

  <script>
    const reportData = __CHART_DATA__;
    const plotConfig = { responsive: true, displaylogo: false };
    const baseLayout = {
      paper_bgcolor: 'rgba(0,0,0,0)',
      plot_bgcolor: 'rgba(2,6,23,0.20)',
      font: { color: '#dbeafe', family: 'Inter, system-ui, sans-serif' },
      margin: { l: 58, r: 28, t: 24, b: 56 },
      hovermode: 'x unified',
      legend: { orientation: 'h', y: 1.08, x: 0 },
      xaxis: { title: 'elapsed seconds', gridcolor: 'rgba(148,163,184,0.16)', zerolinecolor: 'rgba(148,163,184,0.16)' },
      yaxis: { gridcolor: 'rgba(148,163,184,0.16)', zerolinecolor: 'rgba(148,163,184,0.16)' }
    };

    function mergeLayout(layout) {
      return Object.assign({}, baseLayout, layout || {});
    }

    function hasValues(trace) {
      const values = trace.y || trace.values;
      return Array.isArray(values) && values.some(value => value !== null && value !== undefined && value !== '');
    }

    function plotIfData(id, traces, layout) {
      const element = document.getElementById(id);
      if (!element) return;
      if (!traces.some(hasValues)) {
        element.innerHTML = '<p class="muted">No data available for this chart.</p>';
        return;
      }
      Plotly.newPlot(element, traces, mergeLayout(layout), plotConfig);
    }

    function serviceColors(labels) {
      return labels.map(label => {
        const match = String(label).match(/\b(\d{3})\b/);
        const value = match ? Number(match[1]) : Number(label);
        if (value >= 500) return '#fb7185';
        if (value >= 400) return '#fbbf24';
        if (value >= 200 && value < 400) return '#34d399';
        return '#38bdf8';
      });
    }

    const comparison = reportData.comparison;
    plotIfData('comparison-events', [
      { x: comparison.labels, y: comparison.events, name: 'Traffic events', type: 'bar', marker: { color: '#38bdf8' } },
      { x: comparison.labels, y: comparison.issues, name: 'Issues', type: 'bar', marker: { color: '#fb7185' } }
    ], { barmode: 'group', xaxis: { gridcolor: 'rgba(148,163,184,0.16)' }, yaxis: { title: 'count', gridcolor: 'rgba(148,163,184,0.16)' } });

    plotIfData('comparison-availability', [
      { x: comparison.labels, y: comparison.availability, name: 'Availability %', type: 'bar', marker: { color: '#34d399' } }
    ], { xaxis: { gridcolor: 'rgba(148,163,184,0.16)' }, yaxis: { title: 'availability %', range: [0, 100], gridcolor: 'rgba(148,163,184,0.16)' } });

    plotIfData('comparison-service-status', comparison.serviceStatusSeries.map(series => ({
      x: comparison.labels,
      y: series.values,
      name: series.status,
      type: 'bar'
    })), { barmode: 'stack', xaxis: { gridcolor: 'rgba(148,163,184,0.16)' }, yaxis: { title: 'responses', gridcolor: 'rgba(148,163,184,0.16)' } });

    reportData.scenarios.forEach((scenario) => {
      const history = scenario.history || [];
      const elapsed = history.map(row => row.elapsedSeconds);
      plotIfData(`traffic-${scenario.id}`, [
        { x: elapsed, y: history.map(row => row.rps), name: `${scenario.metricBasis}/s`, type: 'scatter', mode: 'lines', fill: 'tozeroy', line: { color: '#38bdf8', width: 3 } },
        { x: elapsed, y: history.map(row => row.failuresPerSecond), name: `${scenario.issueBasis}/s`, type: 'scatter', mode: 'lines', line: { color: '#fb7185', width: 3 } },
        { x: elapsed, y: history.map(row => row.users), name: 'Traffic ramp', type: 'scatter', mode: 'lines', yaxis: 'y2', line: { color: '#a78bfa', width: 2, dash: 'dot' } }
      ], { yaxis: { title: 'events per second', gridcolor: 'rgba(148,163,184,0.16)' }, yaxis2: { title: 'ramp level', overlaying: 'y', side: 'right', gridcolor: 'rgba(0,0,0,0)' } });

      plotIfData(`latency-${scenario.id}`, [
        { x: elapsed, y: history.map(row => row.p50), name: 'P50', type: 'scatter', mode: 'lines', line: { color: '#34d399', width: 2 } },
        { x: elapsed, y: history.map(row => row.avg), name: 'Average', type: 'scatter', mode: 'lines', line: { color: '#fbbf24', width: 2 } },
        { x: elapsed, y: history.map(row => row.p95), name: 'P95', type: 'scatter', mode: 'lines', line: { color: '#38bdf8', width: 3 } },
        { x: elapsed, y: history.map(row => row.p99), name: 'P99', type: 'scatter', mode: 'lines', line: { color: '#fb7185', width: 3 } }
      ], { yaxis: { title: 'milliseconds', gridcolor: 'rgba(148,163,184,0.16)' } });

      const observer = scenario.observer || [];
      plotIfData(`observer-${scenario.id}`, [
        { x: observer.map(row => row.elapsedSeconds), y: observer.map(row => row.healthy ? 1 : null), name: 'Available probe', type: 'scatter', mode: 'markers', marker: { color: '#34d399', size: 8 } },
        { x: observer.map(row => row.elapsedSeconds), y: observer.map(row => row.failed ? 0 : null), name: 'Unavailable probe', type: 'scatter', mode: 'markers', marker: { color: '#fb7185', size: 8 } },
        { x: observer.map(row => row.elapsedSeconds), y: observer.map(row => row.latencyMs), name: 'Probe latency ms', type: 'scatter', mode: 'lines', yaxis: 'y2', line: { color: '#fbbf24', width: 2 } }
      ], { yaxis: { title: 'availability state', range: [-0.1, 1.1], tickvals: [0, 1], ticktext: ['unavailable', 'available'], gridcolor: 'rgba(148,163,184,0.16)' }, yaxis2: { title: 'latency ms', overlaying: 'y', side: 'right', gridcolor: 'rgba(0,0,0,0)' } });

      const endpoints = scenario.endpoints || [];
      plotIfData(`volume-${scenario.id}`, [
        { x: endpoints.map(row => row.name), y: endpoints.map(row => row.events), name: scenario.metricBasis, type: 'bar', marker: { color: '#38bdf8' } },
        { x: endpoints.map(row => row.name), y: endpoints.map(row => row.issues), name: scenario.issueBasis, type: 'bar', marker: { color: '#fb7185' } }
      ], { barmode: 'group', xaxis: { gridcolor: 'rgba(148,163,184,0.16)' }, yaxis: { title: 'count', gridcolor: 'rgba(148,163,184,0.16)' } });

      const statusLabels = Object.keys(scenario.responseOutcomeCounts || {});
      const statusValues = Object.values(scenario.responseOutcomeCounts || {});
      plotIfData(`service-status-${scenario.id}`, [
        { labels: statusLabels, values: statusValues, type: 'pie', hole: 0.55, textinfo: 'label+percent', marker: { colors: serviceColors(statusLabels) } }
      ], { margin: { l: 20, r: 20, t: 20, b: 20 }, showlegend: true, xaxis: { title: '' } });

      const sources = scenario.sources || [];
      plotIfData(`sources-${scenario.id}`, [
        { x: sources.map(row => row.label), y: sources.map(row => row.requests), name: 'Requests', type: 'bar', marker: { color: '#a78bfa' } },
        { x: sources.map(row => row.label), y: sources.map(row => row.weight), name: 'Configured weight', type: 'scatter', mode: 'lines+markers', yaxis: 'y2', line: { color: '#fbbf24', width: 3 } }
      ], { xaxis: { gridcolor: 'rgba(148,163,184,0.16)' }, yaxis: { title: 'requests', gridcolor: 'rgba(148,163,184,0.16)' }, yaxis2: { title: 'weight', overlaying: 'y', side: 'right', gridcolor: 'rgba(0,0,0,0)' } });

      const failures = scenario.failures || [];
      plotIfData(`failures-${scenario.id}`, [
        { x: failures.map(row => row.name), y: failures.map(row => row.occurrences), text: failures.map(row => row.error), name: scenario.issueBasis, type: 'bar', marker: { color: '#fb7185' } }
      ], { xaxis: { gridcolor: 'rgba(148,163,184,0.16)' }, yaxis: { title: 'occurrences', gridcolor: 'rgba(148,163,184,0.16)' } });
    });
  </script>
</body>
</html>
"""

    html_content = (
        template
        .replace("__PLOTLY_CDN_URL__", PLOTLY_CDN_URL)
        .replace("__LOGO_HTML__", logo_html())
        .replace("__REPORT_DATE__", escape(REPORT_DATE_TEXT))
        .replace("__VARIANT_COUNT_LABEL__", escape(variant_count_label(len(scenarios))))
        .replace("__SCOPE_HTML__", scope_html(len(scenarios)))
        .replace("__OVERVIEW_CARDS__", overview_cards_html(summary, len(scenarios)))
        .replace("__SCENARIO_SECTIONS__", scenario_sections)
        .replace("__CHART_DATA__", json.dumps(chart_data, ensure_ascii=False))
    )

    report_path = report_output_path(results_path, "report.html")
    report_path.write_text(html_content, encoding="utf-8")
    return report_path


def markdown_scenario(scenario: dict) -> list[str]:
    lines = [
        f"## Scenario {scenario['order']}: {scenario['label']}",
        "",
        f"- Execution mode: {scenario['modeLabel']}",
        f"- {scenario['metricBasis']}: {format_metric(scenario['totalEvents'])}",
        f"- {scenario['issueBasis']}: {format_metric(scenario['totalIssues'])}",
        f"- Issue rate: {format_percent(scenario['issueRate'])}",
        f"- Average RPS: {format_metric(numeric_value(scenario['aggregate'], 'Requests/s'), digits=2)}",
        f"- Peak RPS: {format_metric(max_history_metric(scenario['history'], 'rps'), digits=2)}",
        f"- P95 {scenario['timeBasis'].lower()}: {format_metric(numeric_value(scenario['aggregate'], '95%'), suffix=' ms')}",
        f"- Observed availability: {format_percent(scenario['observerSummary'].get('availability'))}",
        f"- First observer failure after: {format_elapsed(scenario['observerSummary'].get('firstFailureAfterSeconds'))}",
        f"- Observer interpretation: {observer_interpretation(scenario['observerSummary'])}",
        f"- Protection 429 responses: {format_metric(scenario['protection429'])}",
        "",
        "### Scenario Parameters",
        "",
        f"| Scenario | {scenario['metricBasis']} | {scenario['issueBasis']} | Avg ms | P95 ms | Max ms |",
        "| --- | ---: | ---: | ---: | ---: | ---: |",
    ]
    for row in scenario["endpoints"]:
        lines.append(
            "| "
            f"{scenario_name(value(row, 'Name'))} | "
            f"{value(row, 'Request Count')} | "
            f"{value(row, 'Failure Count')} | "
            f"{value(row, 'Average Response Time')} | "
            f"{value(row, '95%')} | "
            f"{value(row, 'Max Response Time')} |"
        )
    lines.extend(["", "### Availability Observer", ""])
    lines.extend(["| Metric | Value |", "| --- | --- |"])
    for key, row_value in observer_table_rows(scenario["observerSummary"]):
        lines.append(f"| {key} | {row_value} |")
    lines.extend(["", "### Execution Log", ""])
    if scenario["executionLogRows"]:
        lines.extend(["| Source | Count | Message |", "| --- | ---: | --- |"])
        for row in scenario["executionLogRows"]:
            message = str(row["message"]).replace("|", "\\|")
            lines.append(f"| {row['source']} | {row['count']} | {message} |")
    else:
        lines.append("No execution log entries or Locust exceptions recorded.")
    lines.extend(["", "### Scenario Configuration", ""])
    lines.extend(["| Parameter | Value |", "| --- | --- |"])
    for key, row_value in scenario_config_rows(scenario["config"], scenario["sendOnly"]):
        lines.append(f"| {key} | {row_value} |")
    return lines


def generate_markdown_report(results_path: Path, manifest: dict, scenarios: list[dict]) -> Path:
    summary = combined_summary(scenarios)
    lines = [
        "# DDoS Resilience Assessment Report",
        "",
        "## Report Context",
        "",
        f"- Report date: {REPORT_DATE_TEXT}",
        f"- Target: {TARGET_LABEL}",
        "- Assessment: Application-layer DDoS simulation",
        f"- Variants: {variant_count_label(len(scenarios))}",
        "",
        "## Assessment Objectives and Scope:",
        "",
        "The assessment focused on the observable behavior of the service during the simulation, including traffic generation, service availability, HTTP response behavior, error conditions, workload distribution and the point at which service degradation became visible.",
        "",
        "The results are intended to identify resilience gaps within the tested scenario and to provide recommendations for further investigation, remediation and subsequent validation.",
        "",
        "## Combined Summary",
        "",
        f"- Total traffic events: {format_metric(summary['totalEvents'])}",
        f"- Total issues: {format_metric(summary['totalIssues'])}",
        f"- Lowest observed availability: {format_percent(summary['lowestAvailability'])}",
        f"- Dominant service error: {summary['dominantServiceError'][0]} ({format_metric(summary['dominantServiceError'][1])})",
        f"- Traffic sources: {format_metric(summary['sourceCount'])}",
        "",
    ]
    for scenario in scenarios:
        lines.extend(markdown_scenario(scenario))
        lines.append("")
    report_path = report_output_path(results_path, "report.md")
    report_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return report_path


def generate_report(results_dir: Path | str) -> Path:
    results_path = Path(results_dir)
    manifest, scenario_entries = discover_scenarios(results_path)
    if not scenario_entries:
        raise FileNotFoundError(f"No scenario result data found under {results_path}")

    scenarios = [load_scenario(path, entry) for path, entry in scenario_entries]
    scenarios.sort(key=lambda item: item["order"])
    markdown_path = generate_markdown_report(results_path, manifest, scenarios)
    generate_html_report(results_path, manifest, scenarios)
    return markdown_path


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate Markdown and HTML DDoS assessment reports.")
    parser.add_argument("results_dir", nargs="?", help="Directory containing scenario results. Defaults to the latest attacker/results run.")
    args = parser.parse_args()
    results_dir = Path(args.results_dir) if args.results_dir else find_latest_results_dir()
    report_path = generate_report(results_dir)
    print(report_path)
    print(report_output_path(Path(results_dir), "report.html"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
