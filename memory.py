"""
OKR Flywheel — хранилище памяти (Фаза 1).

SQLite-база с двумя таблицами:
  • team_profiles   — профили команд
  • validation_log  — лог каждой валидации

Используется agent_loop.py (log_validation, get_team_context)
и web/server.py (get_or_create_team, get_metrics).
"""
import json
import sqlite3
import uuid
from datetime import datetime
from pathlib import Path

DB_PATH = Path(__file__).parent / "okr_memory.db"


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(str(DB_PATH), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    """Создать таблицы если не существуют. Вызывается при старте сервера."""
    with _connect() as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS team_profiles (
                team_id      TEXT PRIMARY KEY,
                name         TEXT NOT NULL,
                industry     TEXT DEFAULT '',
                created_at   TEXT NOT NULL,
                session_count INTEGER DEFAULT 0
            );

            CREATE TABLE IF NOT EXISTS validation_log (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                team_id      TEXT NOT NULL,
                session_id   TEXT NOT NULL,
                timestamp    TEXT NOT NULL,
                input_okr    TEXT NOT NULL,
                score_total  REAL DEFAULT 0.0,
                top_errors   TEXT DEFAULT '[]',
                suggestions  TEXT NOT NULL,
                revised      INTEGER DEFAULT 0,
                revised_okr  TEXT DEFAULT '',
                revised_score REAL DEFAULT 0.0,
                delta_score  REAL DEFAULT 0.0,
                FOREIGN KEY (team_id) REFERENCES team_profiles(team_id)
            );

            CREATE INDEX IF NOT EXISTS idx_vlog_team ON validation_log(team_id);
            CREATE INDEX IF NOT EXISTS idx_vlog_session ON validation_log(session_id);
        """)


# ── Команды ──────────────────────────────────────────────────────────────────

def get_or_create_team(name: str, industry: str = "") -> dict:
    """Вернуть существующую команду по имени или создать новую."""
    name = name.strip()
    if not name:
        raise ValueError("Имя команды не может быть пустым")

    with _connect() as conn:
        row = conn.execute(
            "SELECT * FROM team_profiles WHERE name = ? COLLATE NOCASE",
            (name,)
        ).fetchone()

        if row:
            return dict(row)

        team_id = str(uuid.uuid4())
        now = datetime.utcnow().isoformat()
        conn.execute(
            "INSERT INTO team_profiles (team_id, name, industry, created_at) VALUES (?, ?, ?, ?)",
            (team_id, name, industry.strip(), now)
        )
        return {
            "team_id": team_id,
            "name": name,
            "industry": industry.strip(),
            "created_at": now,
            "session_count": 0,
        }


def increment_session_count(team_id: str) -> None:
    with _connect() as conn:
        conn.execute(
            "UPDATE team_profiles SET session_count = session_count + 1 WHERE team_id = ?",
            (team_id,)
        )


# ── Лог валидаций ─────────────────────────────────────────────────────────────

def log_validation(
    team_id: str,
    session_id: str,
    input_okr: str,
    score: float,
    top_errors: list[str],
    suggestions: str,
) -> int:
    """Записать валидацию. Возвращает id записи для последующей пометки пересдачи."""
    now = datetime.utcnow().isoformat()
    with _connect() as conn:
        cur = conn.execute(
            """INSERT INTO validation_log
               (team_id, session_id, timestamp, input_okr, score_total, top_errors, suggestions)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (
                team_id, session_id, now,
                input_okr, round(score, 2),
                json.dumps(top_errors, ensure_ascii=False),
                suggestions,
            )
        )
        return cur.lastrowid


