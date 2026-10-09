# SELF BOT — Self-Hosted Telegram Profile Clock + Diamond Economy

[![Python](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org/)
[![Railway](https://img.shields.io/badge/deploy-Railway-0B0D0E.svg)](https://railway.app/)
[![Cloudflare](https://img.shields.io/badge/proxy-Cloudflare%20Worker-F38020.svg)](https://workers.cloudflare.com/)
[![License](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)

**SELF BOT** updates your Telegram profile first name to `<base_name> <HH:MM>` on a schedule, with a full diamond economy, subscription plans, dual web panels (admin + user), referral system, diamond shop, backups, and a Cloudflare Worker WebSocket→TCP bridge for MTProto behind restrictive networks.

---

## Table of Contents

1. [Features](#features)
2. [Architecture](#architecture)
3. [Prerequisites](#prerequisites)
4. [Default Credentials](#default-credentials)
5. [Default Owner ID](#default-owner-id)
6. [Cloudflare Worker Deployment](#cloudflare-worker-deployment)
7. [Railway Deployment](#railway-deployment)
8. [Local Development](#local-development)
9. [Usage Guide](#usage-guide)
10. [Bot Command Reference](#bot-command-reference)
11. [Diamond Economy](#diamond-economy)
12. [Subscription Plans](#subscription-plans)
13. [Backup & Restore](#backup--restore)
14. [Admin Panel Tour](#admin-panel-tour)
15. [User Panel Tour](#user-panel-tour)
16. [Troubleshooting](#troubleshooting)
17. [Architecture Deep Dive](#architecture-deep-dive)
18. [Security Notes](#security-notes)
19. [Performance & Scaling](#performance--scaling)
20. [FAQ](#faq)

---

## Features

- **Clock system** — first name becomes `<base_name> <HH:MM>` every N minutes (1–60).
- **Fonts** — 14 built-in digit fonts, 8 base-name fonts, plus custom user fonts (A–Z / 0–9).
- **Timezone** — configurable via `zoneinfo`.
- **Telegram bot** — pairing code, `/login`, `/logout`, owner-only access.
- **Repeat jobs** — `.rep <sec> <min> <text>` with templates, schedules, variables, `.txt` uploads.
- **Rich messages** — native `sendRichMessage` with graceful Markdown fallback (Unicode box-drawing tables).
- **Diamond economy** — signup bonus, transaction log, per-action costs, diamond shop.
- **Subscription plans** — free/basic/pro/vip with enforced limits (max jobs, min interval, max duration).
- **Requests** — users request diamonds or subscriptions; admin approves/rejects with one click.
- **Referral system** — per-user codes, `/refer <code>`, bonus for both sides.
- **Backups** — daily auto-backup at 3 AM (ZIP to owner DM, last 7 kept) + manual + restore.
- **Admin panel** — dark theme, live Chart.js diamond chart, users, requests, transactions, shop, audit, broadcast, backups.
- **User panel** — light theme, balance, subscription progress bar, transaction history.
- **Analytics** — totals, revenue chart, top spenders, subscription pie, approval rate.
- **Audit log** — every admin action recorded with IP + timestamp.
- **Rate limiting** — 30 msg/min token bucket per user; per-IP on web panels.
- **Structured logging** — `QueueHandler`/`QueueListener` → `data/logs/app.log` + `error.log`.
- **Retry with backoff** — `backoff.on_exception` on all external HTTP calls.
- **Graceful shutdown** — strips time from name, writes base name via `asyncio.shield` + sync fallback.
- **Auto-reconnect watchdog** — every 30s for bot and user clients.
- **i18n** — English + Persian only.
- **.env support** — precedence: env → `config.json` → defaults.
- **Two-factor admin** — optional TOTP.
- **API endpoints** — `/api/stats`, `/api/user/<id>`, `/api/transactions` (bearer token).
- **Health & metrics** — `/health`, `/metrics` (Prometheus-compatible).

---

## Architecture
┌──────────────────────────────────────────────────────────────────────┐
│ RAILWAY (Python) │
│ │
│ ┌──────────────┐ ┌─────────────────┐ ┌──────────────────┐ │
│ │ Flask app │ │ Telethon bot │ │ Telethon user │ │
│ │ (admin + │ │ (bot.session) │ │ (user.session) │ │
│ │ user) │ │ │ │ │ │
│ └──────┬───────┘ └────────┬────────┘ └────────┬─────────┘ │
│ │ │ │ │
│ │ └──────────┬───────────┘ │
│ │ │ │
│ │ ┌──────────▼───────────┐ │
│ │ │ python-socks Proxy │ │
│ │ │ (custom connector) │ │
│ │ └──────────┬───────────┘ │
│ │ │ SOCKS5 │
│ │ ┌──────────▼───────────┐ │
│ │ │ Embedded SOCKS5 │ │
│ │ │ bridge 127.0.0.1:1080│ │
│ │ └──────────┬───────────┘ │
│ │ │ WebSocket │
│ │ ▼ │
│ │ wss://<worker>/apiws?dst=<DC_IP> │
└─────────┼────────────────────────────────┬───────────────────────────┘
│ │
│ HTTPS │ Cloudflare edge
▼ ▼
┌─────────────┐ ┌──────────────────────┐
│ Browser │ │ Cloudflare Worker │
│ (admin / │ │ connect() → TCP │
│ user) │ └──────────┬───────────┘
└─────────────┘ │ TCP 443
▼
┌──────────────────────────┐
│ Telegram DCs │
│ 149.154.175.50 │
│ 149.154.167.51 │
│ 149.154.175.100 │
│ 149.154.167.91 │
│ 149.154.171.5 │
│ 91.105.192.100 │
└──────────────────────────┘