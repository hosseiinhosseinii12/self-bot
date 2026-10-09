"""Flask admin panel — modern dark glassmorphism theme."""
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
# HTML — Base layout + CSS
# ===========================================================================
BASE_TEMPLATE = """
<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>{{ title }} · SELF BOT Admin</title>
<meta name="viewport" content="width=device-width, initial-scale=1">
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&family=JetBrains+Mono:wght@400;600&display=swap" rel="stylesheet">
<script src="https://cdn.jsdelivr.net/npm/chart.js@4"></script>
<style>
:root {
  --bg-0: #070912;
  --bg-1: #0c101e;
  --bg-2: #121828;
  --bg-3: #1a2135;
  --stroke: rgba(255,255,255,.07);
  --stroke-strong: rgba(255,255,255,.14);
  --text: #eef2ff;
  --text-dim: #8b96b8;
  --text-mute: #5a6486;
  --indigo: #6366f1;
  --violet: #a855f7;
  --cyan: #22d3ee;
  --emerald: #10b981;
  --amber: #f59e0b;
  --rose: #f43f5e;
  --grad: linear-gradient(135deg, #6366f1 0%, #a855f7 50%, #ec4899 100%);
  --grad-soft: linear-gradient(135deg, rgba(99,102,241,.15) 0%, rgba(168,85,247,.08) 100%);
}
* { box-sizing: border-box; margin: 0; padding: 0; }
html, body { height: 100%; }
body {
  font-family: 'Inter', -apple-system, BlinkMacSystemFont, sans-serif;
  background: var(--bg-0);
  color: var(--text);
  line-height: 1.55;
  font-feature-settings: "cv11", "ss01";
  -webkit-font-smoothing: antialiased;
  min-height: 100vh;
  background-image:
    radial-gradient(1200px 800px at 10% -10%, rgba(99,102,241,.18), transparent 60%),
    radial-gradient(1000px 700px at 100% 0%, rgba(168,85,247,.12), transparent 55%),
    radial-gradient(900px 600px at 50% 100%, rgba(34,211,238,.06), transparent 60%);
  background-attachment: fixed;
}
a { color: var(--indigo); text-decoration: none; transition: color .15s; }
a:hover { color: var(--cyan); }

/* ---------- Layout ---------- */
.layout { display: grid; grid-template-columns: 260px 1fr; min-height: 100vh; }
@media (max-width: 900px) { .layout { grid-template-columns: 1fr; } .side { display: none; } }

/* ---------- Sidebar ---------- */
.side {
  background: rgba(12,16,30,.72);
  backdrop-filter: blur(20px);
  -webkit-backdrop-filter: blur(20px);
  border-right: 1px solid var(--stroke);
  padding: 24px 16px;
  position: sticky; top: 0; height: 100vh;
  display: flex; flex-direction: column;
}
.brand {
  display: flex; align-items: center; gap: 10px;
  padding: 8px 12px 24px;
  font-weight: 800; font-size: 18px;
  letter-spacing: -.02em;
}
.brand-mark {
  width: 34px; height: 34px; border-radius: 10px;
  background: var(--grad);
  display: grid; place-items: center;
  font-size: 16px;
  box-shadow: 0 8px 24px rgba(99,102,241,.4);
}
.brand-text {
  background: linear-gradient(135deg, #fff 0%, #a5b4fc 100%);
  -webkit-background-clip: text; background-clip: text;
  -webkit-text-fill-color: transparent;
}
.nav-section { color: var(--text-mute); font-size: 10.5px; text-transform: uppercase;
  letter-spacing: .14em; padding: 18px 12px 8px; font-weight: 700; }
.nav-item {
  display: flex; align-items: center; gap: 12px;
  padding: 10px 12px; border-radius: 10px;
  color: var(--text-dim); font-size: 13.5px; font-weight: 500;
  transition: all .15s;
}
.nav-item:hover { background: rgba(255,255,255,.04); color: var(--text); }
.nav-item.active {
  background: var(--grad-soft);
  color: var(--text);
  border: 1px solid rgba(99,102,241,.25);
}
.nav-item .ico { width: 18px; text-align: center; font-size: 14px; }
.side-footer { margin-top: auto; padding-top: 16px; border-top: 1px solid var(--stroke); }
.user-chip {
  display: flex; align-items: center; gap: 10px;
  padding: 10px 12px; border-radius: 10px;
  background: rgba(255,255,255,.03);
  font-size: 12px; color: var(--text-dim);
}

/* ---------- Main ---------- */
.main { padding: 32px 40px 60px; max-width: 1440px; }
.page-head { display: flex; align-items: flex-end; justify-content: space-between;
  margin-bottom: 28px; gap: 20px; flex-wrap: wrap; }
.page-title { font-size: 26px; font-weight: 800; letter-spacing: -.03em; }
.page-sub { color: var(--text-dim); font-size: 14px; margin-top: 4px; }
.crumb { color: var(--text-mute); font-size: 12px; text-transform: uppercase;
  letter-spacing: .12em; font-weight: 700; margin-bottom: 6px; }

/* ---------- Cards / stats ---------- */
.stats { display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
  gap: 16px; margin-bottom: 24px; }
.stat {
  position: relative; overflow: hidden;
  background: linear-gradient(180deg, rgba(18,24,40,.85), rgba(12,16,30,.85));
  border: 1px solid var(--stroke);
  border-radius: 16px;
  padding: 20px;
  transition: transform .2s, border-color .2s;
}
.stat:hover { transform: translateY(-2px); border-color: var(--stroke-strong); }
.stat::before {
  content: ''; position: absolute; inset: 0;
  background: radial-gradient(400px 100px at 100% 0%, rgba(99,102,241,.14), transparent 60%);
  pointer-events: none;
}
.stat .k { display: flex; align-items: center; gap: 8px;
  color: var(--text-dim); font-size: 11.5px; font-weight: 600;
  letter-spacing: .08em; text-transform: uppercase; }
.stat .v { font-size: 30px; font-weight: 800; margin-top: 12px;
  letter-spacing: -.03em; }
.stat.indigo .v { color: #c7d2fe; }
.stat.violet .v { color: #e9d5ff; }
.stat.emerald .v { color: #a7f3d0; }
.stat.amber .v { color: #fde68a; }
.stat.rose .v { color: #fecdd3; }
.stat .v .unit { font-size: 14px; color: var(--text-mute); font-weight: 600; margin-left: 4px; }

.card {
  background: rgba(18,24,40,.65);
  border: 1px solid var(--stroke);
  border-radius: 18px;
  padding: 22px;
  backdrop-filter: blur(12px);
  margin-bottom: 20px;
}
.card-head { display: flex; align-items: center; justify-content: space-between;
  margin-bottom: 16px; gap: 12px; flex-wrap: wrap; }
.card h2 { font-size: 15px; font-weight: 700; letter-spacing: -.01em; }
.card h2 .sub { color: var(--text-mute); font-weight: 500; font-size: 12.5px; margin-left: 8px; }
.grid-2 { display: grid; grid-template-columns: 1fr 1fr; gap: 20px; margin-bottom: 20px; }
@media (max-width: 1000px) { .grid-2 { grid-template-columns: 1fr; } }

/* ---------- Tables ---------- */
.table-wrap { overflow-x: auto; border-radius: 14px; border: 1px solid var(--stroke); }
table { width: 100%; border-collapse: collapse; font-size: 13.5px; }
thead th {
  background: rgba(255,255,255,.025);
  color: var(--text-mute); text-align: left;
  font-size: 11px; text-transform: uppercase; letter-spacing: .1em;
  font-weight: 700; padding: 12px 16px;
  border-bottom: 1px solid var(--stroke);
}
tbody td { padding: 12px 16px; border-bottom: 1px solid rgba(255,255,255,.03); }
tbody tr:last-child td { border-bottom: none; }
tbody tr:hover { background: rgba(99,102,241,.04); }
.mono { font-family: 'JetBrains Mono', ui-monospace, monospace; font-size: 12px; }
.muted { color: var(--text-dim); font-size: 13px; }
.tiny { font-size: 11.5px; }

/* ---------- Badges ---------- */
.badge {
  display: inline-flex; align-items: center; gap: 5px;
  padding: 3px 10px; border-radius: 999px;
  font-size: 11px; font-weight: 700; letter-spacing: .03em;
  border: 1px solid transparent;
}
.badge.ok      { background: rgba(16,185,129,.12); color: #6ee7b7; border-color: rgba(16,185,129,.25); }
.badge.pending { background: rgba(245,158,11,.12); color: #fcd34d; border-color: rgba(245,158,11,.25); }
.badge.bad     { background: rgba(244,63,94,.12); color: #fda4af; border-color: rgba(244,63,94,.25); }
.badge.info    { background: rgba(99,102,241,.12); color: #a5b4fc; border-color: rgba(99,102,241,.25); }
.badge.vip     { background: rgba(168,85,247,.14); color: #d8b4fe; border-color: rgba(168,85,247,.3); }
.badge.free    { background: rgba(148,163,184,.1); color: #cbd5e1; border-color: rgba(148,163,184,.2); }

/* ---------- Buttons ---------- */
.btn {
  display: inline-flex; align-items: center; justify-content: center;
  gap: 6px;
  padding: 9px 16px; border-radius: 10px;
  border: 1px solid var(--stroke-strong);
  background: rgba(255,255,255,.04);
  color: var(--text); font-size: 13px; font-weight: 600;
  cursor: pointer; transition: all .15s;
  font-family: inherit;
}
.btn:hover { background: rgba(255,255,255,.08); border-color: rgba(255,255,255,.25); }
.btn.primary {
  background: var(--grad); border-color: transparent;
  box-shadow: 0 8px 24px rgba(99,102,241,.35);
}
.btn.primary:hover { box-shadow: 0 10px 30px rgba(99,102,241,.5); transform: translateY(-1px); }
.btn.success { background: linear-gradient(135deg, #10b981, #059669); border-color: transparent; }
.btn.danger  { background: linear-gradient(135deg, #f43f5e, #dc2626); border-color: transparent; }
.btn.ghost   { background: transparent; }
.btn.sm      { padding: 6px 12px; font-size: 12px; }
.btn.icon    { width: 32px; height: 32px; padding: 0; border-radius: 8px; }

/* ---------- Inputs ---------- */
input, select, textarea {
  width: 100%;
  background: rgba(255,255,255,.03);
  border: 1px solid var(--stroke);
  border-radius: 10px;
  padding: 10px 14px;
  color: var(--text);
  font-size: 13.5px;
  font-family: inherit;
  transition: border-color .15s, background .15s;
}
input:focus, select:focus, textarea:focus {
  outline: none; border-color: rgba(99,102,241,.6);
  background: rgba(99,102,241,.05);
  box-shadow: 0 0 0 3px rgba(99,102,241,.12);
}
label { display: block; font-size: 12px; color: var(--text-dim);
  margin: 12px 0 6px; font-weight: 600; letter-spacing: .02em; }
.field-row { display: flex; gap: 12px; flex-wrap: wrap; align-items: flex-end; }
.field-row > * { flex: 1; min-width: 140px; }

/* ---------- Flash ---------- */
.flash {
  padding: 12px 18px; border-radius: 12px; margin-bottom: 18px;
  font-size: 13.5px; font-weight: 500;
  background: rgba(16,185,129,.1);
  border: 1px solid rgba(16,185,129,.3);
  color: #a7f3d0;
  display: flex; align-items: center; gap: 10px;
}
.flash.err { background: rgba(244,63,94,.1); border-color: rgba(244,63,94,.3); color: #fda4af; }

/* ---------- Login ---------- */
.login-page {
  min-height: 100vh; display: grid; place-items: center; padding: 40px 20px;
}
.login-card {
  width: 100%; max-width: 400px;
  padding: 40px 36px;
  background: rgba(18,24,40,.7);
  backdrop-filter: blur(24px);
  border: 1px solid var(--stroke);
  border-radius: 24px;
  box-shadow: 0 40px 80px rgba(0,0,0,.5);
}
.login-logo {
  width: 56px; height: 56px; margin: 0 auto 24px;
  border-radius: 16px; background: var(--grad);
  display: grid; place-items: center;
  font-size: 26px;
  box-shadow: 0 16px 40px rgba(99,102,241,.45);
}
.login-card h1 { font-size: 22px; text-align: center; margin-bottom: 6px;
  letter-spacing: -.02em; }
.login-card p.sub { text-align: center; color: var(--text-dim);
  font-size: 13.5px; margin-bottom: 28px; }

/* ---------- Charts ---------- */
.chart-box { height: 220px; position: relative; }

/* ---------- Misc ---------- */
.empty { text-align: center; padding: 40px 20px; color: var(--text-mute); font-size: 13.5px; }
.mt-3 { margin-top: 16px; }
.mt-4 { margin-top: 24px; }
.flex { display: flex; align-items: center; gap: 10px; }
.flex-wrap { flex-wrap: wrap; }
.spacer { flex: 1; }
code { font-family: 'JetBrains Mono', monospace; font-size: 12.5px;
  background: rgba(255,255,255,.05); padding: 2px 8px; border-radius: 6px; }
</style>
</head>
<body>

{% if session.get('admin') %}
<div class="layout">
  <!-- Sidebar -->
  <aside class="side">
    <div class="brand">
      <div class="brand-mark">◆</div>
      <div class="brand-text">SELF BOT</div>
    </div>
    <div class="nav-section">Main</div>
    <a href="{{ url_for('dashboard') }}" class="nav-item {{ 'active' if page=='dashboard' else '' }}">
      <span class="ico">▦</span> Dashboard
    </a>
    <a href="{{ url_for('users_page') }}" class="nav-item {{ 'active' if page=='users' else '' }}">
      <span class="ico">◉</span> Users
    </a>
    <a href="{{ url_for('requests_page') }}" class="nav-item {{ 'active' if page=='requests' else '' }}">
      <span class="ico">✉</span> Requests
    </a>
    <a href="{{ url_for('transactions_page') }}" class="nav-item {{ 'active' if page=='tx' else '' }}">
      <span class="ico">⇄</span> Transactions
    </a>
    <div class="nav-section">System</div>
    <a href="{{ url_for('shop_page') }}" class="nav-item {{ 'active' if page=='shop' else '' }}">
      <span class="ico">◈</span> Shop
    </a>
    <a href="{{ url_for('proxy_page') }}" class="nav-item {{ 'active' if page=='proxy' else '' }}">
      <span class="ico">⇆</span> Proxy
    </a>
    <a href="{{ url_for('audit_page') }}" class="nav-item {{ 'active' if page=='audit' else '' }}">
      <span class="ico">◷</span> Audit
    </a>
    <a href="{{ url_for('backups_page') }}" class="nav-item {{ 'active' if page=='backups' else '' }}">
      <span class="ico">◱</span> Backups
    </a>
    <a href="{{ url_for('broadcast_page') }}" class="nav-item {{ 'active' if page=='broadcast' else '' }}">
      <span class="ico">◬</span> Broadcast
    </a>
    <a href="{{ url_for('settings_page') }}" class="nav-item {{ 'active' if page=='settings' else '' }}">
      <span class="ico">⚙</span> Settings
    </a>
    <div class="side-footer">
      <a href="{{ url_for('logout') }}" class="nav-item">
        <span class="ico">⇤</span> Sign out
      </a>
    </div>
  </aside>

  <!-- Main -->
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


# ===========================================================================
# Login template
# ===========================================================================
LOGIN_TEMPLATE = """
<div class="login-page">
  <form class="login-card" method="post" action="{{ url_for('login_post') }}">
    <div class="login-logo">◆</div>
    <h1>Welcome back</h1>
    <p class="sub">Sign in to SELF BOT admin</p>

    <input type="hidden" name="csrf" value="{{ csrf }}">

    <label>Password</label>
    <input type="password" name="password" placeholder="••••••••••••" autofocus required>

    {% if totp_enabled %}
    <label>2FA code</label>
    <input type="text" name="totp" inputmode="numeric" pattern="[0-9]*" maxlength="6" placeholder="123456">
    {% endif %}

    <div style="height:18px"></div>
    <button type="submit" class="btn primary" style="width:100%">Sign in →</button>
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
  <div class="stat indigo">
    <div class="k">◆ Total users</div>
    <div class="v">{{ users_total }}</div>
  </div>
  <div class="stat emerald">
    <div class="k">◉ Active 24h</div>
    <div class="v">{{ users_active }}</div>
  </div>
  <div class="stat cyan" style="--x:0">
    <div class="k">✚ New 7 days</div>
    <div class="v">{{ users_new }}</div>
  </div>
  <div class="stat violet">
    <div class="k">◆ Diamonds in circulation</div>
    <div class="v">{{ diamonds }}</div>
  </div>
  <div class="stat emerald">
    <div class="k">↑ Granted (all time)</div>
    <div class="v">{{ granted }}</div>
  </div>
  <div class="stat rose">
    <div class="k">↓ Spent (all time)</div>
    <div class="v">{{ spent }}</div>
  </div>
