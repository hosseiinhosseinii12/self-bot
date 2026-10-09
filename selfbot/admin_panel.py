"""Flask admin panel: dark theme, Chart.js, users, requests, transactions,
shop, audit, backups, broadcast, 2FA, proxy configs."""
import functools
import io
import json
import os
import secrets
import string
import time
import zipfile
from datetime import datetime, timezone as dt_timezone
from pathlib import Path

from flask import (Blueprint, Flask, abort, flash, jsonify, redirect,
                   render_template_string, request, send_file, session,
                   url_for)

from .audit import all_entries as audit_all, record as audit_record
from .config import CONFIG, DB_PATH, save_config
from .economy import (get_balance, grant, hourly_consumption, set_balance,
                      spend, total_in_circulation)
from .logging_setup import log_flask
from .rate_limit import ip_limiter
from .requests_mod import (all_requests, approval_rate_series, approve, pending,
                           reject)
from .shop import all_items as shop_items, delete_item, set_item
from .subscriptions import (PLANS, cancel_sub, days_left, distribution,
                            effective_plan, get_sub, set_sub)
from .transactions import (for_user as tx_for_user, recent as tx_recent,
                           top_spenders, totals as tx_totals)
from .users import (active_last_24h, all_users, ban, count as user_count,
                    delete_user, get_user, new_last_7d, unban)

try:
    import pyotp
    HAS_PYOTP = True
except ImportError:
    HAS_PYOTP = False

try:
    import qrcode  # type: ignore
    HAS_QRCODE = True
except ImportError:
    HAS_QRCODE = False


# ===========================================================================
# Password management
# ===========================================================================
def _load_or_create_password() -> str:
    env_pw = os.environ.get("ADMIN_PASSWORD")
    if env_pw:
        return env_pw
    pw_file = DB_PATH / "admin_secret.txt"
    if pw_file.exists():
        return pw_file.read_text(encoding="utf-8").strip()
    pw = "".join(secrets.choice(string.ascii_letters + string.digits) for _ in range(16))
    pw_file.write_text(pw, encoding="utf-8")
    try:
        os.chmod(pw_file, 0o600)
    except Exception:
        pass
    return pw


ADMIN_PASSWORD = _load_or_create_password()


# ===========================================================================
# Auth decorators
# ===========================================================================
def _login_required(fn):
    @functools.wraps(fn)
    def wrapper(*a, **kw):
        if not session.get("admin"):
            return redirect(url_for("login"))
        return fn(*a, **kw)
    return wrapper


def _rate_limit(fn):
    @functools.wraps(fn)
    def wrapper(*a, **kw):
        ip = request.remote_addr or "0.0.0.0"
        if not ip_limiter.allow(ip):
            return ("Too Many Requests", 429)
        return fn(*a, **kw)
    return wrapper


def _csrf_token() -> str:
    tok = session.get("csrf")
    if not tok:
        tok = secrets.token_urlsafe(24)
        session["csrf"] = tok
    return tok


def _csrf_check() -> None:
    tok = request.form.get("csrf") or request.headers.get("X-CSRF-Token")
    if not tok or tok != session.get("csrf"):
        abort(400, "CSRF check failed")


