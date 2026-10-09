"""User panel (Blueprint mounted at /user).

Token-based access: /user/?token=<secret> — the token is issued by the bot
via the 'My Web Panel' button. Each user sees their own account data.
"""
import secrets
from datetime import datetime

from flask import (Blueprint, abort, flash, redirect, render_template_string,
                   request, session, url_for)

from .config import CONFIG, DB_PATH
from .economy import get_balance
from .logging_setup import log_flask
from .requests_mod import (create_diamond_request, create_subscription_request,
                           for_user)
from .store import users_store
from .subscriptions import PLANS, days_left, effective_plan, get_sub
from .transactions import for_user as tx_for_user
from .users import ensure_user, get_user, get_user_settings

bp = Blueprint("user", __name__, url_prefix="/user")


# ===========================================================================
# Token helpers
# ===========================================================================
def issue_web_token(user_id: int) -> str:
    """Issue (or reuse) a web-panel token for a user."""
    key = str(user_id)
    u = users_store.get(key) or {"id": int(user_id)}
    tok = u.get("web_token")
    if not tok:
        tok = secrets.token_urlsafe(32)
        u["web_token"] = tok
        users_store.set(key, u)
    return tok


def _resolve_from_token() -> int:
    """Return user_id from ?token= or session."""
    tok = request.args.get("token") or session.get("user_token")
    if not tok:
        abort(401)
    for uid_str, u in (users_store.all() or {}).items():
        if u.get("web_token") == tok:
            session["user_token"] = tok
            return int(uid_str)
    abort(403)


