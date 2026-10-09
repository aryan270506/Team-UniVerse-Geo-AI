"""Reward coins for contributed footage.

Rules (settled once a drive's analysis finishes):
  earn  = 1 coin per COIN_SECONDS of *valid* footage (GPS track, actually moving)
  bonus = HAZARD_BONUS per confirmed hazard the AI found on the drive
  per drive <= DRIVE_CAP, per user per UTC day <= DAILY_CAP
  no coins for: drives under MIN_SECONDS, duplicate videos, parked/no-GPS footage.

While a drive is queued/processing the user sees a pending *estimate*; on settle it is voided
and replaced by credited 'earn'/'bonus' rows. The ledger is append-only: admins revoke or
adjust by adding rows, never by editing history.
"""
from datetime import datetime, timedelta, timezone

from . import db

COIN_SECONDS = 30
HAZARD_BONUS = 5
DRIVE_CAP = 60
DAILY_CAP = 300
MIN_SECONDS = 30
MIN_ROUTE_KM = 0.2
MIN_SPEED_KMH = 5.0

RULES = {"coin_seconds": COIN_SECONDS, "hazard_bonus": HAZARD_BONUS, "drive_cap": DRIVE_CAP,
         "daily_cap": DAILY_CAP, "min_seconds": MIN_SECONDS, "min_route_km": MIN_ROUTE_KM,
         "min_speed_kmh": MIN_SPEED_KMH}


