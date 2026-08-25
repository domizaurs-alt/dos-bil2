#!/usr/bin/env bash
set -euo pipefail

IMDS_URL="http://169.254.169.254/metadata/instance/network/interface?api-version=2021-02-01"
ENV_FILE=""
INTERFACE=""
VERIFY=false
DRY_RUN=false

usage() {
  printf '%s\n' \
    "Usage: $0 [--interface IFACE] [--write-env attacker/config/attacker.env] [--verify] [--dry-run]" \
    "" \
    "Configures Azure VM secondary private IPs on Linux and prints traffic-spreader real_bind env." \
    "Run on the attacker VM. It reads Azure Instance Metadata Service, adds missing private IPs" \
    "to the primary interface, and can update attacker/config/attacker.env."
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --interface)
      INTERFACE="$2"
      shift 2
      ;;
    --write-env)
      ENV_FILE="$2"
      shift 2
      ;;
    --verify)
      VERIFY=true
      shift
      ;;
    --dry-run)
      DRY_RUN=true
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      printf 'Unknown argument: %s\n' "$1" >&2
      usage >&2
      exit 2
      ;;
  esac
done

if [[ -z "$INTERFACE" ]]; then
  INTERFACE=$(ip route show default | awk '{print $5; exit}')
fi

if [[ -z "$INTERFACE" ]]; then
  printf 'Could not detect default network interface. Use --interface IFACE.\n' >&2
  exit 1
fi

PRIMARY_CIDR=$(ip -o -4 addr show dev "$INTERFACE" scope global | awk 'NR == 1 {print $4}')
if [[ -z "$PRIMARY_CIDR" ]]; then
  printf 'No IPv4 address found on interface %s.\n' "$INTERFACE" >&2
  exit 1
fi
PREFIX_LEN="${PRIMARY_CIDR#*/}"

METADATA=$(curl -fsS -H Metadata:true "$IMDS_URL")
PRIVATE_IPS=$(printf '%s' "$METADATA" | python3 -c '
import json
import sys

data = json.load(sys.stdin)
interfaces = data if isinstance(data, list) else data.get("interface", [])
ips = []
for interface in interfaces:
    if not isinstance(interface, dict):
        continue
    ipv4 = interface.get("ipv4", {})
    if not isinstance(ipv4, dict):
        continue
    for item in ipv4.get("ipAddress", []):
        if not isinstance(item, dict):
            continue
        ip = item.get("privateIpAddress")
        if ip and ip not in ips:
            ips.append(ip)
print(",".join(ips))
')

if [[ -z "$PRIVATE_IPS" ]]; then
  printf 'Azure metadata returned no private IPs. Are you running this on an Azure VM?\n' >&2
  exit 1
fi

IFS=',' read -r -a IP_ARRAY <<< "$PRIVATE_IPS"

run_ip_addr_add() {
  local ip_address="$1"
  if ip -o -4 addr show dev "$INTERFACE" | grep -q "inet ${ip_address}/"; then
    printf 'Already configured: %s on %s\n' "$ip_address" "$INTERFACE"
    return
  fi

  printf 'Adding: %s/%s to %s\n' "$ip_address" "$PREFIX_LEN" "$INTERFACE"
  if [[ "$DRY_RUN" == "true" ]]; then
    return
  fi

  if [[ "${EUID}" -eq 0 ]]; then
    ip addr add "${ip_address}/${PREFIX_LEN}" dev "$INTERFACE"
  else
    sudo ip addr add "${ip_address}/${PREFIX_LEN}" dev "$INTERFACE"
  fi
}

for private_ip in "${IP_ARRAY[@]}"; do
  run_ip_addr_add "$private_ip"
done

WEIGHTS_JSON=$(python3 -c '
import json
import sys
ips = [ip for ip in sys.argv[1].split(",") if ip]
print(json.dumps({ip: 1 for ip in ips}, separators=(",", ":")))
' "$PRIVATE_IPS")

printf '\nAdd or keep these values in attacker/config/attacker.env:\n\n'
printf 'TRAFFIC_SPREADER__SOURCE_IP_MODE=real_bind\n'
printf 'TRAFFIC_SPREADER__REAL_SOURCE_IPS=%s\n' "$PRIVATE_IPS"
printf 'TRAFFIC_SPREADER__REAL_SOURCE_IP_WEIGHTS=%s\n' "$WEIGHTS_JSON"

if [[ -n "$ENV_FILE" ]]; then
  if [[ "$DRY_RUN" == "true" ]]; then
    printf '\nDry-run: not writing %s\n' "$ENV_FILE"
  else
    python3 - "$ENV_FILE" "$PRIVATE_IPS" "$WEIGHTS_JSON" <<'PY'
from pathlib import Path
import sys

path = Path(sys.argv[1])
source_ips = sys.argv[2]
weights = sys.argv[3]
updates = {
    "TRAFFIC_SPREADER__SOURCE_IP_MODE": "real_bind",
    "TRAFFIC_SPREADER__REAL_SOURCE_IPS": source_ips,
    "TRAFFIC_SPREADER__REAL_SOURCE_IP_WEIGHTS": weights,
}

lines = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
filtered = [line for line in lines if not any(line.startswith(f"{key}=") for key in updates)]
if filtered and filtered[-1].strip():
    filtered.append("")
filtered.extend(f"{key}={value}" for key, value in updates.items())
path.parent.mkdir(parents=True, exist_ok=True)
path.write_text("\n".join(filtered) + "\n", encoding="utf-8")
PY
    printf '\nUpdated %s\n' "$ENV_FILE"
  fi
fi

if [[ "$VERIFY" == "true" ]]; then
  printf '\nVerifying public egress IP per private IP:\n'
  for private_ip in "${IP_ARRAY[@]}"; do
    printf '%s -> ' "$private_ip"
    curl --interface "$private_ip" -fsS --max-time 8 https://ifconfig.me || true
    printf '\n'
  done
fi