</div>

<div class="grid-2">
  <div class="card">
    <div class="card-head">
      <h2>◆ Diamond consumption <span class="sub">last 24h</span></h2>
    </div>
    <div class="chart-box"><canvas id="diamondChart"></canvas></div>
  </div>
  <div class="card">
    <div class="card-head">
      <h2>◆ Subscription distribution</h2>
    </div>
    <div class="chart-box"><canvas id="subChart"></canvas></div>
  </div>
</div>

<div class="grid-2">
  <div class="card">
    <div class="card-head">
      <h2>◆ Request approval rate <span class="sub">last 14 days</span></h2>
    </div>
    <div class="chart-box"><canvas id="approvalChart"></canvas></div>
  </div>
  <div class="card">
    <div class="card-head">
      <h2>◆ Top spenders</h2>
    </div>
    <div class="table-wrap">
      <table>
        <thead><tr><th>#</th><th>User</th><th style="text-align:right">Spent</th></tr></thead>
        <tbody>
        {% for s in top_spenders %}
        <tr>
          <td class="mono">{{ loop.index }}</td>
          <td><a href="{{ url_for('user_detail', uid=s.user_id) }}">#{{ s.user_id }}</a></td>
          <td style="text-align:right"><span class="badge info">{{ s.spent }} ◆</span></td>
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
Chart.defaults.color = '#8b96b8';
Chart.defaults.font.family = 'Inter, sans-serif';
Chart.defaults.font.size = 11;