# ===========================================================================
# HTML
# ===========================================================================
USER_BASE = """
<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>My Account · SELF BOT</title>
<meta name="viewport" content="width=device-width, initial-scale=1">
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&family=JetBrains+Mono:wght@400;500&display=swap" rel="stylesheet">
<style>
:root {
  --bg: #fafafa;
  --surface: #ffffff;
  --text: #0a0a0a;
  --text-2: #525252;
  --text-3: #a3a3a3;
  --border: #e5e5e5;
  --border-2: #d4d4d4;
  --hover: #f5f5f5;
  --black: #0a0a0a;
  --white: #ffffff;
  --green: #16a34a;
  --red: #dc2626;
  --amber: #d97706;
  --mono: 'JetBrains Mono', ui-monospace, monospace;
}
* { box-sizing: border-box; margin: 0; padding: 0; }
html { -webkit-text-size-adjust: 100%; }
body {
  font-family: 'Inter', -apple-system, sans-serif;
  background: var(--bg);
  color: var(--text);
  font-size: 14px;
  line-height: 1.55;
  -webkit-font-smoothing: antialiased;
  font-feature-settings: 'cv11', 'ss01';
  min-height: 100vh;
}
.wrap { max-width: 1000px; margin: 0 auto; padding: 40px 20px 60px; }

/* ---------- Header ---------- */
.head {
  display: flex; align-items: flex-end; justify-content: space-between;
  gap: 20px; margin-bottom: 32px; flex-wrap: wrap;
  padding-bottom: 24px;
  border-bottom: 1px solid var(--border);
}
.head-title {
  font-size: 28px; font-weight: 800;
  letter-spacing: -.035em;
  color: var(--text);
}
.head-sub {
  color: var(--text-2); font-size: 13.5px; margin-top: 4px;
}
.head-id {
  font-family: var(--mono);
  font-size: 12.5px;
  background: var(--white);
  padding: 6px 12px;
  border-radius: 8px;
  border: 1px solid var(--border);
  color: var(--text-2);
}

/* ---------- Stats ---------- */
.stats {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
  gap: 16px; margin-bottom: 24px;
}
.stat {
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: 12px;
  padding: 20px;
}
.stat .k {
  font-size: 11px; font-weight: 700;
  letter-spacing: .1em; text-transform: uppercase;
  color: var(--text-3);
}
.stat .v {
  font-size: 28px; font-weight: 800;
  letter-spacing: -.03em;
  margin-top: 8px;
  font-variant-numeric: tabular-nums;
  color: var(--text);
}
.stat .v .unit {
  font-size: 14px; color: var(--text-3); font-weight: 500; margin-left: 4px;
}
.progress {
  height: 6px; background: #f0f0f0; border-radius: 999px;
  overflow: hidden; margin-top: 12px;
}
.progress > div { height: 100%; background: var(--black); border-radius: 999px; }

/* ---------- Card ---------- */
.card {
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: 12px;
  padding: 24px;
  margin-bottom: 20px;
}
.card h2 {
  font-size: 15px; font-weight: 700;
  letter-spacing: -.01em;
  margin-bottom: 16px;
  color: var(--text);
}
.card h2 .sub {
  color: var(--text-3); font-weight: 500; font-size: 12.5px; margin-left: 8px;
}

/* ---------- Forms ---------- */
.field-row {
  display: flex; gap: 12px; flex-wrap: wrap; align-items: flex-end;
}
.field-row > * { flex: 1; min-width: 140px; }
label {
  display: block; font-size: 12px; font-weight: 600;
  color: var(--text-2); margin-bottom: 6px;
}
input, select {
  width: 100%; padding: 10px 12px;
  border: 1px solid var(--border-2); border-radius: 8px;
  background: var(--white); color: var(--text);
  font-size: 13.5px; font-family: inherit;
  transition: border-color .15s, box-shadow .15s;
}
input:focus, select:focus {
  outline: none;
  border-color: var(--black);
  box-shadow: 0 0 0 3px rgba(10,10,10,.06);
}

/* ---------- Buttons ---------- */
.btn {
  display: inline-flex; align-items: center; justify-content: center;
  gap: 6px;
  padding: 10px 18px;
  border-radius: 8px;
  border: 1px solid var(--black);
  background: var(--black); color: var(--white);
  font-size: 13.5px; font-weight: 500;
  cursor: pointer;
  transition: opacity .15s;
  font-family: inherit;
  text-decoration: none;
  line-height: 1.2;
}
.btn:hover { opacity: .88; text-decoration: none; }
.btn:disabled { opacity: .5; cursor: not-allowed; }

/* ---------- Table ---------- */
.table-wrap {
  border: 1px solid var(--border);
  border-radius: 12px;
  overflow: hidden;
  background: var(--surface);
}
table { width: 100%; border-collapse: collapse; }
thead th {
  background: #fafafa;
  color: var(--text-2);
  font-size: 11px; font-weight: 700;
  letter-spacing: .08em; text-transform: uppercase;
  text-align: left;
  padding: 12px 16px;
  border-bottom: 1px solid var(--border);
}
tbody td {
  padding: 12px 16px;
  border-bottom: 1px solid var(--border);
  font-size: 13.5px;
  color: var(--text);
}
tbody tr:last-child td { border-bottom: none; }
tbody tr:hover { background: var(--hover); }

.mono { font-family: var(--mono); font-size: 12.5px; }
.muted { color: var(--text-2); }

/* ---------- Badge ---------- */
.badge {
  display: inline-flex; align-items: center; gap: 4px;
  padding: 2px 9px;
  border-radius: 999px;
  font-size: 11px; font-weight: 600;
  letter-spacing: .02em;
  border: 1px solid var(--border-2);
  background: var(--white);
  color: var(--text);
}
.badge.solid { background: var(--black); color: var(--white); border-color: var(--black); }
.badge.green { background: #f0fdf4; color: var(--green); border-color: #bbf7d0; }
.badge.amber { background: #fffbeb; color: var(--amber); border-color: #fde68a; }
.badge.red { background: #fef2f2; color: var(--red); border-color: #fecaca; }

/* ---------- Flash ---------- */
.flash {
  padding: 12px 16px; border-radius: 10px; margin-bottom: 20px;
  background: #f0fdf4; color: #166534;
  border: 1px solid #bbf7d0;
  font-size: 13.5px; font-weight: 500;
  display: flex; align-items: center; gap: 8px;
}
.flash.err { background: #fef2f2; color: #991b1b; border-color: #fecaca; }

/* ---------- Empty ---------- */
.empty {
  padding: 40px 20px;
  text-align: center;
  color: var(--text-3);
  font-size: 13.5px;
}

code {
  font-family: var(--mono);
  font-size: 12.5px;
  background: #f5f5f5;
  padding: 3px 8px;
  border-radius: 5px;
  color: var(--text);
  word-break: break-all;
}

.referral-card {
  display: flex; align-items: center; justify-content: space-between;
  gap: 16px; flex-wrap: wrap;
}
</style>
</head>
<body>
<div class="wrap">{{ body|safe }}</div>
</body>
</html>
"""


