"""Rewards marketplace: spend coins on Amazon.in gift vouchers.

Fulfilment is from a code inventory: the admin uploads gift-card codes per denomination
(encrypted at rest, server/vault.py) and a redemption atomically assigns the oldest unused
code. With no stock the redemption is still accepted as 'pending' (coins held); it is filled
automatically when codes are uploaded, or by the admin entering a code, or rejected with a
refund. So rewards are always available. Everything that moves coins or codes happens in one db.tx() (global lock), so a balance
can't be double-spent and a code can't be issued twice.

Rate: 10 coins = ₹1. Limits per user: MAX_PER_DAY redemptions and MAX_INR_PER_MONTH.
"""
import csv
import io
import re
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from . import auth, db, rewards, vault

router = APIRouter()
COINS_PER_INR = 10
MAX_PER_DAY = 3
MAX_INR_PER_MONTH = 2000
LOW_STOCK = 5
REDEEM_URL = "https://www.amazon.in/gc/redeem"
FULFIL_HOURS = 48             # promise shown to users for vouchers that wait for stock
_CODE = re.compile(r"^[A-Z0-9]{8,24}$")


def _items(active_only: bool = True) -> list[dict]:
    rows = db.all_("SELECT c.*, (SELECT COUNT(*) FROM voucher_codes v WHERE v.item_id = c.id AND v.status = 'available' "
                   "AND (v.expires_on IS NULL OR v.expires_on >= date('now'))) AS stock FROM rewards_catalog c "
                   + ("WHERE c.active = 1 " if active_only else "") + "ORDER BY c.sort, c.value_inr")
    return [dict(r) for r in rows]


def _limits(c, user_id: int, value_inr: int):
    now = datetime.now(timezone.utc)
    day = (now - timedelta(days=1)).isoformat(timespec="seconds")
    month = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0).isoformat(timespec="seconds")
    n = c.execute("SELECT COUNT(*) FROM redemptions WHERE user_id = ? AND status != 'void' AND created_at >= ?",
                  (user_id, day)).fetchone()[0]
    if n >= MAX_PER_DAY:
        raise HTTPException(429, f"You can redeem up to {MAX_PER_DAY} vouchers per day. Try again tomorrow.")
    spent = c.execute("SELECT COALESCE(SUM(value_inr), 0) FROM redemptions WHERE user_id = ? AND status != 'void' "
                      "AND created_at >= ?", (user_id, month)).fetchone()[0]
    if spent + value_inr > MAX_INR_PER_MONTH:
        raise HTTPException(429, f"Monthly limit is ₹{MAX_INR_PER_MONTH:,} in vouchers (₹{spent:,} used this month).")


def _voucher(r, reveal: bool = False) -> dict:
    out = {"id": r["id"], "title": r["title"], "brand": r["brand"], "value_inr": r["value_inr"], "coins": r["coins"],
           "status": r["status"], "created_at": r["created_at"], "expires_on": r["expires_on"],
           "code_masked": vault.mask(r["last4"]) if r["last4"] else None, "redeem_url": REDEEM_URL,
           "void_reason": r["void_reason"], "fulfil_hours": FULFIL_HOURS}
    if reveal and r["status"] == "issued":
        out["code"] = vault.decrypt(r["code_enc"])
        out["pin"] = vault.decrypt(r["pin_enc"])
    return out


_VOUCHER_SQL = ("SELECT r.*, c.title, c.brand, v.code_enc, v.pin_enc, v.last4, v.expires_on FROM redemptions r "
                "JOIN rewards_catalog c ON c.id = r.item_id LEFT JOIN voucher_codes v ON v.id = r.code_id ")


