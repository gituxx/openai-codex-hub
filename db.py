"""数据库层 - SQLite 账号管理 + 流量日志 + Token 统计 + 冷却机制"""
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
            cooldown_until INTEGER DEFAULT 0,
            error_count   INTEGER DEFAULT 0,
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
        _migrate(conn)

def _migrate(conn):
    existing = [r[1] for r in conn.execute("PRAGMA table_info(accounts)").fetchall()]
    for col, ddl in [
        ("input_tokens",   "ALTER TABLE accounts ADD COLUMN input_tokens INTEGER DEFAULT 0"),
        ("output_tokens",  "ALTER TABLE accounts ADD COLUMN output_tokens INTEGER DEFAULT 0"),
        ("disabled",       "ALTER TABLE accounts ADD COLUMN disabled INTEGER DEFAULT 0"),
        ("cooldown_until", "ALTER TABLE accounts ADD COLUMN cooldown_until INTEGER DEFAULT 0"),
        ("error_count",    "ALTER TABLE accounts ADD COLUMN error_count INTEGER DEFAULT 0"),
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
            "INSERT OR REPLACE INTO accounts (email,access,refresh,expires,status,disabled,cooldown_until,error_count) VALUES (?,?,?,?,'active',0,0,0)",
            (email, access, refresh, expires)
        )
        return cur.lastrowid

def get_all_accounts():
    with get_conn() as conn:
        return [dict(r) for r in conn.execute(
            "SELECT id,email,expires,status,disabled,last_used,use_count,last_error,input_tokens,output_tokens,cooldown_until,error_count FROM accounts ORDER BY id"
        ).fetchall()]

def get_active_accounts():
    """获取可用账号：active + 未禁用 + 不在冷却中，按 error_count 升序 + last_used 升序"""
    now = int(time.time())
    with get_conn() as conn:
        return [dict(r) for r in conn.execute(
            "SELECT * FROM accounts WHERE status='active' AND disabled=0 AND cooldown_until<=? ORDER BY error_count ASC, last_used ASC",
            (now,)
        ).fetchall()]

