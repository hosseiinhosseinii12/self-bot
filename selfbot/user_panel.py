"""Light-themed user panel (Blueprint mounted at /user)."""
from datetime import datetime
from flask import (Blueprint, flash, redirect, render_template_string,
                   request, session, url_for)

from .config import CONFIG, DB_PATH
from .economy import get_balance
from .logging_setup import log_flask
from .requests_mod import create_diamond_request, create_subscription_request, for_user
from .subscriptions import PLANS, days_left, effective_plan, get_sub
from .transactions import for_user as tx_for_user
from .users import ensure_user, get_user

bp = Blueprint("user", __name__, url_prefix="/user")


USER_BASE = """
<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>SELF BOT — Account</title>
<meta name="viewport" content="width=device-width, initial-scale=1">
<style>
:root {
  --bg:#f4f6fb; --card:#ffffff; --border:#e2e8f0;
  --text:#0f172a; --muted:#64748b;
  --blue:#3b82f6; --purple:#8b5cf6; --green:#22c55e;
}
*{box-sizing:border-box;}
body{margin:0;background:var(--bg);color:var(--text);
  font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif;}
.wrap{max-width:960px;margin:36px auto;padding:0 18px;}
h1{margin:0 0 18px;font-size:24px;}
.cards{display:grid;grid-template-columns:repeat(auto-fit,minmax(220px,1fr));gap:16px;}
.card{background:var(--card);border:1px solid var(--border);border-radius:16px;
  padding:20px;box-shadow:0 10px 30px rgba(15,23,42,.04);}
.label{color:var(--muted);font-size:12px;text-transform:uppercase;letter-spacing:.5px;}
.value{font-size:32px;font-weight:700;margin-top:8px;
  background:linear-gradient(90deg,var(--blue),var(--purple));
  -webkit-background-clip:text;-webkit-text-fill-color:transparent;}
.progress{height:10px;background:#e5e7eb;border-radius:999px;overflow:hidden;margin-top:10px;}
.progress > div{height:100%;background:linear-gradient(90deg,var(--blue),var(--purple));}
table{width:100%;border-collapse:collapse;background:var(--card);
  border:1px solid var(--border);border-radius:12px;overflow:hidden;margin-top:14px;}
th,td{padding:10px 12px;text-align:left;font-size:14px;}
th{background:#f8fafc;color:var(--muted);font-size:12px;text-transform:uppercase;}
tr+tr td{border-top:1px solid var(--border);}
button{background:linear-gradient(90deg,var(--blue),var(--purple));
  border:0;color:#fff;padding:10px 16px;border-radius:10px;font-weight:600;
  cursor:pointer;font-size:13px;}
button:hover{filter:brightness(1.05);}
input,select{background:#fff;border:1px solid var(--border);border-radius:10px;
  padding:9px 11px;font-size:14px;width:100%;max-width:280px;}
.flash{padding:10px 14px;border-radius:10px;margin-bottom:14px;
  background:rgba(34,197,94,.12);color:#065f46;border:1px solid rgba(34,197,94,.25);}
.flash.err{background:rgba(239,68,68,.1);color:#991b1b;border-color:rgba(239,68,68,.25);}
.muted{color:var(--muted);font-size:13px;}
.pill{display:inline-block;padding:3px 10px;border-radius:999px;font-size:11px;font-weight:600;}
.pill.ok{background:rgba(34,197,94,.15);color:#166534;}
.pill.pending{background:rgba(245,158,11,.15);color:#b45309;}
.pill.rejected{background:rgba(239,68,68,.15);color:#991b1b;}
</style>
</head>
<body>
<div class="wrap">
{{ body|safe }}
</div>
</body>
</html>
"""