def mark_revised(log_id: int, revised_okr: str, revised_score: float) -> None:
    """Пометить запись как пересданную и посчитать дельту."""
    with _connect() as conn:
        row = conn.execute(
            "SELECT score_total FROM validation_log WHERE id = ?", (log_id,)
        ).fetchone()
        if not row:
            return
        delta = round(revised_score - row["score_total"], 2)
        conn.execute(
            """UPDATE validation_log
               SET revised=1, revised_okr=?, revised_score=?, delta_score=?
               WHERE id=?""",
            (revised_okr, round(revised_score, 2), delta, log_id)
        )


# ── Контекст команды для инжекции в промпт ───────────────────────────────────

def get_team_context(team_id: str, limit: int = 5) -> dict:
    """
    Возвращает словарь для инжекции в промпт агента:
      history_count, avg_score, top_errors (list), last_okrs_preview (str)
    """
    with _connect() as conn:
        rows = conn.execute(
            """SELECT score_total, top_errors, input_okr, timestamp
               FROM validation_log
               WHERE team_id = ?
               ORDER BY id DESC
               LIMIT 20""",
            (team_id,)
        ).fetchall()

    if not rows:
        return {
            "history_count": 0,
            "avg_score": 0.0,
            "top_errors": [],
            "last_okrs_preview": "",
        }

    scores = [r["score_total"] for r in rows if r["score_total"] > 0]
    avg_score = round(sum(scores) / len(scores), 1) if scores else 0.0

    # Считаем частоту ошибок
    error_counts: dict[str, int] = {}
    for r in rows:
        try:
            errs = json.loads(r["top_errors"] or "[]")
        except Exception:
            errs = []
        for e in errs:
            error_counts[e] = error_counts.get(e, 0) + 1
    top_errors = sorted(error_counts, key=lambda k: -error_counts[k])[:3]

    last_okrs = [r["input_okr"][:80].replace("\n", " ") for r in rows[:limit]]
    last_okrs_preview = " | ".join(last_okrs)

    return {
        "history_count": len(rows),
        "avg_score": avg_score,
        "top_errors": top_errors,
        "last_okrs_preview": last_okrs_preview,
    }


# ── Агрегированные метрики для Roman ─────────────────────────────────────────

def get_metrics() -> dict:
    """
    Сводная статистика по всем командам.
    Используется /api/admin/metrics.
    """
    with _connect() as conn:
        total = conn.execute(
            "SELECT COUNT(*) as cnt FROM validation_log"
        ).fetchone()["cnt"]

        revised = conn.execute(
            "SELECT COUNT(*) as cnt FROM validation_log WHERE revised = 1"
        ).fetchone()["cnt"]

        avg_delta_row = conn.execute(
            "SELECT AVG(delta_score) as avg FROM validation_log WHERE revised = 1"
        ).fetchone()
        avg_delta = round(avg_delta_row["avg"] or 0.0, 2)

        avg_score_row = conn.execute(
            "SELECT AVG(score_total) as avg FROM validation_log WHERE score_total > 0"
        ).fetchone()
        avg_score = round(avg_score_row["avg"] or 0.0, 1)

        teams_count = conn.execute(
            "SELECT COUNT(*) as cnt FROM team_profiles"
        ).fetchone()["cnt"]

        # Топ ошибок глобально
        error_rows = conn.execute(
            "SELECT top_errors FROM validation_log WHERE top_errors != '[]'"
        ).fetchall()

    error_counts: dict[str, int] = {}
    for r in error_rows:
        try:
            errs = json.loads(r["top_errors"])
        except Exception:
            continue
        for e in errs:
            error_counts[e] = error_counts.get(e, 0) + 1

    top_errors_global = [
        {"code": code, "count": cnt}
        for code, cnt in sorted(error_counts.items(), key=lambda x: -x[1])[:10]
    ]

    revision_rate = round(revised / total * 100, 1) if total > 0 else 0.0

    return {
        "total_validations": total,
        "teams_count": teams_count,
        "revision_rate_pct": revision_rate,
        "avg_delta_score": avg_delta,
        "avg_score": avg_score,
        "top_errors_global": top_errors_global,
    }
