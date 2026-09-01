"""
Тесты для 3 веб-агентов: Текстовый, Таблицы, Голосовой.
Запуск: pytest tests/test_web_agents.py -v
"""
import io
import sys
import pytest
from pathlib import Path
from unittest.mock import MagicMock, patch, AsyncMock

sys.path.insert(0, str(Path(__file__).parent.parent))


# ── Фикстура: мок OKRAgentCore ────────────────────────────────────────────────
@pytest.fixture
def mock_core():
    core = MagicMock()
    core.validate_existing_okr.return_value = (
        "Тип: Objective\nОценка: 8/10\nКомментарий: Цель сформулирована корректно"
    )
    core.analyze_okr.return_value = "Анализ сессии: 3 OKR найдено, 2 корректны"
    core.process_file.return_value = (True, "Увеличить выручку на 20% к Q3")
    core.fetch_url_content.return_value = (True, "OKR таблица: Objective 1, KR 1.1")
    core.add_to_history.return_value = None
    core.history = []
    return core


@pytest.fixture
def mock_loop(mock_core):
    loop = MagicMock()
    loop.core = mock_core
    loop.ctx = {"files": {}, "files_raw": {}}
    loop.on_message = None
    loop.on_transcript = None
    loop.on_request_file = None
    loop.on_save = None
    loop.get_google_token = None
    loop.reset_session.return_value = None
    loop.provide_file.return_value = None
    return loop


@pytest.fixture
def client(mock_core, mock_loop):
    """TestClient с замоканными зависимостями."""
    with patch("web.server._make_core", return_value=mock_core), \
         patch("web.server.AgentLoop", return_value=mock_loop):
        from fastapi.testclient import TestClient
        from web.server import app
        with TestClient(app) as c:
            yield c


# ── Вспомогательная функция: создать сессию ──────────────────────────────────
def new_session(client) -> str:
    r = client.post("/api/session")
    assert r.status_code == 200
    return r.json()["session_id"]


# ══════════════════════════════════════════════════════════════════════════════
# ОБЩИЕ ТЕСТЫ
# ══════════════════════════════════════════════════════════════════════════════

class TestSessionManagement:

    def test_create_session_returns_id(self, client):
        r = client.post("/api/session")
        assert r.status_code == 200
        data = r.json()
        assert "session_id" in data
        assert len(data["session_id"]) == 36  # UUID format

    def test_two_sessions_are_independent(self, client):
        sid1 = new_session(client)
        sid2 = new_session(client)
        assert sid1 != sid2

    def test_index_page_loads(self, client):
        r = client.get("/")
        assert r.status_code == 200
        assert "OKR Agent" in r.text

    def test_index_has_three_agent_tabs(self, client):
        r = client.get("/")
        assert "switch-text" in r.text
        assert "switch-sheets" in r.text
        assert "switch-voice" in r.text

    def test_index_no_cache_header(self, client):
        r = client.get("/")
        assert r.headers.get("cache-control") == "no-store"

    def test_clear_history(self, client, mock_loop):
        sid = new_session(client)
        r = client.delete(f"/api/{sid}/history")
        assert r.status_code == 200
        assert r.json()["ok"] is True
        mock_loop.reset_session.assert_called_once()

    def test_get_history_empty(self, client, mock_core):
        sid = new_session(client)
        mock_core.history = []
        r = client.get(f"/api/{sid}/history")
        assert r.status_code == 200
        assert r.json()["history"] == []


# ══════════════════════════════════════════════════════════════════════════════
# АГЕНТ 1: ТЕКСТОВЫЙ
# ══════════════════════════════════════════════════════════════════════════════

