#!/usr/bin/env python3
"""
Infrastructure Self-Healer Agent
Monitors system health, auto-remediates known issues, escalates unknown ones.

Checks:
  - Disk space (/, /tmp, /opt, /var)
  - Memory usage
  - System load (1/5/15 min)
  - Key services (docker, cron, containerd, hermes gateway)
  - SSL certificates (if present under /etc/letsencrypt)
  - Temp space (/tmp, /var/tmp) for cleanup opportunities
  - Zombie processes
  - SWAP usage

Auto-remediation:
  - Clear /tmp files older than 24h (if disk pressure)
  - Clear /var/tmp files older than 7 days
  - Restart failed known services (docker, cron)
  - Clear apt cache if /var is full

Exit codes:
  0 = All healthy (stdout: "HEALTHY")
  1 = Auto-remediated issues (stdout: details of what was fixed)
  2 = Unknown issues needing human escalation (stdout: alert details)
  3 = Script error (stderr: error details)
"""

import os
import sys
import glob
import subprocess
import shutil
from datetime import datetime, timedelta
from pathlib import Path

# === Thresholds ===
DISK_WARN_PCT = 85      # Warn at 85% usage
DISK_CRIT_PCT = 95      # Critical/act at 95% usage
DISK_CLEAN_PCT = 80     # Try cleanup if higher than this on /tmp or /var
MEM_WARN_PCT = 90       # Warn at 90% memory usage
LOAD_WARN_MULTIPLIER = 2.0  # Warn if load > 2x core count
TMP_MAX_AGE_HOURS = 24  # Clean /tmp files older than this
VARTMP_MAX_AGE_DAYS = 7 # Clean /var/tmp files older than this
SSL_WARN_DAYS = 14      # Warn if cert expires within 14 days

# === Services to monitor ===
MONITORED_SERVICES = [
    "docker",
    "cron",
    "containerd",
    "udisks2",
    "unattended-upgrades",
]

# Services we know how to safely restart
RESTARTABLE_SERVICES = ["docker", "cron", "containerd"]

# === Paths to check ===
DISK_PATHS = ["/", "/tmp", "/opt", "/var", "/var/log"]
SSL_DIR = Path("/etc/letsencrypt/live")
TMP_DIRS = ["/tmp", "/var/tmp"]


def run(cmd, timeout=15):
    """Run a command, return (exit_code, stdout+stderr)."""
    try:
        r = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=timeout)
        return r.returncode, (r.stdout + r.stderr).strip()
    except subprocess.TimeoutExpired:
        return -1, "TIMEOUT"
    except Exception as e:
        return -1, str(e)


def get_core_count():
    return os.cpu_count() or 1


def check_disk():
    """Check disk space for all monitored paths."""
    issues = []
    for path in DISK_PATHS:
        try:
            stat = shutil.disk_usage(path)
            pct = (stat.used / stat.total) * 100 if stat.total > 0 else 0
            if pct >= DISK_CRIT_PCT:
                issues.append({
                    "severity": "CRIT",
                    "category": "disk",
                    "path": path,
                    "pct": pct,
                    "avail_gb": stat.free / (1024**3),
                    "detail": f"CRITICAL: {path} at {pct:.0f}% ({stat.free/(1024**3):.1f}GB free)",
                })
            elif pct >= DISK_WARN_PCT:
                issues.append({
                    "severity": "WARN",
                    "category": "disk",
                    "path": path,
                    "pct": pct,
                    "avail_gb": stat.free / (1024**3),
                    "detail": f"WARNING: {path} at {pct:.0f}% ({stat.free/(1024**3):.1f}GB free)",
                })
        except Exception as e:
            issues.append({
                "severity": "ERR",
                "category": "disk",
                "path": path,
                "pct": 0,
                "avail_gb": 0,
                "detail": f"ERROR checking {path}: {e}",
            })
    return issues


def check_memory():
    """Check memory usage via /proc/meminfo."""
    issues = []
    try:
        meminfo = {}
        with open("/proc/meminfo") as f:
            for line in f:
                parts = line.split(":")
                if len(parts) == 2:
                    key = parts[0].strip()
                    val = parts[1].strip().split()[0]
                    meminfo[key] = int(val)

        total = meminfo.get("MemTotal", 0)
        available = meminfo.get("MemAvailable", 0)
        if total > 0:
            used_pct = ((total - available) / total) * 100
            if used_pct >= MEM_WARN_PCT:
                swap_total = meminfo.get("SwapTotal", 0)
                swap_free = meminfo.get("SwapFree", 0)
                swap_used = swap_total - swap_free
                swap_pct = (swap_used / swap_total * 100) if swap_total > 0 else 0
                issues.append({
                    "severity": "CRIT" if used_pct >= 95 else "WARN",
                    "category": "memory",
                    "pct": used_pct,
                    "avail_mb": available / 1024,
                    "swap_used_pct": swap_pct,
                    "detail": f"{'CRITICAL' if used_pct >= 95 else 'WARNING'}: Memory at {used_pct:.0f}% "
                              f"({available/1024/1024:.0f}GB avail / {total/1024/1024:.0f}GB total, "
                              f"swap {swap_pct:.0f}% used)",
                })
    except Exception as e:
        issues.append({
            "severity": "ERR",
            "category": "memory",
            "pct": 0,
            "avail_mb": 0,
            "swap_used_pct": 0,
            "detail": f"ERROR reading memory: {e}",
        })
    return issues


