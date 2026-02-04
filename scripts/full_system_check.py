#!/usr/bin/env python3
"""Full system diagnostics for AILA Trading Bot. Read-only, changes nothing."""

import json
import os
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

BASE = Path("/opt/aila")
DATA = BASE / "data" / "ai_trade"
LOGS = BASE / "logs"
CREDS_OPT = BASE / ".claude" / ".credentials.json"
CREDS_HOME = Path("/home/aila/.claude/.credentials.json")
SUB_FILE = BASE / "data" / "subscription.json"
AUTOPILOT_FILE = DATA / "autopilot.json"
API = "http://localhost:8080"

report = {
    "timestamp": datetime.now().isoformat(),
    "verdict": "ALL_OK",
    "checks": {},
    "errors": [],
    "warnings": [],
    "recommendations": [],
}


def log(msg: str) -> None:
    """Print to stderr for human-readable output."""
    print(msg, file=sys.stderr)


def run(cmd: str, timeout: int = 10) -> tuple[int, str]:
    """Run shell command, return (returncode, stdout)."""
    try:
        r = subprocess.run(
            cmd, shell=True, capture_output=True, text=True, timeout=timeout
        )
        return r.returncode, (r.stdout.strip() or r.stderr.strip())
    except subprocess.TimeoutExpired:
        return -1, "TIMEOUT"
    except Exception as e:
        return -1, str(e)


def read_json(path: Path) -> dict | list | None:
    """Read JSON file safely."""
    try:
        if path.exists():
            return json.loads(path.read_text())
    except Exception:
        pass
    return None


def add_error(msg: str) -> None:
    report["errors"].append(msg)
    report["verdict"] = "CRITICAL"


def add_warning(msg: str) -> None:
    report["warnings"].append(msg)
    if report["verdict"] == "ALL_OK":
        report["verdict"] = "WARNINGS"


# =============================================
# 1. Infrastructure
# =============================================
def check_infrastructure() -> dict:
    log("\n=== 1. INFRASTRUCTURE ===")
    details = {}

    # Service status
    rc, out = run("systemctl is-active aila")
    details["service"] = out
    if out != "active":
        add_error(f"Service aila is {out}")
    log(f"  Service: {out}")

    # Port
    rc, out = run("ss -tlnp | grep ':8080'")
    details["port_8080"] = bool(out)
    if not out:
        add_error("Port 8080 not listening")
    log(f"  Port 8080: {'OK' if out else 'NOT LISTENING'}")

    # Disk
    rc, out = run("df -h /opt/aila --output=size,used,avail,pcent | tail -1")
    details["disk"] = out.strip()
    if out:
        parts = out.split()
        if len(parts) >= 4:
            pct = int(parts[3].replace("%", ""))
            if pct > 90:
                add_warning(f"Disk usage {pct}%")
    log(f"  Disk: {out.strip()}")

    # RAM
    rc, out = run("free -m | grep Mem | awk '{print $3\"/\"$2\" MB (\"int($3/$2*100)\"%)\"}' ")
    details["ram"] = out
    log(f"  RAM: {out}")

    # Uptime
    rc, out = run("uptime -p")
    details["uptime"] = out
    log(f"  Uptime: {out}")

    # PID
    rc, out = run("systemctl show aila --property=MainPID --value")
    details["pid"] = out
    log(f"  PID: {out}")

    status = "ok" if not any("Infrastructure" in e or "Service" in e or "Port" in e for e in report["errors"]) else "error"
    return {"status": status, "details": details}


