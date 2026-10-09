"""Flask admin panel — clean monochrome design."""
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

from flask import (Flask, abort, flash, jsonify, redirect,
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
# Password
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
# Auth helpers
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
# Base layout
# ===========================================================================
BASE_TEMPLATE = """
<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>{{ title }} · Admin</title>
<meta name="viewport" content="width=device-width, initial-scale=1">
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&family=JetBrains+Mono:wght@400;500&display=swap" rel="stylesheet">
<script src="https://cdn.jsdelivr.net/npm/chart.js@4"></script>
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
  --active: #f0f0f0;
  --black: #0a0a0a;
  --white: #ffffff;
  --accent: #171717;
  --green: #16a34a;
  --red: #dc2626;
  --amber: #d97706;
  --mono: 'JetBrains Mono', ui-monospace, monospace;
}
* { box-sizing: border-box; margin: 0; padding: 0; }
html { -webkit-text-size-adjust: 100%; }
body {
  font-family: 'Inter', -apple-system, BlinkMacSystemFont, sans-serif;
  background: var(--bg);
  color: var(--text);
  font-size: 14px;
  line-height: 1.5;
  -webkit-font-smoothing: antialiased;
  font-feature-settings: 'cv11', 'ss01';
}
a { color: var(--text); text-decoration: none; }
a:hover { text-decoration: underline; }
button { font-family: inherit; }

/* ---------- Layout ---------- */
.layout {
  display: grid;
  grid-template-columns: 240px 1fr;
  min-height: 100vh;
}
@media (max-width: 900px) {
  .layout { grid-template-columns: 1fr; }
  .sidebar { display: none !important; }
}

/* ---------- Sidebar ---------- */
.sidebar {
  background: var(--black);
  color: var(--white);
  display: flex;
  flex-direction: column;
  padding: 24px 16px;
  position: sticky;
  top: 0;
  height: 100vh;
}
.brand {
  display: flex; align-items: center; gap: 10px;
  padding: 0 8px 24px;
  font-weight: 800; font-size: 15px;
  letter-spacing: -.02em;
}
.brand-mark {
  width: 28px; height: 28px;
  background: var(--white);
  color: var(--black);
  border-radius: 8px;
  display: grid; place-items: center;
  font-size: 15px; font-weight: 800;
}
.nav-group {
  font-size: 10px;
  text-transform: uppercase;
  letter-spacing: .12em;
  font-weight: 700;
  color: #737373;
  padding: 18px 8px 8px;
}
.nav-link {
  display: flex; align-items: center; gap: 10px;
  padding: 9px 12px;
  border-radius: 8px;
  color: #d4d4d4;
  font-size: 13px;
  font-weight: 500;
  transition: background .12s, color .12s;
}
.nav-link:hover {
  background: #1a1a1a; color: var(--white);
  text-decoration: none;
}
.nav-link.active {
  background: #262626;
  color: var(--white);
  font-weight: 600;
}
.nav-link .dot {
  width: 4px; height: 4px; border-radius: 50%;
  background: currentColor; opacity: .5;
}
.sidebar-footer { margin-top: auto; padding-top: 16px; border-top: 1px solid #262626; }

/* ---------- Main ---------- */
.main {
  padding: 40px 48px 60px;
  max-width: 1400px;
  width: 100%;
}
.page-head {
  display: flex; align-items: flex-end; justify-content: space-between;
  gap: 20px; margin-bottom: 32px; flex-wrap: wrap;
  padding-bottom: 24px;
  border-bottom: 1px solid var(--border);
}
.page-title {
  font-size: 28px; font-weight: 800;
  letter-spacing: -.035em;
  color: var(--text);
}
.page-sub {
  color: var(--text-2); font-size: 13.5px; margin-top: 4px;
}
.crumb {
  font-size: 11px; font-weight: 700;
  letter-spacing: .12em; text-transform: uppercase;
  color: var(--text-3); margin-bottom: 6px;
}

/* ---------- Buttons ---------- */
.btn {
  display: inline-flex; align-items: center; justify-content: center;
  gap: 6px;
  padding: 9px 16px;
  border-radius: 8px;
  border: 1px solid var(--border-2);
  background: var(--white);
  color: var(--text);
  font-size: 13px; font-weight: 500;
  cursor: pointer;
  transition: all .12s;
  text-decoration: none;
  line-height: 1.2;
}
.btn:hover { background: var(--hover); text-decoration: none; }
.btn-dark {
  background: var(--black); color: var(--white);
  border-color: var(--black);
}
.btn-dark:hover { background: #262626; }
.btn-danger { background: var(--red); color: var(--white); border-color: var(--red); }
.btn-danger:hover { background: #b91c1c; }
.btn-success { background: var(--green); color: var(--white); border-color: var(--green); }
.btn-success:hover { background: #15803d; }
.btn-sm { padding: 5px 10px; font-size: 12px; border-radius: 6px; }
.btn-ghost { background: transparent; border-color: transparent; }
.btn-ghost:hover { background: var(--hover); }
.btn:disabled { opacity: .5; cursor: not-allowed; }

/* ---------- Forms ---------- */
label {
  display: block; font-size: 12px; font-weight: 600;
  color: var(--text-2); margin: 14px 0 6px;
}
input, select, textarea {
  width: 100%;
  padding: 10px 12px;
  border: 1px solid var(--border-2);
  border-radius: 8px;
  background: var(--white);
  color: var(--text);
  font-size: 13.5px;
  font-family: inherit;
  transition: border-color .15s, box-shadow .15s;
}
input:focus, select:focus, textarea:focus {
  outline: none;
  border-color: var(--text);
  box-shadow: 0 0 0 3px rgba(10,10,10,.06);
}
textarea { resize: vertical; }
.field-row {
  display: flex; gap: 12px; flex-wrap: wrap; align-items: flex-end;
}
.field-row > * { flex: 1; min-width: 140px; }

/* ---------- Cards ---------- */
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
  font-size: 12.5px; color: var(--text-3);
  font-weight: 500; margin-left: 8px;
}

/* ---------- Stats ---------- */
.stats {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
  gap: 16px;
  margin-bottom: 24px;
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
  color: var(--text);
  font-variant-numeric: tabular-nums;
}
.stat .v small { font-size: 14px; color: var(--text-3); font-weight: 500; margin-left: 4px; }

/* ---------- Tables ---------- */
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

/* ---------- Badges ---------- */
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
.badge.red { background: #fef2f2; color: var(--red); border-color: #fecaca; }
.badge.amber { background: #fffbeb; color: var(--amber); border-color: #fde68a; }

/* ---------- Flash ---------- */
.flash {
  padding: 12px 16px; border-radius: 10px;
  font-size: 13.5px; font-weight: 500;
  margin-bottom: 20px;
  background: #f0fdf4; color: #166534;
  border: 1px solid #bbf7d0;
  display: flex; align-items: center; gap: 8px;
}
.flash.err { background: #fef2f2; color: #991b1b; border-color: #fecaca; }

/* ---------- Chart ---------- */
.chart-box { height: 240px; position: relative; }
.grid-2 {
  display: grid; grid-template-columns: 1fr 1fr;
  gap: 20px; margin-bottom: 20px;
}
@media (max-width: 1000px) { .grid-2 { grid-template-columns: 1fr; } }

/* ---------- Login ---------- */
.login-page {
  min-height: 100vh;
  display: grid; place-items: center;
  padding: 40px 20px;
  background: var(--bg);
}
.login-card {
  width: 100%; max-width: 380px;
  background: var(--white);
  border: 1px solid var(--border);
  border-radius: 16px;
  padding: 40px 32px;
  box-shadow: 0 1px 2px rgba(0,0,0,.03), 0 8px 24px rgba(0,0,0,.04);
}
.login-mark {
  width: 48px; height: 48px;
  background: var(--black); color: var(--white);
  border-radius: 12px;
  display: grid; place-items: center;
  font-size: 22px; font-weight: 800;
  margin: 0 auto 20px;
}
.login-card h1 {
  font-size: 20px; font-weight: 800;
  text-align: center;
  letter-spacing: -.02em;
  color: var(--text);
}
.login-card .sub {
  text-align: center;
  color: var(--text-2);
  font-size: 13.5px;
  margin: 4px 0 24px;
}

/* ---------- Empty ---------- */
.empty {
  padding: 40px 20px;
  text-align: center;
  color: var(--text-3);
  font-size: 13.5px;
}
.mt-2 { margin-top: 8px; }
.mt-3 { margin-top: 16px; }
.mt-4 { margin-top: 24px; }

code {
  font-family: var(--mono);
  font-size: 12.5px;
  background: #f5f5f5;
  padding: 2px 6px;
  border-radius: 5px;
  color: var(--text);
}
</style>
</head>
<body>

{% if session.get('admin') %}
<div class="layout">
  <aside class="sidebar">
    <div class="brand">
      <div class="brand-mark">S</div>
      <span>SELF BOT</span>
    </div>

    <div class="nav-group">Overview</div>
    <a href="{{ url_for('dashboard') }}" class="nav-link {{ 'active' if page=='dashboard' else '' }}">
      <span class="dot"></span> Dashboard
    </a>
    <a href="{{ url_for('users_page') }}" class="nav-link {{ 'active' if page=='users' else '' }}">
      <span class="dot"></span> Users
    </a>
    <a href="{{ url_for('requests_page') }}" class="nav-link {{ 'active' if page=='requests' else '' }}">
      <span class="dot"></span> Requests
    </a>
    <a href="{{ url_for('transactions_page') }}" class="nav-link {{ 'active' if page=='tx' else '' }}">
      <span class="dot"></span> Transactions
    </a>

    <div class="nav-group">System</div>
    <a href="{{ url_for('shop_page') }}" class="nav-link {{ 'active' if page=='shop' else '' }}">
      <span class="dot"></span> Shop
    </a>
    <a href="{{ url_for('proxy_page') }}" class="nav-link {{ 'active' if page=='proxy' else '' }}">
      <span class="dot"></span> Proxy
    </a>
    <a href="{{ url_for('audit_page') }}" class="nav-link {{ 'active' if page=='audit' else '' }}">
      <span class="dot"></span> Audit
    </a>
    <a href="{{ url_for('backups_page') }}" class="nav-link {{ 'active' if page=='backups' else '' }}">
      <span class="dot"></span> Backups
    </a>
    <a href="{{ url_for('broadcast_page') }}" class="nav-link {{ 'active' if page=='broadcast' else '' }}">
      <span class="dot"></span> Broadcast
    </a>
    <a href="{{ url_for('settings_page') }}" class="nav-link {{ 'active' if page=='settings' else '' }}">
      <span class="dot"></span> Settings
    </a>

    <div class="sidebar-footer">
      <a href="{{ url_for('logout') }}" class="nav-link">
        <span class="dot"></span> Sign out
      </a>
    </div>
  </aside>

  <main class="main">
    {% with msgs = get_flashed_messages(with_categories=true) %}
      {% for cat, msg in msgs %}
        <div class="flash {{ 'err' if cat=='error' else '' }}">{{ msg }}</div>
      {% endfor %}
    {% endwith %}
    {{ body|safe }}
  </main>
</div>
{% else %}
{{ body|safe }}
{% endif %}

</body>
</html>
"""


LOGIN_TEMPLATE = """
<div class="login-page">
  <form class="login-card" method="post" action="{{ url_for('login_post') }}">
    <div class="login-mark">S</div>
    <h1>Admin Sign In</h1>
    <p class="sub">SELF BOT control panel</p>

    <input type="hidden" name="csrf" value="{{ csrf }}">

    <label>Password</label>
    <input type="password" name="password" placeholder="••••••••••" autofocus required>

    {% if totp_enabled %}
    <label>2FA code</label>
    <input type="text" name="totp" inputmode="numeric" pattern="[0-9]*" maxlength="6" placeholder="000000">
    {% endif %}

    <div style="height:20px"></div>
    <button type="submit" class="btn btn-dark" style="width:100%">Sign in</button>
  </form>
</div>
"""


# ===========================================================================
# Dashboard
# ===========================================================================
DASHBOARD_TEMPLATE = """
<div class="page-head">
  <div>
    <div class="crumb">Overview</div>
    <div class="page-title">Dashboard</div>
    <div class="page-sub">Live snapshot of your bot economy</div>
  </div>
</div>

<div class="stats">
  <div class="stat">
    <div class="k">Total users</div>
    <div class="v">{{ users_total }}</div>
  </div>
  <div class="stat">
    <div class="k">Active 24h</div>
    <div class="v">{{ users_active }}</div>
  </div>
  <div class="stat">
    <div class="k">New 7 days</div>
    <div class="v">{{ users_new }}</div>
  </div>
  <div class="stat">
    <div class="k">Diamonds in circulation</div>
    <div class="v">{{ diamonds }}</div>
  </div>
  <div class="stat">
    <div class="k">Granted all time</div>
    <div class="v">{{ granted }}</div>
  </div>
  <div class="stat">
    <div class="k">Spent all time</div>
    <div class="v">{{ spent }}</div>
  </div>
</div>

<div class="grid-2">
  <div class="card">
    <h2>Diamond consumption <span class="sub">last 24 hours</span></h2>
    <div class="chart-box"><canvas id="diamondChart"></canvas></div>
  </div>
  <div class="card">
    <h2>Subscription distribution</h2>
    <div class="chart-box"><canvas id="subChart"></canvas></div>
  </div>
</div>

<div class="grid-2">
  <div class="card">
    <h2>Request approval rate <span class="sub">last 14 days</span></h2>
    <div class="chart-box"><canvas id="approvalChart"></canvas></div>
  </div>
  <div class="card">
    <h2>Top spenders</h2>
    <div class="table-wrap">
      <table>
        <thead><tr><th>#</th><th>User</th><th style="text-align:right">Spent</th></tr></thead>
        <tbody>
        {% for s in top_spenders %}
        <tr>
          <td class="mono">{{ loop.index }}</td>
          <td><a href="{{ url_for('user_detail', uid=s.user_id) }}">#{{ s.user_id }}</a></td>
          <td style="text-align:right" class="mono">{{ s.spent }} ◆</td>
        </tr>
        {% else %}
        <tr><td colspan="3" class="empty">No spenders yet</td></tr>
        {% endfor %}
        </tbody>
      </table>
    </div>
  </div>
</div>

<script>
Chart.defaults.color = '#a3a3a3';
Chart.defaults.font.family = 'Inter, sans-serif';
Chart.defaults.font.size = 11;
Chart.defaults.borderColor = '#e5e5e5';

const diamondData = {{ diamond_series|safe }};
new Chart(document.getElementById('diamondChart'), {
  type: 'line',
  data: {
    labels: diamondData.map(d => d.hour.slice(11, 16)),
    datasets: [{
      label: 'Spent',
      data: diamondData.map(d => d.spent),
      borderColor: '#0a0a0a',
      backgroundColor: 'rgba(10,10,10,.06)',
      fill: true, tension: .35, pointRadius: 0, pointHoverRadius: 5,
      borderWidth: 2,
    }]
  },
  options: {
    responsive: true, maintainAspectRatio: false,
    plugins: { legend: { display: false } },
    scales: {
      x: { grid: { display: false }, border: { display: false } },
      y: { grid: { color: '#f0f0f0' }, border: { display: false }, beginAtZero: true }
    }
  }
});

const subData = {{ sub_dist|safe }};
new Chart(document.getElementById('subChart'), {
  type: 'doughnut',
  data: {
    labels: Object.keys(subData),
    datasets: [{
      data: Object.values(subData),
      backgroundColor: ['#171717', '#525252', '#a3a3a3', '#e5e5e5'],
      borderWidth: 0,
      hoverOffset: 6,
    }]
  },
  options: {
    responsive: true, maintainAspectRatio: false,
    cutout: '68%',
    plugins: { legend: { position: 'bottom', labels: { padding: 14, boxWidth: 10, boxHeight: 10 } } }
  }
});

const approvalData = {{ approval_series|safe }};
new Chart(document.getElementById('approvalChart'), {
  type: 'bar',
  data: {
    labels: approvalData.map(d => d.date.slice(5)),
    datasets: [
      { label: 'Approved', data: approvalData.map(d => d.approved),
        backgroundColor: '#0a0a0a', borderRadius: 4, barThickness: 10 },
      { label: 'Rejected', data: approvalData.map(d => d.rejected),
        backgroundColor: '#d4d4d4', borderRadius: 4, barThickness: 10 },
    ]
  },
  options: {
    responsive: true, maintainAspectRatio: false,
    plugins: { legend: { position: 'bottom', labels: { padding: 14, boxWidth: 10, boxHeight: 10 } } },
    scales: {
      x: { stacked: true, grid: { display: false }, border: { display: false } },
      y: { stacked: true, grid: { color: '#f0f0f0' }, border: { display: false } }
    }
  }
});
</script>
"""


# ===========================================================================
# Users
# ===========================================================================
USERS_TEMPLATE = """
<div class="page-head">
  <div>
    <div class="crumb">Manage</div>
    <div class="page-title">Users</div>
    <div class="page-sub">{{ users|length }} registered accounts</div>
  </div>
</div>

<div class="table-wrap">
  <table>
    <thead>
      <tr>
        <th>ID</th><th>Name</th><th>Username</th><th>Plan</th>
        <th>Days</th><th>Diamonds</th><th>Status</th><th></th>
      </tr>
    </thead>
    <tbody>
    {% for u in users %}
    <tr>
      <td class="mono">#{{ u.id }}</td>
      <td>{{ u.first_name or '—' }}</td>
      <td class="muted">{{ ('@' + u.username) if u.username else '—' }}</td>
      <td>{% if u.plan != 'free' %}<span class="badge solid">{{ u.plan }}</span>{% else %}<span class="badge">{{ u.plan }}</span>{% endif %}</td>
      <td class="mono">{{ u.days }}</td>
      <td class="mono">{{ u.diamonds }}</td>
      <td>{% if u.banned %}<span class="badge red">banned</span>{% else %}<span class="badge green">active</span>{% endif %}</td>
      <td style="text-align:right">
        <a class="btn btn-sm" href="{{ url_for('user_detail', uid=u.id) }}">Open</a>
      </td>
    </tr>
    {% else %}
    <tr><td colspan="8" class="empty">No users yet.</td></tr>
    {% endfor %}
    </tbody>
  </table>
</div>
"""


USER_DETAIL_TEMPLATE = """
<div class="page-head">
  <div>
    <div class="crumb">User</div>
    <div class="page-title">#{{ u.id }}</div>
    <div class="page-sub">{{ u.first_name or 'Unnamed' }} · joined {{ u.created_at[:10] if u.created_at else '—' }}</div>
  </div>
  <a class="btn" href="{{ url_for('users_page') }}">← Back</a>
</div>

<div class="stats">
  <div class="stat"><div class="k">Diamonds</div><div class="v">{{ u.diamonds }}</div></div>
  <div class="stat"><div class="k">Plan</div><div class="v" style="font-size:22px">{{ u.plan }}</div></div>
  <div class="stat"><div class="k">Days left</div><div class="v">{{ u.days }}</div></div>
  <div class="stat"><div class="k">Referral code</div><div class="v mono" style="font-size:15px">{{ u.referral_code }}</div></div>
</div>

<div class="grid-2">
  <div class="card">
    <h2>Grant diamonds</h2>
    <form method="post" action="{{ url_for('grant_diamonds', uid=u.id) }}">
      <input type="hidden" name="csrf" value="{{ csrf }}">
      <div class="field-row">
        <div><label>Amount</label><input type="number" name="amount" value="10"></div>
        <div><label>Reason</label><input type="text" name="reason" value="admin_grant"></div>
        <button class="btn btn-dark">Apply</button>
      </div>
    </form>
  </div>

  <div class="card">
    <h2>Set subscription</h2>
    <form method="post" action="{{ url_for('set_subscription', uid=u.id) }}">
      <input type="hidden" name="csrf" value="{{ csrf }}">
      <div class="field-row">
        <div><label>Plan</label>
          <select name="plan">
            {% for p in plans %}<option value="{{ p }}" {{ 'selected' if p==u.plan else '' }}>{{ p }}</option>{% endfor %}
          </select>
        </div>
        <div style="flex:0 0 110px"><label>Days</label><input type="number" name="days" value="30"></div>
        <div style="flex:0 0 130px"><label>Auto-renew</label>
          <select name="auto_renew"><option value="0">No</option><option value="1">Yes</option></select>
        </div>
        <button class="btn btn-dark">Set</button>
      </div>
    </form>
    <div class="mt-2">
      <form method="post" action="{{ url_for('cancel_subscription', uid=u.id) }}" style="display:inline">
        <input type="hidden" name="csrf" value="{{ csrf }}">
        <button class="btn btn-sm">Cancel subscription</button>
      </form>
    </div>
  </div>
</div>

<div class="card">
  <h2>Moderation</h2>
  <form method="post" action="{{ url_for('toggle_ban', uid=u.id) }}" style="display:inline">
    <input type="hidden" name="csrf" value="{{ csrf }}">
    <button class="btn {{ 'btn-success' if u.banned else 'btn-danger' }}">
      {{ 'Unban user' if u.banned else 'Ban user' }}
    </button>
  </form>
  <form method="post" action="{{ url_for('delete_user_route', uid=u.id) }}" style="display:inline"
        onsubmit="return confirm('Delete user? This cannot be undone.');">
    <input type="hidden" name="csrf" value="{{ csrf }}">
    <button class="btn btn-danger">Delete permanently</button>
  </form>
</div>

<div class="card">
  <h2>Recent transactions</h2>
  <div class="table-wrap">
    <table>
      <thead><tr><th>When</th><th>Kind</th><th>Amount</th><th>Reason</th><th style="text-align:right">Balance</th></tr></thead>
      <tbody>
      {% for tx in txs %}
      <tr>
        <td class="mono muted">{{ tx.created_at[:19] }}</td>
        <td>{% if tx.kind == 'spend' %}<span class="badge red">{{ tx.kind }}</span>{% else %}<span class="badge green">{{ tx.kind }}</span>{% endif %}</td>
        <td class="mono">{{ tx.amount }}</td>
        <td class="muted">{{ tx.reason }}</td>
        <td class="mono" style="text-align:right">{{ tx.balance_after }}</td>
      </tr>
      {% else %}
      <tr><td colspan="5" class="empty">No transactions.</td></tr>
      {% endfor %}
      </tbody>
    </table>
  </div>
</div>
"""


# ===========================================================================
# Requests
# ===========================================================================
REQUESTS_TEMPLATE = """
<div class="page-head">
  <div>
    <div class="crumb">Manage</div>
    <div class="page-title">Requests</div>
    <div class="page-sub">Approve or reject user submissions</div>
  </div>
</div>

<div class="table-wrap">
  <table>
    <thead>
      <tr><th>ID</th><th>User</th><th>Type</th><th>Details</th><th>Status</th><th>Created</th><th style="text-align:right">Actions</th></tr>
    </thead>
    <tbody>
    {% for r in requests %}
    <tr>
      <td class="mono muted">{{ r.id }}</td>
      <td><a href="{{ url_for('user_detail', uid=r.user_id) }}">#{{ r.user_id }}</a></td>
      <td>{{ r.type }}</td>
      <td><strong>{{ r.amount or r.plan or '—' }}</strong></td>
      <td>
        {% if r.status == 'pending' %}<span class="badge amber">pending</span>
        {% elif r.status == 'approved' %}<span class="badge green">approved</span>
        {% else %}<span class="badge red">rejected</span>{% endif %}
      </td>
      <td class="mono muted">{{ r.created_at[:19] }}</td>
      <td style="text-align:right">
        {% if r.status == 'pending' %}
        <form method="post" action="{{ url_for('approve_request', rid=r.id) }}" style="display:inline">
          <input type="hidden" name="csrf" value="{{ csrf }}">
          <button class="btn btn-success btn-sm">Approve</button>
        </form>
        <form method="post" action="{{ url_for('reject_request', rid=r.id) }}" style="display:inline">
          <input type="hidden" name="csrf" value="{{ csrf }}">
          <button class="btn btn-danger btn-sm">Reject</button>
        </form>
        {% endif %}
      </td>
    </tr>
    {% else %}
    <tr><td colspan="7" class="empty">No requests.</td></tr>
    {% endfor %}
    </tbody>
  </table>
</div>
"""


# ===========================================================================
# Transactions
# ===========================================================================
TX_TEMPLATE = """
<div class="page-head">
  <div>
    <div class="crumb">Manage</div>
    <div class="page-title">Transactions</div>
    <div class="page-sub">Complete ledger of diamond flow</div>
  </div>
</div>

<div class="stats">
  <div class="stat"><div class="k">Granted</div><div class="v">{{ totals.granted }}</div></div>
  <div class="stat"><div class="k">Spent</div><div class="v">{{ totals.spent }}</div></div>
  <div class="stat"><div class="k">Refunded</div><div class="v">{{ totals.refunded }}</div></div>
</div>

<div class="table-wrap">
  <table>
    <thead><tr><th>When</th><th>User</th><th>Kind</th><th>Amount</th><th>Reason</th><th style="text-align:right">Balance</th></tr></thead>
    <tbody>
    {% for tx in txs %}
    <tr>
      <td class="mono muted">{{ tx.created_at[:19] }}</td>
      <td><a href="{{ url_for('user_detail', uid=tx.user_id) }}">#{{ tx.user_id }}</a></td>
      <td>{% if tx.kind == 'spend' %}<span class="badge red">{{ tx.kind }}</span>{% else %}<span class="badge green">{{ tx.kind }}</span>{% endif %}</td>
      <td class="mono">{{ tx.amount }}</td>
      <td class="muted">{{ tx.reason }}</td>
      <td class="mono" style="text-align:right">{{ tx.balance_after }}</td>
    </tr>
    {% else %}
    <tr><td colspan="6" class="empty">No transactions.</td></tr>
    {% endfor %}
    </tbody>
  </table>
</div>
"""


# ===========================================================================
# Shop
# ===========================================================================
SHOP_TEMPLATE = """
<div class="page-head">
  <div>
    <div class="crumb">System</div>
    <div class="page-title">Shop</div>
    <div class="page-sub">Items users can buy with diamonds</div>
  </div>
</div>

<div class="card">
  <h2>Add or update item</h2>
  <form method="post" action="{{ url_for('shop_save') }}">
    <input type="hidden" name="csrf" value="{{ csrf }}">
    <div class="field-row">
      <div><label>ID</label><input name="item_id" placeholder="extra_job_slot"></div>
      <div><label>Name</label><input name="name" placeholder="Extra job slot"></div>
      <div style="flex:0 0 120px"><label>Price</label><input type="number" name="price" value="20"></div>
      <div style="flex:0 0 140px"><label>Max per user</label><input type="number" name="max_per_user" value="1"></div>
    </div>
    <label>Description</label>
    <input name="description" placeholder="What does this item do?">
    <div class="mt-3">
      <button class="btn btn-dark">Save item</button>
    </div>
  </form>
</div>

<div class="table-wrap">
  <table>
    <thead><tr><th>ID</th><th>Name</th><th>Price</th><th>Max</th><th>Description</th><th></th></tr></thead>
    <tbody>
    {% for iid, it in items.items() %}
    <tr>
      <td><code>{{ iid }}</code></td>
      <td><strong>{{ it.name }}</strong></td>
      <td class="mono">{{ it.price }} ◆</td>
      <td class="mono">{{ it.max_per_user }}</td>
      <td class="muted">{{ it.description }}</td>
      <td style="text-align:right">
        <form method="post" action="{{ url_for('shop_delete', item_id=iid) }}" style="display:inline">
          <input type="hidden" name="csrf" value="{{ csrf }}">
          <button class="btn btn-danger btn-sm">Delete</button>
        </form>
      </td>
    </tr>
    {% else %}
    <tr><td colspan="6" class="empty">No items.</td></tr>
    {% endfor %}
    </tbody>
  </table>
</div>
"""


# ===========================================================================
# Audit
# ===========================================================================
AUDIT_TEMPLATE = """
<div class="page-head">
  <div>
    <div class="crumb">System</div>
    <div class="page-title">Audit log</div>
    <div class="page-sub">Every admin action, timestamped</div>
  </div>
</div>

<div class="table-wrap">
  <table>
    <thead><tr><th>When</th><th>Admin</th><th>Action</th><th>Target</th><th>IP</th></tr></thead>
    <tbody>
    {% for e in entries %}
    <tr>
      <td class="mono muted">{{ e.created_at[:19] }}</td>
      <td class="mono">#{{ e.admin_id }}</td>
      <td><span class="badge">{{ e.action }}</span></td>
      <td class="muted">{{ e.target or '—' }}</td>
      <td class="mono muted">{{ e.ip or '—' }}</td>
    </tr>
    {% else %}
    <tr><td colspan="5" class="empty">No audit entries.</td></tr>
    {% endfor %}
    </tbody>
  </table>
</div>
"""


# ===========================================================================
# Backups
# ===========================================================================
BACKUPS_TEMPLATE = """
<div class="page-head">
  <div>
    <div class="crumb">System</div>
    <div class="page-title">Backups</div>
    <div class="page-sub">Snapshot and restore all bot data</div>
  </div>
</div>

<div class="grid-2">
  <div class="card">
    <h2>Create backup</h2>
    <p class="muted mt-2" style="margin-bottom:16px">
      Zips all JSON data and sends it to the owner's Telegram DM.
    </p>
    <form method="post" action="{{ url_for('backup_now') }}">
      <input type="hidden" name="csrf" value="{{ csrf }}">
      <button class="btn btn-dark">Create backup now</button>
    </form>
  </div>

  <div class="card">
    <h2>Restore from ZIP</h2>
    <p class="muted mt-2" style="margin-bottom:16px">
      Upload a previously created backup archive.
    </p>
    <form method="post" action="{{ url_for('restore_backup') }}" enctype="multipart/form-data">
      <input type="hidden" name="csrf" value="{{ csrf }}">
      <input type="file" name="file" accept=".zip">
      <div class="mt-3">
        <button class="btn btn-danger">Restore data</button>
      </div>
    </form>
  </div>
</div>

<div class="table-wrap">
  <table>
    <thead><tr><th>File</th><th>Size</th><th>Modified</th><th></th></tr></thead>
    <tbody>
    {% for b in backups %}
    <tr>
      <td class="mono">{{ b.name }}</td>
      <td class="mono">{{ b.size_kb }} KB</td>
      <td class="muted mono">{{ b.modified }}</td>
      <td style="text-align:right">
        <a class="btn btn-sm" href="{{ url_for('download_backup', name=b.name) }}">Download</a>
      </td>
    </tr>
    {% else %}
    <tr><td colspan="4" class="empty">No backups yet.</td></tr>
    {% endfor %}
    </tbody>
  </table>
</div>
"""


# ===========================================================================
# Broadcast
# ===========================================================================
BROADCAST_TEMPLATE = """
<div class="page-head">
  <div>
    <div class="crumb">System</div>
    <div class="page-title">Broadcast</div>
    <div class="page-sub">Send a message to all non-banned users</div>
  </div>
</div>

<div class="card">
  <form method="post" action="{{ url_for('broadcast_send') }}">
    <input type="hidden" name="csrf" value="{{ csrf }}">
    <label>Message</label>
    <textarea name="message" rows="8" placeholder="Hello everyone..."></textarea>
    <div class="mt-3">
      <button class="btn btn-dark">Send to all users</button>
    </div>
  </form>
</div>
"""


# ===========================================================================
# Settings
# ===========================================================================
SETTINGS_TEMPLATE = """
<div class="page-head">
  <div>
    <div class="crumb">System</div>
    <div class="page-title">Settings</div>
    <div class="page-sub">Runtime economy and bot configuration</div>
  </div>
</div>

<div class="card">
  <h2>Economy</h2>
  <form method="post" action="{{ url_for('settings_save') }}">
    <input type="hidden" name="csrf" value="{{ csrf }}">
    <div class="field-row">
      <div><label>Signup bonus</label><input type="number" name="signup_bonus" value="{{ cfg.signup_bonus }}"></div>
      <div><label>Cost per clock update</label><input type="number" name="diamond_cost_clock" value="{{ cfg.diamond_cost_clock }}"></div>
      <div><label>Cost per job</label><input type="number" name="diamond_cost_job" value="{{ cfg.diamond_cost_job }}"></div>
      <div><label>Referral bonus</label><input type="number" name="referral_bonus" value="{{ cfg.referral_bonus }}"></div>
    </div>
    <div class="field-row mt-3">
      <div><label>Timezone</label><input name="timezone" value="{{ cfg.timezone }}"></div>
      <div><label>Base name</label><input name="base_name" value="{{ cfg.base_name }}"></div>
      <div style="flex:0 0 140px"><label>Interval (min)</label><input type="number" name="interval" value="{{ cfg.interval }}"></div>
    </div>
    <div class="mt-4">
      <button class="btn btn-dark">Save settings</button>
    </div>
  </form>
</div>

<div class="card">
  <h2>Security</h2>
  <p class="muted mt-2" style="margin-bottom:16px">
    Two-factor authentication protects your admin login with a TOTP code.
  </p>
  <a class="btn btn-dark" href="{{ url_for('totp_page') }}">Configure 2FA</a>
</div>

<div class="card">
  <h2>API access</h2>
  <p class="muted mt-2">
    Set the <code>API_TOKEN</code> environment variable to protect <code>/api/*</code> endpoints.
    Pass it as <code>Authorization: Bearer &lt;token&gt;</code>.
  </p>
</div>
"""


# ===========================================================================
# TOTP
# ===========================================================================
TOTP_TEMPLATE = """
<div class="page-head">
  <div>
    <div class="crumb">Security</div>
    <div class="page-title">Two-Factor Authentication</div>
  </div>
  <a class="btn" href="{{ url_for('settings_page') }}">← Back</a>
</div>

<div class="card">
  {% if enabled %}
    <span class="badge green" style="margin-bottom:14px">Enabled</span>
    <p class="muted" style="margin-bottom:16px">
      Your account is protected with TOTP. You will be prompted for a code on every login.
    </p>
    <form method="post" action="{{ url_for('totp_disable') }}">
      <input type="hidden" name="csrf" value="{{ csrf }}">
      <button class="btn btn-danger">Disable 2FA</button>
    </form>
  {% else %}
    <span class="badge amber" style="margin-bottom:14px">Disabled</span>
    {% if qr_b64 %}
      <p class="muted">Scan this QR code with your authenticator app:</p>
      <img src="data:image/png;base64,{{ qr_b64 }}" alt="QR"
           style="background:#fff;padding:12px;border-radius:12px;margin:16px 0;border:1px solid var(--border)">
      <p class="muted">Or enter this secret manually:</p>
      <p style="margin:8px 0 20px"><code>{{ pending_secret }}</code></p>
      <form method="post" action="{{ url_for('totp_enable') }}">
        <input type="hidden" name="csrf" value="{{ csrf }}">
        <button class="btn btn-success">Enable 2FA</button>
      </form>
    {% else %}
      <p class="muted">pyotp or qrcode not installed. Add them to requirements.</p>
    {% endif %}
  {% endif %}
</div>
"""


# ===========================================================================
# Proxy
# ===========================================================================
PROXY_TEMPLATE = """
<div class="page-head">
  <div>
    <div class="crumb">System</div>
    <div class="page-title">Proxy configs</div>
    <div class="page-sub">Manage VLESS/Xray configs, test latency and egress IP</div>
  </div>
</div>

<div class="card">
  <h2>Active config: <code>{{ active }}</code></h2>
  <button class="btn btn-dark" id="test-active-btn">Test active config</button>
  <pre id="test-active-result" style="margin-top:14px;white-space:pre-wrap;
       background:#0a0a0a;color:#d4d4d4;padding:14px;border-radius:10px;font-size:12px;
       display:none;font-family:'JetBrains Mono',monospace;line-height:1.6"></pre>
</div>

<div class="table-wrap">
  <table>
    <thead><tr><th>Key</th><th>Name</th><th>URL</th><th>Status</th><th style="text-align:right">Actions</th></tr></thead>
    <tbody>
    {% for c in configs %}
    <tr>
      <td><code>{{ c.key }}</code></td>
      <td>{{ c.name }}</td>
      <td class="mono muted" style="font-size:11.5px">{{ c.url_short }}</td>
      <td>{% if c.active %}<span class="badge solid">active</span>{% else %}<span class="badge">idle</span>{% endif %}</td>
      <td style="text-align:right">
        <button class="btn btn-sm" onclick="testConfig('{{ c.key }}')">Test</button>
        {% if not c.active %}
        <form method="post" action="{{ url_for('proxy_activate', key=c.key) }}" style="display:inline">
          <input type="hidden" name="csrf" value="{{ csrf }}">
          <button class="btn btn-sm btn-dark">Activate</button>
        </form>
        {% endif %}
      </td>
    </tr>
    {% else %}
    <tr><td colspan="5" class="empty">No configs.</td></tr>
    {% endfor %}
    </tbody>
  </table>
</div>

<div id="test-result" style="margin-top:20px"></div>

<div class="card" style="margin-top:20px">
  <h2>Add config</h2>
  <form method="post" action="{{ url_for('proxy_add') }}">
    <input type="hidden" name="csrf" value="{{ csrf }}">
    <div class="field-row">
      <div><label>Key</label><input name="key" placeholder="us-1"></div>
      <div><label>Name</label><input name="name" placeholder="US Server 1"></div>
    </div>
    <label>VLESS URL</label>
    <input name="url" placeholder="vless://...">
    <label>Outbound JSON</label>
    <textarea name="outbound" rows="10" style="font-family:'JetBrains Mono',monospace;font-size:12px"
placeholder='{
  "protocol": "vless",
  "settings": { "vnext": [ { "address": "...", "port": 443, "users": [ { "id": "...", "encryption": "none", "flow": "" } ] } ] },
  "streamSettings": { "network": "ws", "security": "tls", "tlsSettings": { "serverName": "...", "fingerprint": "chrome", "alpn": ["http/1.1"] }, "wsSettings": { "path": "/...", "headers": { "Host": "..." } } }
}'></textarea>
    <div class="mt-3">
      <button class="btn btn-dark">Add config</button>
    </div>
  </form>
</div>

<script>
async function testConfig(key) {
  const el = document.getElementById('test-result');
  el.innerHTML = '<div class="card"><p class="muted">Testing <code>' + key + '</code>…</p></div>';
  const csrf = '{{ csrf }}';
  try {
    const r = await fetch('/admin/proxy/test/' + key, {
      method: 'POST',
      headers: {'X-CSRF-Token': csrf}
    });
    const data = await r.json();
    el.innerHTML = '<div class="card"><h2>Result: ' + key + '</h2>' +
      '<pre style="white-space:pre-wrap;color:#d4d4d4;font-size:12px;background:#0a0a0a;padding:14px;border-radius:10px;font-family:JetBrains Mono,monospace;line-height:1.6">' +
      JSON.stringify(data, null, 2) + '</pre></div>';
  } catch (e) {
    el.innerHTML = '<div class="flash err">Error: ' + e + '</div>';
  }
}
document.getElementById('test-active-btn').addEventListener('click', async (ev) => {
  ev.preventDefault();
  const el = document.getElementById('test-active-result');
  el.style.display = 'block';
  el.textContent = 'Testing active config…';
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

    # ---------- Login ----------
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

    # ---------- Dashboard ----------
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

    # ---------- Users ----------
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
                "first_name": u.get("first_name", ""),
                "diamonds": int(u.get("diamonds", 0) or 0),
                "plan": effective_plan(uid),
                "days": days_left(uid),
                "referral_code": u.get("referral_code", "—"),
                "banned": bool(u.get("banned", False)),
                "created_at": u.get("created_at", ""),
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

    # ---------- Requests ----------
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

    # ---------- Transactions ----------
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

    # ---------- Shop ----------
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

    # ---------- Audit ----------
    @app.route("/admin/audit")
    @_login_required
    def audit_page():
        body = render_template_string(AUDIT_TEMPLATE, entries=audit_all(500))
        return render_template_string(BASE_TEMPLATE, title="Audit", body=body, page="audit")

    # ---------- Backups ----------
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

    # ---------- Broadcast ----------
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

    # ---------- Settings ----------
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

    # ---------- TOTP ----------
    @app.route("/admin/settings/2fa", methods=["GET"])
    @_login_required
    def totp_page():
        from .admin_totp import HAS_PYOTP, get_secret, is_enabled, provision
        import base64 as _b64

        enabled = is_enabled()
        secret = get_secret() if enabled else None
        qr_b64 = ""
        if not enabled and HAS_PYOTP:
            s, uri, png = provision()
            session["pending_totp_secret"] = s
            if png:
                qr_b64 = _b64.b64encode(png).decode()

        body = render_template_string(
            TOTP_TEMPLATE,
            enabled=enabled,
            secret=secret,
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

    # ---------- Proxy ----------
    @app.route("/admin/proxy")
    @_login_required
    def proxy_page():
        from .proxy_test import list_configs
        data = list_configs()
        configs = data.get("configs", {})
        active = data.get("active", "default")
        rows = []
        for key, cfg in configs.items():
            url = cfg.get("url") or ""
            rows.append({
                "key": key,
                "name": cfg.get("name", key),
                "url_short": (url[:60] + "...") if len(url) > 60 else url,
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


def _get_runtime_safe():
    try:
        from app import _get_runtime
        return _get_runtime()
    except Exception:
        return None