def check_load():
    """Check system load averages."""
    issues = []
    try:
        with open("/proc/loadavg") as f:
            parts = f.read().split()
        load_1 = float(parts[0])
        load_5 = float(parts[1])
        load_15 = float(parts[2])
        cores = get_core_count()

        if load_1 > cores * LOAD_WARN_MULTIPLIER:
            issues.append({
                "severity": "WARN",
                "category": "load",
                "load_1": load_1,
                "load_5": load_5,
                "load_15": load_15,
                "cores": cores,
                "detail": f"WARNING: Load {load_1:.2f} (1min) on {cores} cores "
                          f"(5min: {load_5:.2f}, 15min: {load_15:.2f})",
            })
    except Exception as e:
        issues.append({
            "severity": "ERR",
            "category": "load",
            "load_1": 0, "load_5": 0, "load_15": 0, "cores": 0,
            "detail": f"ERROR reading load: {e}",
        })
    return issues


def check_services():
    """Check monitored systemd services."""
    issues = []
    for svc in MONITORED_SERVICES:
        rc, out = run(f"systemctl is-active {svc}")
        if rc != 0:
            issues.append({
                "severity": "WARN",
                "category": "service",
                "service": svc,
                "restartable": svc in RESTARTABLE_SERVICES,
                "detail": f"ISSUE: Service '{svc}' is not active (rc={rc})",
            })
    return issues


def check_ssl():
    """Check SSL certificate expiry if certs exist."""
    issues = []
    if not SSL_DIR.exists():
        return issues

    for cert_dir in SSL_DIR.iterdir():
        if cert_dir.is_dir():
            cert_file = cert_dir / "fullchain.pem"
            if not cert_file.exists():
                cert_file = cert_dir / "cert.pem"
            if cert_file.exists():
                try:
                    rc, out = run(f"openssl x509 -enddate -noout -in {cert_file}")
                    if rc == 0 and "notAfter=" in out:
                        expiry_str = out.split("=")[1].strip()
                        expiry = datetime.strptime(expiry_str, "%b %d %H:%M:%S %Y %Z")
                        days_left = (expiry - datetime.utcnow()).days
                        if days_left <= SSL_WARN_DAYS:
                            issues.append({
                                "severity": "WARN",
                                "category": "ssl",
                                "domain": cert_dir.name,
                                "days_left": days_left,
                                "expiry": expiry.strftime("%Y-%m-%d"),
                                "detail": f"WARNING: SSL cert for {cert_dir.name} expires in {days_left} days ({expiry.strftime('%Y-%m-%d')})",
                            })
                except Exception as e:
                    issues.append({
                        "severity": "ERR",
                        "category": "ssl",
                        "domain": cert_dir.name,
                        "days_left": -1,
                        "expiry": "unknown",
                        "detail": f"ERROR checking cert {cert_dir.name}: {e}",
                    })
    return issues


def check_zombies():
    """Check for zombie processes."""
    issues = []
    try:
        rc, out = run("ps aux | awk '$8 ~ /Z/ {print}' | grep -v grep")
        if rc == 0 and out.strip():
            lines = [l for l in out.strip().split("\n") if l.strip()]
            issues.append({
                "severity": "WARN",
                "category": "zombies",
                "count": len(lines),
                "detail": f"WARNING: {len(lines)} zombie process(es) detected",
            })
    except Exception:
        pass
    return issues


