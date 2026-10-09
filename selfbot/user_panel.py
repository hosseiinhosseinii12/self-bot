"""Light-themed user panel (Blueprint mounted at /user)."""
from datetime import datetime
from flask import (Blueprint, flash, redirect, render_template_string,
                   request, session, url_for)

from .config import CONFIG, DB_PATH
from .economy import get_balance
from .logging_setup import log_flask
from .requests_mod import (create_diamond_request, create_subscription_request,
                           for_user)
from .subscriptions import PLANS, days_left, effective_plan, get_sub
from .transactions import for_user as tx_for_user
from .users import ensure_user, get_user

bp = Blueprint("user", __name__, url_prefix="/user")


USER_BASE = """
<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>SELF BOT · My account</title>
<meta name="viewport" content="width=device-width, initial-scale=1">
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&family=JetBrains+Mono:wght@400;600&display=swap" rel="stylesheet">
<style>
:root {
  --bg: #f7f8fc;
  --card: #ffffff;
  --border: #e5e9f2;
  --border-strong: #d1d8e6;
  --text: #0f172a;
  --text-dim: #64748b;
  --text-mute: #94a3b8;
  --indigo: #6366f1;
  --violet: #a855f7;
  --cyan: #06b6d4;
  --emerald: #10b981;
  --amber: #f59e0b;
  --rose: #f43f5e;
  --grad: linear-gradient(135deg, #6366f1, #a855f7);
}
* { box-sizing: border-box; margin: 0; padding: 0; }
body {
  font-family: 'Inter', sans-serif;
  background: var(--bg);
  color: var(--text);
  line-height: 1.55;
  -webkit-font-smoothing: antialiased;
  min-height: 100vh;
  background-image:
    radial-gradient(800px 500px at 0% 0%, rgba(99,102,241,.08), transparent 60%),
    radial-gradient(700px 500px at 100% 0%, rgba(168,85,247,.06), transparent 55%);
  background-attachment: fixed;
}
a { color: var(--indigo); text-decoration: none; }
a:hover { text-decoration: underline; }
.wrap { max-width: 900px; margin: 0 auto; padding: 40px 20px 60px; }

.head { margin-bottom: 32px; }
.head .title {
  font-size: 30px; font-weight: 800; letter-spacing: -.03em;
  background: var(--grad);
  -webkit-background-clip: text; background-clip: text;
  -webkit-text-fill-color: transparent;
}
.head .sub { color: var(--text-dim); font-size: 14px; margin-top: 4px; }

.stats { display: grid; grid-template-columns: repeat(auto-fit, minmax(190px, 1fr));
  gap: 16px; margin-bottom: 24px; }
.stat {
  background: var(--card);
  border: 1px solid var(--border);
  border-radius: 18px;
  padding: 22px;
  box-shadow: 0 1px 3px rgba(15,23,42,.04), 0 8px 24px rgba(15,23,42,.04);
  transition: transform .2s, box-shadow .2s;
}
.stat:hover { transform: translateY(-2px); box-shadow: 0 12px 32px rgba(15,23,42,.08); }
.stat .k { color: var(--text-dim); font-size: 11.5px; font-weight: 600;
  letter-spacing: .08em; text-transform: uppercase; display: flex; align-items: center; gap: 6px; }
.stat .v { font-size: 30px; font-weight: 800; margin-top: 12px;
  letter-spacing: -.03em; }
.stat.indigo .v { background: var(--grad); -webkit-background-clip: text;
  background-clip: text; -webkit-text-fill-color: transparent; }
.stat.emerald .v { color: var(--emerald); }
.stat.violet .v { color: var(--violet); }
.stat.amber .v { color: var(--amber); }

.progress { height: 8px; background: #eef2f7; border-radius: 999px;
  overflow: hidden; margin-top: 14px; }
.progress > div { height: 100%; background: var(--grad); border-radius: 999px;
  transition: width .6s ease; }

.card {
  background: var(--card);
  border: 1px solid var(--border);
  border-radius: 18px;
  padding: 24px;
  margin-bottom: 20px;
  box-shadow: 0 1px 3px rgba(15,23,42,.04), 0 8px 24px rgba(15,23,42,.04);
}
.card h2 { font-size: 15px; font-weight: 700; letter-spacing: -.01em; margin-bottom: 16px; }
.card h2 .sub { color: var(--text-mute); font-weight: 500; font-size: 12.5px; margin-left: 8px; }

.form-row { display: flex; gap: 12px; flex-wrap: wrap; align-items: flex-end; }
.form-row > * { flex: 1; min-width: 150px; }
label { display: block; font-size: 12px; color: var(--text-dim);
  margin-bottom: 6px; font-weight: 600; letter-spacing: .02em; }
input, select {
  width: 100%; padding: 11px 14px;
  border: 1px solid var(--border-strong); border-radius: 10px;
  background: #fff; color: var(--text); font-size: 13.5px; font-family: inherit;
  transition: border-color .15s, box-shadow .15s;
}
input:focus, select:focus {
  outline: none; border-color: var(--indigo);
  box-shadow: 0 0 0 3px rgba(99,102,241,.15);
}

button, .btn {
  display: inline-flex; align-items: center; gap: 6px;
  padding: 11px 20px;
  border: none; border-radius: 10px;
  background: var(--grad); color: #fff;
  font-size: 13.5px; font-weight: 600; cursor: pointer;
  font-family: inherit;
  transition: transform .15s, box-shadow .15s;
  box-shadow: 0 6px 18px rgba(99,102,241,.3);
}
button:hover, .btn:hover { transform: translateY(-1px);
  box-shadow: 0 10px 24px rgba(99,102,241,.4); text-decoration: none; }

table { width: 100%; border-collapse: collapse; font-size: 13.5px; }
th { color: var(--text-mute); text-align: left; padding: 10px 12px;
  font-size: 11px; text-transform: uppercase; letter-spacing: .1em;
  font-weight: 700; border-bottom: 1px solid var(--border); }
td { padding: 12px; border-bottom: 1px solid var(--border); }
tr:last-child td { border-bottom: none; }
tr:hover td { background: #fafbff; }
.mono { font-family: 'JetBrains Mono', monospace; font-size: 12px; }
.muted { color: var(--text-dim); font-size: 13px; }

.pill {
  display: inline-block; padding: 3px 10px; border-radius: 999px;
  font-size: 11px; font-weight: 700; letter-spacing: .03em;
}
.pill.pending  { background: #fef3c7; color: #92400e; }
.pill.approved { background: #d1fae5; color: #065f46; }
.pill.rejected { background: #fee2e2; color: #991b1b; }

.flash {
  padding: 12px 18px; border-radius: 12px; margin-bottom: 18px;
  background: #d1fae5; color: #065f46;
  border: 1px solid #a7f3d0; font-size: 13.5px; font-weight: 500;
}
.flash.err { background: #fee2e2; color: #991b1b; border-color: #fca5a5; }
</style>
</head>
<body>
<div class="wrap">{{ body|safe }}</div>
</body>
</html>
"""


