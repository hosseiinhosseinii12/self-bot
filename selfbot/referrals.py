"""Referral system: /refer <code>, bonus for both sides."""
from .economy import grant, referral_bonus
from .logging_setup import log
from .users import add_referral, ensure_user, find_by_referral_code, get_user


def get_or_create_code(user_id: int) -> str:
    u = ensure_user(user_id)
    return u.get("referral_code", "")


def apply_referral(new_user_id: int, code: str) -> tuple:
    """Return (ok, message)."""
    code = (code or "").strip().upper()
    if not code:
        return False, "referral_invalid"

    referrer_id = find_by_referral_code(code)
    if referrer_id is None:
        return False, "referral_invalid"
    if referrer_id == int(new_user_id):
        return False, "referral_invalid"

    new_u = ensure_user(new_user_id)
    if new_u.get("referred_by"):
        return False, "referral_invalid"

    # bind
    new_u["referred_by"] = int(referrer_id)
    from .store import users_store
    users_store.set(str(new_user_id), new_u)
    add_referral(referrer_id, new_user_id)

    bonus = referral_bonus()
    grant(referrer_id, bonus, "referral_bonus", meta={"referee": new_user_id})
    grant(int(new_user_id), bonus, "referral_welcome", meta={"referrer": referrer_id})

    log.info(f"referral applied referrer={referrer_id} referee={new_user_id}")
    return True, "referral_applied"


def stats(user_id: int) -> dict:
    u = get_user(user_id) or {}
    refs = u.get("referrals") or []
    return {
        "code": u.get("referral_code", ""),
        "count": len(refs),
        "referrals": list(refs),
    }