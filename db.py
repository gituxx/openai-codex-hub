"""数据库层 - SQLite 账号管理 + 流量日志 + Token 统计"""
import sqlite3
import time
from pathlib import Path
from typing import Optional

DB_PATH = Path(__file__).parent / "data" / "hub.db"

def get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    DB_PATH.parent.mkdir(exist_ok=True)
    with get_conn() as conn:
        conn.executescript("""
        CREATE TABLE IF NOT EXISTS accounts (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            email         TEXT UNIQUE,
            access        TEXT NOT NULL,
            refresh       TEXT NOT NULL,
            expires       INTEGER NOT NULL,
            status        TEXT DEFAULT 'active',
            disabled      INTEGER DEFAULT 0,
            last_used     INTEGER DEFAULT 0,
            last_error    TEXT,
            use_count     INTEGER DEFAULT 0,
            input_tokens  INTEGER DEFAULT 0,
            output_tokens INTEGER DEFAULT 0,
            created_at    INTEGER DEFAULT (strftime('%s','now'))
        );

        CREATE TABLE IF NOT EXISTS request_logs (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            account_id    INTEGER,
            account_email TEXT,
            model         TEXT,
            input_tokens  INTEGER DEFAULT 0,
            output_tokens INTEGER DEFAULT 0,
            status        TEXT DEFAULT 'ok',
            error_msg     TEXT,
            latency_ms    INTEGER DEFAULT 0,
            created_at    INTEGER DEFAULT (strftime('%s','now'))
        );

        CREATE TABLE IF NOT EXISTS settings (
            key   TEXT PRIMARY KEY,
            value TEXT
        );

        INSERT OR IGNORE INTO settings VALUES ('api_key', 'sk-codex-hub-2025');
        INSERT OR IGNORE INTO settings VALUES ('rate_limit_per_hour', '50');
        """)
        # 迁移旧表缺失列
        _migrate(conn)

def _migrate(conn):
    existing = [r[1] for r in conn.execute("PRAGMA table_info(accounts)").fetchall()]
    for col, ddl in [
        ("input_tokens",  "ALTER TABLE accounts ADD COLUMN input_tokens INTEGER DEFAULT 0"),
        ("output_tokens", "ALTER TABLE accounts ADD COLUMN output_tokens INTEGER DEFAULT 0"),
        ("disabled",      "ALTER TABLE accounts ADD COLUMN disabled INTEGER DEFAULT 0"),
    ]:
        if col not in existing:
            conn.execute(ddl)

def get_setting(key: str) -> Optional[str]:
    with get_conn() as conn:
        row = conn.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
        return row["value"] if row else None

def set_setting(key: str, value: str):
    with get_conn() as conn:
        conn.execute("INSERT OR REPLACE INTO settings VALUES (?,?)", (key, value))

def add_account(email: str, access: str, refresh: str, expires: int) -> int:
    with get_conn() as conn:
        cur = conn.execute(
            "INSERT OR REPLACE INTO accounts (email,access,refresh,expires,status,disabled) VALUES (?,?,?,?,'active',0)",
            (email, access, refresh, expires)
        )
        return cur.lastrowid

def get_all_accounts():
    with get_conn() as conn:
        return [dict(r) for r in conn.execute(
            "SELECT id,email,expires,status,disabled,last_used,use_count,last_error,input_tokens,output_tokens FROM accounts ORDER BY id"
        ).fetchall()]

def get_active_accounts():
    with get_conn() as conn:
        return [dict(r) for r in conn.execute(
            "SELECT * FROM accounts WHERE status='active' AND disabled=0 ORDER BY last_used ASC"
        ).fetchall()]

def update_account_tokens(account_id: int, access: str, refresh: str, expires: int):
    with get_conn() as conn:
        conn.execute(
            "UPDATE accounts SET access=?,refresh=?,expires=?,status='active',last_error=NULL WHERE id=?",
            (access, refresh, expires, account_id)
        )

def mark_account_used(account_id: int):
    with get_conn() as conn:
        conn.execute(
            "UPDATE accounts SET last_used=?,use_count=use_count+1 WHERE id=?",
            (int(time.time()), account_id)
        )