const diamondData = {{ diamond_series|safe }};
new Chart(document.getElementById('diamondChart'), {
  type: 'line',
  data: {
    labels: diamondData.map(d => d.hour.slice(11, 16)),
    datasets: [{
      label: 'Spent',
      data: diamondData.map(d => d.spent),
      borderColor: '#818cf8',
      backgroundColor: (ctx) => {
        const g = ctx.chart.ctx.createLinearGradient(0, 0, 0, 220);
        g.addColorStop(0, 'rgba(129,140,248,.35)');
        g.addColorStop(1, 'rgba(129,140,248,0)');
        return g;
      },
      fill: true, tension: .4, pointRadius: 0, pointHoverRadius: 5,
      borderWidth: 2,
    }]
  },
  options: {
    responsive: true, maintainAspectRatio: false,
    plugins: { legend: { display: false } },
    scales: {
      x: { grid: { color: 'rgba(255,255,255,.04)' }, border: { display: false } },
      y: { grid: { color: 'rgba(255,255,255,.04)' }, border: { display: false }, beginAtZero: true }
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
      backgroundColor: ['#64748b','#6366f1','#a855f7','#f59e0b'],
      borderWidth: 0,
      hoverOffset: 8,
    }]
  },
  options: {
    responsive: true, maintainAspectRatio: false,
    cutout: '70%',
    plugins: {
      legend: { position: 'bottom', labels: { padding: 16, boxWidth: 12, boxHeight: 12 } }
    }
  }
});