# =============================================
# 2. OAuth & Subscription
# =============================================
def check_oauth() -> dict:
    log("\n=== 2. OAUTH & SUBSCRIPTION ===")
    details = {}

    now_ms = time.time() * 1000

    # /opt/aila credentials
    creds = read_json(CREDS_OPT)
    if creds and "claudeAiOauth" in creds:
        exp = creds["claudeAiOauth"].get("expiresAt", 0)
        hours = max(0, (exp - now_ms) / 3_600_000)
        details["opt_token_hours"] = round(hours, 1)
        details["subscription_type"] = creds["claudeAiOauth"].get("subscriptionType", "?")
        if hours <= 0:
            add_error("OAuth token EXPIRED in /opt/aila")
        elif hours < 2:
            add_warning(f"OAuth token expires in {hours:.1f}h")
        log(f"  /opt/aila token: {hours:.1f}h remaining ({details['subscription_type']})")
    else:
        add_error("No OAuth credentials in /opt/aila")
        log("  /opt/aila token: NOT FOUND")

    # /home/aila credentials
    creds_home = read_json(CREDS_HOME)
    if creds_home and "claudeAiOauth" in creds_home:
        exp_h = creds_home["claudeAiOauth"].get("expiresAt", 0)
        hours_h = max(0, (exp_h - now_ms) / 3_600_000)
        details["home_token_hours"] = round(hours_h, 1)
        synced = abs(exp - exp_h) < 60000 if creds else False
        details["credentials_synced"] = synced
        if not synced:
            add_warning("Credentials not synced between /opt/aila and /home/aila")
        log(f"  /home/aila token: {hours_h:.1f}h | Synced: {synced}")
    else:
        details["home_token_hours"] = None
        log("  /home/aila token: NOT FOUND")

    # Subscription
    sub = read_json(SUB_FILE)
    if sub:
        billing = sub.get("next_billing_date", "")
        if billing:
            try:
                target = datetime.strptime(billing, "%Y-%m-%d").date()
                days = (target - datetime.now().date()).days
                details["subscription_days"] = days
                details["next_billing"] = billing
                if days <= 0:
                    add_error("Subscription EXPIRED")
                elif days <= 3:
                    add_warning(f"Subscription expires in {days} days")
                log(f"  Subscription: {days} days until {billing}")
            except ValueError:
                log(f"  Subscription: invalid date {billing}")
    else:
        details["subscription_days"] = None
        log("  Subscription: file not found")

    has_err = any("OAuth" in e or "Subscription" in e for e in report["errors"])
    return {"status": "error" if has_err else "ok", "details": details}


# =============================================
# 3. Agents
# =============================================
def check_agents() -> dict:
    log("\n=== 3. AGENTS ===")
    agents_dir = BASE / "aila" / "ai_trade" / "agents"
    details = {"found": [], "missing": []}

    expected = [
        "analyst", "arbitrage", "base_agent", "hedge_master",
        "logger_agent", "mentor", "news_agent", "predictor",
        "researcher", "reviewer", "risk_guard", "sniper",
        "trader", "war_room", "whale_tracker",
    ]

    if agents_dir.exists():
        files = [f.stem for f in agents_dir.glob("*.py") if f.stem != "__init__"]
        details["found"] = sorted(files)
        for a in expected:
            if a not in files:
                details["missing"].append(a)
        log(f"  Found: {len(files)} agent files")
        if details["missing"]:
            add_warning(f"Missing agents: {details['missing']}")
            log(f"  Missing: {details['missing']}")
    else:
        add_warning("Agents directory not found")
        log("  Agents dir: NOT FOUND")

    # Check agent logs
    agent_logs_dir = LOGS / "ai_trade"
    details["recent_activity"] = {}
    if agent_logs_dir.exists():
        for lf in sorted(agent_logs_dir.glob("*.log")):
            rc, out = run(f"tail -1 '{lf}' 2>/dev/null | head -1")
            if out:
                details["recent_activity"][lf.stem] = out[:120]

    log(f"  Log files with activity: {len(details['recent_activity'])}")

    return {"status": "ok" if not details["missing"] else "warning", "details": details}


# =============================================
# 4. Autopilot
# =============================================
def check_autopilot() -> dict:
    log("\n=== 4. AUTOPILOT ===")
    details = {}

    ap = read_json(AUTOPILOT_FILE)
    if ap:
        details["config"] = {
            "enabled": ap.get("enabled"),
            "mode": ap.get("mode"),
            "interval": ap.get("interval_minutes"),
        }
        log(f"  Config: enabled={ap.get('enabled')}, mode={ap.get('mode')}")
    else:
        log("  Config: file not found")

    # API status
    rc, out = run(f"curl -s --max-time 5 {API}/api/ai-trade/autopilot/status")
    try:
        status = json.loads(out)
        details["api_status"] = {
            "running": status.get("running"),
            "mode": status.get("mode"),
            "cycles": status.get("total_cycles"),
        }
        log(f"  API: running={status.get('running')}, cycles={status.get('total_cycles')}")
    except (json.JSONDecodeError, TypeError):
        details["api_status"] = None
        log(f"  API: unavailable")

    return {"status": "ok", "details": details}