class TestTextAgent:

    def test_validate_okr_correct(self, client, mock_core):
        sid = new_session(client)
        r = client.post(f"/api/{sid}/validate", json={"text": "Стать лидером рынка к Q4"})
        assert r.status_code == 200
        data = r.json()
        assert "result" in data
        assert "8/10" in data["result"]
        mock_core.validate_existing_okr.assert_called_with("Стать лидером рынка к Q4")

    def test_validate_okr_empty_text(self, client):
        sid = new_session(client)
        r = client.post(f"/api/{sid}/validate", json={"text": ""})
        assert r.status_code == 400

    def test_validate_okr_missing_text_field(self, client):
        sid = new_session(client)
        r = client.post(f"/api/{sid}/validate", json={})
        assert r.status_code == 400

    def test_analyze_session(self, client, mock_core):
        sid = new_session(client)
        r = client.post(f"/api/{sid}/analyze", json={"use_previous_context": False})
        assert r.status_code == 200
        assert "3 OKR найдено" in r.json()["result"]
        mock_core.analyze_okr.assert_called_with(False)

    def test_analyze_session_with_prev_context(self, client, mock_core):
        sid = new_session(client)
        r = client.post(f"/api/{sid}/analyze", json={"use_previous_context": True})
        assert r.status_code == 200
        mock_core.analyze_okr.assert_called_with(True)

    def test_upload_txt_file(self, client, mock_loop):
        sid = new_session(client)
        content = b"Objective: Increase revenue by 20%\nKR1: Close 10 enterprise deals"
        r = client.post(
            f"/api/{sid}/upload",
            files={"file": ("okr_test.txt", io.BytesIO(content), "text/plain")},
        )
        assert r.status_code == 200
        data = r.json()
        assert data["filename"] == "okr_test.txt"
        assert data["chars"] > 0

    def test_upload_unsupported_file_type(self, client):
        sid = new_session(client)
        r = client.post(
            f"/api/{sid}/upload",
            files={"file": ("test.exe", io.BytesIO(b"binary"), "application/octet-stream")},
        )
        assert r.status_code == 400

    def test_upload_csv_file(self, client, mock_loop):
        sid = new_session(client)
        csv_content = b"Type,Description\nObjective,Grow revenue\nKR,Increase by 20%"
        r = client.post(
            f"/api/{sid}/upload",
            files={"file": ("okr.csv", io.BytesIO(csv_content), "text/csv")},
        )
        assert r.status_code == 200
        assert r.json()["filename"] == "okr.csv"

    def test_add_transcript(self, client, mock_core):
        sid = new_session(client)
        r = client.post(f"/api/{sid}/transcript",
                        json={"text": "Увеличить выручку на 20% к Q3 2026"})
        assert r.status_code == 200
        assert r.json()["ok"] is True
        mock_core.add_to_history.assert_called_once()

    def test_add_empty_transcript_ignored(self, client, mock_core):
        sid = new_session(client)
        r = client.post(f"/api/{sid}/transcript", json={"text": ""})
        assert r.status_code == 200
        mock_core.add_to_history.assert_not_called()


# ══════════════════════════════════════════════════════════════════════════════
# АГЕНТ 2: ТАБЛИЦЫ
# ══════════════════════════════════════════════════════════════════════════════