const approvalData = {{ approval_series|safe }};
new Chart(document.getElementById('approvalChart'), {
  type: 'bar',
  data: {
    labels: approvalData.map(d => d.date.slice(5)),
    datasets: [
      { label: 'Approved', data: approvalData.map(d => d.approved),
        backgroundColor: '#10b981', borderRadius: 6, barThickness: 12 },
      { label: 'Rejected', data: approvalData.map(d => d.rejected),
        backgroundColor: '#f43f5e', borderRadius: 6, barThickness: 12 },
    ]
  },
  options: {
    responsive: true, maintainAspectRatio: false,
    plugins: { legend: { position: 'bottom', labels: { padding: 16, boxWidth: 12, boxHeight: 12 } } },
    scales: {
      x: { stacked: true, grid: { display: false }, border: { display: false } },
      y: { stacked: true, grid: { color: 'rgba(255,255,255,.04)' }, border: { display: false } }
    }
  }
});
</script>
"""


# ===========================================================================
# Users list
# ===========================================================================
USERS_TEMPLATE = """
<div class="page-head">
  <div>
    <div class="crumb">Manage</div>
    <div class="page-title">Users</div>
    <div class="page-sub">{{ users|length }} registered accounts</div>
  </div>
</div>

<div class="card">
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
        <td><span class="badge {{ u.plan }}">{{ u.plan }}</span></td>
        <td class="mono">{{ u.days }}</td>
        <td><strong>{{ u.diamonds }}</strong> <span class="muted">◆</span></td>
        <td>{% if u.banned %}<span class="badge bad">banned</span>{% else %}<span class="badge ok">active</span>{% endif %}</td>
        <td style="text-align:right">
          <a class="btn sm" href="{{ url_for('user_detail', uid=u.id) }}">Open →</a>
        </td>
      </tr>
      {% else %}
      <tr><td colspan="8" class="empty">No users yet.</td></tr>
      {% endfor %}
      </tbody>
    </table>
  </div>
