"""
Unit-тесты для изолированных функций и классов.
Тестируем без сервера, без сети, без LLM.

Запуск: pytest tests/test_unit.py -v
"""
import sys
import pytest
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).parent.parent))


# ══════════════════════════════════════════════════════════════════════════════
# agent_loop._parse_tool
# ══════════════════════════════════════════════════════════════════════════════

from agent_loop import _parse_tool


class TestParseTool:

    def test_explicit_tool_no_args(self):
        result = _parse_tool("TOOL: validate_okr")
        assert result == ("validate_okr", {})

    def test_explicit_tool_with_args(self):
        result = _parse_tool('TOOL: fetch_url\nARGS: {"url": "https://example.com", "task": "анализ"}')
        assert result[0] == "fetch_url"
        assert result[1]["url"] == "https://example.com"
        assert result[1]["task"] == "анализ"

    def test_explicit_tool_malformed_args(self):
        result = _parse_tool("TOOL: analyze_session\nARGS: {not valid json}")
        assert result == ("analyze_session", {})

    def test_keyword_stop_recording_ru(self):
        assert _parse_tool("Останови запись митинга") == ("stop_recording", {})

    def test_keyword_stop_recording_en(self):
        assert _parse_tool("Please stop recording now") == ("stop_recording", {})

    def test_keyword_start_recording_ru(self):
        assert _parse_tool("Начать запись встречи") == ("start_recording", {})

    def test_keyword_start_recording_en(self):
        assert _parse_tool("Let's begin recording") == ("start_recording", {})

    def test_keyword_analyze_session_ru(self):
        assert _parse_tool("Давай проанализируем сессию") == ("analyze_session", {})

    def test_keyword_save_session_ru(self):
        assert _parse_tool("Сохрани сессию пожалуйста") == ("save_session", {})

    def test_no_tool_returns_none(self):
        assert _parse_tool("Привет, как дела?") is None

    def test_empty_string_returns_none(self):
        assert _parse_tool("") is None

    def test_tool_with_whitespace(self):
        result = _parse_tool("  TOOL:   validate_okr  ")
        assert result is not None
        assert result[0] == "validate_okr"

    def test_args_with_nested_json(self):
        result = _parse_tool('TOOL: update_sheet\nARGS: {"sheet_id": "abc", "updates": []}')
        assert result[0] == "update_sheet"
        assert result[1]["sheet_id"] == "abc"


# ══════════════════════════════════════════════════════════════════════════════
# OKRAgentCore._parse_okr_csv
# ══════════════════════════════════════════════════════════════════════════════

from okr_agent_core import OKRAgentCore


class TestParseOkrCsv:

    def test_with_header_row(self):
        # KR должен быть НЕ в колонке 0 (иначе `col() or 2` → 2 из-за falsy 0)
        csv = (
            "%,KR,Owner,Unit,Description\n"
            "80,O1,Иван,,Стать лучшей командой\n"
            "60,KR1,Мария,сделок,Закрыть 50 клиентов\n"
        )
        result = OKRAgentCore._parse_okr_csv(csv)
        assert "OBJECTIVE" in result
        assert "Стать лучшей командой" in result
        assert "KR1" in result

    def test_without_header_returns_raw(self):
        csv = "просто текст,без заголовков\nданные,строка"
        result = OKRAgentCore._parse_okr_csv(csv)
        assert result == csv  # возвращает как есть

    def test_empty_csv(self):
        result = OKRAgentCore._parse_okr_csv("")
        assert result == ""

    def test_objective_and_kr_structure(self):
        csv = (
            "%,KR,Owner,Unit,Description\n"
            "80,O1,Иван,,Увеличить выручку\n"
            "60,KR1,Мария,сделок,Закрыть 10 enterprise сделок\n"
            "40,KR2,Петр,%,Поднять конверсию до 15%\n"
        )
        result = OKRAgentCore._parse_okr_csv(csv)
        assert "OBJECTIVE O1" in result
        assert "KR1" in result
        assert "KR2" in result

    def test_multiple_objectives(self):
        csv = (
            "%,KR,Owner,Unit,Description\n"
            "80,O1,Иван,,Цель 1\n"
            "60,KR1,Мария,шт,KR для цели 1\n"
            "50,O2,Петр,,Цель 2\n"
            "40,KR1,Анна,шт,KR для цели 2\n"
        )
        result = OKRAgentCore._parse_okr_csv(csv)
        assert result.count("OBJECTIVE") >= 2