# ---------------------------------------------------------------------- user API
@router.get("/api/me/market")
def market(request: Request):
    user = auth.require_user(request)
    bal = rewards.balance(user["id"])
    items = [{**{k: i[k] for k in ("id", "brand", "title", "value_inr", "coins")},
              "in_stock": True, "instant": i["stock"] > 0, "affordable": bal["balance"] >= i["coins"],
              "short_by": max(0, i["coins"] - bal["balance"])} for i in _items()]
    return {"items": items, **bal, "rate": COINS_PER_INR, "fulfil_hours": FULFIL_HOURS,
            "limits": {"per_day": MAX_PER_DAY, "inr_per_month": MAX_INR_PER_MONTH}}


class Redeem(BaseModel):
    confirm: bool


@router.post("/api/me/market/{item_id}/redeem")
def redeem(item_id: int, body: Redeem, request: Request):
    user = auth.require_user(request)
    if not body.confirm:
        raise HTTPException(400, "Confirm the redemption")
    if user["role"] == "admin":
        raise HTTPException(403, "Admin accounts can't redeem vouchers")
    with db.tx() as c:
        item = c.execute("SELECT * FROM rewards_catalog WHERE id = ? AND active = 1", (item_id,)).fetchone()
        if item is None:
            raise HTTPException(404, "This reward isn't available")
        balance = c.execute("SELECT COALESCE(SUM(amount), 0) FROM coins WHERE user_id = ? AND status = 'credited'",
                            (user["id"],)).fetchone()[0]
        if balance < item["coins"]:
            raise HTTPException(402, f"Not enough coins: you need {item['coins'] - balance:,} more.")
        _limits(c, user["id"], item["value_inr"])
        code = c.execute("SELECT id FROM voucher_codes WHERE item_id = ? AND status = 'available' "
                         "AND (expires_on IS NULL OR expires_on >= date('now')) ORDER BY expires_on IS NULL, expires_on, id LIMIT 1",
                         (item_id,)).fetchone()
        now = db.now()
        rid = c.execute("INSERT INTO redemptions (user_id, item_id, coins, value_inr, status, code_id, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                        (user["id"], item_id, item["coins"], item["value_inr"], "issued" if code else "pending",
                         code["id"] if code else None, now)).lastrowid
        if code:
            upd = c.execute("UPDATE voucher_codes SET status = 'issued', issued_to = ?, redemption_id = ? WHERE id = ? AND status = 'available'",
                            (user["id"], rid, code["id"]))
            if upd.rowcount != 1:                               # belt and braces: never issue a code twice
                raise HTTPException(409, "Please try again")
        c.execute("INSERT INTO coins (user_id, amount, kind, status, note, created_at) VALUES (?, ?, 'redeem', 'credited', ?, ?)",
                  (user["id"], -item["coins"], f"{item['title']} ₹{item['value_inr']:,}", now))
    return my_voucher(rid, request)


@router.get("/api/me/redemptions")
def my_vouchers(request: Request):
    user = auth.require_user(request)
    rows = db.all_(_VOUCHER_SQL + "WHERE r.user_id = ? ORDER BY r.id DESC", (user["id"],))
    return {"vouchers": [_voucher(r) for r in rows]}


@router.get("/api/me/redemptions/{rid}")
def my_voucher(rid: int, request: Request):
    """Full code + PIN: the owner only (admins see masked codes)."""
    user = auth.require_user(request)
    r = db.one(_VOUCHER_SQL + "WHERE r.id = ? AND r.user_id = ?", (rid, user["id"]))
    if r is None:
        raise HTTPException(404, "voucher not found")
    with db.tx() as c:
        c.execute("UPDATE redemptions SET views = views + 1, last_viewed_at = ? WHERE id = ?", (db.now(), rid))
    return _voucher(r, reveal=True)


# ---------------------------------------------------------------------- admin API
class Price(BaseModel):
    active: bool | None = None
    coins: int | None = Field(None, ge=1, le=10_000_000)


class Codes(BaseModel):
    text: str = Field(max_length=500_000)        # pasted lines or CSV: code[,pin][,expiry YYYY-MM-DD]
    batch: str = Field("", max_length=60)


class Void(BaseModel):
    reason: str = Field(min_length=3, max_length=200)


