from __future__ import annotations

import os
import secrets
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

RAW_DATABASE_URL = os.getenv("DATABASE_URL", "").strip()
USE_POSTGRES = RAW_DATABASE_URL.startswith("postgres://") or RAW_DATABASE_URL.startswith("postgresql://")


def normalize_database_url(value: str) -> str:
    """Render Postgres 對外連線要求 TLS；若連線字串未指定則自動補 sslmode=require。"""
    if not value:
        return value
    if value.startswith("postgres://"):
        value = "postgresql://" + value[len("postgres://"):]
    try:
        parts = urlsplit(value)
        query = dict(parse_qsl(parts.query, keep_blank_values=True))
        query.setdefault("sslmode", "require")
        return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query), parts.fragment))
    except Exception:
        return value


DATABASE_URL = normalize_database_url(RAW_DATABASE_URL) if USE_POSTGRES else ""

if USE_POSTGRES:
    import psycopg
    from psycopg.rows import dict_row
else:
    DB_PATH = Path(os.getenv("XHS_DB_PATH", Path(__file__).with_name("xhs.db")))
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)


def database_backend() -> str:
    return "postgres" if USE_POSTGRES else "sqlite"


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def iso(dt: datetime | None) -> str | None:
    return dt.isoformat() if dt else None


@contextmanager
def connect():
    if USE_POSTGRES:
        conn = psycopg.connect(DATABASE_URL, row_factory=dict_row, autocommit=False, connect_timeout=10)
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()
    else:
        conn = sqlite3.connect(DB_PATH, timeout=20)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()


def sql(text: str) -> str:
    return text.replace("?", "%s") if USE_POSTGRES else text


