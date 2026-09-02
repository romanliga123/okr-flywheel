"""
OKR Flywheel — хранилище памяти (Фаза 1).

PostgreSQL (Supabase free tier) — данные сохраняются между деплоями.

Таблицы:
  • team_profiles      — профили команд (отрасль, уровень OKR, опыт)
  • validation_log     — лог каждой валидации + feedback 👍/👎
  • quarterly_context  — стратегические приоритеты квартала
  • quarter_results    — итоги квартала по KR (закрытие петли)

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
    """Создать/мигрировать таблицы. Вызывается при старте сервера."""
    with _connect() as conn:
        with conn.cursor() as cur:
            # ── team_profiles ────────────────────────────────────────────
            cur.execute("""
                CREATE TABLE IF NOT EXISTS team_profiles (
                    team_id       TEXT PRIMARY KEY,
                    name          TEXT NOT NULL,
                    industry      TEXT DEFAULT '',
                    created_at    TEXT NOT NULL,
                    session_count INTEGER DEFAULT 0
                );
            """)
            # Миграция: добавить новые колонки если их нет
            cur.execute("ALTER TABLE team_profiles ADD COLUMN IF NOT EXISTS okr_level TEXT DEFAULT '';")
            cur.execute("ALTER TABLE team_profiles ADD COLUMN IF NOT EXISTS okr_experience INTEGER DEFAULT 0;")

            # ── validation_log ───────────────────────────────────────────
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
            # Миграция: колонка feedback (1=👍, -1=👎, NULL=нет оценки)
            cur.execute("ALTER TABLE validation_log ADD COLUMN IF NOT EXISTS feedback SMALLINT DEFAULT NULL;")

            cur.execute("CREATE INDEX IF NOT EXISTS idx_vlog_team ON validation_log(team_id);")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_vlog_session ON validation_log(session_id);")

            # ── quarterly_context ────────────────────────────────────────
            cur.execute("""
                CREATE TABLE IF NOT EXISTS quarterly_context (
                    quarter              TEXT PRIMARY KEY,
                    strategic_priorities TEXT NOT NULL,
                    created_at           TEXT NOT NULL
                );
            """)

            # ── quarter_results ──────────────────────────────────────────
            cur.execute("""
                CREATE TABLE IF NOT EXISTS quarter_results (
                    id           SERIAL PRIMARY KEY,
                    team_id      TEXT NOT NULL,
                    quarter      TEXT NOT NULL,
                    kr_results   TEXT NOT NULL,
                    submitted_at TEXT NOT NULL,
                    FOREIGN KEY (team_id) REFERENCES team_profiles(team_id)
                );
            """)
            cur.execute("CREATE INDEX IF NOT EXISTS idx_qr_team ON quarter_results(team_id);")

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
        "okr_level": "",
        "okr_experience": 0,
    }


def update_team_profile(team_id: str, industry: str = None,
                        okr_level: str = None, okr_experience: int = None) -> None:
    """Обновить профиль команды (отрасль, уровень OKR, опыт)."""
    fields, values = [], []
    if industry is not None:
        fields.append("industry = %s"); values.append(industry.strip())
    if okr_level is not None:
        fields.append("okr_level = %s"); values.append(okr_level.strip())
    if okr_experience is not None:
        fields.append("okr_experience = %s"); values.append(int(okr_experience))
    if not fields:
        return
    values.append(team_id)
    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                f"UPDATE team_profiles SET {', '.join(fields)} WHERE team_id = %s",
                values
            )
        conn.commit()


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
    """Записать валидацию. Возвращает id записи."""
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


def log_feedback(log_id: int, score: int) -> None:
    """Записать оценку полезности совета: 1 = 👍, -1 = 👎."""
    if score not in (1, -1):
        return
    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE validation_log SET feedback = %s WHERE id = %s",
                (score, log_id)
            )
        conn.commit()


# ── Квартальный контекст (стратегические приоритеты) ─────────────────────────

def set_quarterly_context(quarter: str, strategic_priorities: str) -> None:
    """Создать или обновить приоритеты квартала."""
    now = datetime.utcnow().isoformat()
    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """INSERT INTO quarterly_context (quarter, strategic_priorities, created_at)
                   VALUES (%s, %s, %s)
                   ON CONFLICT (quarter) DO UPDATE
                   SET strategic_priorities = EXCLUDED.strategic_priorities,
                       created_at = EXCLUDED.created_at""",
                (quarter.strip(), strategic_priorities.strip(), now)
            )
        conn.commit()


def get_quarterly_context(quarter: str = None) -> dict | None:
    """Вернуть контекст квартала. Без quarter — возвращает самый свежий."""
    with _connect() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            if quarter:
                cur.execute(
                    "SELECT * FROM quarterly_context WHERE quarter = %s",
                    (quarter,)
                )
            else:
                cur.execute(
                    "SELECT * FROM quarterly_context ORDER BY created_at DESC LIMIT 1"
                )
            row = cur.fetchone()
            return dict(row) if row else None


# ── Итоги квартала (закрытие петли) ──────────────────────────────────────────

def log_quarter_results(team_id: str, quarter: str, kr_results: list) -> int:
    """
    Сохранить итоги квартала.
    kr_results: [{"kr_text": str, "achievement_pct": int, "notes": str}, ...]
    """
    now = datetime.utcnow().isoformat()
    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """INSERT INTO quarter_results (team_id, quarter, kr_results, submitted_at)
                   VALUES (%s, %s, %s, %s)
                   RETURNING id""",
                (team_id, quarter.strip(), json.dumps(kr_results, ensure_ascii=False), now)
            )
            row_id = cur.fetchone()[0]
        conn.commit()
    return row_id


def get_team_quarter_results(team_id: str) -> list:
    """Вернуть все итоги квартала для команды."""
    with _connect() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                "SELECT * FROM quarter_results WHERE team_id = %s ORDER BY submitted_at DESC",
                (team_id,)
            )
            rows = cur.fetchall()
    result = []
    for r in rows:
        d = dict(r)
        try:
            d["kr_results"] = json.loads(d["kr_results"])
        except Exception:
            d["kr_results"] = []
        result.append(d)
    return result


# ── Контекст команды для инжекции в промпт ───────────────────────────────────

def get_team_context(team_id: str, limit: int = 5) -> dict:
    """
    Возвращает словарь для инжекции в промпт агента:
      history_count, avg_score, top_errors (list), last_okrs_preview (str),
      feedback_positive_pct (float)
    """
    with _connect() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                """SELECT score_total, top_errors, input_okr, timestamp, feedback
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
            "feedback_positive_pct": None,
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

    feedbacks = [r["feedback"] for r in rows if r["feedback"] is not None]
    if feedbacks:
        positive = sum(1 for f in feedbacks if f == 1)
        feedback_positive_pct = round(positive / len(feedbacks) * 100)
    else:
        feedback_positive_pct = None

    return {
        "history_count": len(rows),
        "avg_score": avg_score,
        "top_errors": top_errors,
        "last_okrs_preview": last_okrs_preview,
        "feedback_positive_pct": feedback_positive_pct,
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

            # Feedback stats
            cur.execute("SELECT COUNT(*) FROM validation_log WHERE feedback IS NOT NULL")
            feedback_total = cur.fetchone()[0]

            cur.execute("SELECT COUNT(*) FROM validation_log WHERE feedback = 1")
            feedback_positive = cur.fetchone()[0]

            cur.execute("SELECT COUNT(*) FROM quarter_results")
            quarter_results_count = cur.fetchone()[0]

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
    feedback_rate = round(feedback_positive / feedback_total * 100, 1) if feedback_total > 0 else None

    return {
        "total_validations": total,
        "teams_count": teams_count,
        "revision_rate_pct": revision_rate,
        "avg_delta_score": avg_delta,
        "avg_score": avg_score,
        "top_errors_global": top_errors_global,
        "feedback_total": feedback_total,
        "feedback_positive_pct": feedback_rate,
        "quarter_results_submitted": quarter_results_count,
    }
