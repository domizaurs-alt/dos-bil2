# Controlled Load-Test PoC

This repository contains a controlled proof of concept for testing a sample HTTP service under high request volume.

Use it only against systems owned by the team or systems where load testing is explicitly approved.

## Local Flow

The client side and attacker side are intentionally separated.

```text
client Docker network:   test-client-app -> reverse-proxy -> host port 8080
attacker Docker network: Locust engine -> traffic-spreader -> host.docker.internal:8080
```

There is no shared Docker network between the client compose file and attacker compose file. The attacker reaches the client through the host-published reverse proxy port, matching the real setup where both sides are separate machines/services.

## Configuration

Config ownership is explicit:

- `test-client/config/client.env` configures only the test client app and reverse proxy.
- `attacker/config/attacker.env` configures only the attacker-side traffic spreader.
- `attacker/config/attack.json` configures the Locust test scenario.

Client env prefixes:

- `APP__*` controls `test-client-app`.
- `PROXY__*` controls `reverse-proxy`.

Attacker env prefixes:

- `TRAFFIC_SPREADER__*` controls `traffic-spreader`.
- `OBSERVER__*` controls the lightweight availability observer.

Use `fire_and_forget` in `attacker/config/attack.json` to switch attacker behavior:

- `false` runs measured mode and records response latency/status data.
- `true` sends a complete HTTP GET request and closes the socket without reading the response body.

## Docker Run

Start the client side:

```bash
docker compose --env-file test-client/config/client.env -f docker-compose.client.yml up --build
```

In a second terminal, run the attacker:

```bash
docker compose --env-file attacker/config/attacker.env -f docker-compose.attacker.yml up --build
```

The attacker starts Locust with the web UI, autostarts the configured test, and exposes `http://127.0.0.1:8089` while the run is active.

The attacker also runs an observer that checks `http://host.docker.internal:8080/quick_response` once per second by default. Results are written to `attacker/results/<timestamp>/` after the run finishes.

## Pressure Tests

For RAM pressure tests, edit `test-client/config/client.env` before starting the client side:

```dotenv
APP__LONG_RESPONSE_DELAY_MS=20000
APP__LONG_RESPONSE_MEMORY_MB=16
APP__MEMORY_LIMIT=2500m
APP__MEMORY_SWAP_LIMIT=2500m
```

The default pressure profile uses `spawn_rate=1`, `users=170`, and `run_time=240s`. With `16MB` held per long response, memory should climb slowly enough to observe it around `2GB` before the container reaches its `2500m` limit. Proxy and traffic-spreader timeouts are higher than the long response delay, so slow successful responses are not counted as failures just because they took close to 30 seconds.

To disable proxy protection and let all traffic reach the app, set:

```dotenv
PROXY__DDOS_PROTECTION_ENABLED=false
```

For fire-and-forget pressure, set this in `attacker/config/attack.json`:

```json
"fire_and_forget": true,
"fire_and_forget_rps_per_user": 0.2
```

`fire_and_forget_rps_per_user` limits how fast each Locust user sends requests in fire-and-forget mode. Approximate total send rate is `users * fire_and_forget_rps_per_user`. Only run one attacker instance at a time.

In fire-and-forget mode, Locust does not wait for application responses. Use the observer results in `observer.csv`, `report.md`, and `report.html` to see when the application stopped returning healthy responses.

## Azure VM Real Source IPs

For an attacker VM with multiple Azure public IPs, run:

```bash
./scripts/vm_config.sh --write-env attacker/config/attacker.env --verify
```

Set `TRAFFIC_SPREADER__TARGET_BASE_URL` and `OBSERVER__TARGET_URL` in `attacker/config/attacker.env` to the public reverse proxy FQDN, then start the VM compose:

```bash
docker compose --env-file attacker/config/attacker.env -f docker-compose.attacker.vm.yml up --build
```

Local runs keep `TRAFFIC_SPREADER__SOURCE_IP_MODE=simulated_header`. Azure VM real source-IP runs use `TRAFFIC_SPREADER__SOURCE_IP_MODE=real_bind`.

Report semantics depend on mode:

- Measured mode: Locust failures and latency describe HTTP responses from the tested path.
- Fire-and-forget mode: Locust failures and timing describe socket send attempts; application health comes from the observer section.

## Azure Infrastructure

Pulumi infrastructure is under `infra/`. Deployment is manual. See `DEPLOYMENT.md` for the full sequence.

The local simulated IP header is a PoC mechanism. Real source-IP selection on the attacker VM remains a later networking phase.