</div>
"""


# ===========================================================================
# User detail
# ===========================================================================
USER_DETAIL_TEMPLATE = """
<div class="page-head">
  <div>
    <div class="crumb">User</div>
    <div class="page-title">#{{ u.id }}</div>
    <div class="page-sub">{{ u.first_name or 'Unnamed' }} · joined {{ u.created_at[:10] if u.created_at else '—' }}</div>
  </div>
  <a class="btn ghost" href="{{ url_for('users_page') }}">← Back to users</a>
</div>

<div class="stats">
  <div class="stat violet">
    <div class="k">◆ Diamonds</div>
    <div class="v">{{ u.diamonds }}</div>
  </div>
  <div class="stat indigo">
    <div class="k">◆ Plan</div>
    <div class="v" style="font-size:22px">{{ u.plan }}</div>
  </div>
  <div class="stat emerald">
    <div class="k">◷ Days left</div>
    <div class="v">{{ u.days }}</div>
  </div>
  <div class="stat amber">
    <div class="k">◆ Referral code</div>
    <div class="v mono" style="font-size:16px">{{ u.referral_code }}</div>
  </div>
</div>

<div class="grid-2">
  <div class="card">
    <div class="card-head"><h2>Grant diamonds</h2></div>
    <form method="post" action="{{ url_for('grant_diamonds', uid=u.id) }}">
      <input type="hidden" name="csrf" value="{{ csrf }}">
      <div class="field-row">
        <div style="flex:1">
          <label>Amount (negative to subtract)</label>
          <input type="number" name="amount" value="10">
        </div>
        <div style="flex:1">
          <label>Reason</label>
          <input type="text" name="reason" value="admin_grant">
        </div>
        <button class="btn primary">Apply</button>
      </div>
    </form>
  </div>

  <div class="card">
    <div class="card-head"><h2>Set subscription</h2></div>
    <form method="post" action="{{ url_for('set_subscription', uid=u.id) }}">
      <input type="hidden" name="csrf" value="{{ csrf }}">
      <div class="field-row">
        <div style="flex:1">
          <label>Plan</label>
          <select name="plan">
            {% for p in plans %}<option value="{{ p }}" {{ 'selected' if p==u.plan else '' }}>{{ p }}</option>{% endfor %}
          </select>
        </div>
        <div style="flex:0 0 110px">
          <label>Days</label>
          <input type="number" name="days" value="30">
        </div>
        <div style="flex:0 0 130px">
          <label>Auto-renew</label>
          <select name="auto_renew"><option value="0">No</option><option value="1">Yes</option></select>
        </div>
        <button class="btn success">Set</button>
      </div>
    </form>
    <div style="margin-top:12px">
      <form method="post" action="{{ url_for('cancel_subscription', uid=u.id) }}" style="display:inline">
        <input type="hidden" name="csrf" value="{{ csrf }}">
        <button class="btn ghost sm">Cancel subscription</button>
      </form>
    </div>
  </div>