# =============================================
# 5. Trading
# =============================================
def check_trading() -> dict:
    log("\n=== 5. TRADING ===")
    details = {}

    # Positions
    rc, out = run(f"curl -s --max-time 5 {API}/api/ai-trade/positions")
    try:
        data = json.loads(out)
        positions = data if isinstance(data, list) else data.get("positions", [])
        details["open_positions"] = len(positions)
        log(f"  Open positions: {len(positions)}")
    except (json.JSONDecodeError, TypeError):
        details["open_positions"] = "unavailable"
        log("  Positions: endpoint unavailable")

    # Balance
    rc, out = run(f"curl -s --max-time 5 {API}/api/ai-trade/balance")
    try:
        data = json.loads(out)
        bal = data.get("balance") or data.get("total")
        details["balance"] = bal
        log(f"  Balance: ${bal}")
    except (json.JSONDecodeError, TypeError):
        details["balance"] = "unavailable"
        log("  Balance: endpoint unavailable")

    # Trading state
    ts = read_json(DATA / "trading_state.json")
    if ts:
        details["trading_state"] = {
            "total_trades": ts.get("total_trades"),
            "active_pairs": len(ts.get("active_pairs", [])),
        }
        log(f"  Trading state: {ts.get('total_trades')} trades")

    return {"status": "ok", "details": details}


# =============================================
# 6. Data files
# =============================================
def check_data() -> dict:
    log("\n=== 6. DATA FILES ===")
    details = {"files": {}}

    critical_files = [
        "autopilot.json", "capital.json", "evolution.json",
        "knowledge_base.json", "paper_trading.json", "risk_stats.json",
        "sniper_stats.json", "trader_stats.json", "trading_state.json",
        "war_room.json",
    ]

    missing = []
    for fn in critical_files:
        fp = DATA / fn
        if fp.exists():
            stat = fp.stat()
            details["files"][fn] = {
                "size": stat.st_size,
                "modified": datetime.fromtimestamp(stat.st_mtime).isoformat(),
            }
        else:
            missing.append(fn)

    details["total_files"] = len(details["files"])
    details["missing"] = missing
    log(f"  Found: {len(details['files'])}/{len(critical_files)} critical files")
    if missing:
        add_warning(f"Missing data files: {missing}")
        log(f"  Missing: {missing}")

    return {"status": "ok" if not missing else "warning", "details": details}


# =============================================
# 7. Logs
# =============================================
def check_logs() -> dict:
    log("\n=== 7. LOGS ===")
    details = {}

    # Total size
    rc, out = run("du -sh /opt/aila/logs/ 2>/dev/null")
    details["total_size"] = out.split("\t")[0] if out else "?"
    log(f"  Total size: {details['total_size']}")

    # Error counts
    log_dirs = [LOGS, LOGS / "ai_trade"]
    error_count = 0
    for ld in log_dirs:
        if ld.exists():
            rc, out = run(f"grep -rci 'ERROR\\|EXCEPTION\\|TRACEBACK' '{ld}'/*.log 2>/dev/null | awk -F: '{{s+=$2}}END{{print s}}'")
            try:
                error_count += int(out)
            except (ValueError, TypeError):
                pass
    details["total_errors"] = error_count
    if error_count > 100:
        add_warning(f"High error count in logs: {error_count}")
    log(f"  Total errors/exceptions: {error_count}")

    # Last 5 errors
    rc, out = run("grep -rh 'ERROR\\|EXCEPTION' /opt/aila/logs/ai_trade/*.log 2>/dev/null | tail -5")
    details["recent_errors"] = out.split("\n") if out else []
    for e in details["recent_errors"][:3]:
        log(f"  >> {e[:100]}")

    return {"status": "ok" if error_count < 50 else "warning", "details": details}


