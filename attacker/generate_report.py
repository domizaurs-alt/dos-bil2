import argparse
import csv
import html
import json
import os
from datetime import datetime, timezone
from pathlib import Path


BASE_DIR = Path(__file__).resolve().parent
PLOTLY_CDN_URL = "https://cdn.plot.ly/plotly-3.7.0.min.js"


def read_csv(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []

    with path.open("r", encoding="utf-8", newline="") as file:
        return list(csv.DictReader(file))


def read_json(path: Path) -> dict:
    if not path.exists():
        return {}

    with path.open("r", encoding="utf-8") as file:
        return json.load(file)


def find_latest_results_dir(results_root: Path | None = None) -> Path:
    root = results_root or BASE_DIR / "results"
    candidates = [
        path
        for path in root.iterdir()
        if path.is_dir() and (path / "locust_stats.csv").exists()
    ] if root.exists() else []
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


def find_aggregate(stats: list[dict[str, str]]) -> dict[str, str]:
    for row in stats:
        if row.get("Name") == "Aggregated":
            return row
    return stats[-1] if stats else {}


def value(row: dict[str, str], key: str, default: str = "n/a") -> str:
    return row.get(key) or default


def endpoint_rows(stats: list[dict[str, str]]) -> list[dict[str, str]]:
    return [row for row in stats if row.get("Name") != "Aggregated"]


def integer_value(row: dict[str, str], key: str) -> int:
    try:
        return int(float(row.get(key, "0") or "0"))
    except ValueError:
        return 0


def numeric_value(row: dict[str, str], key: str) -> float | None:
    raw = row.get(key, "")
    if raw in ("", "N/A", None):
        return None

    try:
        return float(raw)
    except (TypeError, ValueError):
        return None


def format_metric(metric: float | int | None, digits: int = 1, suffix: str = "") -> str:
    if metric is None:
        return "n/a"

    value = float(metric)
    if value.is_integer():
        text = f"{value:,.0f}"
    else:
        text = f"{value:,.{digits}f}"
    return f"{text}{suffix}"


def failure_rate_percent(requests: int, failures: int) -> float | None:
    if requests <= 0:
        return None
    return failures / requests * 100


def timestamp_label(raw: str) -> str:
    try:
        return datetime.fromtimestamp(float(raw), tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    except (TypeError, ValueError, OSError):
        return raw or ""


def duration_label(history: list[dict[str, str]]) -> str:
    timestamps: list[float] = []
    for row in history:
        try:
            timestamps.append(float(row.get("Timestamp", "")))
        except ValueError:
            continue

    if len(timestamps) < 2:
        return "n/a"

    seconds = int(max(timestamps) - min(timestamps))
    minutes, remaining_seconds = divmod(seconds, 60)
    if minutes:
        return f"{minutes}m {remaining_seconds}s"
    return f"{remaining_seconds}s"


def escape(value_to_escape: object) -> str:
    return html.escape(str(value_to_escape), quote=True)


def table_html(headers: list[str], rows: list[list[object]], empty_message: str = "No data recorded.") -> str:
    if not rows:
        return f'<p class="muted">{escape(empty_message)}</p>'

    header_html = "".join(f"<th>{escape(header)}</th>" for header in headers)
    row_html = []
    for row in rows:
        cells = "".join(f"<td>{escape(cell)}</td>" for cell in row)
        row_html.append(f"<tr>{cells}</tr>")

    return (
        '<div class="table-wrap"><table>'
        f"<thead><tr>{header_html}</tr></thead>"
        f"<tbody>{''.join(row_html)}</tbody>"
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


def scenario_name(name: str) -> str:
    cleaned = name.strip("/").replace("_", " ").strip()
    return cleaned.title() if cleaned else name


def error_label(error: str) -> str:
    if "HTTP " in error:
        return error.split("HTTP ", 1)[1].split("'", 1)[0].split(")", 1)[0]
    return error


def target_label(config: dict) -> str:
    return "Protected service"


def execution_mode_label(mode: str) -> str:
    return "Send-only pressure" if mode in {"fire_and_forget", "fire-and-forget"} else "Measured response"


def is_send_only_mode(mode: str) -> bool:
    return mode in {"fire_and_forget", "fire-and-forget"}


def first_failure_label(observer: dict[str, object]) -> str:
    value = observer.get("first_failure_time", "n/a")
    return "None recorded" if value in {"", "n/a", None} else str(value)


def client_config_rows(config: dict) -> list[list[object]]:
    rows = [
        ["Concurrent clients", config.get("users", "n/a")],
        ["Ramp-up rate", config.get("spawn_rate", "n/a")],
        ["Run duration", config.get("run_time", "n/a")],
        ["Minimum wait time", f"{config.get('wait_time_min_seconds', 'n/a')} s"],
        ["Maximum wait time", f"{config.get('wait_time_max_seconds', 'n/a')} s"],
        ["Short response weight", config.get("quick_response_weight", "n/a")],
        ["Long response weight", config.get("long_response_weight", "n/a")],
        ["Download weight", config.get("download_file_weight", "n/a")],
        ["Health check weight", config.get("healthcheck_weight", "n/a")],
    ]
    if "fire_and_forget" in config:
        mode = "Send-only pressure" if config.get("fire_and_forget") else "Measured response"
        rows.append(["Execution mode", mode])
    if "fire_and_forget_rps_per_user" in config:
        rows.append(["Fire-and-forget RPS per user", config.get("fire_and_forget_rps_per_user")])
        try:
            rows.append(["Approximate target send rate", float(config.get("users", 0)) * float(config.get("fire_and_forget_rps_per_user", 0))])
        except (TypeError, ValueError):
            pass
    return rows


def observer_summary(observer_rows: list[dict[str, str]]) -> dict[str, object]:
    total = len(observer_rows)
    ok_rows = [row for row in observer_rows if row.get("ok") == "true"]
    failed_rows = [row for row in observer_rows if row.get("ok") != "true"]
    latencies = []
    for row in observer_rows:
        try:
            latencies.append(float(row.get("response_time_ms", "")))
        except ValueError:
            continue

    availability = (len(ok_rows) / total * 100) if total else None
    first_failure = failed_rows[0] if failed_rows else {}
    return {
        "total": total,
        "ok": len(ok_rows),
        "failed": len(failed_rows),
        "availability": availability,
        "average_latency_ms": (sum(latencies) / len(latencies)) if latencies else None,
        "first_failure_time": first_failure.get("timestamp", "n/a"),
        "first_failure_status": first_failure.get("status_code", "n/a"),
        "first_failure_error": first_failure.get("error", "n/a"),
        "target_url": observer_rows[0].get("target_url", "n/a") if observer_rows else "n/a",
    }


def observer_table_rows(observer_rows: list[dict[str, str]]) -> list[list[object]]:
    summary = observer_summary(observer_rows)
    return [
        ["Target", summary["target_url"]],
        ["Probes", summary["total"]],
        ["Healthy probes", summary["ok"]],
        ["Failed probes", summary["failed"]],
        ["Availability", format_metric(summary["availability"], digits=2, suffix="%")],
        ["Average probe latency", format_metric(summary["average_latency_ms"], digits=1, suffix=" ms")],
        ["First failure time", summary["first_failure_time"]],
        ["First failure status", summary["first_failure_status"]],
        ["First failure error", summary["first_failure_error"]],
    ]


def history_chart_rows(history: list[dict[str, str]]) -> list[dict[str, float | str | None]]:
    rows = [row for row in history if row.get("Name") == "Aggregated"]
    return [
        {
            "time": timestamp_label(row.get("Timestamp", "")),
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
        for row in rows
    ]


def generate_html_report(
    results_path: Path,
    stats: list[dict[str, str]],
    history: list[dict[str, str]],
    failures: list[dict[str, str]],
    source_ips: dict,
    config: dict,
    observer_rows: list[dict[str, str]],
) -> Path:
    aggregate = find_aggregate(stats)
    endpoints = endpoint_rows(stats)
    request_count = integer_value(aggregate, "Request Count")
    failure_count = integer_value(aggregate, "Failure Count")
    blocked_requests = blocked_request_count(failures)
    spreader_status_counts = source_ips.get("spreader_status_code_counts", {})
    spreader_blocked_requests = int(spreader_status_counts.get("429", 0)) if isinstance(spreader_status_counts, dict) else 0
    rate = failure_rate_percent(request_count, failure_count)
    observer = observer_summary(observer_rows)
    send_only = is_send_only_mode(source_ips.get("attack_mode", "measured"))
    generated_at = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")

    request_label = "Send attempts" if send_only else "Total requests"
    failure_label_text = "Send error rate" if send_only else "Failure rate"
    throughput_label = "Attempts/s" if send_only else "Requests/s"
    average_label = "Average send time" if send_only else "Average response"
    p95_label = "P95 send time" if send_only else "P95 response"
    scenario_attempts_label = "Send attempts" if send_only else "Requests"
    scenario_failures_label = "Send errors" if send_only else "Failures"
    failure_section_label = "Send Errors" if send_only else "Failures"
    response_counts_label = "Send Result Counts" if send_only else "Response Codes"
    throughput_chart_label = "Throughput and Send Errors" if send_only else "Throughput and Failures"
    latency_chart_label = "Send Duration Percentiles" if send_only else "Response Time Percentiles"
    scenario_volume_label = "Send Volume" if send_only else "Scenario Volume"

    endpoint_chart_rows = [
        {
            "name": scenario_name(row.get("Name", "")),
            "type": row.get("Type", ""),
            "requests": integer_value(row, "Request Count"),
            "failures": integer_value(row, "Failure Count"),
            "avg": numeric_value(row, "Average Response Time"),
            "p95": numeric_value(row, "95%"),
            "p99": numeric_value(row, "99%"),
            "max": numeric_value(row, "Max Response Time"),
        }
        for row in endpoints
    ]
    ip_counts = source_ips.get("ip_counts", {}) if isinstance(source_ips.get("ip_counts", {}), dict) else {}
    configured_weights = source_ips.get("configured_ip_weights", {}) if isinstance(source_ips.get("configured_ip_weights", {}), dict) else {}
    status_code_counts = source_ips.get("status_code_counts", {}) if isinstance(source_ips.get("status_code_counts", {}), dict) else {}
    spreader_error_counts = source_ips.get("spreader_error_counts", {}) if isinstance(source_ips.get("spreader_error_counts", {}), dict) else {}

    chart_data = {
        "history": history_chart_rows(history),
        "endpoints": endpoint_chart_rows,
        "statusCodes": status_code_counts,
        "spreaderStatusCodes": spreader_status_counts if isinstance(spreader_status_counts, dict) else {},
        "ipUsage": [
            {"ip": ip, "requests": count, "weight": configured_weights.get(ip)}
            for ip, count in ip_counts.items()
        ],
        "failures": [
            {
                "name": scenario_name(row.get("Name", "")),
                "error": error_label(row.get("Error", "")),
                "occurrences": integer_value(row, "Occurrences"),
            }
            for row in failures
        ],
    }

    if send_only:
        cards = [
            metric_card("Observer availability", format_metric(observer["availability"], digits=2, suffix="%"), f"{format_metric(observer['failed'])} failed probes"),
            metric_card("First app failure", first_failure_label(observer), "From observer probes"),
            metric_card("Send attempts", format_metric(request_count), "Fire-and-forget sends"),
            metric_card("Send error rate", format_metric(rate, digits=2, suffix="%"), f"{format_metric(failure_count)} send errors"),
            metric_card("Attempts/s", format_metric(numeric_value(aggregate, "Requests/s"), digits=2), "Average send throughput"),
            metric_card("Average send time", format_metric(numeric_value(aggregate, "Average Response Time"), suffix=" ms"), "Socket send duration"),
            metric_card("Duration", duration_label(history), f"Concurrent clients: {config.get('users', 'n/a')}"),
            metric_card("Traffic sources", format_metric(source_ips.get("used_ip_count", 0)), "Sources used"),
            metric_card("Delivery errors", format_metric(source_ips.get("spreader_forward_errors", 0)), "Traffic spreader errors"),
        ]
    else:
        cards = [
            metric_card("Total requests", format_metric(request_count), "Overall traffic"),
            metric_card("Failure rate", format_metric(rate, digits=2, suffix="%"), f"{format_metric(failure_count)} failed"),
            metric_card("Requests/s", format_metric(numeric_value(aggregate, "Requests/s"), digits=2), "Average throughput"),
            metric_card("Average response", format_metric(numeric_value(aggregate, "Average Response Time"), suffix=" ms"), "Across all scenarios"),
            metric_card("P95 response", format_metric(numeric_value(aggregate, "95%"), suffix=" ms"), "Aggregate percentile"),
            metric_card("Duration", duration_label(history), f"Concurrent clients: {config.get('users', 'n/a')}"),
            metric_card("Traffic sources", format_metric(source_ips.get("used_ip_count", 0)), "Sources used"),
            metric_card("Protection 429", format_metric(blocked_requests + spreader_blocked_requests), "Observed protection responses"),
            metric_card("Observer availability", format_metric(observer["availability"], digits=2, suffix="%"), f"{format_metric(observer['failed'])} failed probes"),
        ]
    cards_html = "".join(cards)

    endpoint_rows_html = [
        [
            scenario_name(row.get("Name", "")),
            format_metric(integer_value(row, "Request Count")),
            format_metric(integer_value(row, "Failure Count")),
            format_metric(numeric_value(row, "Average Response Time"), suffix=" ms"),
            format_metric(numeric_value(row, "95%"), suffix=" ms"),
            format_metric(numeric_value(row, "Max Response Time"), suffix=" ms"),
        ]
        for row in endpoints
    ]
    failure_rows_html = [
        [scenario_name(row.get("Name", "")), error_label(row.get("Error", "")), format_metric(integer_value(row, "Occurrences"))]
        for row in failures
    ]
    ip_rows_html = [
        [ip, configured_weights.get(ip, "n/a"), format_metric(count)]
        for ip, count in ip_counts.items()
    ]
    status_rows_html = [[code, format_metric(count)] for code, count in status_code_counts.items()]
    spreader_status_rows_html = [[code, format_metric(count)] for code, count in (spreader_status_counts.items() if isinstance(spreader_status_counts, dict) else [])]
    spreader_error_rows_html = [[error, format_metric(count)] for error, count in spreader_error_counts.items()]
    observer_rows_html = observer_table_rows(observer_rows)
    config_rows_html = client_config_rows(config)

    template = """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>DDoS Assessment Report</title>
  <script src="__PLOTLY_CDN_URL__" charset="utf-8"></script>
  <style>
    :root {
      color-scheme: dark;
      --bg: #0a1020;
      --panel: rgba(17, 25, 45, 0.86);
      --panel-strong: rgba(24, 36, 64, 0.94);
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
    .hero {
      display: grid;
      grid-template-columns: 1.4fr 0.8fr;
      gap: 24px;
      align-items: end;
      margin-bottom: 28px;
    }
    h1 { font-size: clamp(2rem, 5vw, 4.7rem); line-height: 0.94; letter-spacing: -0.07em; margin: 0 0 18px; }
    h2 { margin: 0 0 16px; font-size: 1.05rem; letter-spacing: 0.01em; }
    p { color: var(--muted); line-height: 1.6; }
    .eyebrow { color: var(--accent); font-weight: 700; text-transform: uppercase; letter-spacing: 0.18em; font-size: 0.75rem; }
    .summary {
      padding: 22px;
      border: 1px solid var(--line);
      border-radius: 28px;
      background: linear-gradient(160deg, rgba(17, 25, 45, 0.94), rgba(17, 25, 45, 0.62));
      box-shadow: 0 24px 80px rgba(0, 0, 0, 0.32);
    }
    .summary dl { display: grid; grid-template-columns: 1fr 1fr; gap: 12px 18px; margin: 0; }
    .summary dt { color: var(--muted); font-size: 0.75rem; text-transform: uppercase; letter-spacing: 0.08em; }
    .summary dd { margin: 3px 0 0; font-weight: 700; word-break: break-word; }
    .metrics { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 14px; margin: 24px 0; }
    .metric-card, .panel {
      border: 1px solid var(--line);
      background: var(--panel);
      backdrop-filter: blur(18px);
      border-radius: 24px;
      box-shadow: 0 18px 50px rgba(0, 0, 0, 0.22);
    }
    .metric-card { padding: 18px; min-height: 128px; display: flex; flex-direction: column; justify-content: space-between; }
    .metric-card span, .metric-card small, .muted { color: var(--muted); }
    .metric-card strong { display: block; font-size: clamp(1.5rem, 3vw, 2.35rem); letter-spacing: -0.05em; margin: 10px 0; }
    .grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 18px; }
    .panel { padding: 20px; overflow: hidden; }
    .panel.full { grid-column: 1 / -1; }
    .chart { width: 100%; min-height: 390px; }
    .table-wrap { overflow-x: auto; border-radius: 18px; border: 1px solid var(--line); }
    table { width: 100%; border-collapse: collapse; min-width: 680px; background: rgba(2, 6, 23, 0.28); }
    th, td { padding: 13px 14px; text-align: left; border-bottom: 1px solid var(--line); font-size: 0.92rem; }
    th { color: #cbd5e1; background: rgba(148, 163, 184, 0.08); font-size: 0.76rem; text-transform: uppercase; letter-spacing: 0.08em; }
    tr:last-child td { border-bottom: 0; }
    .footer { margin-top: 24px; color: var(--muted); font-size: 0.9rem; }
    @media (max-width: 900px) {
      main { width: min(100vw - 20px, 760px); padding-top: 28px; }
      .hero, .grid { grid-template-columns: 1fr; }
      .metrics { grid-template-columns: repeat(2, minmax(0, 1fr)); }
      .summary dl { grid-template-columns: 1fr; }
    }
    @media (max-width: 560px) {
      .metrics { grid-template-columns: 1fr; }
      .chart { min-height: 330px; }
    }
  </style>
</head>
<body>
  <main>
    <section class="hero">
      <div>
        <div class="eyebrow">Controlled DDoS assessment</div>
        <h1>DDoS Assessment Report</h1>
        <p>Executive report generated from the latest approved scenario. It focuses on traffic parameters, service response, protection events, and source distribution.</p>
      </div>
      <aside class="summary">
        <dl>
          <div><dt>Results</dt><dd>__RESULTS_NAME__</dd></div>
          <div><dt>Generated</dt><dd>__GENERATED_AT__</dd></div>
          <div><dt>Target</dt><dd>__TARGET_URL__</dd></div>
          <div><dt>Mode</dt><dd>__ATTACK_MODE__</dd></div>
        </dl>
      </aside>
    </section>

    <section class="metrics">__CARDS_HTML__</section>

    <section class="grid">
      <article class="panel full"><h2>__THROUGHPUT_CHART_TITLE__</h2><div id="throughput-chart" class="chart"></div></article>
      <article class="panel full"><h2>__LATENCY_CHART_TITLE__</h2><div id="latency-chart" class="chart"></div></article>
      <article class="panel"><h2>__SCENARIO_VOLUME_TITLE__</h2><div id="endpoint-chart" class="chart"></div></article>
      <article class="panel"><h2>__RESPONSE_COUNTS_TITLE__</h2><div id="status-chart" class="chart"></div></article>
      <article class="panel"><h2>Source Distribution</h2><div id="ip-chart" class="chart"></div></article>
      <article class="panel"><h2>Failure Occurrences</h2><div id="failure-chart" class="chart"></div></article>
      <article class="panel full"><h2>Scenario Parameters</h2>__ENDPOINT_TABLE__</article>
      <article class="panel full"><h2>__FAILURE_SECTION_TITLE__</h2>__FAILURE_TABLE__</article>
      <article class="panel"><h2>Source Usage</h2>__IP_TABLE__</article>
      <article class="panel"><h2>Response Codes</h2>__STATUS_TABLE__</article>
      <article class="panel"><h2>Service Response Codes</h2>__SPREADER_STATUS_TABLE__</article>
      <article class="panel"><h2>Delivery Errors</h2>__SPREADER_ERROR_TABLE__</article>
      <article class="panel full"><h2>Availability Observer</h2>__OBSERVER_TABLE__</article>
      <article class="panel full"><h2>Scenario Configuration</h2>__CONFIG_TABLE__</article>
    </section>

    <p class="footer">Generated from the approved DDoS scenario dataset. Technical implementation details are intentionally omitted from this client-facing report.</p>
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
      xaxis: { gridcolor: 'rgba(148,163,184,0.16)', zerolinecolor: 'rgba(148,163,184,0.16)' },
      yaxis: { gridcolor: 'rgba(148,163,184,0.16)', zerolinecolor: 'rgba(148,163,184,0.16)' }
    };

    function mergeLayout(layout) {
      return Object.assign({}, baseLayout, layout || {});
    }

    function plotIfData(id, traces, layout) {
      const element = document.getElementById(id);
      if (!element) return;
      if (!traces.some(trace => Array.isArray(trace.y || trace.values) && (trace.y || trace.values).length > 0)) {
        element.innerHTML = '<p class="muted">No data available for this chart.</p>';
        return;
      }
      Plotly.newPlot(element, traces, mergeLayout(layout), plotConfig);
    }

    const history = reportData.history;
    const time = history.map(row => row.time);
    plotIfData('throughput-chart', [
      { x: time, y: history.map(row => row.rps), name: '__THROUGHPUT_TRACE_NAME__', type: 'scatter', mode: 'lines', fill: 'tozeroy', line: { color: '#38bdf8', width: 3 } },
      { x: time, y: history.map(row => row.failuresPerSecond), name: '__FAILURE_TRACE_NAME__', type: 'scatter', mode: 'lines', line: { color: '#fb7185', width: 3 } },
      { x: time, y: history.map(row => row.users), name: 'Users', type: 'scatter', mode: 'lines', yaxis: 'y2', line: { color: '#a78bfa', width: 2, dash: 'dot' } }
    ], { yaxis: { title: '__THROUGHPUT_AXIS_TITLE__', gridcolor: 'rgba(148,163,184,0.16)' }, yaxis2: { title: 'users', overlaying: 'y', side: 'right', gridcolor: 'rgba(0,0,0,0)' } });

    plotIfData('latency-chart', [
      { x: time, y: history.map(row => row.p50), name: 'P50', type: 'scatter', mode: 'lines', line: { color: '#34d399', width: 2 } },
      { x: time, y: history.map(row => row.avg), name: 'Average', type: 'scatter', mode: 'lines', line: { color: '#fbbf24', width: 2 } },
      { x: time, y: history.map(row => row.p95), name: 'P95', type: 'scatter', mode: 'lines', line: { color: '#38bdf8', width: 3 } },
      { x: time, y: history.map(row => row.p99), name: 'P99', type: 'scatter', mode: 'lines', line: { color: '#fb7185', width: 3 } }
    ], { yaxis: { title: 'milliseconds', gridcolor: 'rgba(148,163,184,0.16)' } });

    const endpoints = reportData.endpoints;
    plotIfData('endpoint-chart', [
      { x: endpoints.map(row => row.name), y: endpoints.map(row => row.requests), name: '__SCENARIO_ATTEMPTS_TRACE_NAME__', type: 'bar', marker: { color: '#38bdf8' } },
      { x: endpoints.map(row => row.name), y: endpoints.map(row => row.failures), name: '__SCENARIO_FAILURES_TRACE_NAME__', type: 'bar', marker: { color: '#fb7185' } }
    ], { barmode: 'group', yaxis: { title: 'count', gridcolor: 'rgba(148,163,184,0.16)' } });

    const statusLabels = Object.keys(reportData.statusCodes || {});
    const statusValues = Object.values(reportData.statusCodes || {});
    plotIfData('status-chart', [
      { labels: statusLabels, values: statusValues, type: 'pie', hole: 0.55, textinfo: 'label+percent', marker: { colors: ['#34d399', '#fb7185', '#38bdf8', '#fbbf24', '#a78bfa'] } }
    ], { margin: { l: 20, r: 20, t: 20, b: 20 }, showlegend: true });

    const ipUsage = reportData.ipUsage;
    plotIfData('ip-chart', [
      { x: ipUsage.map(row => row.ip), y: ipUsage.map(row => row.requests), name: 'Requests', type: 'bar', marker: { color: '#a78bfa' } },
      { x: ipUsage.map(row => row.ip), y: ipUsage.map(row => row.weight), name: 'Configured weight', type: 'scatter', mode: 'lines+markers', yaxis: 'y2', line: { color: '#fbbf24', width: 3 } }
    ], { yaxis: { title: 'requests', gridcolor: 'rgba(148,163,184,0.16)' }, yaxis2: { title: 'weight', overlaying: 'y', side: 'right', gridcolor: 'rgba(0,0,0,0)' } });

    const failures = reportData.failures;
    plotIfData('failure-chart', [
      { x: failures.map(row => row.name), y: failures.map(row => row.occurrences), text: failures.map(row => row.error), name: 'Failures', type: 'bar', marker: { color: '#fb7185' } }
    ], { yaxis: { title: 'occurrences', gridcolor: 'rgba(148,163,184,0.16)' } });
  </script>
</body>
</html>
"""

    html_content = (
        template
        .replace("__PLOTLY_CDN_URL__", PLOTLY_CDN_URL)
        .replace("__RESULTS_NAME__", escape(results_path.name))
        .replace("__GENERATED_AT__", escape(generated_at))
        .replace("__TARGET_URL__", escape(target_label(config)))
        .replace("__ATTACK_MODE__", escape(execution_mode_label(source_ips.get("attack_mode", "measured"))))
        .replace("__CARDS_HTML__", cards_html)
        .replace("__THROUGHPUT_CHART_TITLE__", throughput_chart_label)
        .replace("__LATENCY_CHART_TITLE__", latency_chart_label)
        .replace("__SCENARIO_VOLUME_TITLE__", scenario_volume_label)
        .replace("__FAILURE_SECTION_TITLE__", failure_section_label)
        .replace("__RESPONSE_COUNTS_TITLE__", response_counts_label)
        .replace("__THROUGHPUT_TRACE_NAME__", throughput_label)
        .replace("__FAILURE_TRACE_NAME__", "Send errors/s" if send_only else "Failures/s")
        .replace("__THROUGHPUT_AXIS_TITLE__", "attempts per second" if send_only else "requests per second")
        .replace("__SCENARIO_ATTEMPTS_TRACE_NAME__", scenario_attempts_label)
        .replace("__SCENARIO_FAILURES_TRACE_NAME__", scenario_failures_label)
        .replace("__ENDPOINT_TABLE__", table_html(["Scenario", scenario_attempts_label, scenario_failures_label, "Avg", "P95", "Max"], endpoint_rows_html))
        .replace("__FAILURE_TABLE__", table_html(["Scenario", "Error", "Occurrences"], failure_rows_html, f"No {failure_section_label.lower()} recorded."))
        .replace("__IP_TABLE__", table_html(["Source", "Weight", "Requests"], ip_rows_html, "No source distribution data recorded."))
        .replace("__STATUS_TABLE__", table_html(["Status", "Count"], status_rows_html, "No response code data recorded."))
        .replace("__SPREADER_STATUS_TABLE__", table_html(["Status", "Count"], spreader_status_rows_html, "No service response code data recorded."))
        .replace("__SPREADER_ERROR_TABLE__", table_html(["Error", "Count"], spreader_error_rows_html, "No delivery errors recorded."))
        .replace("__OBSERVER_TABLE__", table_html(["Metric", "Value"], observer_rows_html, "No observer data recorded."))
        .replace("__CONFIG_TABLE__", table_html(["Key", "Value"], config_rows_html, "No config data recorded."))
        .replace("__CHART_DATA__", json.dumps(chart_data, ensure_ascii=False))
    )

    report_path = report_output_path(results_path, "report.html")
    report_path.write_text(html_content, encoding="utf-8")
    return report_path


def blocked_request_count(failures: list[dict[str, str]]) -> int:
    total = 0
    for row in failures:
        if "429" in value(row, "Error"):
            total += integer_value(row, "Occurrences")
    return total


def generate_report(results_dir: Path | str) -> Path:
    results_path = Path(results_dir)
    stats = read_csv(results_path / "locust_stats.csv")
    history = read_csv(results_path / "locust_stats_history.csv")
    failures = read_csv(results_path / "locust_failures.csv")
    observer_rows = read_csv(results_path / "observer.csv")
    source_ips = read_json(results_path / "source_ips.json")
    config = read_json(results_path / "config.json")
    aggregate = find_aggregate(stats)
    blocked_requests = blocked_request_count(failures)
    used_ip_count = source_ips.get("used_ip_count", 0)
    attack_mode = source_ips.get("attack_mode", "measured")
    observer = observer_summary(observer_rows)
    send_only = is_send_only_mode(attack_mode)
    spreader_status_counts = source_ips.get("spreader_status_code_counts", {})
    spreader_blocked_requests = int(spreader_status_counts.get("429", 0)) if isinstance(spreader_status_counts, dict) else 0
    report_path = report_output_path(results_path, "report.md")

    request_label = "Send attempts" if send_only else "Total requests"
    failure_label_text = "Send errors" if send_only else "Failed requests"
    throughput_label = "Attempts/s" if send_only else "Requests/s"
    failures_per_second_label = "Send errors/s" if send_only else "Failures/s"
    average_label = "Average send time ms" if send_only else "Average response time ms"
    median_label = "Median send time ms" if send_only else "Median response time ms"
    p95_label = "P95 send time ms" if send_only else "P95 response time ms"
    max_label = "Max send time ms" if send_only else "Max response time ms"
    scenario_attempts_label = "Send attempts" if send_only else "Requests"
    scenario_failures_label = "Send errors" if send_only else "Failures"
    failure_section_label = "Send Errors" if send_only else "Failures"
    response_counts_label = "Send Result Counts" if send_only else "Response Counts"
    notes_text = (
        "In send-only mode, Locust metrics describe socket send attempts. Application availability is determined by the observer section."
        if send_only
        else "This client-facing report presents scenario parameters and observed results without exposing implementation details."
    )

    lines = [
        "# DDoS Assessment Report",
        "",
        "## Summary",
        "",
        f"- Execution mode: {execution_mode_label(attack_mode)}",
        f"- {request_label}: {value(aggregate, 'Request Count')}",
        f"- {failure_label_text}: {value(aggregate, 'Failure Count')}",
        f"- {throughput_label}: {value(aggregate, 'Requests/s')}",
        f"- {failures_per_second_label}: {value(aggregate, 'Failures/s')}",
        f"- {average_label}: {value(aggregate, 'Average Response Time')}",
        f"- {median_label}: {value(aggregate, 'Median Response Time')}",
        f"- {p95_label}: {value(aggregate, '95%')}",
        f"- {max_label}: {value(aggregate, 'Max Response Time')}",
        f"- Protection 429 responses observed at request level: {blocked_requests}",
        f"- Protection 429 responses observed service-side: {spreader_blocked_requests}",
        f"- Traffic sources used: {used_ip_count}",
        f"- Observer availability: {format_metric(observer['availability'], digits=2, suffix='%')}",
        f"- Observer first failure: {first_failure_label(observer)}",
        "",
        "## Scenario Parameters",
        "",
        f"| Scenario | {scenario_attempts_label} | {scenario_failures_label} | Avg ms | P95 ms | Max ms |",
        "| --- | ---: | ---: | ---: | ---: | ---: |",
    ]

    for row in endpoint_rows(stats):
        lines.append(
            "| "
            f"{scenario_name(value(row, 'Name'))} | "
            f"{value(row, 'Request Count')} | "
            f"{value(row, 'Failure Count')} | "
            f"{value(row, 'Average Response Time')} | "
            f"{value(row, '95%')} | "
            f"{value(row, 'Max Response Time')} |"
        )

    lines.extend(["", f"## {failure_section_label}", ""])
    if failures:
        lines.extend([
            "| Scenario | Error | Occurrences |",
            "| --- | --- | ---: |",
        ])
        for row in failures:
            error = value(row, "Error").replace("|", "\\|")
            lines.append(
                f"| {scenario_name(value(row, 'Name'))} | {error_label(error)} | {value(row, 'Occurrences')} |"
            )
    else:
        
        lines.append(f"No {failure_section_label.lower()} recorded.")

    lines.extend(["", "## Source Usage", ""])
    ip_counts = source_ips.get("ip_counts", {})
    if ip_counts:
        configured_weights = source_ips.get("configured_ip_weights", {})
        lines.extend(
            [
                f"- Configured sources: {len(source_ips.get('configured_ips', []))}",
                f"- Used sources: {used_ip_count}",
                f"- Requests with assigned source: {source_ips.get('total_requests_with_simulated_ip', 0)}",
                "",
                "| Source | Weight | Requests |",
                "| --- | ---: | ---: |",
            ]
        )
        for ip, count in ip_counts.items():
            lines.append(f"| {ip} | {configured_weights.get(ip, 'n/a')} | {count} |")
    else:
        lines.append("No source distribution data recorded.")

    lines.extend(["", f"## {response_counts_label}", ""])
    status_code_counts = source_ips.get("status_code_counts", {})
    if status_code_counts:
        lines.extend(["| Result | Count |", "| --- | ---: |"])
        for result, count in status_code_counts.items():
            lines.append(f"| {result} | {count} |")
    else:
        lines.append("No send result data recorded." if send_only else "No response data recorded.")

    lines.extend(["", "## Service Results", ""])
    if source_ips.get("traffic_spreader_enabled"):
        lines.extend(
            [
                f"- Delivered requests: {source_ips.get('spreader_forwarded_requests', 0)}",
                f"- Delivery errors: {source_ips.get('spreader_forward_errors', 0)}",
                "",
            ]
        )
        spreader_statuses = source_ips.get("spreader_status_code_counts", {})
        if spreader_statuses:
            lines.extend(["| Service Status Code | Count |", "| --- | ---: |"])
            for status_code, count in spreader_statuses.items():
                lines.append(f"| {status_code} | {count} |")
        else:
            lines.append("No service status code data recorded.")

        spreader_errors = source_ips.get("spreader_error_counts", {})
        if spreader_errors:
            lines.extend(["", "| Delivery Error | Count |", "| --- | ---: |"])
            for error, count in spreader_errors.items():
                lines.append(f"| {error} | {count} |")
    else:
        lines.append("Service-side metrics were not configured for this run.")

    lines.extend(["", "## Availability Observer", ""])
    if observer_rows:
        lines.extend(["| Metric | Value |", "| --- | --- |"])
        for key, row_value in observer_table_rows(observer_rows):
            lines.append(f"| {key} | {row_value} |")
    else:
        lines.append("No observer data recorded.")

    lines.extend(
        [
            "",
            "## Protection Events",
            "",
            f"- 429 responses observed at request level: {blocked_requests}",
            f"- 429 responses observed service-side: {spreader_blocked_requests}",
            "- Excessive request volume is controlled per source.",
            "",
            "## Notes",
            "",
            notes_text,
        ]
    )

    report_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    generate_html_report(results_path, stats, history, failures, source_ips, config, observer_rows)
    return report_path


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
