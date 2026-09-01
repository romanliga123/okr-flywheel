"""
OKR Flywheel — хранилище памяти (Фаза 1).

PostgreSQL (Supabase free tier) — данные сохраняются между деплоями.

Таблицы:
  • team_profiles   — профили команд
  • validation_log  — лог каждой валидации

Требует переменную окружения DATABASE_URL.
"""
import json
import os
import uuid
from datetime import datetime

import psycopg2
import psycopg2.extras

DATABASE_URL = os.getenv("DATABASE_URL", "")


def _connect():
    conn = psycopg2.connect(DATABASE_URL)
    conn.autocommit = False
    return conn


def init_db() -> None:
    """Создать таблицы если не существуют. Вызывается при старте сервера."""
    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                CREATE TABLE IF NOT EXISTS team_profiles (
                    team_id       TEXT PRIMARY KEY,
                    name          TEXT NOT NULL,
                    industry      TEXT DEFAULT '',
                    created_at    TEXT NOT NULL,
                    session_count INTEGER DEFAULT 0
                );
            """)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS validation_log (
                    id            SERIAL PRIMARY KEY,
                    team_id       TEXT NOT NULL,
                    session_id    TEXT NOT NULL,
                    timestamp     TEXT NOT NULL,
                    input_okr     TEXT NOT NULL,
                    score_total   REAL DEFAULT 0.0,
                    top_errors    TEXT DEFAULT '[]',
                    suggestions   TEXT NOT NULL,
                    revised       BOOLEAN DEFAULT FALSE,
                    revised_okr   TEXT DEFAULT '',
                    revised_score REAL DEFAULT 0.0,
                    delta_score   REAL DEFAULT 0.0,
                    FOREIGN KEY (team_id) REFERENCES team_profiles(team_id)
                );
            """)
            cur.execute("""
                CREATE INDEX IF NOT EXISTS idx_vlog_team
                ON validation_log(team_id);
            """)
            cur.execute("""
                CREATE INDEX IF NOT EXISTS idx_vlog_session
                ON validation_log(session_id);
            """)
        conn.commit()


# ── Команды ──────────────────────────────────────────────────────────────────

def get_or_create_team(name: str, industry: str = "") -> dict:
    """Вернуть существующую команду по имени или создать новую."""
    name = name.strip()
    if not name:
        raise ValueError("Имя команды не может быть пустым")

    with _connect() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                "SELECT * FROM team_profiles WHERE LOWER(name) = LOWER(%s)",
                (name,)
            )
            row = cur.fetchone()
            if row:
                return dict(row)

            team_id = str(uuid.uuid4())
            now = datetime.utcnow().isoformat()
            cur.execute(
                """INSERT INTO team_profiles (team_id, name, industry, created_at)
                   VALUES (%s, %s, %s, %s)""",
                (team_id, name, industry.strip(), now)
            )
        conn.commit()

    return {
        "team_id": team_id,
        "name": name,
        "industry": industry.strip(),
        "created_at": now,
        "session_count": 0,
    }


def increment_session_count(team_id: str) -> None:
    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE team_profiles SET session_count = session_count + 1 WHERE team_id = %s",
                (team_id,)
            )
        conn.commit()


# ── Лог валидаций ─────────────────────────────────────────────────────────────

def log_validation(
    team_id: str,
    session_id: str,
    input_okr: str,
    score: float,
    top_errors: list,
    suggestions: str,
) -> int:
    """Записать валидацию. Возвращает id записи для последующей пометки пересдачи."""
    now = datetime.utcnow().isoformat()
    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """INSERT INTO validation_log
                   (team_id, session_id, timestamp, input_okr, score_total, top_errors, suggestions)
                   VALUES (%s, %s, %s, %s, %s, %s, %s)
                   RETURNING id""",
                (
                    team_id, session_id, now,
                    input_okr, round(score, 2),
                    json.dumps(top_errors, ensure_ascii=False),
                    suggestions,
                )
            )
            log_id = cur.fetchone()[0]
        conn.commit()
    return log_id


def mark_revised(log_id: int, revised_okr: str, revised_score: float) -> None:
    """Пометить запись как пересданную и посчитать дельту."""
    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT score_total FROM validation_log WHERE id = %s",
                (log_id,)
            )
            row = cur.fetchone()
            if not row:
                return
            delta = round(revised_score - row[0], 2)
            cur.execute(
                """UPDATE validation_log
                   SET revised=TRUE, revised_okr=%s, revised_score=%s, delta_score=%s
                   WHERE id=%s""",
                (revised_okr, round(revised_score, 2), delta, log_id)
            )
        conn.commit()


# ── Контекст команды для инжекции в промпт ───────────────────────────────────

def get_team_context(team_id: str, limit: int = 5) -> dict:
    """
    Возвращает словарь для инжекции в промпт агента:
      history_count, avg_score, top_errors (list), last_okrs_preview (str)
    """
    with _connect() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                """SELECT score_total, top_errors, input_okr, timestamp
                   FROM validation_log
                   WHERE team_id = %s
                   ORDER BY id DESC
                   LIMIT 20""",
                (team_id,)
            )
            rows = cur.fetchall()

    if not rows:
        return {
            "history_count": 0,
            "avg_score": 0.0,
            "top_errors": [],
            "last_okrs_preview": "",
        }

    scores = [r["score_total"] for r in rows if r["score_total"] > 0]
    avg_score = round(sum(scores) / len(scores), 1) if scores else 0.0

    error_counts: dict = {}
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
    """Сводная статистика по всем командам. Используется /api/admin/metrics."""
    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM validation_log")
            total = cur.fetchone()[0]

            cur.execute("SELECT COUNT(*) FROM validation_log WHERE revised = TRUE")
            revised = cur.fetchone()[0]

            cur.execute("SELECT AVG(delta_score) FROM validation_log WHERE revised = TRUE")
            avg_delta = round(cur.fetchone()[0] or 0.0, 2)

            cur.execute("SELECT AVG(score_total) FROM validation_log WHERE score_total > 0")
            avg_score = round(cur.fetchone()[0] or 0.0, 1)

            cur.execute("SELECT COUNT(*) FROM team_profiles")
            teams_count = cur.fetchone()[0]

            cur.execute("SELECT top_errors FROM validation_log WHERE top_errors != '[]'")
            error_rows = cur.fetchall()

    error_counts: dict = {}
    for (raw,) in error_rows:
        try:
            errs = json.loads(raw)
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