# ===========================================================================
# HTML templates
# ===========================================================================
BASE_TEMPLATE = """
<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>{{ title }} — SELF BOT Admin</title>
<meta name="viewport" content="width=device-width, initial-scale=1">
<script src="https://cdn.jsdelivr.net/npm/chart.js@4"></script>
<style>
:root {
  --bg:#0b0f1a; --card:#131a2b; --card2:#1a2338; --border:#243049;
  --text:#e5ecf7; --muted:#8fa0bd; --blue:#4f7dff; --purple:#8b5cf6;
  --green:#22c55e; --red:#ef4444; --yellow:#f59e0b;
}
*{box-sizing:border-box;}
body{margin:0;background:var(--bg);color:var(--text);
  font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif;}
a{color:var(--blue);text-decoration:none;}
a:hover{text-decoration:underline;}
.nav{display:flex;align-items:center;gap:18px;padding:14px 26px;
  background:linear-gradient(90deg,#0b0f1a,#131a2b);
  border-bottom:1px solid var(--border);flex-wrap:wrap;}
.nav .logo{font-weight:700;font-size:18px;
  background:linear-gradient(90deg,var(--blue),var(--purple));
  -webkit-background-clip:text;-webkit-text-fill-color:transparent;}
.nav a{color:var(--muted);font-size:14px;padding:6px 10px;border-radius:8px;}
.nav a:hover{color:var(--text);background:var(--card2);text-decoration:none;}
.nav a.active{color:var(--text);background:var(--card2);}
.container{max-width:1220px;margin:26px auto;padding:0 20px;}
h1{margin:0 0 18px;font-size:24px;}
h2{margin:0 0 12px;font-size:18px;}
.cards{display:grid;grid-template-columns:repeat(auto-fit,minmax(200px,1fr));
  gap:16px;margin-bottom:22px;}
.card{background:var(--card);border:1px solid var(--border);border-radius:14px;
  padding:18px;transition:.2s;box-shadow:0 6px 24px rgba(0,0,0,.25);}
.card:hover{transform:translateY(-2px);border-color:#35507f;}
.card .label{color:var(--muted);font-size:12px;text-transform:uppercase;
  letter-spacing:.6px;}
.card .value{font-size:28px;font-weight:700;margin-top:8px;
  background:linear-gradient(90deg,var(--blue),var(--purple));
  -webkit-background-clip:text;-webkit-text-fill-color:transparent;}
table{width:100%;border-collapse:collapse;background:var(--card);
  border-radius:12px;overflow:hidden;border:1px solid var(--border);}
th,td{padding:10px 12px;text-align:left;font-size:14px;}
th{background:var(--card2);color:var(--muted);font-weight:600;font-size:12px;
  text-transform:uppercase;letter-spacing:.5px;}
tr+tr td{border-top:1px solid var(--border);}
.pill{display:inline-block;padding:3px 10px;border-radius:999px;
  font-size:11px;font-weight:600;letter-spacing:.3px;}
.pill.pending{background:rgba(245,158,11,.15);color:var(--yellow);}
.pill.approved,.pill.ok{background:rgba(34,197,94,.15);color:var(--green);}
.pill.rejected,.pill.bad{background:rgba(239,68,68,.15);color:var(--red);}
.pill.info{background:rgba(79,125,255,.15);color:var(--blue);}
.pill.vip{background:rgba(139,92,246,.15);color:var(--purple);}
button,.btn{background:linear-gradient(90deg,var(--blue),var(--purple));
  border:none;color:white;padding:9px 15px;border-radius:9px;cursor:pointer;
  font-size:13px;font-weight:600;transition:.2s;display:inline-block;}
button:hover,.btn:hover{filter:brightness(1.1);text-decoration:none;}
button.ghost{background:transparent;border:1px solid var(--border);color:var(--text);}
button.danger{background:linear-gradient(90deg,#dc2626,#ef4444);}
button.success{background:linear-gradient(90deg,#16a34a,#22c55e);}
input,select,textarea{background:var(--card2);color:var(--text);
  border:1px solid var(--border);border-radius:9px;padding:9px 11px;
  font-size:14px;width:100%;max-width:320px;}
label{display:block;color:var(--muted);font-size:12px;margin:12px 0 4px;}
.row{display:flex;gap:12px;flex-wrap:wrap;align-items:flex-end;}
.flash{padding:10px 14px;background:rgba(34,197,94,.12);
  border:1px solid rgba(34,197,94,.3);border-radius:10px;margin-bottom:16px;
  color:#86efac;}
.flash.err{background:rgba(239,68,68,.12);border-color:rgba(239,68,68,.3);
  color:#fca5a5;}
.chart-card{padding:18px;}
.grid-2{display:grid;grid-template-columns:1fr 1fr;gap:18px;}
@media (max-width:900px){.grid-2{grid-template-columns:1fr;}}
.login-box{max-width:380px;margin:100px auto;padding:30px;
  background:var(--card);border-radius:16px;border:1px solid var(--border);}
.muted{color:var(--muted);font-size:13px;}
.mono{font-family:ui-monospace,'SF Mono',Consolas,monospace;font-size:12px;}
</style>
</head>
<body>
{% if session.get('admin') %}
<div class="nav">
  <div class="logo">◆ SELF BOT</div>
  <a href="{{ url_for('dashboard') }}" class="{{ 'active' if page=='dashboard' else '' }}">Dashboard</a>
  <a href="{{ url_for('users_page') }}" class="{{ 'active' if page=='users' else '' }}">Users</a>
  <a href="{{ url_for('requests_page') }}" class="{{ 'active' if page=='requests' else '' }}">Requests</a>
  <a href="{{ url_for('transactions_page') }}" class="{{ 'active' if page=='tx' else '' }}">Transactions</a>
  <a href="{{ url_for('shop_page') }}" class="{{ 'active' if page=='shop' else '' }}">Shop</a>
  <a href="{{ url_for('audit_page') }}" class="{{ 'active' if page=='audit' else '' }}">Audit</a>
  <a href="{{ url_for('proxy_page') }}" class="{{ 'active' if page=='proxy' else '' }}">Proxy</a>
  <a href="{{ url_for('backups_page') }}" class="{{ 'active' if page=='backups' else '' }}">Backups</a>
  <a href="{{ url_for('broadcast_page') }}" class="{{ 'active' if page=='broadcast' else '' }}">Broadcast</a>
  <a href="{{ url_for('settings_page') }}" class="{{ 'active' if page=='settings' else '' }}">Settings</a>
  <span style="flex:1"></span>
  <a href="{{ url_for('logout') }}">Logout</a>
</div>
{% endif %}
<div class="container">
{% with msgs = get_flashed_messages(with_categories=true) %}
  {% for cat, msg in msgs %}
    <div class="flash {{ 'err' if cat=='error' else '' }}">{{ msg }}</div>
  {% endfor %}
{% endwith %}
{{ body|safe }}
</div>
</body>
</html>
"""


LOGIN_TEMPLATE = """
<div class="login-box">
  <h1 style="text-align:center">SELF BOT Admin</h1>
  <form method="post" action="{{ url_for('login_post') }}">
    <input type="hidden" name="csrf" value="{{ csrf }}">
    <label>Password</label>
    <input type="password" name="password" autofocus>
    {% if totp_enabled %}
    <label>2FA code</label>
    <input type="text" name="totp" inputmode="numeric" pattern="[0-9]*" maxlength="6">
    {% endif %}
    <div style="height:14px"></div>
    <button style="width:100%">Sign in</button>
  </form>
</div>
"""


DASHBOARD_TEMPLATE = """
<h1>Dashboard</h1>
<div class="cards">
  <div class="card"><div class="label">Total users</div><div class="value">{{ users_total }}</div></div>
  <div class="card"><div class="label">Active 24h</div><div class="value">{{ users_active }}</div></div>
  <div class="card"><div class="label">New 7d</div><div class="value">{{ users_new }}</div></div>
  <div class="card"><div class="label">Diamonds in circulation</div><div class="value">{{ diamonds }}</div></div>
  <div class="card"><div class="label">Granted (all time)</div><div class="value">{{ granted }}</div></div>
  <div class="card"><div class="label">Spent (all time)</div><div class="value">{{ spent }}</div></div>
</div>

<div class="grid-2">
  <div class="card chart-card">
    <h2>Diamond consumption (last 24h)</h2>
    <canvas id="diamondChart" height="140"></canvas>
  </div>
  <div class="card chart-card">
    <h2>Subscription distribution</h2>
    <canvas id="subChart" height="140"></canvas>
  </div>
</div>

<div class="grid-2" style="margin-top:18px">
  <div class="card chart-card">
    <h2>Request approval rate (last 14 days)</h2>
    <canvas id="approvalChart" height="140"></canvas>
  </div>
  <div class="card chart-card">
    <h2>Top spenders</h2>
    <table>
      <tr><th>#</th><th>User</th><th>Spent</th></tr>
      {% for s in top_spenders %}
      <tr><td>{{ loop.index }}</td><td>#{{ s.user_id }}</td><td>{{ s.spent }} 💎</td></tr>
      {% endfor %}
    </table>
  </div>
</div>

<script>
const diamondData = {{ diamond_series|safe }};
new Chart(document.getElementById('diamondChart'), {
  type: 'line',
  data: {
    labels: diamondData.map(d => d.hour.slice(11, 16)),
    datasets: [{
      label: 'Diamonds spent',
      data: diamondData.map(d => d.spent),
      borderColor: '#4f7dff',
      backgroundColor: 'rgba(79,125,255,.15)',
      fill: true, tension: .35, pointRadius: 3,
    }]
  },
  options: { plugins: { legend: { display: false } },
    scales: { x: { ticks: { color: '#8fa0bd' } }, y: { ticks: { color: '#8fa0bd' } } } }
});

const subData = {{ sub_dist|safe }};
new Chart(document.getElementById('subChart'), {
  type: 'doughnut',
  data: {
    labels: Object.keys(subData),
    datasets: [{
      data: Object.values(subData),
      backgroundColor: ['#8fa0bd','#4f7dff','#8b5cf6','#22c55e'],
    }]
  },
  options: { plugins: { legend: { labels: { color: '#e5ecf7' } } } }
});

const approvalData = {{ approval_series|safe }};
new Chart(document.getElementById('approvalChart'), {
  type: 'bar',
  data: {
    labels: approvalData.map(d => d.date.slice(5)),
    datasets: [
      { label: 'Approved', data: approvalData.map(d => d.approved),
        backgroundColor: '#22c55e' },
      { label: 'Rejected', data: approvalData.map(d => d.rejected),
        backgroundColor: '#ef4444' },
    ]
  },
  options: { plugins: { legend: { labels: { color: '#e5ecf7' } } },
    scales: { x: { ticks: { color: '#8fa0bd' } }, y: { ticks: { color: '#8fa0bd' } } } }
});
</script>
"""