def estimate(seconds: float | None) -> int:
    return 0 if not seconds or seconds < MIN_SECONDS else min(DRIVE_CAP, int(seconds // COIN_SECONDS))


def add_estimate(c, user_id: int, run_id: str, seconds: float | None):
    n = estimate(seconds)
    if n:
        c.execute("INSERT INTO coins (user_id, run_id, amount, kind, status, note, created_at) "
                  "VALUES (?, ?, ?, 'estimate', 'pending', ?, ?)",
                  (user_id, run_id, n, f"Estimate for {round(seconds or 0)} s of footage", db.now()))


def _today_credited(c, user_id: int) -> int:
    start = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0).isoformat(timespec="seconds")
    return c.execute("SELECT COALESCE(SUM(amount), 0) FROM coins WHERE user_id = ? AND status = 'credited' "
                     "AND kind IN ('earn', 'bonus') AND created_at >= ?", (user_id, start)).fetchone()[0]


def assess(summary: dict, duplicate: bool) -> tuple[float, str | None]:
    """(valid seconds, reason when not rewardable)."""
    dur = float(summary.get("duration_s") or 0)
    km = float(summary.get("route_km") or 0)
    if duplicate:
        return 0.0, "Duplicate video: this footage was already uploaded"
    if dur < MIN_SECONDS:
        return 0.0, f"Too short: drives need at least {MIN_SECONDS} s"
    if km < MIN_ROUTE_KM:
        return 0.0, "No usable GPS track or the vehicle did not move"
    if km / (dur / 3600) < MIN_SPEED_KMH:
        return 0.0, "Average speed too low: footage must be recorded while driving"
    return dur, None


def settle(c, run_id: str, user_id: int, summary: dict, hazards: int, duplicate: bool) -> dict:
    """Replace the pending estimate with credited coins. Called inside a db transaction."""
    c.execute("UPDATE coins SET status = 'void' WHERE run_id = ? AND kind = 'estimate' AND status = 'pending'", (run_id,))
    if c.execute("SELECT 1 FROM coins WHERE run_id = ? AND kind IN ('earn', 'bonus') AND status = 'credited'",
                 (run_id,)).fetchone():
        return {"valid_s": None, "coins": 0, "note": "already settled"}           # idempotent
    valid_s, reason = assess(summary, duplicate)
    earn = int(valid_s // COIN_SECONDS)
    bonus = HAZARD_BONUS * hazards if valid_s else 0
    total = min(DRIVE_CAP, earn + bonus)
    room = max(0, DAILY_CAP - _today_credited(c, user_id))
    capped = min(total, room)
    note = reason
    if not reason and capped < earn + bonus:
        note = (f"Daily limit of {DAILY_CAP} coins reached" if capped < total
                else f"Capped at {DRIVE_CAP} coins per drive")
    earn_part = min(earn, capped)
    bonus_part = capped - earn_part
    now = db.now()
    if earn_part:
        c.execute("INSERT INTO coins (user_id, run_id, amount, kind, status, note, created_at) VALUES (?, ?, ?, 'earn', 'credited', ?, ?)",
                  (user_id, run_id, earn_part, f"{round(valid_s)} s of valid footage", now))
    if bonus_part:
        c.execute("INSERT INTO coins (user_id, run_id, amount, kind, status, note, created_at) VALUES (?, ?, ?, 'bonus', 'credited', ?, ?)",
                  (user_id, run_id, bonus_part, f"{hazards} hazard{'s' if hazards != 1 else ''} found", now))
    return {"valid_s": valid_s, "coins": capped, "note": note}


def revoke_drive(c, run_id: str, actor: str, note: str) -> int:
    row = c.execute("SELECT user_id, COALESCE(SUM(amount), 0) AS n FROM coins WHERE run_id = ? AND status = 'credited' "
                    "GROUP BY user_id", (run_id,)).fetchone()
    if not row or row["n"] <= 0:
        return 0
    c.execute("INSERT INTO coins (user_id, run_id, amount, kind, status, note, actor, created_at) "
              "VALUES (?, ?, ?, 'revoke', 'credited', ?, ?, ?)", (row["user_id"], run_id, -row["n"], note, actor, db.now()))
    return row["n"]


def adjust(c, user_id: int, amount: int, actor: str, note: str):
    c.execute("INSERT INTO coins (user_id, amount, kind, status, note, actor, created_at) VALUES (?, ?, 'adjust', 'credited', ?, ?, ?)",
              (user_id, amount, note, actor, db.now()))


def balance(user_id: int) -> dict:
    r = db.one("SELECT COALESCE(SUM(CASE WHEN status = 'credited' THEN amount END), 0) AS balance, "
               "COALESCE(SUM(CASE WHEN status = 'pending' THEN amount END), 0) AS pending, "
               "COALESCE(SUM(CASE WHEN status = 'credited' AND kind IN ('earn', 'bonus', 'adjust') AND amount > 0 THEN amount END), 0) AS earned, "
               "COALESCE(-SUM(CASE WHEN status = 'credited' AND kind = 'redeem' THEN amount END), 0) - "
               "COALESCE(SUM(CASE WHEN status = 'credited' AND kind = 'refund' THEN amount END), 0) AS spent "
               "FROM coins WHERE user_id = ?", (user_id,))
    return {"balance": r["balance"], "pending": r["pending"], "earned": r["earned"], "spent": r["spent"]}


def ledger(user_id: int, limit: int = 100) -> list[dict]:
    rows = db.all_("SELECT c.*, d.name AS drive_name FROM coins c LEFT JOIN drives d ON d.run_id = c.run_id "
                   "WHERE c.user_id = ? AND c.status != 'void' ORDER BY c.id DESC LIMIT ?", (user_id, limit))
    return [{k: r[k] for k in ("id", "run_id", "drive_name", "amount", "kind", "status", "note", "created_at")} for r in rows]


def week_start() -> str:
    now = datetime.now(timezone.utc)
    return (now - timedelta(days=now.weekday())).replace(hour=0, minute=0, second=0, microsecond=0).isoformat(timespec="seconds")


def leaderboard(user_id: int | None = None, limit: int = 10) -> dict:
    rows = db.all_("SELECT u.id, u.name, SUM(c.amount) AS coins FROM coins c JOIN users u ON u.id = c.user_id "
                   "WHERE c.status = 'credited' AND c.kind NOT IN ('redeem', 'refund') AND c.created_at >= ? "
                   "AND u.role = 'user' AND u.disabled = 0 "
                   "GROUP BY u.id HAVING coins > 0 ORDER BY coins DESC, MIN(c.created_at)", (week_start(),))
    ranked = [{"rank": i + 1, "id": r["id"], "name": r["name"], "coins": r["coins"]} for i, r in enumerate(rows)]
    me = next((r for r in ranked if r["id"] == user_id), None)
    return {"since": week_start(), "top": [{k: r[k] for k in ("rank", "name", "coins")} | {"me": r["id"] == user_id}
                                           for r in ranked[:limit]],
            "me": me and {"rank": me["rank"], "coins": me["coins"]}}