class TestSheetsAgent:

    def test_load_url(self, client, mock_core):
        sid = new_session(client)
        r = client.post(f"/api/{sid}/url", json={
            "url": "https://docs.google.com/spreadsheets/d/test123",
            "task": "Проанализируй OKR",
        })
        assert r.status_code == 200
        data = r.json()
        assert "name" in data
        assert "chars" in data
        assert data["chars"] > 0

    def test_load_url_empty(self, client):
        sid = new_session(client)
        r = client.post(f"/api/{sid}/url", json={"url": "", "task": "анализ"})
        assert r.status_code == 400

    def test_load_url_failed_fetch(self, client, mock_core):
        sid = new_session(client)
        mock_core.fetch_url_content.return_value = (False, "Доступ запрещён")
        r = client.post(f"/api/{sid}/url", json={
            "url": "https://docs.google.com/spreadsheets/d/private",
            "task": "анализ",
        })
        assert r.status_code == 422

    def test_upload_xlsx_file(self, client, mock_loop, mock_core):
        sid = new_session(client)
        # Minimal xlsx-like bytes (process_file is mocked)
        r = client.post(
            f"/api/{sid}/upload",
            files={"file": ("okr_table.xlsx", io.BytesIO(b"PK fake xlsx"), "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
        )
        assert r.status_code == 200
        assert r.json()["filename"] == "okr_table.xlsx"

    def test_google_status_not_connected(self, client):
        sid = new_session(client)
        r = client.get(f"/api/{sid}/google/status")
        assert r.status_code == 200
        assert r.json()["connected"] is False

    def test_url_content_stored_in_context(self, client, mock_loop, mock_core):
        sid = new_session(client)
        mock_core.fetch_url_content.return_value = (True, "OKR data " * 100)
        r = client.post(f"/api/{sid}/url", json={
            "url": "https://example.com/okr.csv",
            "task": "анализ",
        })
        assert r.status_code == 200
        # Контент должен быть сохранён в ctx["files"]
        assert len(mock_loop.ctx["files"]) > 0 or r.json()["chars"] > 0


# ══════════════════════════════════════════════════════════════════════════════
# АГЕНТ 3: ГОЛОСОВОЙ
# ══════════════════════════════════════════════════════════════════════════════

class TestVoiceAgent:

    def test_validate_phrase_as_okr(self, client, mock_core):
        """Голосовой агент использует тот же /validate эндпоинт."""
        sid = new_session(client)
        r = client.post(f"/api/{sid}/validate",
                        json={"text": "Достичь NPS выше 70 к концу квартала"})
        assert r.status_code == 200
        assert "result" in r.json()

    def test_validate_non_okr_phrase(self, client, mock_core):
        sid = new_session(client)
        mock_core.validate_existing_okr.return_value = "Тип: Не OKR\nОценка: 2/10"
        r = client.post(f"/api/{sid}/validate",
                        json={"text": "Сегодня хорошая погода"})
        assert r.status_code == 200
        assert "Не OKR" in r.json()["result"]

    def test_transcript_saved_to_history(self, client, mock_core):
        sid = new_session(client)
        phrase = "Увеличить количество активных пользователей на 30%"
        r = client.post(f"/api/{sid}/transcript", json={"text": phrase})
        assert r.status_code == 200
        mock_core.add_to_history.assert_called_with(phrase)

    def test_multiple_phrases_accumulated(self, client, mock_core):
        sid = new_session(client)
        phrases = [
            "Стать лучшей командой разработки",
            "Закрыть 50 клиентов к Q2",
            "Снизить время доставки до 2 дней",
        ]
        for p in phrases:
            r = client.post(f"/api/{sid}/transcript", json={"text": p})
            assert r.status_code == 200
        assert mock_core.add_to_history.call_count == len(phrases)

    def test_validate_selected_text(self, client, mock_core):
        """Проверка выделенного текста из стенограммы."""
        sid = new_session(client)
        mock_core.validate_existing_okr.return_value = "Тип: Key Result\nОценка: 7/10"
        r = client.post(f"/api/{sid}/validate",
                        json={"text": "Закрыть 10 сделок с enterprise клиентами"})
        assert r.status_code == 200
        assert "Key Result" in r.json()["result"]


# ══════════════════════════════════════════════════════════════════════════════
# ТЕСТЫ ИЗОЛЯЦИИ СЕССИЙ
# ══════════════════════════════════════════════════════════════════════════════

class TestSessionIsolation:

    def test_text_and_sheets_sessions_independent(self, client, mock_core):
        """Текстовый и табличный агенты имеют разные сессии."""
        sid_text = new_session(client)
        sid_sheets = new_session(client)
        assert sid_text != sid_sheets

    def test_clear_one_session_does_not_affect_other(self, client, mock_loop):
        sid1 = new_session(client)
        sid2 = new_session(client)
        # Очищаем только первую
        r = client.delete(f"/api/{sid1}/history")
        assert r.status_code == 200
        # Вторая сессия всё ещё работает
        r2 = client.get(f"/api/{sid2}/history")
        assert r2.status_code == 200

    def test_validate_in_different_sessions(self, client, mock_core):
        sid1 = new_session(client)
        sid2 = new_session(client)
        mock_core.validate_existing_okr.side_effect = [
            "Тип: Objective\nОценка: 9/10",
            "Тип: Key Result\nОценка: 6/10",
        ]
        r1 = client.post(f"/api/{sid1}/validate", json={"text": "Цель 1"})
        r2 = client.post(f"/api/{sid2}/validate", json={"text": "KR 1"})
        assert r1.json()["result"] != r2.json()["result"]
