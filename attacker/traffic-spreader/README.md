# Traffic Spreader

Local attacker-side traffic distribution service.

Locust sends normal requests to this service. The traffic spreader forwards them to the configured target and, for local PoC runs, adds `X-Simulated-Source-IP` based on its own configuration.

This keeps source IP simulation outside Locust. Later, on a VM with multiple real IP addresses, this module is the place to add real source IP selection.

## Environment

Docker config lives in `attacker/config/attacker.env` and uses `TRAFFIC_SPREADER__*` names. Docker Compose maps those values to the runtime variables below.

- `TARGET_BASE_URL` is the upstream target, usually the reverse proxy.
- `SOURCE_IP_MODE=simulated_header` adds a simulated source IP header for local PoC runs.
- `SOURCE_IP_MODE=real_bind` binds outgoing requests to real local source IPs on an Azure VM.
- `SIMULATED_SOURCE_IPS` is a comma-separated list of local simulated IPs.
- `SIMULATED_SOURCE_IP_WEIGHTS` is a JSON object with traffic weights per IP.
- `SIMULATED_IP_HEADER` defaults to `X-Simulated-Source-IP`.
- `REAL_SOURCE_IPS` is a comma-separated list of VM private IPs used by `real_bind`.
- `REAL_SOURCE_IP_WEIGHTS` is a JSON object with traffic weights per real source IP.
- `REQUEST_TIMEOUT_SECONDS` controls upstream timeout.

For Azure `real_bind`, run the spreader on host networking or directly on the VM host, otherwise Docker bridge NAT hides host source IPs.

## Internal Endpoints

- `GET /spreader_healthcheck` returns health and current config.
- `GET /spreader_metrics` returns cumulative forwarding and source IP counters.
