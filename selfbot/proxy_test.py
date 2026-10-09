"""Proxy testing: build xray.json from a config, run Xray on a test port,
measure TCP latency, and detect the real egress IP via the proxy."""
import asyncio
import json
import os
import socket
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Optional

import requests

from .config import CONFIG, DB_PATH
from .logging_setup import log
from .retry import http_retry

XRAY_CONFIGS_FILE = Path("/app/xray-configs.json")
if not XRAY_CONFIGS_FILE.parent.exists():
    XRAY_CONFIGS_FILE = DB_PATH.parent / "xray-configs.json"

XRAY_BIN = "xray"
TEST_PORT_BASE = 12080


# ---------------------------------------------------------------------------
# Load / save configs
# ---------------------------------------------------------------------------
def _load_configs() -> dict:
    if XRAY_CONFIGS_FILE.exists():
        try:
            return json.loads(XRAY_CONFIGS_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {"active": "default", "configs": {}}


def _save_configs(data: dict) -> None:
    tmp = XRAY_CONFIGS_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
    os.replace(tmp, XRAY_CONFIGS_FILE)


def list_configs() -> dict:
    return _load_configs()


def add_config(key: str, name: str, url: str, outbound: dict) -> None:
    data = _load_configs()
    data.setdefault("configs", {})[key] = {
        "name": name, "type": "vless", "url": url, "xray_outbound": outbound
    }
    _save_configs(data)


def set_active(key: str) -> bool:
    data = _load_configs()
    if key not in (data.get("configs") or {}):
        return False
    data["active"] = key
    _save_configs(data)
    try:
        write_live_xray_config(key)
    except Exception as e:
        log.warning(f"could not write live xray.json: {e}")
    return True


def get_active_config() -> Optional[dict]:
    data = _load_configs()
    key = data.get("active", "default")
    return (data.get("configs") or {}).get(key)


# ---------------------------------------------------------------------------
# Build a full Xray config JSON for a given outbound
# ---------------------------------------------------------------------------
def build_xray_config(outbound: dict, socks_port: int) -> dict:
    return {
        "log": {"loglevel": "warning"},
        "inbounds": [{
            "port": socks_port,
            "listen": "127.0.0.1",
            "protocol": "socks",
            "settings": {"auth": "noauth", "udp": True, "ip": "127.0.0.1"},
            "sniffing": {"enabled": True, "destOverride": ["http", "tls"]}
        }],
        "outbounds": [outbound, {"protocol": "freedom", "tag": "direct"}]
    }


def write_live_xray_config(key: str, live_path: Path = None) -> None:
    data = _load_configs()
    cfg = (data.get("configs") or {}).get(key)
    if not cfg:
        raise ValueError(f"config {key!r} not found")
    outbound = cfg.get("xray_outbound")
    if not outbound:
        raise ValueError(f"config {key!r} has no xray_outbound")
    full = build_xray_config(outbound, socks_port=1080)
    target = live_path or Path("/app/xray.json")
    tmp = target.with_suffix(".tmp")
    tmp.write_text(json.dumps(full, indent=2), encoding="utf-8")
    os.replace(tmp, target)


# ---------------------------------------------------------------------------
# TCP latency test to Telegram DCs through a SOCKS port
# ---------------------------------------------------------------------------
TG_DCS = [
    ("149.154.167.51", 443),
    ("149.154.175.100", 443),
    ("149.154.171.5", 443),
]


def tcp_latency_via_socks(socks_host: str, socks_port: int, dest_host: str,
                          dest_port: int, timeout: float = 8.0) -> Optional[float]:
    try:
        import socks  # PySocks
    except ImportError:
        return None
    s = socks.socksocket()
    s.set_proxy(socks.SOCKS5, socks_host, socks_port)
    s.settimeout(timeout)
    t0 = time.time()
    try:
        s.connect((dest_host, dest_port))
        ms = (time.time() - t0) * 1000.0
        return ms
    except Exception:
        return None
    finally:
        try:
            s.close()
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Egress IP detection
# ---------------------------------------------------------------------------
@http_retry(max_tries=2, max_time=10.0)
def _fetch_ip_via_socks(socks_port: int, url: str) -> Optional[str]:
    proxies = {
        "http": f"socks5h://127.0.0.1:{socks_port}",
        "https": f"socks5h://127.0.0.1:{socks_port}",
    }
    r = requests.get(url, timeout=10, proxies=proxies)
    r.raise_for_status()
    return r.text.strip()


def detect_egress_ip(socks_port: int) -> dict:
    result = {"ipv4": None, "ipv6": None, "country": None, "error": None}
    try:
        result["ipv4"] = _fetch_ip_via_socks(socks_port, "https://api.ipify.org")
    except Exception as e:
        result["error"] = f"ipv4: {e}"
    try:
        result["ipv6"] = _fetch_ip_via_socks(socks_port, "https://api64.ipify.org")
    except Exception:
        pass
    if result["ipv4"]:
        try:
            r = requests.get(f"http://ip-api.com/json/{result['ipv4']}", timeout=8)
            info = r.json()
            result["country"] = info.get("country")
        except Exception:
            pass
    return result


# ---------------------------------------------------------------------------
# Full test of a config
# ---------------------------------------------------------------------------
def test_config(key: str, timeout: float = 20.0) -> dict:
    data = _load_configs()
    cfg = (data.get("configs") or {}).get(key)
    if not cfg:
        return {"ok": False, "error": f"config {key!r} not found"}

    outbound = cfg.get("xray_outbound")
    if not outbound:
        return {"ok": False, "error": "missing xray_outbound"}

    port = TEST_PORT_BASE
    full = build_xray_config(outbound, socks_port=port)

    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False,
                                     dir=str(DB_PATH), encoding="utf-8") as f:
        json.dump(full, f)
        tmp_config = f.name

    proc = None
    result = {"ok": False, "config_key": key, "latencies_ms": [],
              "egress": None, "error": None}
    try:
        proc = subprocess.Popen(
            [XRAY_BIN, "run", "-c", tmp_config],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        )
        deadline = time.time() + 8
        ready = False
        while time.time() < deadline:
            try:
                with socket.create_connection(("127.0.0.1", port), timeout=0.5):
                    ready = True
                    break
            except Exception:
                time.sleep(0.2)
        if not ready:
            result["error"] = "xray did not open SOCKS port in time"
            return result

        for host, dport in TG_DCS:
            ms = tcp_latency_via_socks("127.0.0.1", port, host, dport)
            if ms is not None:
                result["latencies_ms"].append({"dc": host, "ms": round(ms, 1)})

        if not result["latencies_ms"]:
            result["error"] = "no Telegram DC reachable through proxy"
            return result

        result["egress"] = detect_egress_ip(port)
        result["ok"] = True
        return result
    except Exception as e:
        result["error"] = str(e)
        return result
    finally:
        if proc:
            try:
                proc.terminate()
                proc.wait(timeout=5)
            except Exception:
                try:
                    proc.kill()
                except Exception:
                    pass
        try:
            os.unlink(tmp_config)
        except Exception:
            pass