PANEL = """
<div class="head">
  <div>
    <div class="head-title">My Account</div>
    <div class="head-sub">
      Welcome{% if first_name %}, {{ first_name }}{% endif %} 👋
    </div>
  </div>
  <div class="head-id">ID {{ user_id }}</div>
</div>

{% with msgs = get_flashed_messages(with_categories=true) %}
  {% for cat, msg in msgs %}
    <div class="flash {{ 'err' if cat=='error' else '' }}">{{ msg }}</div>
  {% endfor %}
{% endwith %}

<div class="stats">
  <div class="stat">
    <div class="k">Diamond balance</div>
    <div class="v">{{ diamonds }}<span class="unit">💎</span></div>
  </div>
  <div class="stat">
    <div class="k">Plan</div>
    <div class="v" style="font-size:22px">{{ plan }}</div>
  </div>
  <div class="stat">
    <div class="k">Clock status</div>
    <div class="v" style="font-size:22px">
      {% if clock_on %}🟢 On{% else %}🔴 Off{% endif %}
    </div>
  </div>
  <div class="stat">
    <div class="k">Interval</div>
    <div class="v" style="font-size:22px">{{ interval }}<span class="unit">min</span></div>
  </div>
</div>

<div class="card">
  <h2>Request diamonds</h2>
  <form method="post" action="{{ url_for('user.request_diamonds') }}">
    <input type="hidden" name="csrf" value="{{ csrf }}">
    <div class="field-row">
      <div style="flex:0 0 200px">
        <label>Amount (1–10000)</label>
        <input type="number" name="amount" value="50" min="1" max="10000" required>
      </div>
      <button class="btn">Send request</button>
    </div>
  </form>
</div>

<div class="card">
  <h2>Request subscription</h2>
  <form method="post" action="{{ url_for('user.request_subscription') }}">
    <input type="hidden" name="csrf" value="{{ csrf }}">
    <div class="field-row">
      <div style="flex:0 0 260px">
        <label>Plan</label>
        <select name="plan">
          <option value="basic">Basic — 30 days</option>
          <option value="pro">Pro — 30 days</option>
          <option value="vip">VIP — 30 days</option>
        </select>
      </div>
      <button class="btn">Send request</button>
    </div>
  </form>
</div>

<div class="card">
  <h2>
    My requests
    <span class="sub">{{ requests|length }}</span>
  </h2>
  <div class="table-wrap">
    <table>
      <thead>
        <tr><th>ID</th><th>Type</th><th>Value</th><th>Status</th><th>Created</th></tr>
      </thead>
      <tbody>
      {% for r in requests %}
      <tr>
        <td class="mono muted">{{ r.id }}</td>
        <td>{{ r.type }}</td>
        <td><strong>{{ r.amount or r.plan or '—' }}</strong></td>
        <td>
          {% if r.status == 'pending' %}<span class="badge amber">pending</span>
          {% elif r.status == 'approved' %}<span class="badge green">approved</span>
          {% else %}<span class="badge red">rejected</span>{% endif %}
        </td>
        <td class="mono muted">{{ r.created_at[:19] }}</td>
      </tr>
      {% else %}
      <tr><td colspan="5" class="empty">No requests yet.</td></tr>
      {% endfor %}
      </tbody>
    </table>
  </div>
</div>

<div class="card">
  <h2>
    Transaction history
    <span class="sub">{{ txs|length }}</span>
  </h2>
  <div class="table-wrap">
    <table>
      <thead>
        <tr><th>When</th><th>Kind</th><th>Amount</th><th>Reason</th><th>Balance</th></tr>
      </thead>
      <tbody>
      {% for tx in txs %}
      <tr>
        <td class="mono muted">{{ tx.created_at[:19] }}</td>
        <td>
          {% if tx.kind == 'spend' %}<span class="badge red">{{ tx.kind }}</span>
          {% elif tx.kind == 'refund' %}<span class="badge amber">{{ tx.kind }}</span>
          {% else %}<span class="badge green">{{ tx.kind }}</span>{% endif %}
        </td>
        <td class="mono"><strong>{{ tx.amount }}</strong></td>
        <td class="muted">{{ tx.reason }}</td>
        <td class="mono">{{ tx.balance_after }}</td>
      </tr>
      {% else %}
      <tr><td colspan="5" class="empty">No transactions yet.</td></tr>
      {% endfor %}
      </tbody>
    </table>
  </div>
</div>

<div class="card">
  <div class="referral-card">
    <div>
      <h2 style="margin-bottom:6px">Share your referral</h2>
      <p class="muted" style="font-size:13px">
        Give this code to friends. When they join, both of you receive bonus diamonds.
      </p>
    </div>
    <code style="font-size:15px">{{ referral_code }}</code>
  </div>
</div>
"""