def init_db() -> None:
    with connect() as conn:
        if USE_POSTGRES:
            statements = [
                """CREATE TABLE IF NOT EXISTS licenses (
                    id BIGSERIAL PRIMARY KEY,
                    license_key TEXT NOT NULL UNIQUE,
                    status TEXT NOT NULL DEFAULT 'active',
                    max_devices INTEGER NOT NULL DEFAULT 1,
                    created_at TEXT NOT NULL,
                    expires_at TEXT,
                    note TEXT NOT NULL DEFAULT ''
                )""",
                """CREATE TABLE IF NOT EXISTS devices (
                    id BIGSERIAL PRIMARY KEY,
                    license_id BIGINT NOT NULL REFERENCES licenses(id) ON DELETE CASCADE,
                    device_id TEXT NOT NULL,
                    platform TEXT NOT NULL DEFAULT 'ios',
                    first_seen_at TEXT NOT NULL,
                    last_seen_at TEXT NOT NULL,
                    UNIQUE(license_id, device_id)
                )""",
                """CREATE TABLE IF NOT EXISTS api_logs (
                    id BIGSERIAL PRIMARY KEY,
                    license_key TEXT NOT NULL,
                    device_id TEXT NOT NULL DEFAULT '',
                    platform TEXT NOT NULL DEFAULT '',
                    result TEXT NOT NULL,
                    payload TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL
                )""",
                "CREATE INDEX IF NOT EXISTS idx_devices_license ON devices(license_id)",
                "CREATE INDEX IF NOT EXISTS idx_logs_created ON api_logs(created_at DESC)",
            ]
            for statement in statements:
                conn.execute(statement)
        else:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS licenses (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    license_key TEXT NOT NULL UNIQUE,
                    status TEXT NOT NULL DEFAULT 'active',
                    max_devices INTEGER NOT NULL DEFAULT 1,
                    created_at TEXT NOT NULL,
                    expires_at TEXT,
                    note TEXT NOT NULL DEFAULT ''
                );
                CREATE TABLE IF NOT EXISTS devices (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    license_id INTEGER NOT NULL,
                    device_id TEXT NOT NULL,
                    platform TEXT NOT NULL DEFAULT 'ios',
                    first_seen_at TEXT NOT NULL,
                    last_seen_at TEXT NOT NULL,
                    UNIQUE(license_id, device_id),
                    FOREIGN KEY(license_id) REFERENCES licenses(id) ON DELETE CASCADE
                );
                CREATE TABLE IF NOT EXISTS api_logs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    license_key TEXT NOT NULL,
                    device_id TEXT NOT NULL DEFAULT '',
                    platform TEXT NOT NULL DEFAULT '',
                    result TEXT NOT NULL,
                    payload TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_devices_license ON devices(license_id);
                CREATE INDEX IF NOT EXISTS idx_logs_created ON api_logs(created_at DESC);
                """
            )


def new_license_key(prefix: str = "XHS") -> str:
    token = secrets.token_hex(6).upper()
    return f"{prefix}-{token[:4]}-{token[4:8]}-{token[8:12]}"


def create_license(*, days: int | None, max_devices: int, note: str = "", key: str | None = None) -> dict:
    if max_devices < 1:
        raise ValueError("max_devices must be at least 1")
    now = utcnow()
    expires = now + timedelta(days=days) if days is not None else None
    license_key = (key or new_license_key()).strip()
    with connect() as conn:
        if USE_POSTGRES:
            row = conn.execute(
                "INSERT INTO licenses (license_key, status, max_devices, created_at, expires_at, note) VALUES (%s, 'active', %s, %s, %s, %s) RETURNING *",
                (license_key, max_devices, iso(now), iso(expires), note.strip()),
            ).fetchone()
        else:
            cur = conn.execute(
                "INSERT INTO licenses (license_key, status, max_devices, created_at, expires_at, note) VALUES (?, 'active', ?, ?, ?, ?)",
                (license_key, max_devices, iso(now), iso(expires), note.strip()),
            )
            row = conn.execute("SELECT * FROM licenses WHERE id = ?", (cur.lastrowid,)).fetchone()
    return dict(row)


def list_licenses() -> list[dict]:
    with connect() as conn:
        rows = conn.execute(
            """SELECT l.*, COUNT(d.id) AS device_count
               FROM licenses l LEFT JOIN devices d ON d.license_id = l.id
               GROUP BY l.id ORDER BY l.id DESC"""
        ).fetchall()
    return [dict(row) for row in rows]


def set_license_status(license_id: int, status: str) -> None:
    if status not in {"active", "disabled"}:
        raise ValueError("invalid status")
    with connect() as conn:
        conn.execute(sql("UPDATE licenses SET status = ? WHERE id = ?"), (status, license_id))


def delete_license(license_id: int) -> None:
    with connect() as conn:
        conn.execute(sql("DELETE FROM licenses WHERE id = ?"), (license_id,))


def list_devices() -> list[dict]:
    with connect() as conn:
        rows = conn.execute(
            """SELECT d.*, l.license_key
               FROM devices d JOIN licenses l ON l.id = d.license_id
               ORDER BY d.last_seen_at DESC"""
        ).fetchall()
    return [dict(row) for row in rows]


def unbind_device(device_row_id: int) -> None:
    with connect() as conn:
        conn.execute(sql("DELETE FROM devices WHERE id = ?"), (device_row_id,))


def log_request(license_key: str, device_id: str, platform: str, result: str, payload: str = "") -> None:
    with connect() as conn:
        conn.execute(
            sql("INSERT INTO api_logs (license_key, device_id, platform, result, payload, created_at) VALUES (?, ?, ?, ?, ?, ?)"),
            (license_key, device_id, platform, result, payload[:2000], iso(utcnow())),
        )


def list_logs(limit: int = 200) -> list[dict]:
    limit = max(1, min(limit, 1000))
    with connect() as conn:
        rows = conn.execute(sql("SELECT * FROM api_logs ORDER BY id DESC LIMIT ?"), (limit,)).fetchall()
    return [dict(row) for row in rows]


def verify_and_bind(license_key: str, device_id: str, platform: str) -> tuple[bool, str]:
    key = license_key.strip()
    dev = device_id.strip()
    plat = (platform or "ios").strip().lower()
    if not key or not dev:
        return False, "missing"

    now = utcnow()
    with connect() as conn:
        row = conn.execute(sql("SELECT * FROM licenses WHERE license_key = ?"), (key,)).fetchone()
        if row is None:
            return False, "invalid"
        if row["status"] != "active":
            return False, "disabled"
        if row["expires_at"]:
            try:
                expires = datetime.fromisoformat(row["expires_at"])
            except ValueError:
                return False, "expired"
            if expires <= now:
                return False, "expired"

        existing = conn.execute(
            sql("SELECT id FROM devices WHERE license_id = ? AND device_id = ?"),
            (row["id"], dev),
        ).fetchone()
        if existing:
            conn.execute(
                sql("UPDATE devices SET last_seen_at = ?, platform = ? WHERE id = ?"),
                (iso(now), plat, existing["id"]),
            )
            return True, "ok"

        count_row = conn.execute(sql("SELECT COUNT(*) AS c FROM devices WHERE license_id = ?"), (row["id"],)).fetchone()
        if int(count_row["c"]) >= int(row["max_devices"]):
            return False, "device_limit"

        conn.execute(
            sql("INSERT INTO devices (license_id, device_id, platform, first_seen_at, last_seen_at) VALUES (?, ?, ?, ?, ?)"),
            (row["id"], dev, plat, iso(now), iso(now)),
        )
        return True, "ok"


init_db()
