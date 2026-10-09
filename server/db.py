"""Platform database (data/platform.db): users, sessions, contributed drives and the coin ledger.

Separate from data/cases.db (the admin case workflow). One connection shared across threads,
serialised by `lock`; every write goes through `tx()`.
"""
import os
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from hazardmap.config import ROOT

DB_PATH = Path(os.environ.get("TERRATRACE_PLATFORM_DB") or ROOT / "data" / "platform.db")
lock = threading.RLock()
_conn: sqlite3.Connection | None = None

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    email TEXT UNIQUE NOT NULL COLLATE NOCASE,
    name TEXT NOT NULL,
    password_hash TEXT NOT NULL,
    role TEXT NOT NULL DEFAULT 'user' CHECK (role IN ('user', 'admin')),
    disabled INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS sessions (
    token_hash TEXT PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    created_at TEXT NOT NULL, expires_at TEXT NOT NULL, ip TEXT, user_agent TEXT);
CREATE TABLE IF NOT EXISTS login_failures (
    key TEXT NOT NULL, ts REAL NOT NULL);
CREATE INDEX IF NOT EXISTS login_failures_key ON login_failures(key, ts);
CREATE TABLE IF NOT EXISTS drives (
    run_id TEXT PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id),
    source TEXT NOT NULL CHECK (source IN ('upload', 'record', 'live')),
    name TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'queued',
    duration_s REAL, valid_s REAL, hazards INTEGER, route_km REAL,
    video_sha256 TEXT, note TEXT, error TEXT,
    created_at TEXT NOT NULL, finished_at TEXT);
CREATE INDEX IF NOT EXISTS drives_user ON drives(user_id, created_at);
CREATE INDEX IF NOT EXISTS drives_sha ON drives(video_sha256);
CREATE TABLE IF NOT EXISTS coins (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL REFERENCES users(id),
    run_id TEXT,
    amount INTEGER NOT NULL,
    kind TEXT NOT NULL CHECK (kind IN ('earn', 'bonus', 'revoke', 'adjust', 'estimate', 'redeem', 'refund')),
    status TEXT NOT NULL CHECK (status IN ('pending', 'credited', 'void')),
    note TEXT, actor TEXT, created_at TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS coins_user ON coins(user_id, created_at);
CREATE TABLE IF NOT EXISTS rewards_catalog (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    brand TEXT NOT NULL, title TEXT NOT NULL, value_inr INTEGER NOT NULL, coins INTEGER NOT NULL,
    active INTEGER NOT NULL DEFAULT 1, sort INTEGER NOT NULL DEFAULT 0,
    UNIQUE (brand, value_inr));
CREATE TABLE IF NOT EXISTS voucher_codes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    item_id INTEGER NOT NULL REFERENCES rewards_catalog(id),
    code_hash TEXT UNIQUE NOT NULL, code_enc TEXT NOT NULL, pin_enc TEXT, last4 TEXT NOT NULL,
    expires_on TEXT, batch TEXT, added_by TEXT, added_at TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'available' CHECK (status IN ('available', 'issued', 'void')),
    issued_to INTEGER REFERENCES users(id), redemption_id INTEGER);
CREATE INDEX IF NOT EXISTS voucher_pick ON voucher_codes(item_id, status, expires_on, id);
CREATE TABLE IF NOT EXISTS redemptions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL REFERENCES users(id),
    item_id INTEGER NOT NULL REFERENCES rewards_catalog(id),
    coins INTEGER NOT NULL, value_inr INTEGER NOT NULL,
    status TEXT NOT NULL DEFAULT 'issued' CHECK (status IN ('pending', 'issued', 'void')),
    code_id INTEGER REFERENCES voucher_codes(id),
    created_at TEXT NOT NULL, voided_at TEXT, void_reason TEXT, voided_by TEXT,
    views INTEGER NOT NULL DEFAULT 0, last_viewed_at TEXT);
CREATE INDEX IF NOT EXISTS redemptions_user ON redemptions(user_id, created_at);
"""

# Amazon.in gift vouchers at 10 coins = ₹1 (server/market.py). Seeded once; admins edit afterwards.
SEED_CATALOG = [("Amazon.in", "Amazon.in Gift Card", v, v * 10, i) for i, v in enumerate((100, 250, 500, 1000))]


def _migrate(c: sqlite3.Connection):
    """coins.kind gained 'redeem'/'refund': SQLite can't alter a CHECK, so rebuild the table once."""
    sql = c.execute("SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'coins'").fetchone()
    if sql and "'redeem'" not in sql[0]:
        with c:
            c.execute("ALTER TABLE coins RENAME TO coins_old")
            c.executescript(SCHEMA)                    # recreates coins with the new CHECK
            c.execute("INSERT INTO coins (id, user_id, run_id, amount, kind, status, note, actor, created_at) "
                      "SELECT id, user_id, run_id, amount, kind, status, note, actor, created_at FROM coins_old")
            c.execute("DROP TABLE coins_old")
    sql = c.execute("SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'redemptions'").fetchone()
    if sql and "'pending'" not in sql[0]:              # redemptions gained 'pending' (awaiting a code)
        with c:
            cols = [r[1] for r in c.execute("PRAGMA table_info(redemptions)")]
            c.execute("ALTER TABLE redemptions RENAME TO redemptions_old")
            c.execute("DROP INDEX IF EXISTS redemptions_user")
            c.executescript(SCHEMA)
            c.execute(f"INSERT INTO redemptions ({', '.join(cols)}) SELECT {', '.join(cols)} FROM redemptions_old")
            c.execute("DROP TABLE redemptions_old")
    with c:
        c.executemany("INSERT OR IGNORE INTO rewards_catalog (brand, title, value_inr, coins, sort) VALUES (?, ?, ?, ?, ?)",
                      SEED_CATALOG)


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def conn() -> sqlite3.Connection:
    global _conn
    with lock:
        if _conn is None:
            DB_PATH.parent.mkdir(parents=True, exist_ok=True)
            _conn = sqlite3.connect(DB_PATH, check_same_thread=False)
            _conn.row_factory = sqlite3.Row
            _conn.execute("PRAGMA foreign_keys = ON")
            _conn.executescript(SCHEMA)
            _migrate(_conn)
        return _conn


@contextmanager
def tx():
    """Serialised transaction: commit on success, roll back on error."""
    with lock:
        c = conn()
        with c:
            yield c


def one(sql: str, args: tuple = ()) -> sqlite3.Row | None:
    with lock:
        return conn().execute(sql, args).fetchone()


def all_(sql: str, args: tuple = ()) -> list[sqlite3.Row]:
    with lock:
        return conn().execute(sql, args).fetchall()
