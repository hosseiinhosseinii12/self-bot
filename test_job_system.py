#!/usr/bin/env python3
"""Standalone test for the job system.

Run: python test_job_system.py
It will create test users, jobs, and verify everything is working.
"""
import os
import sys
import tempfile
import time
from pathlib import Path

# Use a temporary DB for testing
tmp = tempfile.mkdtemp(prefix="selfbot_test_")
os.environ["DB_PATH"] = tmp
print(f"[TEST] Using DB_PATH = {tmp}")

# Add project to path
BASE = Path(__file__).parent.resolve()
sys.path.insert(0, str(BASE))

# ---------------------------------------------------------------------------
def section(title):
    print()
    print("=" * 60)
    print(f"  {title}")
    print("=" * 60)


def check(ok, msg):
    icon = "✅" if ok else "❌"
    print(f"  {icon} {msg}")
    return ok


# ---------------------------------------------------------------------------
section("1. Import test")

try:
    from selfbot import db
    check(True, "db module imported")
except Exception as e:
    check(False, f"db import failed: {e}")
    sys.exit(1)

try:
    from selfbot import jobs as J
    check(True, "jobs module imported")
except Exception as e:
    check(False, f"jobs import failed: {e}")
    sys.exit(1)

try:
    from selfbot import economy as E
    check(True, "economy module imported")
except Exception as e:
    check(False, f"economy import failed: {e}")
    sys.exit(1)

try:
    from selfbot import users as U
    check(True, "users module imported")
except Exception as e:
    check(False, f"users import failed: {e}")
    sys.exit(1)


# ---------------------------------------------------------------------------
section("2. DB init test")

try:
    db.init_db()
    check(True, "db.init_db() ran")
except Exception as e:
    check(False, f"db.init_db() failed: {e}")
    sys.exit(1)

# check tables exist
try:
    conn = db.get_conn()
    tables = [r["name"] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
    ).fetchall()]
    print(f"  Tables found: {tables}")
    for t in ("users", "jobs", "transactions", "requests", "templates", "history"):
        check(t in tables, f"table '{t}' exists")
except Exception as e:
    check(False, f"table check failed: {e}")
    sys.exit(1)


# ---------------------------------------------------------------------------
section("3. User creation test")

try:
    u = U.ensure_user(111111, first_name="TestUser")
    check(True, f"user created: {u.get('id')} with {u.get('diamonds')} diamonds")
except Exception as e:
    check(False, f"ensure_user failed: {e}")
    import traceback; traceback.print_exc()
    sys.exit(1)

balance = E.get_balance(111111)
check(balance > 0, f"balance = {balance} (expect > 0)")


# ---------------------------------------------------------------------------
section("4. Job creation test")

try:
    ok, res = J.create_job(
        owner_id=111111,
        chat_id=-1001234567890,
        interval_seconds=60,
        duration_minutes=1,
        text="Test message",
    )
    if ok:
        job = res
        check(True, f"job created: id={job['id']} target={job['chat_id']}")
    else:
        check(False, f"job creation failed: {res}")
        sys.exit(1)
except Exception as e:
    check(False, f"create_job raised: {e}")
    import traceback; traceback.print_exc()
    sys.exit(1)


# ---------------------------------------------------------------------------
section("5. Job persisted in DB test")

try:
    j = db.get_job(job["id"])
    check(j is not None, f"job found in DB: {j}")
except Exception as e:
    check(False, f"db.get_job failed: {e}")


# ---------------------------------------------------------------------------
section("6. active_jobs test")

try:
    active = J.active_jobs(owner_id=111111)
    check(len(active) == 1, f"active_jobs returned {len(active)} (expect 1)")
    if active:
        print(f"     job: {active[0]}")
except Exception as e:
    check(False, f"active_jobs failed: {e}")


# ---------------------------------------------------------------------------
section("7. Job text render test")

try:
    rendered = J.render_variables("Hello {name} at {time} on {date}",
                                   job, 1)
    check(True, f"rendered: {rendered}")
except Exception as e:
    check(False, f"render_variables failed: {e}")


# ---------------------------------------------------------------------------
section("8. Job stop test")

try:
    ok = J.stop_job(job["id"])
    check(ok, f"stop_job returned {ok}")
    active = J.active_jobs(owner_id=111111)
    check(len(active) == 0, f"after stop: {len(active)} active (expect 0)")
except Exception as e:
    check(False, f"stop_job failed: {e}")


# ---------------------------------------------------------------------------
section("9. Templates test")

try:
    J.save_template(111111, "greeting", "Hello from template")
    tpl = J.load_template(111111, "greeting")
    check(tpl == "Hello from template", f"template loaded: {tpl!r}")
except Exception as e:
    check(False, f"template test failed: {e}")


# ---------------------------------------------------------------------------
section("10. Transaction log test")

try:
    txs = db.user_transactions(111111, 10)
    check(len(txs) > 0, f"user has {len(txs)} transactions")
    for tx in txs[:3]:
        print(f"     {tx['kind']:8s} {tx['amount']:+5d} — {tx['reason']}")
except Exception as e:
    check(False, f"transaction test failed: {e}")


# ---------------------------------------------------------------------------
section("11. Full job simulation test (with fake bot)")

class FakeBot:
    def __init__(self):
        self.sent = []
    async def send_message(self, chat_id, text):
        self.sent.append((chat_id, text))
        print(f"     [FAKE SEND] to {chat_id}: {text[:60]}")


async def simulate_job():
    fake_bot = FakeBot()

    # create new job with short duration
    ok, res = J.create_job(
        owner_id=111111,
        chat_id=-1001234567890,
        interval_seconds=60,   # 60s minimum
        duration_minutes=1,
        text="Sim test",
    )
    if not ok:
        print(f"     ❌ create_job: {res}")
        return False

    job = res
    print(f"     created job id={job['id']}")

    # run for 2 seconds then cancel
    task = __import__("asyncio").create_task(J.run_job(fake_bot, job))
    print("     waiting 3s...")
    await __import__("asyncio").sleep(3)
    task.cancel()
    try:
        await task
    except __import__("asyncio").CancelledError:
        pass

    check(len(fake_bot.sent) >= 1, f"job sent {len(fake_bot.sent)} messages (expect >=1)")

    # check job in DB after run
    j = db.get_job(job["id"])
    check(j is not None, f"job still in DB: status={j.get('status') if j else None}")

    return True


try:
    import asyncio
    result = asyncio.run(simulate_job())
    check(result, "full simulation done")
except Exception as e:
    check(False, f"simulation failed: {e}")
    import traceback; traceback.print_exc()


# ---------------------------------------------------------------------------
section("Summary")

print()
print("All tests complete.")
print(f"Test DB at: {tmp}")
print()
print("If any ❌ above, that's the problem.")
print()