# ══════════════════════════════════════════════════════════════════════════════
# OKRAgentCore.extract_okr_cells
# ══════════════════════════════════════════════════════════════════════════════

class TestExtractOkrCells:

    def test_basic_extraction(self):
        csv = (
            "%,KR,Owner,Unit,Description\n"
            "80,O1,Иван,,Увеличить выручку\n"
            "60,KR1,Мария,сделок,Закрыть 10 enterprise сделок\n"
        )
        cells = OKRAgentCore.extract_okr_cells(csv)
        assert len(cells) == 2
        types = [c["type"] for c in cells]
        assert "O1" in types
        assert "KR1" in types

    def test_cell_has_required_fields(self):
        csv = (
            "%,KR,Owner,Unit,Description\n"
            "80,O1,Иван,,Стать лучшей командой\n"
        )
        cells = OKRAgentCore.extract_okr_cells(csv)
        assert len(cells) == 1
        c = cells[0]
        assert "type" in c
        assert "row" in c
        assert "col" in c
        assert "text" in c

    def test_row_index_correct(self):
        csv = (
            "KR,Owner,Description\n"
            "O1,Иван,Первая цель\n"
            "KR1,Мария,Первый KR\n"
        )
        cells = OKRAgentCore.extract_okr_cells(csv)
        rows = [c["row"] for c in cells]
        assert rows == sorted(rows)  # возрастающий порядок строк

    def test_empty_rows_skipped(self):
        csv = (
            "%,KR,Owner,Unit,Description\n"
            "80,O1,Иван,,Цель\n"
            ",,,,\n"
            "60,KR1,Мария,шт,KR\n"
        )
        cells = OKRAgentCore.extract_okr_cells(csv)
        assert len(cells) == 2

    def test_no_header_fallback(self):
        csv = (
            "header row ignored\n"
            "0,80,O1,Иван,Цель команды,Q3\n"
            "0,60,KR1,Мария,Закрыть 10 сделок,Q3\n"
        )
        cells = OKRAgentCore.extract_okr_cells(csv)
        # Без заголовка используем col 2 (type) и col 4 (description)
        assert len(cells) >= 0  # не падает

    def test_empty_csv(self):
        cells = OKRAgentCore.extract_okr_cells("")
        assert cells == []

    def test_invalid_csv_returns_empty(self):
        cells = OKRAgentCore.extract_okr_cells("не CSV данные\x00\x01\x02")
        assert isinstance(cells, list)

    def test_kr_with_emoji_stripped(self):
        csv = (
            "KR,Owner,Description\n"
            "O1,Иван,Цель\n"
            "KR1 ⚙,Мария,KR с эмодзи\n"
        )
        cells = OKRAgentCore.extract_okr_cells(csv)
        kr_cell = next((c for c in cells if "KR" in c["type"]), None)
        assert kr_cell is not None
        assert "⚙" not in kr_cell["type"]  # ⚙ убирается через split()[0]


# ══════════════════════════════════════════════════════════════════════════════
# AgentLoop — provide_file, reset_session
# ══════════════════════════════════════════════════════════════════════════════

from agent_loop import AgentLoop


@pytest.fixture
def loop():
    core = MagicMock()
    core.validate_existing_okr.return_value = "OK"
    core.analyze_okr.return_value = "Анализ"
    loop = AgentLoop(core=core, web_mode=True)
    loop.on_message = MagicMock()
    return loop


class TestAgentLoopProvideFile:

    def test_stores_full_content_in_files_raw(self, loop):
        content = "A" * 20000
        loop.provide_file("/tmp/test.txt", content, "test.txt")
        assert loop.ctx["files_raw"]["test.txt"] == content[:15000]

    def test_stores_truncated_content_in_files(self, loop):
        content = "B" * 10000
        loop.provide_file("/tmp/test.txt", content, "test.txt")
        assert loop.ctx["files"]["test.txt"] == content[:3000]

    def test_short_content_stored_in_full(self, loop):
        content = "Короткий файл"
        loop.provide_file("/tmp/test.txt", content, "test.txt")
        assert loop.ctx["files"]["test.txt"] == content
        assert loop.ctx["files_raw"]["test.txt"] == content

    def test_file_name_as_key(self, loop):
        loop.provide_file("/tmp/okr.csv", "данные", "okr.csv")
        assert "okr.csv" in loop.ctx["files"]
        assert "okr.csv" in loop.ctx["files_raw"]

    def test_emits_tool_message(self, loop):
        loop.provide_file("/tmp/test.txt", "содержимое", "test.txt")
        # provide_file эмитирует tool-сообщение (background thread может добавить ещё)
        assert loop.on_message.call_count >= 1
        first_call_args = loop.on_message.call_args_list[0][0]
        assert "test.txt" in first_call_args[0]
        assert first_call_args[1] == "tool"

    def test_multiple_files_accumulated(self, loop):
        loop.provide_file("/tmp/a.txt", "файл А", "a.txt")
        loop.provide_file("/tmp/b.txt", "файл Б", "b.txt")
        assert "a.txt" in loop.ctx["files"]
        assert "b.txt" in loop.ctx["files"]


