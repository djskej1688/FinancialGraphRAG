"""Round 3 Neo4j connection diagnostics.

This script performs connection diagnostics only. It does not run coverage,
does not query graph contents, and does not execute write Cypher.
"""

from __future__ import annotations

import argparse
import json
import os
import socket
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlparse


REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_RUN_DIR = REPO_ROOT / "outputs" / "round3_orchestration" / "20260525_132801"
ENV_FILES = (REPO_ROOT / ".env", REPO_ROOT.parent / ".env")
ENV_KEYS = ("NEO4J_URI", "NEO4J_USERNAME", "NEO4J_PASSWORD", "NEO4J_DATABASE")


def now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text.rstrip() + "\n", encoding="utf-8", newline="\n")


def run_dir_from_arg(value: str | None) -> Path:
    if value:
        path = Path(value)
        return path if path.is_absolute() else REPO_ROOT / path
    return DEFAULT_RUN_DIR


def read_env_files() -> dict[str, str]:
    values: dict[str, str] = {}
    for path in ENV_FILES:
        if not path.exists():
            continue
        for raw in path.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            key = key.strip()
            if key.startswith("NEO4J_"):
                values[key] = value.strip().strip("\"'")
    return values


def effective_env() -> dict[str, str]:
    file_values = read_env_files()
    values = {
        "NEO4J_URI": os.environ.get("NEO4J_URI") or file_values.get("NEO4J_URI", ""),
        "NEO4J_USERNAME": os.environ.get("NEO4J_USERNAME") or file_values.get("NEO4J_USERNAME", ""),
        "NEO4J_PASSWORD": os.environ.get("NEO4J_PASSWORD") or file_values.get("NEO4J_PASSWORD", ""),
        "NEO4J_DATABASE": os.environ.get("NEO4J_DATABASE") or file_values.get("NEO4J_DATABASE", ""),
    }
    if not values["NEO4J_USERNAME"]:
        values["NEO4J_USERNAME"] = os.environ.get("NEO4J_USER") or file_values.get("NEO4J_USER", "")
    return values


def parse_neo4j_uri(uri: str) -> dict[str, Any]:
    parsed = urlparse(uri)
    default_port = 7687
    return {
        "uri_scheme": parsed.scheme or "",
        "host": parsed.hostname or "",
        "port": parsed.port or default_port,
    }


def sanitize_error(exc: BaseException) -> str:
    message = str(exc)
    # Avoid accidental credential echo if a driver embeds auth in a message.
    for key in ("NEO4J_PASSWORD", "password", "auth"):
        message = message.replace(key, "[redacted]")
    return f"{type(exc).__name__}: {message}"


def test_tcp(host: str, port: int, timeout: float = 5.0) -> tuple[str, str | None]:
    if not host or not port:
        return "unknown", "missing host or port"
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return "yes", None
    except Exception as exc:  # noqa: BLE001 - diagnostics should capture safely.
        return "no", sanitize_error(exc)


def verify_driver(env: dict[str, str]) -> tuple[bool, str | None]:
    try:
        from neo4j import GraphDatabase

        driver = GraphDatabase.driver(
            env["NEO4J_URI"],
            auth=(env["NEO4J_USERNAME"], env["NEO4J_PASSWORD"]),
        )
        try:
            driver.verify_connectivity()
        finally:
            driver.close()
        return True, None
    except Exception as exc:  # noqa: BLE001 - diagnostics should capture safely.
        return False, sanitize_error(exc)


def likely_issue(config_loaded: bool, tcp_reachable: str, driver_ok: bool, error: str | None) -> str:
    text = (error or "").lower()
    if not config_loaded:
        return "missing_config"
    if tcp_reachable == "no":
        if "refused" in text or "10061" in text:
            return "neo4j_not_listening_or_wrong_port"
        if "timed out" in text or "timeout" in text:
            return "network_timeout_or_firewall"
        return "tcp_unreachable"
    if tcp_reachable == "yes" and not driver_ok:
        if "authentication" in text or "unauthorized" in text:
            return "auth_failed"
        if "database" in text and ("not found" in text or "does not exist" in text):
            return "wrong_database"
        return "driver_handshake_or_auth_issue"
    if driver_ok:
        return "none"
    return "unknown"