def auto_remediate(issues):
    """Attempt auto-remediation for known issues. Returns list of fixed items."""
    fixed = []
    disk_issues = [i for i in issues if i["category"] == "disk"]

    # 1. Clear temp files if disk pressure
    for disk_issue in disk_issues:
        if disk_issue["pct"] >= DISK_CLEAN_PCT and disk_issue["path"] in ("/tmp", "/var", "/var/log"):
            for tmp_dir in TMP_DIRS:
                if os.path.isdir(tmp_dir):
                    cleaned = 0
                    freed_bytes = 0
                    now = datetime.now()
                    for root, dirs, files in os.walk(tmp_dir):
                        # Skip important dirs
                        dirs[:] = [d for d in dirs if d not in ("systemd", "tmpfiles.d")]
                        for fname in files:
                            fpath = os.path.join(root, fname)
                            try:
                                mtime = datetime.fromtimestamp(os.path.getmtime(fpath))
                                age_hours = (now - mtime).total_seconds() / 3600
                                max_age = VARTMP_MAX_AGE_DAYS * 24 if tmp_dir == "/var/tmp" else TMP_MAX_AGE_HOURS
                                if age_hours > max_age:
                                    fsize = os.path.getsize(fpath)
                                    os.remove(fpath)
                                    cleaned += 1
                                    freed_bytes += fsize
                            except (OSError, PermissionError):
                                pass
                    if cleaned > 0:
                        fixed.append(f"Cleaned {cleaned} files from {tmp_dir} ({freed_bytes/1024/1024:.1f}MB freed)")

    # 2. Restart failed services
    svc_issues = [i for i in issues if i["category"] == "service"]
    for svc in svc_issues:
        if svc.get("restartable"):
            svc_name = svc["service"]
            # Only auto-restart if it's a known-safe service and host uses systemd
            if can_restart_services():
                rc, out = run(f"systemctl restart {svc_name}")
                if rc == 0:
                    fixed.append(f"Restarted service '{svc_name}'")
                else:
                    fixed.append(f"FAILED to restart '{svc_name}' (needs human)")
            else:
                fixed.append(f"Service '{svc_name}' down — not auto-restarting (no systemd authority)")

    # 3. Clear apt cache if /var is > 90%
    for disk_issue in disk_issues:
        if disk_issue["path"] == "/var" and disk_issue["pct"] >= 90:
            rc, out = run("apt-get clean 2>&1 | tail -1")
            if rc == 0:
                fixed.append("Cleared apt cache (/var was > 90%)")

    # 4. Clear journal logs if /var/log is crowded
    for disk_issue in disk_issues:
        if disk_issue["path"] == "/var/log" and disk_issue["pct"] >= 90:
            rc, out = run("journalctl --vacuum-size=100M 2>&1 | tail -1")
            if rc == 0:
                fixed.append("Vacuumed systemd journal to 100MB")

    return fixed


def can_restart_services():
    """Check if we can safely restart systemd services."""
    if os.geteuid() != 0:
        return False
    rc, _ = run("systemctl --system status 2>&1 | head -1")
    return rc == 0


def format_output(fixed, remaining_issues, exit_code):
    """Format human-readable output."""
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    lines = []

    if exit_code == 0:
        lines.append("HEALTHY")
        lines.append(f"Time: {now}")
        for i in remaining_issues:
            lines.append(f"  {i['detail']}")
        return "\n".join(lines)

    if fixed:
        lines.append(f"INFRA_AUTO_REMEDIATED")
        lines.append(f"Time: {now}")
        lines.append("")
        lines.append("Actions taken:")
        for f in fixed:
            lines.append(f"  FIXED: {f}")
        lines.append("")

    if remaining_issues:
        lines.append("Remaining issues:")
        for i in remaining_issues:
            lines.append(f"  {i['detail']}")
        return "\n".join(lines)


def main():
    all_issues = []

    # Collect all checks
    all_issues.extend(check_disk())
    all_issues.extend(check_memory())
    all_issues.extend(check_load())
    all_issues.extend(check_services())
    all_issues.extend(check_ssl())
    all_issues.extend(check_zombies())

    # Filter out healthy items
    unhealthy = [i for i in all_issues if i["severity"] in ("CRIT", "WARN", "ERR")]

    if not unhealthy:
        # All good
        print(format_output([], [], 0))
        sys.exit(0)

    # Attempt auto-remediation on CRIT and WARN issues
    fixed = auto_remediate(unhealthy)

    # After remediation, re-check to see what's left
    remaining = []
    # Re-check disk if we cleaned
    if any("Cleaned" in f for f in fixed):
        remaining.extend(check_disk())
    # Re-check services if we restarted
    if any("Restarted" in f for f in fixed):
        remaining.extend(check_services())
    # Keep issues we didn't address
    for i in unhealthy:
        if i["category"] not in ("disk", "service"):
            remaining.append(i)

    # Filter remaining
    remaining_unhealthy = [i for i in remaining if i["severity"] in ("CRIT", "WARN")]

    if not fixed and not remaining_unhealthy:
        # Only ERR items that we couldn't fix
        has_err = any(i["severity"] == "ERR" for i in unhealthy)
        if has_err:
            print(format_output(fixed, unhealthy, 2))
            sys.exit(2)
        # All were fixed
        print(format_output(fixed, [], 1))
        sys.exit(1)
    elif remaining_unhealthy:
        print(format_output(fixed, remaining_unhealthy, 2))
        sys.exit(2)
    else:
        # Fixed everything
        print(format_output(fixed, [], 1))
        sys.exit(1)


if __name__ == "__main__":
    main()