USERS_TEMPLATE = """
<h1>Users</h1>
<table>
  <tr>
    <th>ID</th><th>Name</th><th>Username</th><th>Plan</th><th>Days</th>
    <th>Diamonds</th><th>Banned</th><th>Actions</th>
  </tr>
  {% for u in users %}
  <tr>
    <td>#{{ u.id }}</td>
    <td>{{ u.first_name or '—' }}</td>
    <td>{{ ('@' + u.username) if u.username else '—' }}</td>
    <td><span class="pill {{ u.plan }}">{{ u.plan }}</span></td>
    <td>{{ u.days }}</td>
    <td>{{ u.diamonds }} 💎</td>
    <td>{% if u.banned %}<span class="pill bad">banned</span>{% else %}<span class="pill ok">ok</span>{% endif %}</td>
    <td><a class="btn" href="{{ url_for('user_detail', uid=u.id) }}">Open</a></td>
  </tr>
  {% endfor %}
</table>
"""


USER_DETAIL_TEMPLATE = """
<h1>User #{{ u.id }}</h1>
<div class="cards">
  <div class="card"><div class="label">Diamonds</div><div class="value">{{ u.diamonds }}</div></div>
  <div class="card"><div class="label">Plan</div><div class="value">{{ u.plan }}</div></div>
  <div class="card"><div class="label">Days left</div><div class="value">{{ u.days }}</div></div>
  <div class="card"><div class="label">Referral code</div><div class="value" style="font-size:18px">{{ u.referral_code }}</div></div>
</div>

<div class="card">
  <h2>Grant diamonds</h2>
  <form method="post" action="{{ url_for('grant_diamonds', uid=u.id) }}">
    <input type="hidden" name="csrf" value="{{ csrf }}">
    <div class="row">
      <div><label>Amount (negative to subtract)</label><input type="number" name="amount" value="10"></div>
      <div><label>Reason</label><input type="text" name="reason" value="admin_grant"></div>
      <button>Apply</button>
    </div>
  </form>
</div>

<div class="card" style="margin-top:16px">
  <h2>Set subscription</h2>
  <form method="post" action="{{ url_for('set_subscription', uid=u.id) }}">
    <input type="hidden" name="csrf" value="{{ csrf }}">
    <div class="row">
      <div>
        <label>Plan</label>
        <select name="plan">
          {% for p in plans %}<option value="{{ p }}" {{ 'selected' if p==u.plan else '' }}>{{ p }}</option>{% endfor %}
        </select>
      </div>
      <div><label>Days</label><input type="number" name="days" value="30"></div>
      <div>
        <label>Auto-renew</label>
        <select name="auto_renew"><option value="0">No</option><option value="1">Yes</option></select>
      </div>
      <button>Set</button>
    </div>
  </form>
  <form method="post" action="{{ url_for('cancel_subscription', uid=u.id) }}" style="margin-top:10px">
    <input type="hidden" name="csrf" value="{{ csrf }}">
    <button class="ghost">Cancel subscription</button>
  </form>
</div>

<div class="card" style="margin-top:16px">
  <h2>Moderation</h2>
  <form method="post" action="{{ url_for('toggle_ban', uid=u.id) }}" style="display:inline">
    <input type="hidden" name="csrf" value="{{ csrf }}">
    <button class="{{ 'success' if u.banned else 'danger' }}">{{ 'Unban' if u.banned else 'Ban' }}</button>
  </form>
  <form method="post" action="{{ url_for('delete_user_route', uid=u.id) }}" style="display:inline"
        onsubmit="return confirm('Delete user? This cannot be undone.');">
    <input type="hidden" name="csrf" value="{{ csrf }}">
    <button class="danger">Delete user</button>
  </form>
</div>

<div class="card" style="margin-top:16px">
  <h2>Recent transactions</h2>
  <table>
    <tr><th>When</th><th>Kind</th><th>Amount</th><th>Reason</th><th>Balance</th></tr>
    {% for tx in txs %}
    <tr>
      <td>{{ tx.created_at[:19] }}</td>
      <td><span class="pill {{ 'bad' if tx.kind=='spend' else 'ok' }}">{{ tx.kind }}</span></td>
      <td>{{ tx.amount }}</td>
      <td>{{ tx.reason }}</td>
      <td>{{ tx.balance_after }}</td>
    </tr>
    {% endfor %}
  </table>
</div>
"""


REQUESTS_TEMPLATE = """
<h1>Requests</h1>
<table>
  <tr><th>ID</th><th>User</th><th>Type</th><th>Amount/Plan</th><th>Status</th><th>Created</th><th>Actions</th></tr>
  {% for r in requests %}
  <tr>
    <td>{{ r.id }}</td>
    <td>#{{ r.user_id }}</td>
    <td>{{ r.type }}</td>
    <td>{{ r.amount or r.plan or '—' }}</td>
    <td><span class="pill {{ r.status }}">{{ r.status }}</span></td>
    <td>{{ r.created_at[:19] }}</td>
    <td>
      {% if r.status == 'pending' %}
      <form method="post" action="{{ url_for('approve_request', rid=r.id) }}" style="display:inline">
        <input type="hidden" name="csrf" value="{{ csrf }}">
        <button class="success">✓</button>
      </form>
      <form method="post" action="{{ url_for('reject_request', rid=r.id) }}" style="display:inline">
        <input type="hidden" name="csrf" value="{{ csrf }}">
        <button class="danger">✕</button>
      </form>
      {% endif %}
    </td>
  </tr>
  {% endfor %}
</table>
"""