@router.get("/api/admin/market")
def admin_market(request: Request):
    auth.require_admin(request)
    items = []
    for i in _items(active_only=False):
        counts = {r["status"]: r["n"] for r in db.all_("SELECT status, COUNT(*) AS n FROM voucher_codes WHERE item_id = ? GROUP BY status", (i["id"],))}
        waiting = db.one("SELECT COUNT(*) AS n FROM redemptions WHERE item_id = ? AND status = 'pending'", (i["id"],))["n"]
        items.append({**i, "issued": counts.get("issued", 0), "void": counts.get("void", 0), "pending": waiting,
                      "low": bool(i["active"] and i["stock"] < LOW_STOCK)})
    rows = db.all_(_VOUCHER_SQL.replace("SELECT r.*", "SELECT r.*, u.name AS user_name, u.email AS user_email")
                   + "JOIN users u ON u.id = r.user_id ORDER BY r.id DESC LIMIT 100")
    recent = [{**_voucher(r), "user": r["user_name"], "email": r["user_email"], "views": r["views"]} for r in rows]
    total = db.one("SELECT COUNT(*) AS n, COALESCE(SUM(value_inr), 0) AS inr FROM redemptions WHERE status = 'issued'")
    pending = db.one("SELECT COUNT(*) AS n FROM redemptions WHERE status = 'pending'")["n"]
    return {"items": items, "recent": recent, "redeemed": total["n"], "redeemed_inr": total["inr"], "pending": pending,
            "rate": COINS_PER_INR}


@router.patch("/api/admin/market/{item_id}")
def admin_item(item_id: int, body: Price, request: Request):
    auth.require_admin(request)
    with db.tx() as c:
        if body.active is not None:
            c.execute("UPDATE rewards_catalog SET active = ? WHERE id = ?", (int(body.active), item_id))
        if body.coins is not None:
            c.execute("UPDATE rewards_catalog SET coins = ? WHERE id = ?", (body.coins, item_id))
    return admin_market(request)


@router.post("/api/admin/market/{item_id}/codes")
def admin_codes(item_id: int, body: Codes, request: Request):
    admin = auth.require_admin(request)
    if not db.one("SELECT 1 FROM rewards_catalog WHERE id = ?", (item_id,)):
        raise HTTPException(404, "item not found")
    added = dup = 0
    invalid = []
    today = datetime.now(timezone.utc).date().isoformat()
    rows = [r for r in csv.reader(io.StringIO(body.text)) if r and any(x.strip() for x in r)]
    with db.tx() as c:
        for n, r in enumerate(rows, 1):
            code = vault.normalise(r[0])
            if n == 1 and code in ("CODE", "CLAIMCODE"):
                continue                                            # header row
            pin = r[1].strip() if len(r) > 1 and r[1].strip() else None
            exp = r[2].strip() if len(r) > 2 and r[2].strip() else None
            if not _CODE.match(code):
                invalid.append(f"line {n}: not a gift-card code"); continue
            if exp and not re.match(r"^\d{4}-\d{2}-\d{2}$", exp):
                invalid.append(f"line {n}: expiry must be YYYY-MM-DD"); continue
            if exp and exp < today:
                invalid.append(f"line {n}: already expired"); continue
            h = vault.code_hash(code)
            if c.execute("SELECT 1 FROM voucher_codes WHERE code_hash = ?", (h,)).fetchone():
                dup += 1; continue
            c.execute("INSERT INTO voucher_codes (item_id, code_hash, code_enc, pin_enc, last4, expires_on, batch, added_by, added_at) "
                      "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                      (item_id, h, vault.encrypt(code), vault.encrypt(pin), code[-4:], exp, body.batch.strip() or None,
                       admin["name"], db.now()))
            added += 1
        filled = _fill_pending(c, item_id)
    return {"added": added, "duplicates": dup, "invalid": invalid[:20], "invalid_count": len(invalid), "filled": filled}