# =============================================
# 8. Backups
# =============================================
def check_backups() -> dict:
    log("\n=== 8. BACKUPS ===")
    details = {}
    backup_dir = BASE / "backups"

    if backup_dir.exists():
        files = sorted(backup_dir.iterdir(), key=lambda f: f.stat().st_mtime, reverse=True)
        details["count"] = len(files)
        if files:
            latest = files[0]
            details["latest"] = {
                "name": latest.name,
                "size": latest.stat().st_size,
                "date": datetime.fromtimestamp(latest.stat().st_mtime).isoformat(),
            }
            log(f"  Backups: {len(files)}, latest: {latest.name}")
        else:
            add_warning("No backup files found")
            log("  Backups: directory empty")
    else:
        details["count"] = 0
        add_warning("Backup directory not found")
        log("  Backups: directory not found")

    return {"status": "ok" if details.get("count", 0) > 0 else "warning", "details": details}


# =============================================
# 9. Network
# =============================================
def check_network() -> dict:
    log("\n=== 9. NETWORK ===")
    details = {}

    # Bybit API
    rc, out = run("curl -s --max-time 5 'https://api.bybit.com/v5/market/tickers?category=linear&symbol=BTCUSDT'")
    try:
        data = json.loads(out)
        if data.get("retCode") == 0:
            price = data["result"]["list"][0]["lastPrice"]
            details["bybit"] = {"status": "ok", "btc_price": price}
            log(f"  Bybit: OK (BTC=${price})")
        else:
            details["bybit"] = {"status": "error", "msg": data.get("retMsg")}
            add_warning("Bybit API returned error")
            log(f"  Bybit: ERROR {data.get('retMsg')}")
    except Exception:
        details["bybit"] = {"status": "unreachable"}
        add_error("Bybit API unreachable")
        log("  Bybit: UNREACHABLE")

    # Claude CLI
    rc, out = run("claude --version 2>/dev/null")
    details["claude_cli"] = out if rc == 0 else "not found"
    log(f"  Claude CLI: {details['claude_cli']}")

    return {"status": "ok" if details.get("bybit", {}).get("status") == "ok" else "warning", "details": details}


# =============================================
# Main
# =============================================
def main() -> None:
    log("=" * 60)
    log("  AILA Full System Diagnostics")
    log(f"  {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    log("=" * 60)

    report["checks"]["infrastructure"] = check_infrastructure()
    report["checks"]["oauth"] = check_oauth()
    report["checks"]["agents"] = check_agents()
    report["checks"]["autopilot"] = check_autopilot()
    report["checks"]["trading"] = check_trading()
    report["checks"]["data"] = check_data()
    report["checks"]["logs"] = check_logs()
    report["checks"]["backups"] = check_backups()
    report["checks"]["network"] = check_network()

    # Recommendations
    if report["checks"]["oauth"]["details"].get("opt_token_hours", 0) < 4:
        report["recommendations"].append("OAuth token low — check auto-refresh")
    if report["checks"]["data"]["details"].get("missing"):
        report["recommendations"].append("Recreate missing data files")
    if report["checks"]["backups"]["details"].get("count", 0) == 0:
        report["recommendations"].append("Set up automated backups")
    if report["checks"]["logs"]["details"].get("total_errors", 0) > 100:
        report["recommendations"].append("Review and fix frequent errors in logs")

    # Summary
    log("\n" + "=" * 60)
    log(f"  VERDICT: {report['verdict']}")
    log(f"  Errors: {len(report['errors'])}")
    log(f"  Warnings: {len(report['warnings'])}")
    if report["errors"]:
        log("\n  ERRORS:")
        for e in report["errors"]:
            log(f"    ❌ {e}")
    if report["warnings"]:
        log("\n  WARNINGS:")
        for w in report["warnings"]:
            log(f"    ⚠️  {w}")
    if report["recommendations"]:
        log("\n  RECOMMENDATIONS:")
        for r in report["recommendations"]:
            log(f"    💡 {r}")
    log("=" * 60)

    # JSON to stdout
    print(json.dumps(report, indent=2, ensure_ascii=False, default=str))


if __name__ == "__main__":
    main()