TX_TEMPLATE = """
<h1>Transactions</h1>
<div class="cards">
  <div class="card"><div class="label">Granted</div><div class="value">{{ totals.granted }}</div></div>
  <div class="card"><div class="label">Spent</div><div class="value">{{ totals.spent }}</div></div>
  <div class="card"><div class="label">Refunded</div><div class="value">{{ totals.refunded }}</div></div>
</div>
<table>
  <tr><th>When</th><th>User</th><th>Kind</th><th>Amount</th><th>Reason</th><th>Balance</th></tr>
  {% for tx in txs %}
  <tr>
    <td>{{ tx.created_at[:19] }}</td>
    <td>#{{ tx.user_id }}</td>
    <td><span class="pill {{ 'bad' if tx.kind=='spend' else 'ok' }}">{{ tx.kind }}</span></td>
    <td>{{ tx.amount }}</td>
    <td>{{ tx.reason }}</td>
    <td>{{ tx.balance_after }}</td>
  </tr>
  {% endfor %}
</table>
"""


SHOP_TEMPLATE = """
<h1>Shop</h1>
<div class="card">
  <h2>Add / update item</h2>
  <form method="post" action="{{ url_for('shop_save') }}">
    <input type="hidden" name="csrf" value="{{ csrf }}">
    <div class="row">
      <div><label>ID</label><input name="item_id" placeholder="extra_job_slot"></div>
      <div><label>Name</label><input name="name"></div>
      <div><label>Price</label><input type="number" name="price" value="20"></div>
      <div><label>Max per user</label><input type="number" name="max_per_user" value="1"></div>
    </div>
    <div><label>Description</label><input name="description" style="max-width:100%"></div>
    <button style="margin-top:12px">Save</button>
  </form>
</div>

<table style="margin-top:18px">
  <tr><th>ID</th><th>Name</th><th>Price</th><th>Max</th><th>Description</th><th></th></tr>
  {% for iid, it in items.items() %}
  <tr>
    <td><code>{{ iid }}</code></td>
    <td>{{ it.name }}</td>
    <td>{{ it.price }} 💎</td>
    <td>{{ it.max_per_user }}</td>
    <td class="muted">{{ it.description }}</td>
    <td>
      <form method="post" action="{{ url_for('shop_delete', item_id=iid) }}">
        <input type="hidden" name="csrf" value="{{ csrf }}">
        <button class="danger">Delete</button>
      </form>
    </td>
  </tr>
  {% endfor %}
</table>
"""


AUDIT_TEMPLATE = """
<h1>Audit log</h1>
<table>
  <tr><th>When</th><th>Admin</th><th>Action</th><th>Target</th><th>IP</th></tr>
  {% for e in entries %}
  <tr>
    <td>{{ e.created_at[:19] }}</td>
    <td>#{{ e.admin_id }}</td>
    <td>{{ e.action }}</td>
    <td>{{ e.target or '—' }}</td>
    <td>{{ e.ip or '—' }}</td>
  </tr>
  {% endfor %}
</table>
"""


BACKUPS_TEMPLATE = """
<h1>Backups</h1>
<div class="grid-2">
  <div class="card">
    <h2>Manual backup</h2>
    <p class="muted">Creates a ZIP of all JSON files and sends it to the owner's Telegram DM.</p>
    <form method="post" action="{{ url_for('backup_now') }}">
      <input type="hidden" name="csrf" value="{{ csrf }}">
      <button>Backup now</button>
    </form>
  </div>
  <div class="card">
    <h2>Restore from ZIP</h2>
    <form method="post" action="{{ url_for('restore_backup') }}" enctype="multipart/form-data">
      <input type="hidden" name="csrf" value="{{ csrf }}">
      <input type="file" name="file" accept=".zip">
      <button style="margin-top:12px" class="danger">Restore</button>
    </form>
  </div>
</div>

<h2 style="margin-top:22px">Local backups</h2>
<table>
  <tr><th>File</th><th>Size</th><th>Modified</th><th></th></tr>
  {% for b in backups %}
  <tr>
    <td>{{ b.name }}</td>
    <td>{{ b.size_kb }} KB</td>
    <td>{{ b.modified }}</td>
    <td><a class="btn" href="{{ url_for('download_backup', name=b.name) }}">Download</a></td>
  </tr>
  {% endfor %}
</table>
"""


BROADCAST_TEMPLATE = """
<h1>Broadcast</h1>
<div class="card">
  <p class="muted">Send a message to every non-banned user via the bot.</p>
  <form method="post" action="{{ url_for('broadcast_send') }}">
    <input type="hidden" name="csrf" value="{{ csrf }}">
    <label>Message</label>
    <textarea name="message" rows="6" style="max-width:100%"></textarea>
    <button style="margin-top:12px">Send broadcast</button>
  </form>
</div>
"""


SETTINGS_TEMPLATE = """
<h1>Settings</h1>
<div class="card">
  <h2>Runtime config</h2>
  <form method="post" action="{{ url_for('settings_save') }}">
    <input type="hidden" name="csrf" value="{{ csrf }}">
    <div class="row">
      <div><label>Signup bonus</label><input type="number" name="signup_bonus" value="{{ cfg.signup_bonus }}"></div>
      <div><label>Cost per clock update</label><input type="number" name="diamond_cost_clock" value="{{ cfg.diamond_cost_clock }}"></div>
      <div><label>Cost per job</label><input type="number" name="diamond_cost_job" value="{{ cfg.diamond_cost_job }}"></div>
      <div><label>Referral bonus</label><input type="number" name="referral_bonus" value="{{ cfg.referral_bonus }}"></div>
    </div>
    <div class="row" style="margin-top:12px">
      <div><label>Timezone</label><input name="timezone" value="{{ cfg.timezone }}"></div>
      <div><label>Base name</label><input name="base_name" value="{{ cfg.base_name }}"></div>
      <div><label>Interval (min)</label><input type="number" name="interval" value="{{ cfg.interval }}"></div>
    </div>
    <button style="margin-top:14px">Save</button>
  </form>
</div>

<div class="card" style="margin-top:16px">
  <h2>API token</h2>
  <p class="muted">Set <code>API_TOKEN</code> as an env var to protect <code>/api/*</code>.</p>
</div>

<div class="card" style="margin-top:16px">
  <h2>Two-Factor Authentication</h2>
  <p class="muted">Add TOTP 2FA to the admin login.</p>
  <a class="btn" href="{{ url_for('totp_page') }}">Configure 2FA</a>
</div>
"""