def mark_account_error(account_id: int, error: str):
    with get_conn() as conn:
        conn.execute(
            "UPDATE accounts SET status='error',last_error=? WHERE id=?",
            (error[:500], account_id)
        )

def set_account_disabled(account_id: int, disabled: bool):
    with get_conn() as conn:
        conn.execute("UPDATE accounts SET disabled=? WHERE id=?", (1 if disabled else 0, account_id))

def delete_account(account_id: int):
    with get_conn() as conn:
        conn.execute("DELETE FROM accounts WHERE id=?", (account_id,))

def reset_account_status(account_id: int):
    with get_conn() as conn:
        conn.execute("UPDATE accounts SET status='active',last_error=NULL WHERE id=?", (account_id,))

def log_request(account_id: int, email: str, model: str,
                input_tokens: int, output_tokens: int,
                status: str, error_msg: str, latency_ms: int):
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO request_logs (account_id,account_email,model,input_tokens,output_tokens,status,error_msg,latency_ms) VALUES (?,?,?,?,?,?,?,?)",
            (account_id, email, model, input_tokens, output_tokens, status, error_msg, latency_ms)
        )
        if status == "ok":
            conn.execute(
                "UPDATE accounts SET input_tokens=input_tokens+?,output_tokens=output_tokens+? WHERE id=?",
                (input_tokens, output_tokens, account_id)
            )

def get_logs(limit: int = 100, offset: int = 0):
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM request_logs ORDER BY created_at DESC LIMIT ? OFFSET ?",
            (limit, offset)
        ).fetchall()
        total = conn.execute("SELECT COUNT(*) FROM request_logs").fetchone()[0]
        return [dict(r) for r in rows], total

def get_stats():
    with get_conn() as conn:
        total_req = conn.execute("SELECT COUNT(*) FROM request_logs").fetchone()[0]
        ok_req    = conn.execute("SELECT COUNT(*) FROM request_logs WHERE status='ok'").fetchone()[0]
        total_in  = conn.execute("SELECT COALESCE(SUM(input_tokens),0) FROM request_logs").fetchone()[0]
        total_out = conn.execute("SELECT COALESCE(SUM(output_tokens),0) FROM request_logs").fetchone()[0]
        today     = int(time.mktime(time.localtime()[:3] + (0,0,0,0,0,0)))
        today_req = conn.execute("SELECT COUNT(*) FROM request_logs WHERE created_at>=?", (today,)).fetchone()[0]
        models    = conn.execute(
            "SELECT model, COUNT(*) as cnt FROM request_logs GROUP BY model ORDER BY cnt DESC LIMIT 5"
        ).fetchall()
        return {
            "total_requests":    total_req,
            "ok_requests":       ok_req,
            "error_requests":    total_req - ok_req,
            "total_input_tokens":  total_in,
            "total_output_tokens": total_out,
            "today_requests":    today_req,
            "top_models": [{"model": r["model"], "count": r["cnt"]} for r in models],
        }

def get_account_model_stats(account_id: int) -> list:
    """某账号各模型的调用次数 + token 统计"""
    with get_conn() as conn:
        rows = conn.execute("""
            SELECT model,
                   COUNT(*) as calls,
                   SUM(input_tokens) as input_tokens,
                   SUM(output_tokens) as output_tokens,
                   SUM(CASE WHEN status='error' THEN 1 ELSE 0 END) as errors
            FROM request_logs
            WHERE account_id=?
            GROUP BY model
            ORDER BY calls DESC
        """, (account_id,)).fetchall()
        return [dict(r) for r in rows]

def export_accounts(ids: list = None) -> list:
    with get_conn() as conn:
        if ids:
            placeholders = ",".join("?" * len(ids))
            rows = conn.execute(
                f"SELECT email,access,refresh,expires FROM accounts WHERE id IN ({placeholders})", ids
            ).fetchall()
        else:
            rows = conn.execute("SELECT email,access,refresh,expires FROM accounts").fetchall()
        return [dict(r) for r in rows]

def import_accounts_batch(accounts: list) -> int:
    count = 0
    for a in accounts:
        try:
            add_account(a["email"], a["access"], a["refresh"], int(a["expires"]))
            count += 1
        except Exception:
            pass
    return count