</div>

<div class="card">
  <div class="card-head">
    <h2>Moderation</h2>
  </div>
  <form method="post" action="{{ url_for('toggle_ban', uid=u.id) }}" style="display:inline">
    <input type="hidden" name="csrf" value="{{ csrf }}">
    <button class="{{ 'btn success' if u.banned else 'btn danger' }}">
      {{ 'Unban user' if u.banned else 'Ban user' }}
    </button>
  </form>
  <form method="post" action="{{ url_for('delete_user_route', uid=u.id) }}" style="display:inline"
        onsubmit="return confirm('Delete user? This cannot be undone.');">
    <input type="hidden" name="csrf" value="{{ csrf }}">
    <button class="btn danger">Delete permanently</button>
  </form>
</div>

<div class="card">
  <div class="card-head"><h2>Recent transactions</h2></div>
  <div class="table-wrap">
    <table>
      <thead><tr><th>When</th><th>Kind</th><th>Amount</th><th>Reason</th><th style="text-align:right">Balance</th></tr></thead>
      <tbody>
      {% for tx in txs %}
      <tr>
        <td class="mono muted">{{ tx.created_at[:19] }}</td>
        <td><span class="badge {{ 'bad' if tx.kind=='spend' else 'ok' }}">{{ tx.kind }}</span></td>
        <td><strong>{{ tx.amount }}</strong></td>
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

<div class="card">
  <div class="table-wrap">
    <table>
      <thead>
        <tr><th>ID</th><th>User</th><th>Type</th><th>Details</th><th>Status</th><th>Created</th><th style="text-align:right">Actions</th></tr>
      </thead>
      <tbody>
      {% for r in requests %}
      <tr>
        <td class="mono tiny">{{ r.id }}</td>
        <td><a href="{{ url_for('user_detail', uid=r.user_id) }}">#{{ r.user_id }}</a></td>
        <td>{{ r.type }}</td>
        <td><strong>{{ r.amount or r.plan or '—' }}</strong></td>
        <td><span class="badge {{ r.status }}">{{ r.status }}</span></td>
        <td class="mono muted tiny">{{ r.created_at[:19] }}</td>
        <td style="text-align:right">
          {% if r.status == 'pending' %}
          <form method="post" action="{{ url_for('approve_request', rid=r.id) }}" style="display:inline">
            <input type="hidden" name="csrf" value="{{ csrf }}">
            <button class="btn success sm">✓ Approve</button>
          </form>
          <form method="post" action="{{ url_for('reject_request', rid=r.id) }}" style="display:inline">
            <input type="hidden" name="csrf" value="{{ csrf }}">
            <button class="btn danger sm">✕</button>
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
    <div class="page-sub">Full ledger of diamond flow</div>
  </div>
</div>

<div class="stats">
  <div class="stat emerald">
    <div class="k">↑ Granted</div>
    <div class="v">{{ totals.granted }}</div>
  </div>
  <div class="stat rose">
    <div class="k">↓ Spent</div>
    <div class="v">{{ totals.spent }}</div>
  </div>
  <div class="stat amber">
    <div class="k">↺ Refunded</div>
    <div class="v">{{ totals.refunded }}</div>
  </div>
</div>

<div class="card">
  <div class="table-wrap">
    <table>
      <thead><tr><th>When</th><th>User</th><th>Kind</th><th>Amount</th><th>Reason</th><th style="text-align:right">Balance</th></tr></thead>
      <tbody>
      {% for tx in txs %}
      <tr>
        <td class="mono muted tiny">{{ tx.created_at[:19] }}</td>
        <td><a href="{{ url_for('user_detail', uid=tx.user_id) }}">#{{ tx.user_id }}</a></td>
        <td><span class="badge {{ 'bad' if tx.kind=='spend' else 'ok' }}">{{ tx.kind }}</span></td>
        <td><strong>{{ tx.amount }}</strong></td>
        <td class="muted">{{ tx.reason }}</td>
        <td class="mono" style="text-align:right">{{ tx.balance_after }}</td>
      </tr>
      {% else %}
      <tr><td colspan="6" class="empty">No transactions.</td></tr>
      {% endfor %}
      </tbody>
    </table>
  </div>