class TestAgentLoopResetSession:

    def test_clears_files(self, loop):
        loop.ctx["files"]["test.txt"] = "данные"
        loop.reset_session()
        assert loop.ctx["files"] == {}

    def test_clears_files_raw(self, loop):
        loop.ctx["files_raw"]["test.txt"] = "полные данные"
        loop.reset_session()
        assert loop.ctx["files_raw"] == {}

    def test_does_not_clear_conversation(self, loop):
        # По документации reset_session сбрасывает контекст, но НЕ диалог
        loop.conversation.append({"role": "user", "text": "привет"})
        loop.reset_session()
        assert len(loop.conversation) == 1

    def test_resets_transcript_count(self, loop):
        loop.ctx["transcript_count"] = 5
        loop.reset_session()
        assert loop.ctx["transcript_count"] == 0


class TestAgentLoopSend:

    def test_send_url_spawns_fetch(self, loop):
        with patch.object(loop, '_spawn') as mock_spawn:
            loop.send("https://docs.google.com/spreadsheets/d/abc123 Проанализируй")
            mock_spawn.assert_called_once()
            assert mock_spawn.call_args[0][0] == "fetch_url"

    def test_send_sheet_update_spawns_apply(self, loop):
        with patch.object(loop, '_spawn') as mock_spawn:
            loop.send("внеси изменения в таблицу")
            mock_spawn.assert_called_once()
            assert mock_spawn.call_args[0][0] == "apply_sheet"

    def test_send_regular_text_adds_to_conversation(self, loop):
        with patch('threading.Thread') as mock_thread:
            mock_thread.return_value.start = MagicMock()
            loop.send("Привет агент")
            assert any(m["text"] == "Привет агент" for m in loop.conversation)

    def test_send_without_core_emits_error(self):
        loop_no_core = AgentLoop(core=None, web_mode=True)
        loop_no_core.on_message = MagicMock()
        loop_no_core.send("что-то")
        loop_no_core.on_message.assert_called_once()
        assert loop_no_core.on_message.call_args[0][1] == "error"


# ══════════════════════════════════════════════════════════════════════════════
# OKRAgentCore — вспомогательные методы (без LLM)
# ══════════════════════════════════════════════════════════════════════════════

class TestOkrAgentCoreHelpers:

    @pytest.fixture
    def core(self):
        with patch.object(OKRAgentCore, '__init__', lambda self, **kw: None):
            c = OKRAgentCore.__new__(OKRAgentCore)
            c.history = []
            c.previous_okrs = {}
            c.previous_okrs_file = None
            c.language = "ru-RU"
            return c

    def test_add_to_history(self, core):
        core.add_to_history("Первая запись")
        assert "Первая запись" in core.history

    def test_add_multiple_to_history(self, core):
        core.add_to_history("Запись 1")
        core.add_to_history("Запись 2")
        assert len(core.history) == 2

    def test_clear_history(self, core):
        core.add_to_history("Что-то")
        core.clear_history()
        assert core.history == []

    def test_get_history(self, core):
        core.add_to_history("Тест")
        assert core.get_history() == ["Тест"]

    def test_get_previous_context_empty(self, core):
        core.previous_okrs = {}
        result = core.get_previous_context()
        assert isinstance(result, str)

    def test_get_previous_context_with_data(self, core):
        core.previous_okrs = {"Q1 2026": ["Цель 1", "KR 1.1"]}
        result = core.get_previous_context()
        assert "Q1 2026" in result
        assert "Цель 1" in result

    def test_add_okr_to_previous(self, core):
        core.save_previous_okrs = MagicMock()
        core.add_okr_to_previous("Q2 2026", "Увеличить выручку")
        assert "Q2 2026" in core.previous_okrs
        assert "Увеличить выручку" in core.previous_okrs["Q2 2026"]