PANEL = """
<div class="head">
  <div class="title">My Account</div>
  <div class="sub">Welcome back, {{ first_name }} 👋</div>
</div>

{% with msgs = get_flashed_messages(with_categories=true) %}
  {% for cat, msg in msgs %}
    <div class="flash {{ 'err' if cat=='error' else '' }}">{{ msg }}</div>
  {% endfor %}
{% endwith %}

<div class="stats">
  <div class="stat indigo">
    <div class="k">◆ Diamonds balance</div>
    <div class="v">{{ diamonds }}</div>
  </div>
  <div class="stat violet">
    <div class="k">◆ Current plan</div>
    <div class="v" style="font-size:24px">{{ plan }}</div>
  </div>
  <div class="stat emerald">
    <div class="k">◷ Days remaining</div>
    <div class="v">{{ days }}</div>
    <div class="progress"><div style="width: {{ pct }}%"></div></div>
  </div>
  <div class="stat amber">
    <div class="k">◆ Referral code</div>
    <div class="v mono" style="font-size:16px">{{ referral_code }}</div>
  </div>
</div>

<div class="card">
  <h2>💎 Request diamonds</h2>
  <form method="post" action="{{ url_for('user.request_diamonds') }}">
    <input type="hidden" name="csrf" value="{{ csrf }}">
    <div class="form-row">
      <div style="max-width:200px">
        <label>Amount (1–10000)</label>
        <input type="number" name="amount" value="50" min="1" max="10000">
      </div>
      <button>Send request →</button>
    </div>
  </form>
</div>

<div class="card">
  <h2>⭐ Request subscription</h2>
  <form method="post" action="{{ url_for('user.request_subscription') }}">
    <input type="hidden" name="csrf" value="{{ csrf }}">
    <div class="form-row">
      <div style="max-width:280px">
        <label>Plan</label>
        <select name="plan">
          <option value="basic">Basic — 30 days</option>
          <option value="pro">Pro — 30 days</option>
          <option value="vip">VIP — 30 days</option>
        </select>
      </div>
      <button>Send request →</button>
    </div>
  </form>
</div>

<div class="card">
  <h2>📜 My requests <span class="sub">{{ requests|length }}</span></h2>
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
      <td><span class="pill {{ r.status }}">{{ r.status }}</span></td>
      <td class="mono muted">{{ r.created_at[:19] }}</td>
    </tr>
    {% else %}
    <tr><td colspan="5" style="text-align:center;color:var(--text-mute);padding:30px">No requests yet.</td></tr>
    {% endfor %}
    </tbody>
  </table>
</div>

<div class="card">
  <h2>⇄ Transaction history <span class="sub">{{ txs|length }}</span></h2>
  <table>
    <thead>
      <tr><th>When</th><th>Kind</th><th>Amount</th><th>Reason</th><th>Balance</th></tr>
    </thead>
    <tbody>
    {% for tx in txs %}
    <tr>
      <td class="mono muted">{{ tx.created_at[:19] }}</td>
      <td>{{ tx.kind }}</td>
      <td><strong>{{ tx.amount }}</strong></td>
      <td class="muted">{{ tx.reason }}</td>
      <td class="mono">{{ tx.balance_after }}</td>
    </tr>
    {% else %}
    <tr><td colspan="5" style="text-align:center;color:var(--text-mute);padding:30px">No transactions yet.</td></tr>
    {% endfor %}
    </tbody>
  </table>
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
    from flask import abort
    tok = request.form.get("csrf")
    if not tok or tok != session.get("csrf_user"):
        abort(400, "CSRF check failed")


def _resolve_uid() -> int:
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
        first_name=u.get("first_name") or "there",
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
    flash("💎 Diamond request sent to admin.")
    return redirect(url_for("user.index"))


@bp.route("/request/subscription", methods=["POST"])
def request_subscription():
    _csrf_check()
    uid = _resolve_uid()
    plan = request.form.get("plan", "basic")
    create_subscription_request(uid, plan)
    flash(f"⭐ Subscription request ({plan}) sent to admin.")
    return redirect(url_for("user.index"))


def build_user_app():
    """Return the blueprint so app.py can register it under /user."""
    return bp