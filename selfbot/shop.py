"""Diamond shop: admin-editable items in data/shop.json."""
from datetime import datetime, timezone as dt_timezone
from typing import Optional

from .economy import spend
from .logging_setup import log
from .store import shop_store, users_store

DEFAULT_ITEMS = {
    "extra_job_slot": {
        "name": "Extra job slot",
        "description": "+1 concurrent repeat job",
        "price": 20,
        "max_per_user": 10,
        "effect": {"extra_job_slots": 1},
    },
    "interval_reduction": {
        "name": "Interval reduction",
        "description": "Reduce your plan's minimum interval by 60 seconds",
        "price": 30,
        "max_per_user": 5,
        "effect": {"interval_discount": 60},
    },
    "duration_extension": {
        "name": "Duration extension",
        "description": "Extend your plan's max duration by 120 minutes",
        "price": 25,
        "max_per_user": 5,
        "effect": {"duration_extension": 120},
    },
    "custom_font_unlock": {
        "name": "Custom font unlock",
        "description": "Unlock custom fonts for name and clock",
        "price": 40,
        "max_per_user": 1,
        "effect": {"unlocked_fonts": ["custom"]},
    },
    "priority_support": {
        "name": "Priority support",
        "description": "Your requests are handled first",
        "price": 100,
        "max_per_user": 1,
        "effect": {"priority_support": True},
    },
}


def _ensure_seeded() -> None:
    if not shop_store.get("items"):
        shop_store.set("items", DEFAULT_ITEMS)


def all_items() -> dict:
    _ensure_seeded()
    items = shop_store.get("items") or {}
    return items


def get_item(item_id: str) -> Optional[dict]:
    return all_items().get(item_id)


def set_item(item_id: str, item: dict) -> None:
    items = all_items()
    items[item_id] = item
    shop_store.set("items", items)


def delete_item(item_id: str) -> None:
    items = all_items()
    items.pop(item_id, None)
    shop_store.set("items", items)


def _purchase_count(user: dict, item_id: str) -> int:
    purchases = user.get("shop_purchases") or {}
    return int(purchases.get(item_id, 0))


def buy(user_id: int, item_id: str) -> tuple:
    """Return (ok, message_or_item)."""
    item = get_item(item_id)
    if not item:
        return False, "Item not found."

    key = str(user_id)
    user = users_store.get(key) or {"id": int(user_id)}
    purchases = user.get("shop_purchases") or {}
    count = _purchase_count(user, item_id)
    max_per_user = int(item.get("max_per_user", 1))
    if count >= max_per_user:
        return False, f"Purchase limit reached ({max_per_user})."

    price = int(item.get("price", 0))
    if not spend(user_id, price, f"shop:{item_id}", meta={"item": item_id}):
        return False, "Insufficient diamonds."

    # apply effect
    effect = item.get("effect") or {}
    for k, v in effect.items():
        if k in ("extra_job_slots", "interval_discount", "duration_extension"):
            user[k] = int(user.get(k, 0)) + int(v)
        elif k == "priority_support":
            user[k] = True
        elif k == "unlocked_fonts":
            lst = set(user.get("unlocked_fonts") or [])
            lst.update(v or [])
            user["unlocked_fonts"] = list(lst)

    purchases[item_id] = count + 1
    user["shop_purchases"] = purchases
    users_store.set(key, user)
    log.info(f"shop purchase user={user_id} item={item_id} price={price}")
    return True, item


def apply_to_limits(user_id: int, base_limits: dict) -> dict:
    """Return plan limits adjusted by shop purchases."""
    user = users_store.get(str(user_id)) or {}
    lim = dict(base_limits)
    lim["max_jobs"] = lim.get("max_jobs", 1) + int(user.get("extra_job_slots", 0) or 0)
    lim["min_interval"] = max(
        30, int(lim.get("min_interval", 300)) - int(user.get("interval_discount", 0) or 0)
    )
    lim["max_duration"] = int(lim.get("max_duration", 120)) + int(
        user.get("duration_extension", 0) or 0
    )
    return lim