PANEL = """
<h1>SELF BOT — Your account</h1>
{% with msgs = get_flashed_messages(with_categories=true) %}
  {% for cat, msg in msgs %}
    <div class="flash {{ 'err' if cat=='error' else '' }}">{{ msg }}</div>
  {% endfor %}
{% endwith %}

<div class="cards">
  <div class="card">
    <div class="label">Diamonds</div>
    <div class="value">{{ diamonds }}</div>
  </div>
  <div class="card">
    <div class="label">Plan</div>
    <div class="value">{{ plan }}</div>
  </div>
  <div class="card">
    <div class="label">Days left</div>
    <div class="value">{{ days }}</div>
    <div class="progress"><div style="width: {{ pct }}%"></div></div>
  </div>
  <div class="card">
    <div class="label">Referral code</div>
    <div class="value" style="font-size:22px">{{ referral_code }}</div>
  </div>
</div>

<div class="card" style="margin-top:22px">
  <h2 style="margin-top:0">Request diamonds</h2>
  <form method="post" action="{{ url_for('user.request_diamonds') }}">
    <input type="hidden" name="csrf" value="{{ csrf }}">
    <div style="display:flex;gap:12px;align-items:flex-end;flex-wrap:wrap;">
      <div>
        <div class="label">Amount (1–10000)</div>
        <input type="number" name="amount" value="50" min="1" max="10000">
      </div>
      <button>Send request</button>
    </div>
  </form>
</div>

<div class="card" style="margin-top:16px">
  <h2 style="margin-top:0">Request subscription</h2>
  <form method="post" action="{{ url_for('user.request_subscription') }}">
    <input type="hidden" name="csrf" value="{{ csrf }}">
    <div style="display:flex;gap:12px;align-items:flex-end;flex-wrap:wrap;">
      <div>
        <div class="label">Plan</div>
        <select name="plan">
          <option value="basic">Basic — 30 days</option>
          <option value="pro">Pro — 30 days</option>
          <option value="vip">VIP — 30 days</option>
        </select>
      </div>
      <button>Send request</button>
    </div>
  </form>
</div>

<h2 style="margin-top:26px">My requests</h2>
<table>
  <tr><th>ID</th><th>Type</th><th>Value</th><th>Status</th><th>Created</th></tr>
  {% for r in requests %}
  <tr>
    <td><code>{{ r.id }}</code></td>
    <td>{{ r.type }}</td>
    <td>{{ r.amount or r.plan or '—' }}</td>
    <td><span class="pill {{ r.status if r.status != 'approved' else 'ok' }}">{{ r.status }}</span></td>
    <td>{{ r.created_at[:19] }}</td>
  </tr>
  {% endfor %}
</table>

<h2 style="margin-top:26px">Transaction history</h2>
<table>
  <tr><th>When</th><th>Kind</th><th>Amount</th><th>Reason</th><th>Balance</th></tr>
  {% for tx in txs %}
  <tr>
    <td>{{ tx.created_at[:19] }}</td>
    <td>{{ tx.kind }}</td>
    <td>{{ tx.amount }}</td>
    <td>{{ tx.reason }}</td>
    <td>{{ tx.balance_after }}</td>
  </tr>
  {% endfor %}
</table>
"""


ACCESS = """
<h1>SELF BOT — Your account</h1>
<div class="card">
  <p class="muted">This page is available to bot users. To view your account:</p>
  <ol>
    <li>Open Telegram</li>
    <li>Send <code>/account</code> to your bot</li>
    <li>You'll get a one-time link or view your info in the bot chat</li>
  </ol>
  <p class="muted">If you are an admin, log in to the admin panel.</p>
</div>
"""


def _csrf_token() -> str:
    import secrets
    tok = session.get("csrf_user")
    if not tok:
        tok = secrets.token_urlsafe(24)
        session["csrf_user"] = tok
    return tok


def _csrf_check() -> None:
    tok = request.form.get("csrf")
    if not tok or tok != session.get("csrf_user"):
        from flask import abort
        abort(400, "CSRF check failed")


def _resolve_uid() -> int:
    """Only owner can use /user directly for now (URL-based view is owner-scoped)."""
    return int(CONFIG.get("owner_id", 338266658))


@bp.route("/", methods=["GET"])
def index():
    uid = _resolve_uid()
    ensure_user(uid)
    u = get_user(uid) or {}
    plan = effective_plan(uid)
    sub = get_sub(uid)
    days = days_left(uid)
    total_days = 30
    if sub.get("started_at") and sub.get("expires_at"):
        try:
            from datetime import datetime as _dt
            s = _dt.fromisoformat(sub["started_at"])
            e = _dt.fromisoformat(sub["expires_at"])
            total_days = max(1, (e - s).days)
        except Exception:
            pass
    pct = min(100, int((days / total_days) * 100)) if total_days else 0

    body = render_template_string(
        PANEL,
        diamonds=get_balance(uid),
        plan=plan,
        days=days,
        pct=pct,
        referral_code=u.get("referral_code", "—"),
        requests=for_user(uid)[:50],
        txs=tx_for_user(uid, 50),
        csrf=_csrf_token(),
    )
    return render_template_string(USER_BASE, body=body)


@bp.route("/request/diamonds", methods=["POST"])
def request_diamonds():
    _csrf_check()
    uid = _resolve_uid()
    try:
        amount = int(request.form.get("amount", "0"))
    except ValueError:
        flash("Invalid amount.", "error")
        return redirect(url_for("user.index"))
    create_diamond_request(uid, amount)
    flash("Diamond request sent.")
    return redirect(url_for("user.index"))


@bp.route("/request/subscription", methods=["POST"])
def request_subscription():
    _csrf_check()
    uid = _resolve_uid()
    plan = request.form.get("plan", "basic")
    create_subscription_request(uid, plan)
    flash("Subscription request sent.")
    return redirect(url_for("user.index"))


def build_user_app():
    """Return the blueprint so app.py can register it under /user."""
    return bp