</div>
"""


# ===========================================================================
# Shop
# ===========================================================================
SHOP_TEMPLATE = """
<div class="page-head">
  <div>
    <div class="crumb">System</div>
    <div class="page-title">Shop items</div>
    <div class="page-sub">Items users can buy with diamonds</div>
  </div>
</div>

<div class="card">
  <div class="card-head"><h2>Add or update item</h2></div>
  <form method="post" action="{{ url_for('shop_save') }}">
    <input type="hidden" name="csrf" value="{{ csrf }}">
    <div class="field-row">
      <div><label>ID</label><input name="item_id" placeholder="extra_job_slot"></div>
      <div><label>Name</label><input name="name" placeholder="Extra job slot"></div>
      <div style="flex:0 0 120px"><label>Price</label><input type="number" name="price" value="20"></div>
      <div style="flex:0 0 130px"><label>Max per user</label><input type="number" name="max_per_user" value="1"></div>
    </div>
    <label>Description</label>
    <input name="description" placeholder="What does this do?">
    <div style="margin-top:14px">
      <button class="btn primary">Save item</button>
    </div>
  </form>
</div>

<div class="card">
  <div class="card-head"><h2>Current items <span class="sub">{{ items|length }}</span></h2></div>
  <div class="table-wrap">
    <table>
      <thead><tr><th>ID</th><th>Name</th><th>Price</th><th>Max</th><th>Description</th><th></th></tr></thead>
      <tbody>
      {% for iid, it in items.items() %}
      <tr>
        <td><code>{{ iid }}</code></td>
        <td><strong>{{ it.name }}</strong></td>
        <td><span class="badge info">{{ it.price }} ◆</span></td>
        <td class="mono">{{ it.max_per_user }}</td>
        <td class="muted">{{ it.description }}</td>
        <td style="text-align:right">
          <form method="post" action="{{ url_for('shop_delete', item_id=iid) }}" style="display:inline">
            <input type="hidden" name="csrf" value="{{ csrf }}">
            <button class="btn danger sm">Delete</button>
          </form>
        </td>
      </tr>
      {% else %}
      <tr><td colspan="6" class="empty">No items.</td></tr>
      {% endfor %}
      </tbody>
    </table>
  </div>
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

<div class="card">
  <div class="table-wrap">
    <table>
      <thead><tr><th>When</th><th>Admin</th><th>Action</th><th>Target</th><th>IP</th></tr></thead>
      <tbody>
      {% for e in entries %}
      <tr>
        <td class="mono muted tiny">{{ e.created_at[:19] }}</td>
        <td class="mono">#{{ e.admin_id }}</td>
        <td><span class="badge info">{{ e.action }}</span></td>
        <td class="muted">{{ e.target or '—' }}</td>
        <td class="mono muted tiny">{{ e.ip or '—' }}</td>
      </tr>
      {% else %}
      <tr><td colspan="5" class="empty">No audit entries.</td></tr>
      {% endfor %}
      </tbody>
    </table>
  </div>
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
    <div class="card-head"><h2>Create backup</h2></div>
    <p class="muted" style="margin-bottom:14px">
      Zips all JSON data and sends it to the owner's Telegram DM.
    </p>
    <form method="post" action="{{ url_for('backup_now') }}">
      <input type="hidden" name="csrf" value="{{ csrf }}">
      <button class="btn primary">Create backup now</button>
    </form>
  </div>

  <div class="card">
    <div class="card-head"><h2>Restore from ZIP</h2></div>
    <p class="muted" style="margin-bottom:14px">
      Upload a previously created backup archive.
    </p>
    <form method="post" action="{{ url_for('restore_backup') }}" enctype="multipart/form-data">
      <input type="hidden" name="csrf" value="{{ csrf }}">
      <input type="file" name="file" accept=".zip">
      <div style="margin-top:14px">
        <button class="btn danger">Restore data</button>
      </div>
    </form>
  </div>
</div>

<div class="card">
  <div class="card-head"><h2>Local backups <span class="sub">{{ backups|length }}</span></h2></div>
  <div class="table-wrap">
    <table>
      <thead><tr><th>File</th><th>Size</th><th>Modified</th><th></th></tr></thead>
      <tbody>
      {% for b in backups %}
      <tr>
        <td class="mono">{{ b.name }}</td>
        <td class="mono">{{ b.size_kb }} KB</td>
        <td class="muted tiny">{{ b.modified }}</td>
        <td style="text-align:right">
          <a class="btn sm" href="{{ url_for('download_backup', name=b.name) }}">Download</a>
        </td>
      </tr>
      {% else %}
      <tr><td colspan="4" class="empty">No backups yet.</td></tr>
      {% endfor %}
      </tbody>
    </table>
  </div>
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
    <div style="margin-top:16px">
      <button class="btn primary">Send to all users →</button>
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
  <div class="card-head"><h2>Economy</h2></div>
  <form method="post" action="{{ url_for('settings_save') }}">
    <input type="hidden" name="csrf" value="{{ csrf }}">
    <div class="field-row">
      <div><label>Signup bonus</label><input type="number" name="signup_bonus" value="{{ cfg.signup_bonus }}"></div>
      <div><label>Cost per clock update</label><input type="number" name="diamond_cost_clock" value="{{ cfg.diamond_cost_clock }}"></div>
      <div><label>Cost per job</label><input type="number" name="diamond_cost_job" value="{{ cfg.diamond_cost_job }}"></div>
      <div><label>Referral bonus</label><input type="number" name="referral_bonus" value="{{ cfg.referral_bonus }}"></div>
    </div>
    <div class="field-row" style="margin-top:14px">
      <div><label>Timezone</label><input name="timezone" value="{{ cfg.timezone }}"></div>
      <div><label>Base name</label><input name="base_name" value="{{ cfg.base_name }}"></div>
      <div style="flex:0 0 140px"><label>Interval (min)</label><input type="number" name="interval" value="{{ cfg.interval }}"></div>
    </div>
    <div style="margin-top:18px">
      <button class="btn primary">Save settings</button>
    </div>
  </form>