def recommended_fix(issue: str) -> str:
    if issue == "missing_config":
        return "Populate NEO4J_URI, NEO4J_USERNAME, NEO4J_PASSWORD, and NEO4J_DATABASE in process env or repo .env."
    if issue == "neo4j_not_listening_or_wrong_port":
        return "Start the intended Neo4j instance and confirm the Bolt listener host/port in .env."
    if issue == "network_timeout_or_firewall":
        return "Check firewall/VPN/network access to the configured Neo4j Bolt host and port."
    if issue == "tcp_unreachable":
        return "Verify the configured Neo4j host and port are reachable from this machine."
    if issue == "auth_failed":
        return "Verify Neo4j username/password in .env without printing them."
    if issue == "wrong_database":
        return "Check NEO4J_DATABASE against the target Neo4j instance."
    if issue == "driver_handshake_or_auth_issue":
        return "Verify URI scheme, TLS mode, credentials, and Neo4j server compatibility."
    if issue == "none":
        return "Connectivity is verified; schema introspection can be rerun next."
    return "Review sanitized diagnostics and update .env or the Neo4j service before rerunning introspection."


def render_markdown(data: dict[str, Any]) -> str:
    present = data["env_present"]
    return f"""# Neo4j Connection Diagnostics

Generated: {data['generated_at']}

## Config Presence

- `NEO4J_URI`: {present['NEO4J_URI']}
- `NEO4J_USERNAME`: {present['NEO4J_USERNAME']}
- `NEO4J_PASSWORD`: {present['NEO4J_PASSWORD']}
- `NEO4J_DATABASE`: {present['NEO4J_DATABASE']}
- secret values printed: false

## Endpoint

- uri scheme: `{data['uri_scheme']}`
- host: `{data['host']}`
- port: {data['port']}
- database used: `{data['database_used']}`

## Connectivity

- TCP reachable: {data['tcp_reachable']}
- driver connectivity verified: {data['driver_connectivity_verified']}
- sanitized error: `{data['sanitized_error'] or 'none'}`

## Decision

- coverage executed: false
- dry-run executed: false
- full eval executed: false
- Neo4j write performed: false
- KG patch applied: false
- likely issue: `{data['likely_issue']}`
- recommended fix: {data['recommended_fix']}
"""


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Neo4j connection diagnostics only.")
    parser.add_argument("--run-dir", default=None)
    args = parser.parse_args(argv)
    run_dir = run_dir_from_arg(args.run_dir)

    env = effective_env()
    env_present = {key: bool(env.get(key)) for key in ENV_KEYS}
    config_loaded = all(env_present.values())
    parsed = parse_neo4j_uri(env.get("NEO4J_URI", ""))
    tcp_reachable = "unknown"
    tcp_error: str | None = None
    driver_ok = False
    driver_error: str | None = None

    if config_loaded:
        tcp_reachable, tcp_error = test_tcp(parsed["host"], int(parsed["port"]))
        if tcp_reachable == "yes":
            driver_ok, driver_error = verify_driver(env)
        else:
            driver_error = "Driver verify_connectivity skipped because TCP reachability failed."

    sanitized_error = driver_error or tcp_error
    issue = likely_issue(config_loaded, tcp_reachable, driver_ok, sanitized_error)
    data: dict[str, Any] = {
        "generated_at": now(),
        "config_loaded": config_loaded,
        "env_present": env_present,
        "uri_scheme": parsed["uri_scheme"],
        "host": parsed["host"],
        "port": parsed["port"],
        "tcp_reachable": tcp_reachable,
        "tcp_error": tcp_error,
        "driver_connectivity_verified": driver_ok,
        "database_used": env.get("NEO4J_DATABASE", ""),
        "sanitized_error": sanitized_error,
        "likely_issue": issue,
        "recommended_fix": recommended_fix(issue),
        "safety": {
            "coverage_executed": False,
            "neo4j_write_performed": False,
            "kg_patch_applied": False,
            "dry_run_executed": False,
            "full_eval_executed": False,
            "secrets_printed": False,
        },
    }
    write_text(
        run_dir / "neo4j_connection_diagnostics.json",
        json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True),
    )
    write_text(run_dir / "neo4j_connection_diagnostics.md", render_markdown(data))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
