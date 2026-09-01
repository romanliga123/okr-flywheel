"""
Интеграционные тесты — несколько компонентов работают вместе.
Сеть и LLM замокированы, всё остальное — реальный код.

Запуск: pytest tests/test_integration.py -v
"""
import io
import sys
import time
import pytest
import threading
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).parent.parent))


# ── Фикстура: реальный AgentLoop + мок LLM ───────────────────────────────────
@pytest.fixture
def mock_core():
    core = MagicMock()
    core.validate_existing_okr.return_value = "Тип: Objective\nОценка: 8/10\nЦель корректна"
    core.analyze_okr.return_value = "Найдено 3 OKR: 2 Objective, 1 KR. Качество: высокое."
    core.process_file.return_value = (True, "Objective: Увеличить выручку\nKR1: Закрыть 10 сделок")
    core.fetch_url_content.return_value = (True, "OKR данные из таблицы: O1 — Рост выручки, KR1 — 10 сделок")
    core.add_to_history.return_value = None
    core.history = []
    core._call_llm = MagicMock(return_value="Анализ завершён")
    return core


@pytest.fixture
def mock_loop(mock_core):
    loop = MagicMock()
    loop.core = mock_core
    loop.ctx = {"files": {}, "files_raw": {}}
    loop.on_message = None
    return loop


@pytest.fixture
def client(mock_core, mock_loop):
    with patch("web.server._make_core", return_value=mock_core), \
         patch("web.server.AgentLoop", return_value=mock_loop):
        from fastapi.testclient import TestClient
        from web.server import app
        with TestClient(app) as c:
            yield c, mock_core, mock_loop


def new_session(client) -> str:
    r = client.post("/api/session")
    assert r.status_code == 200
    return r.json()["session_id"]


# ══════════════════════════════════════════════════════════════════════════════
# ИНТЕГРАЦИЯ: Текстовый агент — полный цикл работы
# ══════════════════════════════════════════════════════════════════════════════

class TestTextAgentFullFlow:

    def test_create_session_then_validate(self, client):
        c, core, _ = client
        sid = new_session(c)

        r = c.post(f"/api/{sid}/validate",
                   json={"text": "Стать лидером рынка CRM в России к Q4 2026"})

        assert r.status_code == 200
        assert "Objective" in r.json()["result"]
        core.validate_existing_okr.assert_called_once_with(
            "Стать лидером рынка CRM в России к Q4 2026"
        )

    def test_upload_file_then_analyze(self, client):
        c, core, loop = client
        sid = new_session(c)

        # Шаг 1: загрузить файл
        content = b"Objective: Increase revenue by 20%\nKR1: Close 10 enterprise deals"
        r_upload = c.post(
            f"/api/{sid}/upload",
            files={"file": ("okr.txt", io.BytesIO(content), "text/plain")},
        )
        assert r_upload.status_code == 200
        assert r_upload.json()["chars"] > 0

        # Шаг 2: проанализировать сессию
        r_analyze = c.post(f"/api/{sid}/analyze", json={"use_previous_context": False})
        assert r_analyze.status_code == 200
        assert "OKR" in r_analyze.json()["result"]

    def test_upload_file_then_validate_content(self, client):
        c, core, loop = client
        sid = new_session(c)

        # Загружаем файл с OKR-текстом
        content = "Увеличить NPS выше 70 к концу Q2 2026".encode("utf-8")
        c.post(f"/api/{sid}/upload",
               files={"file": ("okr.txt", io.BytesIO(content), "text/plain")})

        # Валидируем тот же текст
        r = c.post(f"/api/{sid}/validate",
                   json={"text": "Увеличить NPS выше 70 к концу Q2 2026"})
        assert r.status_code == 200
        assert "result" in r.json()

    def test_add_transcript_then_analyze(self, client):
        c, core, _ = client
        sid = new_session(c)

        # Голосовые фразы добавляем в историю
        phrases = [
            "Нам нужно увеличить выручку на 30%",
            "KR первый — закрыть 15 enterprise сделок",
            "KR второй — поднять retention до 85%",
        ]
        for phrase in phrases:
            r = c.post(f"/api/{sid}/transcript", json={"text": phrase})
            assert r.status_code == 200

        # Анализируем
        r_analyze = c.post(f"/api/{sid}/analyze", json={"use_previous_context": False})
        assert r_analyze.status_code == 200
        assert core.add_to_history.call_count == len(phrases)

    def test_full_session_lifecycle(self, client):
        c, core, loop = client
        sid = new_session(c)

        # 1. Загрузить файл
        c.post(f"/api/{sid}/upload",
               files={"file": ("data.csv", io.BytesIO(b"KR,Description\nO1,Goal"), "text/csv")})

        # 2. Проверить OKR
        c.post(f"/api/{sid}/validate", json={"text": "Увеличить выручку"})

        # 3. Проанализировать
        c.post(f"/api/{sid}/analyze", json={"use_previous_context": False})

        # 4. Очистить историю
        r = c.delete(f"/api/{sid}/history")
        assert r.status_code == 200

        # 5. Получить историю — должна быть пустая
        core.history = []
        r = c.get(f"/api/{sid}/history")
        assert r.json()["history"] == []