def _fill_pending(c, item_id: int) -> int:
    """Give waiting redemptions (oldest first) the freshly stocked codes."""
    n = 0
    for r in c.execute("SELECT id, user_id FROM redemptions WHERE item_id = ? AND status = 'pending' ORDER BY id", (item_id,)).fetchall():
        code = c.execute("SELECT id FROM voucher_codes WHERE item_id = ? AND status = 'available' "
                         "AND (expires_on IS NULL OR expires_on >= date('now')) ORDER BY expires_on IS NULL, expires_on, id LIMIT 1",
                         (item_id,)).fetchone()
        if code is None:
            break
        c.execute("UPDATE voucher_codes SET status = 'issued', issued_to = ?, redemption_id = ? WHERE id = ?", (r["user_id"], r["id"], code["id"]))
        c.execute("UPDATE redemptions SET status = 'issued', code_id = ? WHERE id = ?", (code["id"], r["id"]))
        n += 1
    return n


class Fulfil(BaseModel):
    code: str = Field(min_length=8, max_length=40)
    pin: str = Field("", max_length=20)
    expiry: str = Field("", max_length=10)


@router.post("/api/admin/redemptions/{rid}/fulfil")
def admin_fulfil(rid: int, body: Fulfil, request: Request):
    """Fill one waiting redemption with a code bought for it."""
    admin = auth.require_admin(request)
    code = vault.normalise(body.code)
    if not _CODE.match(code):
        raise HTTPException(400, "That doesn't look like a gift-card code")
    if body.expiry and not re.match(r"^\d{4}-\d{2}-\d{2}$", body.expiry):
        raise HTTPException(400, "Expiry must be YYYY-MM-DD")
    with db.tx() as c:
        r = c.execute("SELECT * FROM redemptions WHERE id = ?", (rid,)).fetchone()
        if r is None or r["status"] != "pending":
            raise HTTPException(409, "This redemption isn't waiting for a code")
        h = vault.code_hash(code)
        if c.execute("SELECT 1 FROM voucher_codes WHERE code_hash = ?", (h,)).fetchone():
            raise HTTPException(409, "This code was already used")
        cid = c.execute("INSERT INTO voucher_codes (item_id, code_hash, code_enc, pin_enc, last4, expires_on, batch, added_by, added_at, "
                        "status, issued_to, redemption_id) VALUES (?, ?, ?, ?, ?, ?, 'manual', ?, ?, 'issued', ?, ?)",
                        (r["item_id"], h, vault.encrypt(code), vault.encrypt(body.pin.strip() or None), code[-4:],
                         body.expiry or None, admin["name"], db.now(), r["user_id"], rid)).lastrowid
        c.execute("UPDATE redemptions SET status = 'issued', code_id = ? WHERE id = ?", (cid, rid))
    return admin_market(request)


@router.post("/api/admin/redemptions/{rid}/void")
def admin_void(rid: int, body: Void, request: Request):
    """Code didn't work: refund the coins and retire the code (never re-issued)."""
    admin = auth.require_admin(request)
    with db.tx() as c:
        r = c.execute("SELECT * FROM redemptions WHERE id = ?", (rid,)).fetchone()
        if r is None:
            raise HTTPException(404, "redemption not found")
        if r["status"] == "void":
            raise HTTPException(409, "already voided")
        now = db.now()
        c.execute("UPDATE redemptions SET status = 'void', voided_at = ?, void_reason = ?, voided_by = ? WHERE id = ?",
                  (now, body.reason.strip(), admin["name"], rid))
        if r["code_id"]:
            c.execute("UPDATE voucher_codes SET status = 'void' WHERE id = ?", (r["code_id"],))
        c.execute("INSERT INTO coins (user_id, amount, kind, status, note, actor, created_at) VALUES (?, ?, 'refund', 'credited', ?, ?, ?)",
                  (r["user_id"], r["coins"], f"Voucher refunded: {body.reason.strip()}", admin["name"], now))
    return admin_market(request)
