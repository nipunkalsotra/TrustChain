"""scripts/push_alert_rules.py — one-off loader: pushes every rule group
in docker/prometheus/alerts.yml into Grafana Cloud's Mimir Ruler API.

Grafana Cloud's hosted Prometheus is Mimir underneath, and Mimir's ruler
accepts the *exact same* rule-group YAML shape Prometheus itself uses —
so this doesn't reformat anything, it just PUTs each of alerts.yml's
existing groups, one at a time (the ruler API takes one group per
request; a namespace groups multiple rule groups together, same as a
Prometheus rule_files entry would).

Not wired into any long-running process — this repo has no local
Prometheus anymore to source these from live, so there's nothing to keep
in sync automatically. Re-run by hand after editing alerts.yml.

Usage: python3 scripts/push_alert_rules.py
Reads GRAFANA_CLOUD_PROMETHEUS_URL/_USER and GRAFANA_CLOUD_RULER_API_KEY
from backend/.env.
"""

import sys
from pathlib import Path
from urllib.parse import urlparse

import httpx
import yaml
from dotenv import dotenv_values

REPO_ROOT = Path(__file__).resolve().parent.parent
ALERTS_FILE = REPO_ROOT / "docker" / "prometheus" / "alerts.yml"
NAMESPACE = "trustchain"


def main() -> int:
    env = dotenv_values(REPO_ROOT / "backend" / ".env")
    prom_url = env.get("GRAFANA_CLOUD_PROMETHEUS_URL")
    user = env.get("GRAFANA_CLOUD_PROMETHEUS_USER")
    ruler_key = env.get("GRAFANA_CLOUD_RULER_API_KEY")
    if not (prom_url and user and ruler_key):
        print("Missing GRAFANA_CLOUD_PROMETHEUS_URL/_USER/GRAFANA_CLOUD_RULER_API_KEY in backend/.env", file=sys.stderr)
        return 1

    # The push URL is .../api/prom/push — the ruler config API lives on
    # the same host, under /prometheus/config/v1/rules/ instead.
    host = urlparse(prom_url).netloc
    ruler_base = f"https://{host}/prometheus/config/v1/rules"

    groups = yaml.safe_load(ALERTS_FILE.read_text())["groups"]
    print(f"Loaded {len(groups)} rule group(s), {sum(len(g['rules']) for g in groups)} rule(s) total from {ALERTS_FILE}")

    auth = (user, ruler_key)
    with httpx.Client(auth=auth, timeout=30.0) as client:
        for group in groups:
            body = yaml.safe_dump(group, sort_keys=False)
            resp = client.post(
                f"{ruler_base}/{NAMESPACE}",
                content=body,
                headers={"Content-Type": "application/yaml"},
            )
            status = "OK" if resp.status_code // 100 == 2 else "FAILED"
            print(f"  [{status}] group={group['name']!r} ({len(group['rules'])} rules) -> HTTP {resp.status_code}")
            if resp.status_code // 100 != 2:
                print(f"    response body: {resp.text[:500]}")
                return 1

        # Verify by reading back, not just trusting the POST status codes.
        print("\nVerifying via GET...")
        resp = client.get(f"{ruler_base}/{NAMESPACE}")
        resp.raise_for_status()
        # Mimir's ruler GET keys the response by namespace name itself
        # (not a "groups" wrapper): {"<namespace>": [{name, rules}, ...]}.
        loaded = yaml.safe_load(resp.text) or {}
        loaded_groups = {g["name"] for g in loaded.get(NAMESPACE, [])}
        expected = {g["name"] for g in groups}
        missing = expected - loaded_groups
        if missing:
            print(f"MISMATCH: groups missing after push: {missing}")
            return 1
        print(f"Confirmed: all {len(expected)} groups present in Grafana Cloud's ruler ({sorted(expected)})")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