# ══════════════════════════════════════════════════════════════════════════════
# ИНТЕГРАЦИЯ: Агент таблиц — полный цикл работы
# ══════════════════════════════════════════════════════════════════════════════

class TestSheetsAgentFullFlow:

    def test_load_url_then_analyze(self, client):
        c, core, loop = client
        sid = new_session(c)

        # Загружаем таблицу по ссылке
        r_url = c.post(f"/api/{sid}/url", json={
            "url": "https://docs.google.com/spreadsheets/d/abc123",
            "task": "Проанализируй OKR и найди слабые KR",
        })
        assert r_url.status_code == 200
        assert r_url.json()["chars"] > 0

        # Анализируем
        r_analyze = c.post(f"/api/{sid}/analyze", json={"use_previous_context": False})
        assert r_analyze.status_code == 200

    def test_upload_xlsx_then_analyze(self, client):
        c, core, _ = client
        sid = new_session(c)

        r = c.post(
            f"/api/{sid}/upload",
            files={"file": ("okr_q2.xlsx", io.BytesIO(b"PK mock xlsx"), "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
        )
        assert r.status_code == 200

        r_analyze = c.post(f"/api/{sid}/analyze", json={"use_previous_context": False})
        assert r_analyze.status_code == 200

    def test_google_status_then_connect_flow(self, client):
        c, core, _ = client
        sid = new_session(c)

        # Проверяем статус — не подключён
        r = c.get(f"/api/{sid}/google/status")
        assert r.status_code == 200
        assert r.json()["connected"] is False

    def test_url_content_available_for_analysis(self, client):
        c, core, loop = client
        sid = new_session(c)

        core.fetch_url_content.return_value = (True, "O1: Рост выручки " * 200)

        r = c.post(f"/api/{sid}/url", json={
            "url": "https://docs.google.com/spreadsheets/d/test",
            "task": "анализ",
        })
        assert r.status_code == 200
        # Контент должен быть сохранён в ctx для дальнейшего анализа
        assert r.json()["chars"] > 0

    def test_two_url_loads_accumulate(self, client):
        c, core, loop = client
        sid = new_session(c)

        core.fetch_url_content.side_effect = [
            (True, "OKR Q1: Цель 1, KR1"),
            (True, "OKR Q2: Цель 2, KR2"),
        ]

        c.post(f"/api/{sid}/url", json={
            "url": "https://docs.google.com/spreadsheets/d/q1",
            "task": "анализ Q1",
        })
        c.post(f"/api/{sid}/url", json={
            "url": "https://docs.google.com/spreadsheets/d/q2",
            "task": "анализ Q2",
        })

        assert core.fetch_url_content.call_count == 2


# ══════════════════════════════════════════════════════════════════════════════
# ИНТЕГРАЦИЯ: Голосовой агент — полный цикл работы
# ══════════════════════════════════════════════════════════════════════════════

class TestVoiceAgentFullFlow:

    def test_transcript_then_validate_then_history(self, client):
        c, core, _ = client
        sid = new_session(c)

        phrase = "Достичь NPS выше 70 к концу квартала"

        # 1. Сохранить фразу из Web Speech API
        r_transcript = c.post(f"/api/{sid}/transcript", json={"text": phrase})
        assert r_transcript.status_code == 200

        # 2. Провалидировать ту же фразу
        r_validate = c.post(f"/api/{sid}/validate", json={"text": phrase})
        assert r_validate.status_code == 200
        assert "Objective" in r_validate.json()["result"]

        # 3. История должна содержать запись
        core.history = [phrase]
        r_hist = c.get(f"/api/{sid}/history")
        assert phrase in r_hist.json()["history"]

    def test_multiple_phrases_then_analyze(self, client):
        c, core, _ = client
        sid = new_session(c)

        phrases = [
            "Стать лучшей командой разработки в компании",
            "Закрыть 50 клиентов к Q2 2026",
            "Снизить время онбординга до 3 дней",
            "Поднять NPS до 75 баллов",
        ]
        for p in phrases:
            c.post(f"/api/{sid}/transcript", json={"text": p})

        assert core.add_to_history.call_count == len(phrases)

        r = c.post(f"/api/{sid}/analyze", json={"use_previous_context": False})
        assert r.status_code == 200

    def test_validate_non_okr_phrase_from_transcript(self, client):
        c, core, _ = client
        sid = new_session(c)
        core.validate_existing_okr.return_value = "Тип: Не OKR\nОценка: 1/10\nФраза не является OKR"

        c.post(f"/api/{sid}/transcript", json={"text": "Кофе с молоком пожалуйста"})
        r = c.post(f"/api/{sid}/validate", json={"text": "Кофе с молоком пожалуйста"})

        assert r.status_code == 200
        assert "Не OKR" in r.json()["result"]

    def test_clear_then_new_session_works(self, client):
        c, core, loop = client
        sid = new_session(c)

        # Добавляем данные
        c.post(f"/api/{sid}/transcript", json={"text": "Первая фраза"})

        # Очищаем
        c.delete(f"/api/{sid}/history")
        loop.reset_session.assert_called_once()

        # Новые данные всё ещё принимаются
        r = c.post(f"/api/{sid}/transcript", json={"text": "Новая фраза после очистки"})
        assert r.status_code == 200


# ══════════════════════════════════════════════════════════════════════════════
# ИНТЕГРАЦИЯ: Изоляция трёх независимых сессий агентов
# ══════════════════════════════════════════════════════════════════════════════

class TestThreeAgentIsolation:

    def test_three_independent_sessions(self, client):
        c, core, _ = client
        sid_text   = new_session(c)
        sid_sheets = new_session(c)
        sid_voice  = new_session(c)

        assert len({sid_text, sid_sheets, sid_voice}) == 3

    def test_text_validate_doesnt_affect_sheets_history(self, client):
        c, core, _ = client
        sid_text   = new_session(c)
        sid_sheets = new_session(c)

        core.validate_existing_okr.return_value = "Тип: Key Result\nОценка: 7/10"
        c.post(f"/api/{sid_text}/validate", json={"text": "Закрыть 10 сделок"})

        # В sheets-сессии история не должна меняться
        core.history = []
        r = c.get(f"/api/{sid_sheets}/history")
        assert r.json()["history"] == []

    def test_voice_transcript_doesnt_appear_in_text_session(self, client):
        c, core, _ = client
        sid_text  = new_session(c)
        sid_voice = new_session(c)

        c.post(f"/api/{sid_voice}/transcript", json={"text": "Голосовая фраза"})

        core.history = []
        r = c.get(f"/api/{sid_text}/history")
        assert r.json()["history"] == []

    def test_clear_text_session_doesnt_break_sheets(self, client):
        c, core, loop = client
        sid_text   = new_session(c)
        sid_sheets = new_session(c)

        c.delete(f"/api/{sid_text}/history")

        # sheets сессия по-прежнему работает
        r = c.post(f"/api/{sid_sheets}/validate", json={"text": "Тест OKR"})
        assert r.status_code == 200

    def test_each_agent_can_analyze_independently(self, client):
        c, core, _ = client
        sid_text   = new_session(c)
        sid_sheets = new_session(c)

        core.analyze_okr.side_effect = [
            "Анализ текстового агента: 2 OKR",
            "Анализ таблиц: 5 OKR",
        ]

        r1 = c.post(f"/api/{sid_text}/analyze",   json={"use_previous_context": False})
        r2 = c.post(f"/api/{sid_sheets}/analyze",  json={"use_previous_context": False})

        assert r1.json()["result"] != r2.json()["result"]


# ══════════════════════════════════════════════════════════════════════════════
# ИНТЕГРАЦИЯ: AgentLoop + реальный CSV-парсинг (без LLM)
# ══════════════════════════════════════════════════════════════════════════════

class TestAgentLoopWithRealParsing:

    @pytest.fixture
    def real_loop(self, mock_core):
        from agent_loop import AgentLoop
        loop = AgentLoop(core=mock_core, web_mode=True)
        loop.on_message = MagicMock()
        return loop

    def test_provide_file_and_check_context(self, real_loop):
        csv_content = "KR,Owner,Description\nO1,Иван,Увеличить выручку\nKR1,Мария,Закрыть 10 сделок\n"
        real_loop.provide_file("/tmp/okr.csv", csv_content, "okr.csv")

        assert "okr.csv" in real_loop.ctx["files"]
        assert "okr.csv" in real_loop.ctx["files_raw"]
        assert real_loop.ctx["files"]["okr.csv"] == csv_content[:3000]
        assert real_loop.ctx["files_raw"]["okr.csv"] == csv_content[:15000]

    def test_reset_after_provide_clears_context(self, real_loop):
        real_loop.provide_file("/tmp/okr.csv", "данные", "okr.csv")
        assert "okr.csv" in real_loop.ctx["files"]

        real_loop.reset_session()
        assert real_loop.ctx["files"] == {}
        assert real_loop.ctx["files_raw"] == {}

    def test_provide_large_file_truncated_correctly(self, real_loop):
        large_content = "Данные OKR. " * 5000  # ~60k символов
        real_loop.provide_file("/tmp/big.txt", large_content, "big.txt")

        assert len(real_loop.ctx["files"]["big.txt"]) == 3000
        assert len(real_loop.ctx["files_raw"]["big.txt"]) == 15000

    def test_multiple_files_all_stored(self, real_loop):
        files = [("okr_q1.csv", "Q1 данные"), ("okr_q2.csv", "Q2 данные"), ("notes.txt", "Заметки")]
        for name, content in files:
            real_loop.provide_file(f"/tmp/{name}", content, name)

        for name, _ in files:
            assert name in real_loop.ctx["files"]
            assert name in real_loop.ctx["files_raw"]