def get_available_accounts_excluding(exclude_ids: list):
    """获取可用账号，排除指定 ID 列表"""
    now = int(time.time())
    with get_conn() as conn:
        if not exclude_ids:
            return [dict(r) for r in conn.execute(
                "SELECT * FROM accounts WHERE status='active' AND disabled=0 AND cooldown_until<=? ORDER BY error_count ASC, last_used ASC",
                (now,)
            ).fetchall()]
        placeholders = ",".join("?" * len(exclude_ids))
        return [dict(r) for r in conn.execute(
            f"SELECT * FROM accounts WHERE status='active' AND disabled=0 AND cooldown_until<=? AND id NOT IN ({placeholders}) ORDER BY error_count ASC, last_used ASC",
            [now] + exclude_ids
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

def mark_account_success(account_id: int):
    """请求成功：重置连续错误计数"""
    with get_conn() as conn:
        conn.execute(
            "UPDATE accounts SET error_count=0,last_error=NULL WHERE id=?",
            (account_id,)
        )

def mark_account_error(account_id: int, error: str):
    with get_conn() as conn:
        conn.execute(
            "UPDATE accounts SET status='error',last_error=? WHERE id=?",
            (error[:500], account_id)
        )

def mark_account_cooldown(account_id: int, error: str, cooldown_seconds: int):
    """账号进入冷却：不标 error，到期自动可用"""
    now = int(time.time())
    with get_conn() as conn:
        conn.execute(
            "UPDATE accounts SET cooldown_until=?,error_count=error_count+1,last_error=? WHERE id=?",
            (now + cooldown_seconds, error[:500], account_id)
        )

def set_account_disabled(account_id: int, disabled: bool):
    with get_conn() as conn:
        conn.execute("UPDATE accounts SET disabled=? WHERE id=?", (1 if disabled else 0, account_id))

def delete_account(account_id: int):
    with get_conn() as conn:
        conn.execute("DELETE FROM accounts WHERE id=?", (account_id,))

def reset_account_status(account_id: int):
    with get_conn() as conn:
        conn.execute("UPDATE accounts SET status='active',last_error=NULL,cooldown_until=0,error_count=0 WHERE id=?", (account_id,))

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

def get_health_stats() -> dict:
    """实时健康统计：近5分钟/近1小时请求情况 + 账号状态"""
    now = int(time.time())
    t5 = now - 300
    t1h = now - 3600

    with get_conn() as conn:
        # --- 近5分钟 ---
        rows_5 = conn.execute(
            "SELECT COUNT(*) as total,"
            " SUM(CASE WHEN status='ok' THEN 1 ELSE 0 END) as ok,"
            " SUM(CASE WHEN status!='ok' THEN 1 ELSE 0 END) as errors,"
            " SUM(CASE WHEN error_msg LIKE '%HTTP 429%' THEN 1 ELSE 0 END) as s429,"
            " SUM(CASE WHEN error_msg LIKE '%HTTP 401%' THEN 1 ELSE 0 END) as s401,"
            " SUM(CASE WHEN error_msg LIKE '%HTTP 5%' THEN 1 ELSE 0 END) as s5xx,"
            " AVG(latency_ms) as avg_lat"
            " FROM request_logs WHERE created_at >= ?",
            (t5,)
        ).fetchone()

        # --- 近1小时 ---
        rows_1h = conn.execute(
            "SELECT COUNT(*) as total,"
            " SUM(CASE WHEN status='ok' THEN 1 ELSE 0 END) as ok,"
            " SUM(CASE WHEN status!='ok' THEN 1 ELSE 0 END) as errors,"
            " SUM(CASE WHEN error_msg LIKE '%HTTP 429%' THEN 1 ELSE 0 END) as s429,"
            " SUM(CASE WHEN error_msg LIKE '%HTTP 401%' THEN 1 ELSE 0 END) as s401,"
            " SUM(CASE WHEN error_msg LIKE '%HTTP 5%' THEN 1 ELSE 0 END) as s5xx,"
            " AVG(latency_ms) as avg_lat"
            " FROM request_logs WHERE created_at >= ?",
            (t1h,)
        ).fetchone()

        # --- 账号状态 ---
        acct_active = conn.execute(
            "SELECT COUNT(*) FROM accounts WHERE status='active' AND disabled=0 AND cooldown_until<=?",
            (now,)
        ).fetchone()[0]
        acct_cooling = conn.execute(
            "SELECT COUNT(*) FROM accounts WHERE status='active' AND disabled=0 AND cooldown_until>?",
            (now,)
        ).fetchone()[0]
        acct_error = conn.execute(
            "SELECT COUNT(*) FROM accounts WHERE status='error' AND disabled=0"
        ).fetchone()[0]
        acct_disabled = conn.execute(
            "SELECT COUNT(*) FROM accounts WHERE disabled=1"
        ).fetchone()[0]

    def _build_period(row):
        total = row["total"] or 0
        ok = row["ok"] or 0
        errors = row["errors"] or 0
        rate = round(errors / total * 100, 1) if total > 0 else 0.0
        return {
            "total": total,
            "ok": ok,
            "errors": errors,
            "error_rate": f"{rate}%",
            "status_429": row["s429"] or 0,
            "status_401": row["s401"] or 0,
            "status_5xx": row["s5xx"] or 0,
            "avg_latency_ms": int(row["avg_lat"] or 0),
        }

    return {
        "last_5min": _build_period(rows_5),
        "last_1h": _build_period(rows_1h),
        "accounts_status": {
            "active": acct_active,
            "cooling": acct_cooling,
            "error": acct_error,
            "disabled": acct_disabled,
        },
    }


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
