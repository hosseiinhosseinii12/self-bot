"""/health, /metrics, and bearer-protected /api/* endpoints."""
import os
import time
from datetime import datetime, timezone as dt_timezone

from flask import jsonify, request

from .config import CONFIG, DB_PATH
from .economy import get_balance, total_in_circulation
from .subscriptions import days_left, distribution, effective_plan
from .transactions import for_user as tx_for_user, recent as tx_recent, totals as tx_totals
from .users import active_last_24h, all_users, count as user_count, new_last_7d

_START_TS = time.time()


def _bearer_ok() -> bool:
    expected = os.environ.get("API_TOKEN") or CONFIG.get("api_token")
    if not expected:
        return True  # if unset, endpoints are open (documented)
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        return False
    return auth[7:] == expected


def _unauth():
    return jsonify({"error": "unauthorized"}), 401


def attach_api_routes(app) -> None:
    @app.route("/health", methods=["GET"])
    def _health():
        proxy_ok = True
        bot_connected = False
        try:
            from app import _get_runtime
            rt = _get_runtime()
            bot_connected = bool(rt and rt.bot_client and rt.bot_client.is_connected())
        except Exception:
            pass
        return jsonify({
            "status": "ok",
            "uptime": round(time.time() - _START_TS, 2),
            "bot_connected": bot_connected,
            "proxy_ok": proxy_ok,
            "time": datetime.now(dt_timezone.utc).isoformat(),
        })

    @app.route("/metrics", methods=["GET"])
    def _metrics():
        lines = [
            "# HELP selfbot_users_total Total users",
            "# TYPE selfbot_users_total gauge",
            f"selfbot_users_total {user_count()}",
            "",
            "# HELP selfbot_users_active_24h Active users in the last 24h",
            "# TYPE selfbot_users_active_24h gauge",
            f"selfbot_users_active_24h {active_last_24h()}",
            "",
            "# HELP selfbot_diamonds_circulating Diamonds in circulation",
            "# TYPE selfbot_diamonds_circulating gauge",
            f"selfbot_diamonds_circulating {total_in_circulation()}",
        ]
        return "\n".join(lines), 200, {"Content-Type": "text/plain; version=0.0.4"}

    @app.route("/api/stats", methods=["GET"])
    def _api_stats():
        if not _bearer_ok():
            return _unauth()
        return jsonify({
            "users_total": user_count(),
            "users_active_24h": active_last_24h(),
            "users_new_7d": new_last_7d(),
            "diamonds_circulating": total_in_circulation(),
            "tx_totals": tx_totals(),
            "subscription_distribution": distribution(),
            "uptime": round(time.time() - _START_TS, 2),
        })

    @app.route("/api/user/<int:uid>", methods=["GET"])
    def _api_user(uid: int):
        if not _bearer_ok():
            return _unauth()
        raw = all_users().get(str(uid))
        if not raw:
            return jsonify({"error": "not found"}), 404
        return jsonify({
            "id": uid,
            "first_name": raw.get("first_name", ""),
            "username": raw.get("username", ""),
            "diamonds": get_balance(uid),
            "plan": effective_plan(uid),
            "days_left": days_left(uid),
            "banned": bool(raw.get("banned", False)),
            "referral_code": raw.get("referral_code", ""),
            "created_at": raw.get("created_at"),
            "last_seen": raw.get("last_seen"),
        })

    @app.route("/api/transactions", methods=["GET"])
    def _api_tx():
        if not _bearer_ok():
            return _unauth()
        uid = request.args.get("user_id")
        limit = min(500, int(request.args.get("limit", "100")))
        if uid:
            return jsonify(tx_for_user(int(uid), limit))
        return jsonify(tx_recent(limit))