</div>

<div class="card">
  <div class="card-head"><h2>Security</h2></div>
  <p class="muted" style="margin-bottom:14px">
    Two-factor authentication protects your admin login with a TOTP code.
  </p>
  <a class="btn primary" href="{{ url_for('totp_page') }}">Configure 2FA →</a>
</div>

<div class="card">
  <div class="card-head"><h2>API access</h2></div>
  <p class="muted">
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
  <a class="btn ghost" href="{{ url_for('settings_page') }}">← Back</a>
</div>

<div class="card">
  {% if enabled %}
    <div class="badge ok" style="margin-bottom:14px">2FA enabled</div>
    <p class="muted" style="margin-bottom:16px">
      Your account is protected with TOTP. You will be prompted for a code on every login.
    </p>
    <form method="post" action="{{ url_for('totp_disable') }}">
      <input type="hidden" name="csrf" value="{{ csrf }}">
      <button class="btn danger">Disable 2FA</button>
    </form>
  {% else %}
    <div class="badge pending" style="margin-bottom:14px">2FA disabled</div>
    {% if qr_b64 %}
      <p class="muted">Scan this QR code with your authenticator app:</p>
      <img src="data:image/png;base64,{{ qr_b64 }}" alt="QR"
           style="background:#fff;padding:14px;border-radius:14px;margin:16px 0;">
      <p class="muted">Or enter this secret manually:</p>
      <p style="margin:8px 0 20px"><code>{{ pending_secret }}</code></p>
      <form method="post" action="{{ url_for('totp_enable') }}">
        <input type="hidden" name="csrf" value="{{ csrf }}">
        <button class="btn success">Enable 2FA</button>
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
  <div class="card-head">
    <h2>Active config: <code>{{ active }}</code></h2>
  </div>
  <button class="btn primary" id="test-active-btn">Test active config</button>
  <pre id="test-active-result" style="margin-top:14px;white-space:pre-wrap;
       background:rgba(0,0,0,.35);padding:14px;border-radius:12px;font-size:12px;
       color:#a5b4fc;display:none;font-family:'JetBrains Mono',monospace;border:1px solid var(--stroke)"></pre>
</div>

<div class="card">
  <div class="card-head"><h2>All configs <span class="sub">{{ configs|length }}</span></h2></div>
  <div class="table-wrap">
    <table>
      <thead><tr><th>Key</th><th>Name</th><th>URL</th><th>Status</th><th style="text-align:right">Actions</th></tr></thead>
      <tbody>
      {% for c in configs %}
      <tr>
        <td><code>{{ c.key }}</code></td>
        <td>{{ c.name }}</td>
        <td class="mono muted tiny">{{ c.url_short }}</td>
        <td>{% if c.active %}<span class="badge ok">active</span>{% else %}<span class="badge free">idle</span>{% endif %}</td>
        <td style="text-align:right">
          <button class="btn sm" onclick="testConfig('{{ c.key }}')">Test</button>
          {% if not c.active %}
          <form method="post" action="{{ url_for('proxy_activate', key=c.key) }}" style="display:inline">
            <input type="hidden" name="csrf" value="{{ csrf }}">
            <button class="btn success sm">Activate</button>
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
</div>

<div id="test-result" style="margin-bottom:20px"></div>

<div class="card">
  <div class="card-head"><h2>Add config</h2></div>
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
    <div style="margin-top:16px">
      <button class="btn primary">Add config</button>
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
    el.innerHTML = '<div class="card"><div class="card-head"><h2>Result: ' + key + '</h2></div>' +
      '<pre style="white-space:pre-wrap;color:#a5b4fc;font-size:12px;background:rgba(0,0,0,.35);padding:14px;border-radius:12px;font-family:JetBrains Mono,monospace">' +
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
    # TOTP
    # ===================================================================
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

    # ===================================================================
    # Proxy
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