# ===========================================================================
# CSRF helpers
# ===========================================================================
def _csrf_token() -> str:
    tok = session.get("csrf_user")
    if not tok:
        tok = secrets.token_urlsafe(24)
        session["csrf_user"] = tok
    return tok


def _csrf_check() -> None:
    tok = request.form.get("csrf")
    if not tok or tok != session.get("csrf_user"):
        abort(400, "CSRF check failed")


# ===========================================================================
# Routes
# ===========================================================================
@bp.route("/", methods=["GET"])
def index():
    uid = _resolve_from_token()
    ensure_user(uid)
    u = get_user(uid) or {}
    settings = get_user_settings(uid)

    bal = get_balance(uid)
    plan = effective_plan(uid)
    clock_on = bool(settings.get("clock_on", False))
    interval = int(settings.get("interval", 5))

    body = render_template_string(
        PANEL,
        user_id=uid,
        diamonds=bal,
        plan=plan,
        clock_on=clock_on,
        interval=interval,
        referral_code=u.get("referral_code", "—"),
        first_name=u.get("first_name") or "",
        requests=for_user(uid)[:50],
        txs=tx_for_user(uid, 50),
        csrf=_csrf_token(),
    )
    return render_template_string(USER_BASE, body=body)


@bp.route("/request/diamonds", methods=["POST"])
def request_diamonds():
    _csrf_check()
    uid = _resolve_from_token()
    try:
        amount = int(request.form.get("amount", "0"))
    except ValueError:
        flash("Invalid amount.", "error")
        return redirect(_redirect_with_token())
    if not (1 <= amount <= 10000):
        flash("Amount must be between 1 and 10000.", "error")
        return redirect(_redirect_with_token())
    create_diamond_request(uid, amount)
    flash(f"💎 Diamond request sent to admin ({amount}).")
    return redirect(_redirect_with_token())


@bp.route("/request/subscription", methods=["POST"])
def request_subscription():
    _csrf_check()
    uid = _resolve_from_token()
    plan = request.form.get("plan", "basic")
    if plan not in ("basic", "pro", "vip"):
        plan = "basic"
    create_subscription_request(uid, plan)
    flash(f"⭐ Subscription request ({plan}) sent to admin.")
    return redirect(_redirect_with_token())


def _redirect_with_token() -> str:
    tok = session.get("user_token", "")
    return url_for("user.index") + (f"?token={tok}" if tok else "")


def build_user_app():
    """Return the blueprint so app.py can register it under /user."""
    return bp