TOTP_TEMPLATE = """
<h1>Two-Factor Authentication</h1>
<div class="card">
  {% if enabled %}
    <p class="muted">2FA is currently <span class="pill ok">enabled</span>.</p>
    <form method="post" action="{{ url_for('totp_disable') }}">
      <input type="hidden" name="csrf" value="{{ csrf }}">
      <button class="danger">Disable 2FA</button>
    </form>
  {% else %}
    <p class="muted">2FA is currently <span class="pill pending">disabled</span>.</p>
    {% if qr_b64 %}
      <p>Scan this QR code with your authenticator app:</p>
      <img src="data:image/png;base64,{{ qr_b64 }}" alt="QR"
           style="background:#fff;padding:10px;border-radius:10px;">
      <p class="muted">Or enter this secret manually:</p>
      <p><code style="font-size:16px">{{ pending_secret }}</code></p>
      <form method="post" action="{{ url_for('totp_enable') }}">
        <input type="hidden" name="csrf" value="{{ csrf }}">
        <button class="success">Enable 2FA</button>
      </form>
    {% else %}
      <p class="muted">pyotp not installed. Add <code>pyotp</code> and <code>qrcode</code> to requirements.</p>
    {% endif %}
  {% endif %}
</div>
"""


PROXY_TEMPLATE = """
<h1>Proxy Configs</h1>
<p class="muted">Manage VLESS/Xray configs. Test latency to Telegram DCs and egress IP.</p>

<div class="card" style="margin-top:16px">
  <h2>Active config: <code>{{ active }}</code></h2>
  <button class="success" id="test-active-btn">Test active config</button>
  <pre id="test-active-result" style="margin-top:12px;white-space:pre-wrap;
       background:#0b0f1a;padding:12px;border-radius:9px;font-size:12px;
       color:#8fa0bd;display:none"></pre>
</div>

<table style="margin-top:18px">
  <tr><th>Key</th><th>Name</th><th>URL</th><th>Status</th><th>Actions</th></tr>
  {% for c in configs %}
  <tr>
    <td><code>{{ c.key }}</code></td>
    <td>{{ c.name }}</td>
    <td class="mono">{{ c.url_short }}</td>
    <td>{% if c.active %}<span class="pill ok">active</span>{% else %}<span class="pill">idle</span>{% endif %}</td>
    <td>
      <button class="btn small" onclick="testConfig('{{ c.key }}')">Test</button>
      {% if not c.active %}
      <form method="post" action="{{ url_for('proxy_activate', key=c.key) }}" style="display:inline">
        <input type="hidden" name="csrf" value="{{ csrf }}">
        <button class="btn small success">Activate</button>
      </form>
      {% endif %}
    </td>
  </tr>
  {% endfor %}
</table>

<div id="test-result" style="margin-top:16px"></div>

<div class="card" style="margin-top:22px">
  <h2>Add config</h2>
  <form method="post" action="{{ url_for('proxy_add') }}">
    <input type="hidden" name="csrf" value="{{ csrf }}">
    <div class="row">
      <div><label>Key</label><input name="key" placeholder="us-1"></div>
      <div><label>Name</label><input name="name" placeholder="US Server 1"></div>
    </div>
    <div><label>VLESS URL</label><input name="url" style="max-width:100%" placeholder="vless://..."></div>
    <div>
      <label>Outbound JSON</label>
      <textarea name="outbound" rows="12" style="max-width:100%;font-family:monospace;font-size:12px"
placeholder='{"protocol":"vless","settings":{"vnext":[{"address":"...","port":443,"users":[{"id":"...","encryption":"none","flow":""}]}]},"streamSettings":{"network":"ws","security":"tls","tlsSettings":{"serverName":"...","fingerprint":"chrome","alpn":["http/1.1"]},"wsSettings":{"path":"/...","headers":{"Host":"..."}}}}'></textarea>
    </div>
    <button style="margin-top:12px">Add</button>
  </form>
</div>

<script>
async function testConfig(key) {
  const el = document.getElementById('test-result');
  el.innerHTML = '<div class="card">Testing <code>' + key + '</code> ...</div>';
  const csrf = '{{ csrf }}';
  try {
    const r = await fetch('/admin/proxy/test/' + key, {
      method: 'POST',
      headers: {'X-CSRF-Token': csrf}
    });
    const data = await r.json();
    el.innerHTML = '<div class="card"><h2>Result: ' + key + '</h2><pre style="white-space:pre-wrap;color:#8fa0bd;font-size:12px">' +
      JSON.stringify(data, null, 2) + '</pre></div>';
  } catch (e) {
    el.innerHTML = '<div class="card"><p style="color:#ef4444">Error: ' + e + '</p></div>';
  }
}
document.getElementById('test-active-btn').addEventListener('click', async (ev) => {
  ev.preventDefault();
  const el = document.getElementById('test-active-result');
  el.style.display = 'block';
  el.textContent = 'Testing active config...';
  const csrf = '{{ csrf }}';
  try {
    const r = await fetch('/admin/proxy/test', {
      method: 'POST',
      headers: {'X-CSRF-Token': csrf}
    });
    const data = await r.json();
    el.textContent = JSON.stringify(data, null, 2);
  } catch (e) {
    el.textContent = 'Error: ' + e;
  }
});
</script>
"""


# ===========================================================================
# App factory
# ===========================================================================
def _is_https_hint() -> bool:
    return bool(os.environ.get("RAILWAY_PUBLIC_DOMAIN")) or os.environ.get("HTTPS", "") == "on"


def build_admin_app() -> Flask:
    app = Flask(__name__, static_folder=None)

    secret = os.environ.get("SECRET_KEY") or os.environ.get("ADMIN_SECRET_KEY")
    if not secret:
        secret_file = DB_PATH / "flask_secret.txt"
        if secret_file.exists():
            secret = secret_file.read_text().strip()
        else:
            secret = secrets.token_urlsafe(32)
            secret_file.write_text(secret)
    app.secret_key = secret

    app.config["SESSION_COOKIE_HTTPONLY"] = True
    app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
    if _is_https_hint():
        app.config["SESSION_COOKIE_SECURE"] = True

    @app.before_request
    def _attach_csrf():
        _csrf_token()

    # ===================================================================
    # Login
    # ===================================================================
    @app.route("/", methods=["GET"])
    def index():
        if session.get("admin"):
            return redirect(url_for("dashboard"))
        return redirect(url_for("login"))

    @app.route("/admin/login", methods=["GET"])
    @_rate_limit
    def login():
        from .admin_totp import is_enabled as _totp_enabled
        body = render_template_string(
            LOGIN_TEMPLATE,
            csrf=_csrf_token(),
            totp_enabled=_totp_enabled(),
        )
        return render_template_string(BASE_TEMPLATE, title="Login", body=body, page="login")

    @app.route("/admin/login", methods=["POST"])
    @_rate_limit
    def login_post():
        _csrf_check()
        pw = request.form.get("password", "")
        if pw != ADMIN_PASSWORD:
            flash("Invalid password", "error")
            return redirect(url_for("login"))

        from .admin_totp import is_enabled as _totp_enabled, verify as _totp_verify
        if _totp_enabled():
            code = request.form.get("totp", "").strip()
            if not _totp_verify(code):
                flash("Invalid 2FA code", "error")
                return redirect(url_for("login"))

        session["admin"] = True
        session.permanent = True
        return redirect(url_for("dashboard"))

    @app.route("/admin/logout")
    def logout():
        session.clear()
        return redirect(url_for("login"))

    # ===================================================================
    # Dashboard
    # ===================================================================
    @app.route("/admin/dashboard")
    @_login_required
    def dashboard():
        tx_total = tx_totals()
        body = render_template_string(
            DASHBOARD_TEMPLATE,
            users_total=user_count(),
            users_active=active_last_24h(),
            users_new=new_last_7d(),
            diamonds=total_in_circulation(),
            granted=tx_total["granted"],
            spent=tx_total["spent"],
            top_spenders=top_spenders(10),
            diamond_series=hourly_consumption(24),
            sub_dist=distribution(),
            approval_series=approval_rate_series(14),
        )
        return render_template_string(BASE_TEMPLATE, title="Dashboard", body=body,
                                      page="dashboard")

    # ===================================================================
    # Users
    # ===================================================================
    @app.route("/admin/users")
    @_login_required
    def users_page():
        raw = all_users()
        enriched = []
        for uid_str, u in raw.items():
            try:
                uid = int(uid_str)
            except ValueError:
                continue
            enriched.append({
                "id": uid,
                "first_name": u.get("first_name", ""),
                "username": u.get("username", ""),
                "plan": effective_plan(uid),
                "days": days_left(uid),
                "diamonds": int(u.get("diamonds", 0) or 0),
                "banned": bool(u.get("banned", False)),
            })
        enriched.sort(key=lambda x: x["id"])
        body = render_template_string(USERS_TEMPLATE, users=enriched)
        return render_template_string(BASE_TEMPLATE, title="Users", body=body, page="users")

    @app.route("/admin/users/<int:uid>")
    @_login_required
    def user_detail(uid: int):
        u = get_user(uid) or {"id": uid}
        body = render_template_string(
            USER_DETAIL_TEMPLATE,
            u={
                "id": uid,
                "diamonds": int(u.get("diamonds", 0) or 0),
                "plan": effective_plan(uid),
                "days": days_left(uid),
                "referral_code": u.get("referral_code", "—"),
                "banned": bool(u.get("banned", False)),
            },
            plans=list(PLANS.keys()),
            txs=tx_for_user(uid, 50),
            csrf=_csrf_token(),
        )
        return render_template_string(BASE_TEMPLATE, title=f"User {uid}", body=body,
                                      page="users")

    @app.route("/admin/users/<int:uid>/grant", methods=["POST"])
    @_login_required
    def grant_diamonds(uid: int):
        _csrf_check()
        try:
            amount = int(request.form.get("amount", "0"))
        except ValueError:
            amount = 0
        reason = request.form.get("reason", "admin_grant")
        if amount >= 0:
            grant(uid, amount, reason, meta={"admin": True})
        else:
            spend(uid, -amount, reason, meta={"admin": True})
        audit_record(0, "grant_diamonds", target=str(uid), ip=request.remote_addr,
                     meta={"amount": amount, "reason": reason})
        flash(f"Applied {amount} diamonds to user {uid}.")
        return redirect(url_for("user_detail", uid=uid))

    @app.route("/admin/users/<int:uid>/subscription", methods=["POST"])
    @_login_required
    def set_subscription(uid: int):
        _csrf_check()
        plan = request.form.get("plan", "basic")
        try:
            days = int(request.form.get("days", "30"))
        except ValueError:
            days = 30
        auto = request.form.get("auto_renew", "0") == "1"
        set_sub(uid, plan, days=days, auto_renew=auto)
        audit_record(0, "set_subscription", target=str(uid), ip=request.remote_addr,
                     meta={"plan": plan, "days": days})
        flash(f"Subscription set to {plan} for {days} days.")
        return redirect(url_for("user_detail", uid=uid))

    @app.route("/admin/users/<int:uid>/subscription/cancel", methods=["POST"])
    @_login_required
    def cancel_subscription(uid: int):
        _csrf_check()
        cancel_sub(uid)
        audit_record(0, "cancel_subscription", target=str(uid), ip=request.remote_addr)
        flash("Subscription cancelled.")
        return redirect(url_for("user_detail", uid=uid))

    @app.route("/admin/users/<int:uid>/ban", methods=["POST"])
    @_login_required
    def toggle_ban(uid: int):
        _csrf_check()
        u = get_user(uid) or {}
        if u.get("banned"):
            unban(uid)
            audit_record(0, "unban", target=str(uid), ip=request.remote_addr)
        else:
            ban(uid)
            audit_record(0, "ban", target=str(uid), ip=request.remote_addr)
        return redirect(url_for("user_detail", uid=uid))

    @app.route("/admin/users/<int:uid>/delete", methods=["POST"])
    @_login_required
    def delete_user_route(uid: int):
        _csrf_check()
        delete_user(uid)
        audit_record(0, "delete_user", target=str(uid), ip=request.remote_addr)
        flash(f"User {uid} deleted.")
        return redirect(url_for("users_page"))

    # ===================================================================
    # Requests
    # ===================================================================
    @app.route("/admin/requests")
    @_login_required
    def requests_page():
        body = render_template_string(
            REQUESTS_TEMPLATE,
            requests=list(reversed(all_requests()[-200:])),
            csrf=_csrf_token(),
        )
        return render_template_string(BASE_TEMPLATE, title="Requests", body=body,
                                      page="requests")

    @app.route("/admin/requests/<rid>/approve", methods=["POST"])
    @_login_required
    def approve_request(rid: str):
        _csrf_check()
        approve(rid, 0)
        audit_record(0, "approve_request", target=rid, ip=request.remote_addr)
        flash(f"Request {rid} approved.")
        return redirect(url_for("requests_page"))

    @app.route("/admin/requests/<rid>/reject", methods=["POST"])
    @_login_required
    def reject_request(rid: str):
        _csrf_check()
        reject(rid, 0)
        audit_record(0, "reject_request", target=rid, ip=request.remote_addr)
        flash(f"Request {rid} rejected.")
        return redirect(url_for("requests_page"))

    # ===================================================================
    # Transactions
    # ===================================================================
    @app.route("/admin/transactions")
    @_login_required
    def transactions_page():
        body = render_template_string(
            TX_TEMPLATE,
            txs=tx_recent(200),
            totals=tx_totals(),
        )
        return render_template_string(BASE_TEMPLATE, title="Transactions", body=body,
                                      page="tx")

    # ===================================================================
    # Shop
    # ===================================================================
    @app.route("/admin/shop")
    @_login_required
    def shop_page():
        body = render_template_string(
            SHOP_TEMPLATE,
            items=shop_items(),
            csrf=_csrf_token(),
        )
        return render_template_string(BASE_TEMPLATE, title="Shop", body=body, page="shop")

    @app.route("/admin/shop/save", methods=["POST"])
    @_login_required
    def shop_save():
        _csrf_check()
        item_id = request.form.get("item_id", "").strip()
        if not item_id:
            flash("Item ID required.", "error")
            return redirect(url_for("shop_page"))
        existing = shop_items().get(item_id, {})
        try:
            price = int(request.form.get("price", "0") or 0)
        except ValueError:
            price = 0
        try:
            max_per_user = int(request.form.get("max_per_user", "1") or 1)
        except ValueError:
            max_per_user = 1
        set_item(item_id, {
            "name": request.form.get("name", item_id),
            "description": request.form.get("description", ""),
            "price": price,
            "max_per_user": max_per_user,
            "effect": existing.get("effect", {}),
        })
        audit_record(0, "shop_save", target=item_id, ip=request.remote_addr)
        flash(f"Item {item_id} saved.")
        return redirect(url_for("shop_page"))

    @app.route("/admin/shop/delete/<item_id>", methods=["POST"])
    @_login_required
    def shop_delete(item_id: str):
        _csrf_check()
        delete_item(item_id)
        audit_record(0, "shop_delete", target=item_id, ip=request.remote_addr)
        flash(f"Item {item_id} deleted.")
        return redirect(url_for("shop_page"))

    # ===================================================================
    # Audit
    # ===================================================================
    @app.route("/admin/audit")
    @_login_required
    def audit_page():
        body = render_template_string(AUDIT_TEMPLATE, entries=audit_all(500))
        return render_template_string(BASE_TEMPLATE, title="Audit", body=body, page="audit")

    # ===================================================================
    # Backups
    # ===================================================================
    @app.route("/admin/backups")
    @_login_required
    def backups_page():
        backups_dir = DB_PATH / "backups"
        items = []
        if backups_dir.exists():
            files = sorted(backups_dir.glob("*.zip"),
                           key=lambda x: x.stat().st_mtime, reverse=True)
            for f in files:
                stat = f.stat()
                items.append({
                    "name": f.name,
                    "size_kb": round(stat.st_size / 1024, 1),
                    "modified": datetime.fromtimestamp(stat.st_mtime).strftime("%Y-%m-%d %H:%M"),
                })
        body = render_template_string(BACKUPS_TEMPLATE, backups=items, csrf=_csrf_token())
        return render_template_string(BASE_TEMPLATE, title="Backups", body=body,
                                      page="backups")

    @app.route("/admin/backups/now", methods=["POST"])
    @_login_required
    def backup_now():
        _csrf_check()
        from .backup import create_backup_zip, send_backup_to_owner
        try:
            path = create_backup_zip()
            send_backup_to_owner(path)
            audit_record(0, "backup_now", ip=request.remote_addr)
            flash(f"Backup created: {path.name}")
        except Exception as e:
            log_flask.error(f"manual backup failed: {e}")
            flash(f"Backup failed: {e}", "error")
        return redirect(url_for("backups_page"))

    @app.route("/admin/backups/download/<name>")
    @_login_required
    def download_backup(name: str):
        backups_dir = (DB_PATH / "backups").resolve()
        target = (backups_dir / name).resolve()
        if not str(target).startswith(str(backups_dir)) or not target.exists():
            abort(404)
        return send_file(target, as_attachment=True, download_name=name)

    @app.route("/admin/backups/restore", methods=["POST"])
    @_login_required
    def restore_backup():
        _csrf_check()
        from .backup import restore_from_zip
        f = request.files.get("file")
        if not f or not f.filename.endswith(".zip"):
            flash("Upload a .zip file.", "error")
            return redirect(url_for("backups_page"))
        try:
            restore_from_zip(f.read())
            audit_record(0, "restore_backup", ip=request.remote_addr)
            flash("Backup restored successfully.")
        except Exception as e:
            log_flask.error(f"restore failed: {e}")
            flash(f"Restore failed: {e}", "error")
        return redirect(url_for("backups_page"))

    # ===================================================================
    # Broadcast
    # ===================================================================
    @app.route("/admin/broadcast")
    @_login_required
    def broadcast_page():
        body = render_template_string(BROADCAST_TEMPLATE, csrf=_csrf_token())
        return render_template_string(BASE_TEMPLATE, title="Broadcast", body=body,
                                      page="broadcast")

    @app.route("/admin/broadcast/send", methods=["POST"])
    @_login_required
    def broadcast_send():
        _csrf_check()
        msg = request.form.get("message", "").strip()
        if not msg:
            flash("Empty message.", "error")
            return redirect(url_for("broadcast_page"))

        rt = _get_runtime_safe()
        if not rt or not rt.bot_client:
            flash("Bot not connected.", "error")
            return redirect(url_for("broadcast_page"))

        try:
            from selfbot.bootstrap import _async_loop
        except Exception:
            _async_loop = None

        if _async_loop is None or not _async_loop.is_running():
            flash("Bot event loop is not running.", "error")
            return redirect(url_for("broadcast_page"))

        import asyncio

        async def _do():
            sent = 0
            for uid_str, u in all_users().items():
                if u.get("banned"):
                    continue
                try:
                    await rt.bot_client.send_message(int(uid_str), msg)
                    sent += 1
                except Exception:
                    pass
            return sent

        try:
            fut = asyncio.run_coroutine_threadsafe(_do(), _async_loop)
            sent = fut.result(timeout=120)
            audit_record(0, "broadcast", ip=request.remote_addr, meta={"sent": sent})
            flash(f"Broadcast sent to {sent} users.")
        except Exception as e:
            flash(f"Broadcast failed: {e}", "error")
        return redirect(url_for("broadcast_page"))

    # ===================================================================
    # Settings
    # ===================================================================
    @app.route("/admin/settings")
    @_login_required
    def settings_page():
        body = render_template_string(SETTINGS_TEMPLATE, cfg=CONFIG, csrf=_csrf_token())
        return render_template_string(BASE_TEMPLATE, title="Settings", body=body,
                                      page="settings")

    @app.route("/admin/settings/save", methods=["POST"])
    @_login_required
    def settings_save():
        _csrf_check()
        int_fields = ("signup_bonus", "diamond_cost_clock", "diamond_cost_job",
                      "referral_bonus", "interval")
        str_fields = ("timezone", "base_name")
        for key in int_fields:
            val = request.form.get(key)
            if val is None:
                continue
            try:
                n = int(val)
                if key == "interval":
                    n = max(1, min(60, n))
                CONFIG[key] = n
            except ValueError:
                pass
        for key in str_fields:
            val = request.form.get(key)
            if val is not None:
                CONFIG[key] = val
        save_config(CONFIG)
        audit_record(0, "settings_save", ip=request.remote_addr)
        flash("Settings saved.")
        return redirect(url_for("settings_page"))

    # ===================================================================
    # Two-Factor Authentication
    # ===================================================================
    @app.route("/admin/settings/2fa", methods=["GET"])
    @_login_required
    def totp_page():
        from .admin_totp import HAS_PYOTP, get_secret, is_enabled, provision
        import base64 as _b64

        enabled = is_enabled()
        secret = get_secret() if enabled else None
        qr_b64 = ""
        uri = ""
        if not enabled and HAS_PYOTP:
            s, uri, png = provision()
            session["pending_totp_secret"] = s
            if png:
                qr_b64 = _b64.b64encode(png).decode()

        body = render_template_string(
            TOTP_TEMPLATE,
            enabled=enabled,
            secret=secret,
            uri=uri,
            qr_b64=qr_b64,
            pending_secret=session.get("pending_totp_secret", ""),
            csrf=_csrf_token(),
        )
        return render_template_string(BASE_TEMPLATE, title="2FA", body=body,
                                      page="settings")

    @app.route("/admin/settings/2fa/enable", methods=["POST"])
    @_login_required
    def totp_enable():
        _csrf_check()
        from .admin_totp import enable as _enable
        secret = session.get("pending_totp_secret", "")
        if not secret:
            flash("No pending secret. Reload the page.", "error")
            return redirect(url_for("totp_page"))
        _enable(secret)
        session.pop("pending_totp_secret", None)
        audit_record(0, "totp_enable", ip=request.remote_addr)
        flash("2FA enabled.")
        return redirect(url_for("totp_page"))

    @app.route("/admin/settings/2fa/disable", methods=["POST"])
    @_login_required
    def totp_disable():
        _csrf_check()
        from .admin_totp import disable as _disable
        _disable()
        audit_record(0, "totp_disable", ip=request.remote_addr)
        flash("2FA disabled.")
        return redirect(url_for("totp_page"))

    # ===================================================================
    # Proxy configs
    # ===================================================================
    @app.route("/admin/proxy")
    @_login_required
    def proxy_page():
        from .proxy_test import list_configs
        data = list_configs()
        configs = data.get("configs", {})
        active = data.get("active", "default")

        rows = []
        for key, cfg in configs.items():
            rows.append({
                "key": key,
                "name": cfg.get("name", key),
                "url_short": ((cfg.get("url") or "")[:60] + "...")
                             if len((cfg.get("url") or "")) > 60
                             else (cfg.get("url") or ""),
                "active": (key == active),
            })

        body = render_template_string(
            PROXY_TEMPLATE, configs=rows, active=active, csrf=_csrf_token(),
        )
        return render_template_string(BASE_TEMPLATE, title="Proxy Configs", body=body,
                                      page="proxy")

    @app.route("/admin/proxy/add", methods=["POST"])
    @_login_required
    def proxy_add():
        _csrf_check()
        from .proxy_test import add_config
        key = request.form.get("key", "").strip()
        name = request.form.get("name", "").strip() or key
        url = request.form.get("url", "").strip()
        outbound_json = request.form.get("outbound", "").strip()
        if not key or not url or not outbound_json:
            flash("Key, URL and outbound JSON are required.", "error")
            return redirect(url_for("proxy_page"))
        try:
            outbound = json.loads(outbound_json)
        except Exception as e:
            flash(f"Invalid outbound JSON: {e}", "error")
            return redirect(url_for("proxy_page"))
        add_config(key, name, url, outbound)
        audit_record(0, "proxy_add", target=key, ip=request.remote_addr)
        flash(f"Config {key} added.")
        return redirect(url_for("proxy_page"))

    @app.route("/admin/proxy/activate/<key>", methods=["POST"])
    @_login_required
    def proxy_activate(key: str):
        _csrf_check()
        from .proxy_test import set_active
        if set_active(key):
            audit_record(0, "proxy_activate", target=key, ip=request.remote_addr)
            flash(f"Config {key} activated. Restart the service to apply.")
        else:
            flash(f"Config {key} not found.", "error")
        return redirect(url_for("proxy_page"))

    @app.route("/admin/proxy/test/<key>", methods=["POST"])
    @_login_required
    def proxy_test_run(key: str):
        _csrf_check()
        from .proxy_test import test_config
        try:
            result = test_config(key)
        except Exception as e:
            result = {"ok": False, "error": str(e)}
        return jsonify(result)

    @app.route("/admin/proxy/test", methods=["POST"])
    @_login_required
    def proxy_test_active():
        _csrf_check()
        from .proxy_test import list_configs, test_config
        data = list_configs()
        key = data.get("active", "default")
        try:
            result = test_config(key)
        except Exception as e:
            result = {"ok": False, "error": str(e)}
        return jsonify(result)

    return app


# ===========================================================================
# Runtime accessor
# ===========================================================================
def _get_runtime_safe():
    try:
        from app import _get_runtime
        return _get_runtime()
    except Exception:
        return None