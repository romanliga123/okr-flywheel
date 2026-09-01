"""
Tests for OKRAgentCore — all external I/O (LLM, audio, files) is mocked.
Run:  pytest tests/test_core.py -v
"""
import json
import os
import sys
import types
import threading
import tempfile
import struct
from pathlib import Path
from unittest.mock import MagicMock, patch, PropertyMock

import pytest

# ---------------------------------------------------------------------------
# Make sure project root is on sys.path
# ---------------------------------------------------------------------------
ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))


# ---------------------------------------------------------------------------
# Helpers to build a minimal OKRAgentCore without a real LLM
# ---------------------------------------------------------------------------

def _make_ollama_agent(tmp_path):
    """Create an OKRAgentCore(ollama) with a fully mocked ollama module."""
    fake_ollama = types.SimpleNamespace(
        generate=MagicMock(return_value=types.SimpleNamespace(response="ok")),
        list=MagicMock(return_value=types.SimpleNamespace(models=[])),
    )
    with patch.dict(sys.modules, {"ollama": fake_ollama}):
        from okr_agent_core import OKRAgentCore
        agent = OKRAgentCore.__new__(OKRAgentCore)
        agent.provider = "ollama"
        agent.ollama_model = "neural-chat"
        agent.language = "ru-RU"
        agent.history = []
        agent.previous_okrs_file = str(tmp_path / "previous_okrs.json")
        agent.previous_okrs = {}
        agent.recognizer = MagicMock()
        agent.ollama = fake_ollama
    return agent


def _make_kimi_agent(tmp_path):
    from okr_agent_core import OKRAgentCore
    agent = OKRAgentCore.__new__(OKRAgentCore)
    agent.provider = "kimi"
    agent.kimi_api_key = "sk-test"
    agent.kimi_model = "moonshot-v1-8k"
    agent.kimi_http = MagicMock()
    agent.language = "ru-RU"
    agent.history = []
    agent.previous_okrs_file = str(tmp_path / "previous_okrs.json")
    agent.previous_okrs = {}
    agent.recognizer = MagicMock()
    return agent


# ===========================================================================
# 1. Initialization
# ===========================================================================

class TestInit:
    def test_invalid_provider_raises(self, tmp_path):
        from okr_agent_core import OKRAgentCore
        with pytest.raises(ValueError, match="Provider must be"):
            OKRAgentCore(provider="gpt4", api_key="x")

    def test_claude_no_key_raises(self, tmp_path):
        from okr_agent_core import OKRAgentCore
        with patch.dict(os.environ, {}, clear=True):
            os.environ.pop("ANTHROPIC_API_KEY", None)
            with pytest.raises(ValueError, match="ANTHROPIC_API_KEY"):
                OKRAgentCore(provider="claude", api_key=None)

    def test_gemini_no_key_raises(self, tmp_path):
        from okr_agent_core import OKRAgentCore
        with patch.dict(os.environ, {}, clear=True):
            os.environ.pop("GEMINI_API_KEY", None)
            with pytest.raises(ValueError, match="GEMINI_API_KEY"):
                OKRAgentCore(provider="gemini", api_key=None)

    def test_groq_no_key_raises(self, tmp_path):
        from okr_agent_core import OKRAgentCore
        with patch.dict(os.environ, {}, clear=True):
            os.environ.pop("GROQ_API_KEY", None)
            fake_groq = types.ModuleType("groq")
            fake_groq.Groq = MagicMock()
            with patch.dict(sys.modules, {"groq": fake_groq}):
                with pytest.raises(ValueError, match="GROQ_API_KEY"):
                    OKRAgentCore(provider="groq", api_key=None)

    def test_mistral_no_key_raises(self, tmp_path):
        from okr_agent_core import OKRAgentCore
        with patch.dict(os.environ, {}, clear=True):
            os.environ.pop("MISTRAL_API_KEY", None)
            fake_mistral = types.ModuleType("mistralai")
            fake_mistral_client = types.ModuleType("mistralai.client")
            fake_mistral_client.Mistral = MagicMock()
            with patch.dict(sys.modules, {
                "mistralai": fake_mistral,
                "mistralai.client": fake_mistral_client,
            }):
                with pytest.raises(ValueError, match="MISTRAL_API_KEY"):
                    OKRAgentCore(provider="mistral", api_key=None)

    def test_kimi_no_key_raises(self, tmp_path):
        from okr_agent_core import OKRAgentCore
        with patch.dict(os.environ, {}, clear=True):
            os.environ.pop("KIMI_API_KEY", None)
            with pytest.raises(ValueError, match="KIMI_API_KEY"):
                OKRAgentCore(provider="kimi", api_key=None)

    def test_kimi_init_stores_key(self, tmp_path):
        from okr_agent_core import OKRAgentCore
        with patch("httpx.Client"):
            agent = OKRAgentCore(provider="kimi", api_key="sk-abc123")
        assert agent.kimi_api_key == "sk-abc123"
        assert agent.kimi_model == "moonshot-v1-8k"

    def test_ollama_init(self, tmp_path):
        fake_ollama = types.SimpleNamespace(
            generate=MagicMock(),
            list=MagicMock(return_value=types.SimpleNamespace(models=[])),
        )
        with patch.dict(sys.modules, {"ollama": fake_ollama}):
            from okr_agent_core import OKRAgentCore
            agent = OKRAgentCore(provider="ollama", api_key=None)
        assert agent.provider == "ollama"


# ===========================================================================
# 2. OKR storage
# ===========================================================================

class TestOKRStorage:
    def test_load_returns_empty_when_no_file(self, tmp_path):
        agent = _make_ollama_agent(tmp_path)
        result = agent.load_previous_okrs()
        assert result == {}

    def test_save_and_load_roundtrip(self, tmp_path):
        agent = _make_ollama_agent(tmp_path)
        data = {"Q1-2025": ["Увеличить ARR на 30%", "NPS > 60"]}
        agent.save_previous_okrs(data)
        loaded = agent.load_previous_okrs()
        assert loaded == data

    def test_add_okr_to_new_quarter(self, tmp_path):
        agent = _make_ollama_agent(tmp_path)
        agent.add_okr_to_previous("Q2-2025", "Запустить мобильное приложение")
        assert "Q2-2025" in agent.previous_okrs
        assert "Запустить мобильное приложение" in agent.previous_okrs["Q2-2025"]

    def test_add_okr_to_existing_quarter(self, tmp_path):
        agent = _make_ollama_agent(tmp_path)
        agent.add_okr_to_previous("Q1-2025", "OKR 1")
        agent.add_okr_to_previous("Q1-2025", "OKR 2")
        assert len(agent.previous_okrs["Q1-2025"]) == 2

    def test_get_previous_context_empty(self, tmp_path):
        agent = _make_ollama_agent(tmp_path)
        assert agent.get_previous_context() == ""

    def test_get_previous_context_with_data(self, tmp_path):
        agent = _make_ollama_agent(tmp_path)
        agent.previous_okrs = {"Q1": ["Goal A", "Goal B"]}
        ctx = agent.get_previous_context()
        assert "Q1" in ctx
        assert "Goal A" in ctx
        assert "OKR предыдущих кварталов" in ctx


# ===========================================================================
# 3. History management
# ===========================================================================

class TestHistory:
    def test_add_and_get_history(self, tmp_path):
        agent = _make_ollama_agent(tmp_path)
        agent.add_to_history("Текст 1")
        agent.add_to_history("Текст 2")
        assert agent.get_history() == ["Текст 1", "Текст 2"]

    def test_clear_history(self, tmp_path):
        agent = _make_ollama_agent(tmp_path)
        agent.add_to_history("something")
        agent.clear_history()
        assert agent.get_history() == []

    def test_get_history_returns_copy(self, tmp_path):
        agent = _make_ollama_agent(tmp_path)
        agent.add_to_history("item")
        h = agent.get_history()
        h.append("injected")
        assert len(agent.get_history()) == 1

    def test_analyze_okr_empty_history(self, tmp_path):
        agent = _make_ollama_agent(tmp_path)
        result = agent.analyze_okr()
        assert "пуста" in result.lower() or "история" in result.lower()


# ===========================================================================
# 4. _call_llm routing
# ===========================================================================

class TestCallLLMRouting:
    def _agent_with_provider(self, tmp_path, provider):
        from okr_agent_core import OKRAgentCore
        a = OKRAgentCore.__new__(OKRAgentCore)
        a.provider = provider
        a.language = "ru-RU"
        a.history = []
        a.previous_okrs = {}
        a.previous_okrs_file = str(tmp_path / "p.json")
        a.recognizer = MagicMock()
        a.ollama_model = "neural-chat"
        return a

    def test_routes_to_claude(self, tmp_path):
        a = self._agent_with_provider(tmp_path, "claude")
        a._call_claude = MagicMock(return_value="claude_response")
        assert a._call_llm("hello") == "claude_response"
        a._call_claude.assert_called_once()

    def test_routes_to_gemini(self, tmp_path):
        a = self._agent_with_provider(tmp_path, "gemini")
        a._call_gemini = MagicMock(return_value="gemini_response")
        assert a._call_llm("hello") == "gemini_response"

    def test_routes_to_groq(self, tmp_path):
        a = self._agent_with_provider(tmp_path, "groq")
        a._call_groq = MagicMock(return_value="groq_response")
        assert a._call_llm("hello") == "groq_response"

    def test_routes_to_mistral(self, tmp_path):
        a = self._agent_with_provider(tmp_path, "mistral")
        a._call_mistral = MagicMock(return_value="mistral_response")
        assert a._call_llm("hello") == "mistral_response"

    def test_routes_to_kimi(self, tmp_path):
        a = self._agent_with_provider(tmp_path, "kimi")
        a._call_kimi = MagicMock(return_value="kimi_response")
        assert a._call_llm("hello") == "kimi_response"

    def test_routes_to_ollama(self, tmp_path):
        a = self._agent_with_provider(tmp_path, "ollama")
        a._call_ollama = MagicMock(return_value="ollama_response")
        assert a._call_llm("hello") == "ollama_response"


# ===========================================================================
# 5. _call_kimi
# ===========================================================================

class TestCallKimi:
    def test_success(self, tmp_path):
        agent = _make_kimi_agent(tmp_path)
        mock_resp = MagicMock()
        mock_resp.json.return_value = {
            "choices": [{"message": {"content": "Kimi ответил"}}]
        }
        mock_resp.raise_for_status = MagicMock()
        agent.kimi_http.post.return_value = mock_resp
        result = agent._call_kimi("test prompt")
        assert result == "Kimi ответил"
        assert "moonshot.cn" in agent.kimi_http.post.call_args[0][0]

    def test_raises_on_http_error(self, tmp_path):
        agent = _make_kimi_agent(tmp_path)
        agent.kimi_http.post.side_effect = Exception("connection refused")
        with pytest.raises(Exception, match="Kimi API Error"):
            agent._call_kimi("test")

    def test_auth_header_contains_key(self, tmp_path):
        agent = _make_kimi_agent(tmp_path)
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"choices": [{"message": {"content": "ok"}}]}
        mock_resp.raise_for_status = MagicMock()
        agent.kimi_http.post.return_value = mock_resp
        agent._call_kimi("hi")
        headers = agent.kimi_http.post.call_args[1]["headers"]
        assert "sk-test" in headers["Authorization"]

    def test_uses_correct_model(self, tmp_path):
        agent = _make_kimi_agent(tmp_path)
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"choices": [{"message": {"content": "ok"}}]}
        mock_resp.raise_for_status = MagicMock()
        agent.kimi_http.post.return_value = mock_resp
        agent._call_kimi("hi")
        payload = agent.kimi_http.post.call_args[1]["json"]
        assert payload["model"] == "moonshot-v1-8k"


# ===========================================================================
# 6. is_on_track
# ===========================================================================

class TestIsOnTrack:
    def test_valid_okr_returns_true(self, tmp_path):
        agent = _make_ollama_agent(tmp_path)
        agent._call_llm = MagicMock(side_effect=["ДА", "ДА"])
        ok, reason = agent.is_on_track("Увеличить ARR на 30% к Q2 2025")
        assert ok is True
        assert reason == ""

    def test_non_business_returns_false(self, tmp_path):
        agent = _make_ollama_agent(tmp_path)
        agent._call_llm = MagicMock(side_effect=["НЕТ", "Это не бизнес-цель."])
        ok, reason = agent.is_on_track("купить кофе")
        assert ok is False
        assert len(reason) > 0

    def test_business_but_no_metrics_returns_false(self, tmp_path):
        agent = _make_ollama_agent(tmp_path)
        agent._call_llm = MagicMock(side_effect=["ДА", "НЕТ", "Не хватает метрики."])
        ok, reason = agent.is_on_track("улучшить продукт")
        assert ok is False
        assert len(reason) > 0


# ===========================================================================
# 7. analyze_okr
# ===========================================================================

class TestAnalyzeOkr:
    def test_returns_analysis_when_history_present(self, tmp_path):
        agent = _make_ollama_agent(tmp_path)
        agent.add_to_history("Увеличить NPS до 70 к Q3")
        agent._call_llm = MagicMock(return_value="Анализ: OKR корректен.")
        assert "Анализ" in agent.analyze_okr()

    def test_uses_all_history_not_just_last_ten(self, tmp_path):
        agent = _make_ollama_agent(tmp_path)
        for i in range(15):
            agent.add_to_history(f"Фрагмент {i}")
        captured = []
        agent._call_llm = MagicMock(
            side_effect=lambda p, **kw: captured.append(p) or "ok"
        )
        agent.analyze_okr(use_previous_context=False)
        assert "15" in captured[0]

    def test_use_previous_context_false_excludes_prev_okrs(self, tmp_path):
        agent = _make_ollama_agent(tmp_path)
        agent.previous_okrs = {"Q1-2024": ["Старая цель ARR"]}
        agent.add_to_history("Текущее обсуждение")
        captured = []
        agent._call_llm = MagicMock(
            side_effect=lambda p, **kw: captured.append(p) or "ok"
        )
        agent.analyze_okr(use_previous_context=False)
        assert "Старая цель ARR" not in captured[0]

    def test_use_previous_context_true_includes_prev_okrs(self, tmp_path):
        agent = _make_ollama_agent(tmp_path)
        agent.previous_okrs = {"Q1-2024": ["Старая цель ARR"]}
        agent.add_to_history("Текущее обсуждение")
        captured = []
        agent._call_llm = MagicMock(
            side_effect=lambda p, **kw: captured.append(p) or "ok"
        )
        agent.analyze_okr(use_previous_context=True)
        assert "Старая цель ARR" in captured[0]

    def test_prompt_requests_concrete_examples(self, tmp_path):
        agent = _make_ollama_agent(tmp_path)
        agent.add_to_history("Обсуждение OKR")
        captured = []
        agent._call_llm = MagicMock(
            side_effect=lambda p, **kw: captured.append(p) or "ok"
        )
        agent.analyze_okr()
        assert "✅" in captured[0] and "❌" in captured[0]

    def test_long_session_keeps_head_and_tail(self, tmp_path):
        agent = _make_ollama_agent(tmp_path)
        agent.add_to_history("НАЧАЛО СЕССИИ: цель A")
        for i in range(50):
            agent.add_to_history("середина " * 10)
        agent.add_to_history("КОНЕЦ СЕССИИ: итог B")
        captured = []
        agent._call_llm = MagicMock(
            side_effect=lambda p, **kw: captured.append(p) or "ok"
        )
        agent.analyze_okr()
        prompt = captured[0]
        assert "НАЧАЛО СЕССИИ" in prompt
        assert "КОНЕЦ СЕССИИ" in prompt

    def test_retries_on_empty_response(self, tmp_path):
        agent = _make_ollama_agent(tmp_path)
        agent.add_to_history("Цель")
        agent._call_llm = MagicMock(side_effect=["", "Повторный ответ"])
        assert agent.analyze_okr() == "Повторный ответ"

    def test_returns_error_message_on_exception(self, tmp_path):
        agent = _make_ollama_agent(tmp_path)
        agent.add_to_history("Цель")
        agent._call_llm = MagicMock(side_effect=Exception("LLM недоступен"))
        assert "Ошибка" in agent.analyze_okr()


# ===========================================================================
# 8. validate_existing_okr / analyze_selected_text
# ===========================================================================

class TestValidateAndAnalyze:
    def test_validate_existing_okr(self, tmp_path):
        agent = _make_ollama_agent(tmp_path)
        agent._call_llm = MagicMock(return_value="ТИП: Key Result\nОЦЕНКА: 8/10")
        assert "8/10" in agent.validate_existing_okr("Увеличить MAU до 100k к июню")

    def test_validate_okr_exception(self, tmp_path):
        agent = _make_ollama_agent(tmp_path)
        agent._call_llm = MagicMock(side_effect=Exception("API error"))
        assert "Ошибка" in agent.validate_existing_okr("some okr")

    def test_analyze_selected_empty_text(self, tmp_path):
        agent = _make_ollama_agent(tmp_path)
        assert "Нет" in agent.analyze_selected_text("")

    def test_analyze_selected_text(self, tmp_path):
        agent = _make_ollama_agent(tmp_path)
        agent._call_llm = MagicMock(return_value="Тип: Key Result. Оценка: 9/10.")
        assert "9/10" in agent.analyze_selected_text("Увеличить конверсию до 5%")


# ===========================================================================
# 9. process_file
# ===========================================================================

class TestProcessFile:
    def test_txt_file(self, tmp_path):
        f = tmp_path / "okr.txt"
        f.write_text("Увеличить ARR\nNPS > 60", encoding="utf-8")
        agent = _make_ollama_agent(tmp_path)
        ok, content = agent.process_file(str(f))
        assert ok and "ARR" in content

    def test_md_file(self, tmp_path):
        f = tmp_path / "goals.md"
        f.write_text("# OKR\n- Goal 1", encoding="utf-8")
        agent = _make_ollama_agent(tmp_path)
        ok, content = agent.process_file(str(f))
        assert ok and "Goal 1" in content

    def test_nonexistent_file(self, tmp_path):
        agent = _make_ollama_agent(tmp_path)
        ok, _ = agent.process_file(str(tmp_path / "missing.txt"))
        assert not ok

    def test_unsupported_extension(self, tmp_path):
        f = tmp_path / "data.xyz"
        f.write_bytes(b"binary")
        agent = _make_ollama_agent(tmp_path)
        ok, msg = agent.process_file(str(f))
        assert not ok and "Unsupported" in msg

    def test_csv_file(self, tmp_path):
        f = tmp_path / "data.csv"
        f.write_text("quarter,okr\nQ1,Goal A\nQ2,Goal B", encoding="utf-8")
        agent = _make_ollama_agent(tmp_path)
        ok, content = agent.process_file(str(f))
        assert ok and "Goal A" in content


# ===========================================================================
# 10. _is_virtual_device
# ===========================================================================

class TestIsVirtualDevice:
    def setup_method(self):
        from okr_agent_core import OKRAgentCore
        self.fn = OKRAgentCore._is_virtual_device

    def test_real_microphone(self):
        assert self.fn("Набор микрофонов (Realtek)") is False

    def test_headset(self):
        assert self.fn("Headset Microphone (USB Audio)") is False

    def test_stereo_mix(self):
        assert self.fn("Stereo Mix (Realtek)") is True

    def test_loopback(self):
        assert self.fn("WASAPI Loopback") is True

    def test_virtual_cable(self):
        assert self.fn("VB-Audio Virtual Cable") is True

    def test_voicemeeter(self):
        assert self.fn("VoiceMeeter Input") is True

    def test_wave_out(self):
        assert self.fn("Wave Out Mix") is True

    def test_case_insensitive(self):
        assert self.fn("STEREO MIX") is True


# ===========================================================================
# 11. _decode_device_name
# ===========================================================================

class TestDecodeDeviceName:
    def setup_method(self):
        from okr_agent_core import OKRAgentCore
        self.fn = OKRAgentCore._decode_device_name

    def test_plain_ascii(self):
        assert self.fn("Microphone (USB)") == "Microphone (USB)"

    def test_cyrillic_str(self):
        name = "Набор микрофонов (Realtek)"
        assert self.fn(name) == name

    def test_bytes_utf8(self):
        assert len(self.fn("Микрофон".encode("utf-8"))) > 0

    def test_bytes_cp1251(self):
        assert len(self.fn("Микрофон".encode("cp1251"))) > 0

    def test_integer_fallback(self):
        assert self.fn(42) == "42"


# ===========================================================================
# 12. transcribe_meeting — mocked _record_with_sounddevice
# ===========================================================================

class TestTranscribeMeeting:
    def _agent(self, tmp_path):
        import speech_recognition as _sr
        from okr_agent_core import OKRAgentCore
        agent = OKRAgentCore.__new__(OKRAgentCore)
        agent.provider = "ollama"
        agent.language = "ru-RU"
        agent.history = []
        agent.previous_okrs = {}
        agent.previous_okrs_file = str(tmp_path / "p.json")
        agent.recognizer = _sr.Recognizer()
        return agent

    def _pcm(self, seconds=2, rate=16000):
        return b'\x00\x00' * rate * seconds

    def _sd_patches(self):
        devices = [{"name": "Mic", "max_input_channels": 1,
                    "hostapi": 0, "default_samplerate": 16000}]
        hostapis = [{"name": "WASAPI"}]
        return (
            patch('sounddevice.query_devices', return_value=devices),
            patch('sounddevice.query_hostapis', return_value=hostapis),
        )

    def _fake_record_factory(self, stop, pcm):
        """Return fake that passes probe (call 1) then sets stop on recording (call 2+)."""
        call_count = [0]

        def fake_record(dev, max_duration=10.0, stop_event=None):
            call_count[0] += 1
            if call_count[0] >= 2:   # main recording call — signal stop after one chunk
                stop.set()
            return (pcm, 16000, 1)

        return fake_record

    def test_text_received_on_speech(self, tmp_path):
        import speech_recognition as _sr
        agent = self._agent(tmp_path)
        pcm = self._pcm()
        stop = threading.Event()
        calls = []

        p1, p2 = self._sd_patches()
        with p1, p2:
            with patch.object(agent, '_record_with_sounddevice',
                              side_effect=self._fake_record_factory(stop, pcm)):
                with patch.object(agent.recognizer, 'recognize_google', return_value="тест"):
                    agent.transcribe_meeting(0, stop, lambda t, ts: calls.append(t),
                                             chunk_seconds=2)
        assert "тест" in calls

    def test_silence_does_not_crash(self, tmp_path):
        import speech_recognition as _sr
        agent = self._agent(tmp_path)
        pcm = self._pcm()
        stop = threading.Event()
        statuses = []

        p1, p2 = self._sd_patches()
        with p1, p2:
            with patch.object(agent, '_record_with_sounddevice',
                              side_effect=self._fake_record_factory(stop, pcm)):
                with patch.object(agent.recognizer, 'recognize_google',
                                  side_effect=_sr.UnknownValueError()):
                    agent.transcribe_meeting(0, stop, lambda t, ts: None,
                                             lambda m: statuses.append(m),
                                             chunk_seconds=2)
        assert any("тишина" in s.lower() for s in statuses)

    def test_stt_error_shown_in_transcript(self, tmp_path):
        import speech_recognition as _sr
        agent = self._agent(tmp_path)
        pcm = self._pcm()
        stop = threading.Event()
        texts = []

        p1, p2 = self._sd_patches()
        with p1, p2:
            with patch.object(agent, '_record_with_sounddevice',
                              side_effect=self._fake_record_factory(stop, pcm)):
                with patch.object(agent.recognizer, 'recognize_google',
                                  side_effect=_sr.RequestError("503")):
                    agent.transcribe_meeting(0, stop, lambda t, ts: texts.append(t),
                                             chunk_seconds=2)
        assert any("Google STT" in t for t in texts)

    def test_no_devices_raises(self, tmp_path):
        agent = self._agent(tmp_path)
        stop = threading.Event()
        stop.set()
        with patch('sounddevice.query_devices', return_value=[]):
            with patch('sounddevice.query_hostapis', return_value=[]):
                with pytest.raises(OSError):
                    agent.transcribe_meeting(None, stop, chunk_seconds=2)

    def test_status_reports_device_name(self, tmp_path):
        import speech_recognition as _sr
        agent = self._agent(tmp_path)
        stop = threading.Event()
        statuses = []

        def fake_record(dev, max_duration=10.0, stop_event=None):
            stop.set()
            return (self._pcm(), 16000, 1)

        devices = [{"name": "Realtek Mic", "max_input_channels": 1,
                    "hostapi": 0, "default_samplerate": 16000}]
        with patch('sounddevice.query_devices', return_value=devices):
            with patch('sounddevice.query_hostapis', return_value=[{"name": "WASAPI"}]):
                with patch.object(agent, '_record_with_sounddevice', side_effect=fake_record):
                    with patch.object(agent.recognizer, 'recognize_google', return_value="ok"):
                        agent.transcribe_meeting(0, stop,
                                                 on_status=lambda m: statuses.append(m),
                                                 chunk_seconds=2)
        assert any("Realtek" in s or "Запись" in s for s in statuses)


# ===========================================================================
# 13. GUI — SettingsDialog
# ===========================================================================

class TestSettingsDialog:
    @pytest.fixture(autouse=True)
    def qt_app(self):
        from PyQt6.QtWidgets import QApplication
        app = QApplication.instance() or QApplication(sys.argv)
        yield app

    def _open_dialog(self, provider="claude", **keys):
        from gui import SettingsDialog
        with patch("gui.OKRAgentCore.list_microphones", return_value=[]):
            with patch("gui.OKRAgentCore.list_ollama_models", return_value=[]):
                return SettingsDialog(
                    parent=None,
                    current_provider=provider,
                    stored_api_key=keys.get("api_key", ""),
                    stored_gemini_key=keys.get("gemini_key", ""),
                    stored_groq_key=keys.get("groq_key", ""),
                    stored_mistral_key=keys.get("mistral_key", ""),
                    stored_kimi_key=keys.get("kimi_key", ""),
                )

    def test_provider_count_is_six(self):
        d = self._open_dialog()
        assert d.provider_combo.count() == 6

    def test_kimi_in_list(self):
        d = self._open_dialog()
        items = [d.provider_combo.itemText(i) for i in range(d.provider_combo.count())]
        assert any("Kimi" in item for item in items)

    def test_ollama_is_last(self):
        d = self._open_dialog()
        assert "Ollama" in d.provider_combo.itemText(d.provider_combo.count() - 1)

    def test_get_settings_has_all_keys(self):
        d = self._open_dialog(provider="claude", api_key="sk-ant")
        s = d.get_settings()
        for key in ("provider", "api_key", "gemini_api_key", "groq_api_key",
                    "mistral_api_key", "kimi_api_key", "ollama_url",
                    "ollama_model", "language", "mic_device_index"):
            assert key in s, f"Missing key: {key}"

    def test_get_settings_kimi(self):
        d = self._open_dialog(provider="kimi", kimi_key="sk-kimi-test")
        s = d.get_settings()
        assert s["provider"] == "kimi"
        assert s["kimi_api_key"] == "sk-kimi-test"

    @pytest.mark.parametrize("provider,idx", [
        ("claude", 0), ("gemini", 1), ("groq", 2),
        ("mistral", 3), ("kimi", 4), ("ollama", 5),
    ])
    def test_provider_index_mapping(self, provider, idx):
        d = self._open_dialog(provider=provider)
        assert d.provider_combo.currentIndex() == idx

    def test_kimi_key_restored(self):
        d = self._open_dialog(provider="kimi", kimi_key="sk-abc")
        assert d.kimi_key_input.text() == "sk-abc"


# ===========================================================================
# 14. Config persistence
# ===========================================================================

class TestConfigPersistence:
    @pytest.fixture(autouse=True)
    def qt_app(self):
        from PyQt6.QtWidgets import QApplication
        app = QApplication.instance() or QApplication(sys.argv)
        yield app

    def _bare_gui(self, tmp_path):
        from gui import OKRAgentGUI
        gui = OKRAgentGUI.__new__(OKRAgentGUI)
        gui.provider = "claude"
        gui.language = "ru-RU"
        gui.api_key = ""
        gui.gemini_api_key = ""
        gui.groq_api_key = ""
        gui.mistral_api_key = ""
        gui.kimi_api_key = ""
        gui.mic_device_index = -1
        gui.config_file = str(tmp_path / "config.json")
        return gui

    def test_kimi_key_persisted(self, tmp_path):
        gui = self._bare_gui(tmp_path)
        gui.kimi_api_key = "sk-persist"
        gui.provider = "kimi"
        gui.save_config()

        gui2 = self._bare_gui(tmp_path)
        gui2.load_config()
        assert gui2.kimi_api_key == "sk-persist"
        assert gui2.provider == "kimi"

    def test_all_provider_keys_in_config(self, tmp_path):
        gui = self._bare_gui(tmp_path)
        gui.groq_api_key = "gsk_x"
        gui.mistral_api_key = "mst_x"
        gui.kimi_api_key = "kmi_x"
        gui.save_config()

        with open(gui.config_file, encoding="utf-8") as f:
            cfg = json.load(f)
        assert cfg.get("kimi_api_key") == "kmi_x"
        assert "groq_api_key" in cfg
        assert "mistral_api_key" in cfg

import json
import os
import sys
import pytest
from pathlib import Path
from unittest.mock import MagicMock, patch, mock_open, PropertyMock

# Make sure project root is on path
sys.path.insert(0, str(Path(__file__).parent.parent))

# ---------------------------------------------------------------------------
# Helpers to build a mocked OKRAgentCore without real API clients
# ---------------------------------------------------------------------------

def make_claude_agent(tmp_path=None, language="ru-RU"):
    """Return OKRAgentCore with a mocked Anthropic client."""
    with patch("anthropic.Anthropic"), patch("httpx.Client"):
        from okr_agent_core import OKRAgentCore
        agent = OKRAgentCore(provider="claude", api_key="sk-test", language=language)
        if tmp_path:
            agent.previous_okrs_file = str(tmp_path / "previous_okrs.json")
            agent.previous_okrs = {}
        return agent


def make_gemini_agent(tmp_path=None):
    """Return OKRAgentCore with a mocked Gemini client."""
    mock_genai = MagicMock()
    with patch.dict("sys.modules", {"google": MagicMock(), "google.genai": mock_genai}):
        from okr_agent_core import OKRAgentCore
        agent = OKRAgentCore(provider="gemini", api_key="AIza-test")
        if tmp_path:
            agent.previous_okrs_file = str(tmp_path / "previous_okrs.json")
            agent.previous_okrs = {}
        agent.gemini_client = MagicMock()
        return agent


def make_ollama_agent(tmp_path=None):
    """Return OKRAgentCore with a mocked Ollama module."""
    mock_ollama = MagicMock()
    with patch.dict("sys.modules", {"ollama": mock_ollama}):
        from okr_agent_core import OKRAgentCore
        agent = OKRAgentCore(provider="ollama", ollama_model="mistral")
        if tmp_path:
            agent.previous_okrs_file = str(tmp_path / "previous_okrs.json")
            agent.previous_okrs = {}
        agent.ollama = mock_ollama
        return agent


# ===========================================================================
# 1. Initialization
# ===========================================================================

class TestInit:
    def test_claude_init_sets_provider(self):
        agent = make_claude_agent()
        assert agent.provider == "claude"

    def test_gemini_init_sets_provider(self):
        agent = make_gemini_agent()
        assert agent.provider == "gemini"

    def test_ollama_init_sets_provider(self):
        agent = make_ollama_agent()
        assert agent.provider == "ollama"
        assert agent.ollama_model == "mistral"

    def test_language_default(self):
        agent = make_claude_agent()
        assert agent.language == "ru-RU"

    def test_language_custom(self):
        agent = make_claude_agent(language="en-US")
        assert agent.language == "en-US"

    def test_history_empty_on_init(self):
        agent = make_claude_agent()
        assert agent.get_history() == []

    def test_claude_missing_api_key_raises(self):
        with patch("anthropic.Anthropic"), patch("httpx.Client"):
            with patch.dict(os.environ, {}, clear=True):
                os.environ.pop("ANTHROPIC_API_KEY", None)
                from okr_agent_core import OKRAgentCore
                with pytest.raises(ValueError, match="ANTHROPIC_API_KEY"):
                    OKRAgentCore(provider="claude", api_key=None)

    def test_gemini_missing_api_key_raises(self):
        mock_genai = MagicMock()
        with patch.dict("sys.modules", {"google": MagicMock(), "google.genai": mock_genai}):
            with patch.dict(os.environ, {}, clear=True):
                os.environ.pop("GEMINI_API_KEY", None)
                from okr_agent_core import OKRAgentCore
                with pytest.raises(ValueError, match="GEMINI_API_KEY"):
                    OKRAgentCore(provider="gemini", api_key=None)

    def test_invalid_provider_raises(self):
        with pytest.raises(ValueError, match="Provider must be"):
            from okr_agent_core import OKRAgentCore
            OKRAgentCore(provider="unknown", api_key="x")


# ===========================================================================
# 2. History management
# ===========================================================================

class TestHistory:
    def test_add_to_history(self):
        agent = make_claude_agent()
        agent.add_to_history("Увеличить выручку")
        assert "Увеличить выручку" in agent.get_history()

    def test_add_multiple(self):
        agent = make_claude_agent()
        agent.add_to_history("Msg 1")
        agent.add_to_history("Msg 2")
        assert len(agent.get_history()) == 2

    def test_clear_history(self):
        agent = make_claude_agent()
        agent.add_to_history("Msg 1")
        agent.clear_history()
        assert agent.get_history() == []

    def test_get_history_returns_copy(self):
        agent = make_claude_agent()
        agent.add_to_history("X")
        h = agent.get_history()
        h.append("Y")
        assert "Y" not in agent.get_history()


# ===========================================================================
# 3. Previous OKRs (load / save / context)
# ===========================================================================

class TestPreviousOKRs:
    def test_load_previous_okrs_missing_file(self, tmp_path):
        agent = make_claude_agent(tmp_path)
        result = agent.load_previous_okrs()
        assert result == {}

    def test_save_and_load(self, tmp_path):
        agent = make_claude_agent(tmp_path)
        data = {"Q1-2026": ["OKR 1", "OKR 2"]}
        agent.save_previous_okrs(data)
        loaded = agent.load_previous_okrs()
        assert loaded == data

    def test_add_okr_to_previous(self, tmp_path):
        agent = make_claude_agent(tmp_path)
        agent.add_okr_to_previous("Q2-2026", "Увеличить NPS до 70")
        assert "Q2-2026" in agent.previous_okrs
        assert "Увеличить NPS до 70" in agent.previous_okrs["Q2-2026"]

    def test_add_okr_appends_to_existing_quarter(self, tmp_path):
        agent = make_claude_agent(tmp_path)
        agent.add_okr_to_previous("Q1-2026", "OKR A")
        agent.add_okr_to_previous("Q1-2026", "OKR B")
        assert len(agent.previous_okrs["Q1-2026"]) == 2

    def test_get_previous_context_empty(self):
        agent = make_claude_agent()
        agent.previous_okrs = {}
        assert agent.get_previous_context() == ""

    def test_get_previous_context_contains_quarter(self, tmp_path):
        agent = make_claude_agent(tmp_path)
        agent.add_okr_to_previous("Q4-2025", "Расширить рынок")
        ctx = agent.get_previous_context()
        assert "Q4-2025" in ctx
        assert "Расширить рынок" in ctx


# ===========================================================================
# 4. Claude API call
# ===========================================================================

class TestCallClaude:
    def _make_response(self, text):
        block = MagicMock()
        block.type = "text"
        block.text = text
        resp = MagicMock()
        resp.content = [block]
        return resp

    def test_returns_text(self):
        agent = make_claude_agent()
        agent.anthropic_client.messages.create.return_value = self._make_response("  Ответ  ")
        result = agent._call_claude("Тест")
        assert result == "Ответ"

    def test_api_error_raises(self):
        agent = make_claude_agent()
        agent.anthropic_client.messages.create.side_effect = Exception("API down")
        with pytest.raises(Exception, match="Anthropic API Error"):
            agent._call_claude("Тест")

    def test_empty_content_returns_empty(self):
        agent = make_claude_agent()
        resp = MagicMock()
        resp.content = []
        agent.anthropic_client.messages.create.return_value = resp
        result = agent._call_claude("Тест")
        assert result == ""


# ===========================================================================
# 5. Gemini API call + fallback
# ===========================================================================

class TestCallGemini:
    def _make_response(self, text):
        resp = MagicMock()
        resp.text = text
        return resp

    def test_returns_text_on_success(self):
        agent = make_gemini_agent()
        agent.gemini_client.models.generate_content.return_value = self._make_response("  ОК  ")
        result = agent._call_gemini("Тест")
        assert result == "ОК"

    def test_fallback_on_429(self):
        """If first model returns 429, should try next model and succeed."""
        agent = make_gemini_agent()
        call_count = [0]

        def side_effect(model, contents):
            call_count[0] += 1
            if call_count[0] == 1:
                raise Exception("429 RESOURCE_EXHAUSTED quota exceeded")
            return self._make_response("Успех")

        agent.gemini_client.models.generate_content.side_effect = side_effect
        result = agent._call_gemini("Тест")
        assert result == "Успех"
        assert call_count[0] == 2

    def test_remembers_working_model(self):
        """After fallback, gemini_model attribute should be updated."""
        agent = make_gemini_agent()
        original_model = agent.gemini_model
        call_count = [0]

        def side_effect(model, contents):
            call_count[0] += 1
            if call_count[0] == 1:
                raise Exception("429 RESOURCE_EXHAUSTED")
            return self._make_response("OK")

        agent.gemini_client.models.generate_content.side_effect = side_effect
        agent._call_gemini("Тест")
        assert agent.gemini_model != original_model

    def test_all_models_fail_raises(self):
        """If all models return 429, should raise with helpful message."""
        agent = make_gemini_agent()
        agent.gemini_client.models.generate_content.side_effect = Exception("429 RESOURCE_EXHAUSTED")
        with pytest.raises(Exception, match="Превышен лимит"):
            agent._call_gemini("Тест")

    def test_non_quota_error_raises_immediately(self):
        """Non-429 errors should not trigger fallback."""
        agent = make_gemini_agent()
        call_count = [0]

        def side_effect(model, contents):
            call_count[0] += 1
            raise Exception("Invalid API key")

        agent.gemini_client.models.generate_content.side_effect = side_effect
        with pytest.raises(Exception, match="Gemini API Error"):
            agent._call_gemini("Тест")
        assert call_count[0] == 1  # stopped immediately, no fallback


# ===========================================================================
# 6. Ollama API call + auto-detection
# ===========================================================================

class TestCallOllama:
    def test_success(self):
        agent = make_ollama_agent()
        agent.ollama.generate.return_value = {"response": "  Результат  "}
        result = agent._call_ollama("Тест")
        assert result == "Результат"

    def test_success_with_object_response(self):
        """Ollama library may return an object with .response attribute instead of dict."""
        agent = make_ollama_agent()

        class OllamaResponse:
            response = "Результат объект"

        agent.ollama.generate.return_value = OllamaResponse()
        result = agent._call_ollama("Тест")
        assert result == "Результат объект"

    def test_404_auto_switches_to_available_model(self):
        """On 404, should auto-detect installed models and retry."""
        agent = make_ollama_agent()
        call_count = [0]

        def generate_side(model, prompt, stream, options=None):
            call_count[0] += 1
            if call_count[0] == 1:
                raise Exception("model 'neural-chat' not found (status code: 404)")
            return {"response": "OK с другой моделью"}

        agent.ollama.generate.side_effect = generate_side

        with patch.object(type(agent), "list_ollama_models", staticmethod(lambda: ["llama3", "mistral"])):
            result = agent._call_ollama("Тест")

        assert result == "OK с другой моделью"
        assert agent.ollama_model == "llama3"

    def test_404_no_models_raises(self):
        agent = make_ollama_agent()
        agent.ollama.generate.side_effect = Exception("not found (status code: 404)")
        with patch.object(type(agent), "list_ollama_models", staticmethod(lambda: [])):
            with pytest.raises(Exception, match="Нет установленных моделей"):
                agent._call_ollama("Тест")

    def test_connection_error_raises_friendly(self):
        agent = make_ollama_agent()
        agent.ollama.generate.side_effect = Exception("connection refused")
        with pytest.raises(Exception, match="Ollama не запущена"):
            agent._call_ollama("Тест")


# ===========================================================================
# 7. list_ollama_models
# ===========================================================================

class TestListOllamaModels:
    def test_returns_model_names(self):
        from okr_agent_core import OKRAgentCore
        mock_model = MagicMock()
        mock_model.model = "mistral:latest"
        mock_response = MagicMock()
        mock_response.models = [mock_model]

        mock_ollama = MagicMock()
        mock_ollama.list.return_value = mock_response

        with patch.dict("sys.modules", {"ollama": mock_ollama}):
            result = OKRAgentCore.list_ollama_models()

        assert "mistral:latest" in result

    def test_returns_empty_on_error(self):
        from okr_agent_core import OKRAgentCore
        mock_ollama = MagicMock()
        mock_ollama.list.side_effect = Exception("not running")

        with patch.dict("sys.modules", {"ollama": mock_ollama}):
            result = OKRAgentCore.list_ollama_models()

        assert result == []


# ===========================================================================
# 8. is_on_track
# ===========================================================================

class TestIsOnTrack:
    def test_business_goal_with_metrics_is_on_track(self):
        agent = make_claude_agent()
        # binary check → ДА, structure check → ДА
        agent._call_llm = MagicMock(side_effect=["ДА", "ДА"])
        on_track, details = agent.is_on_track("Увеличить выручку на 20% к концу Q2")
        assert on_track is True
        assert details == ""

    def test_non_business_goal_is_off_track(self):
        agent = make_claude_agent()
        agent._call_llm = MagicMock(side_effect=["НЕТ", "не бизнес-цель, личные предпочтения, нет метрик"])
        on_track, details = agent.is_on_track("Хочу купить сапоги")
        assert on_track is False
        assert details != ""

    def test_business_goal_without_metrics_is_off_track(self):
        agent = make_claude_agent()
        # binary → ДА (it's business), structure → НЕТ (no metrics/timeframe)
        agent._call_llm = MagicMock(side_effect=["ДА", "НЕТ", "нет метрик, нет сроков, нет измеримого результата"])
        on_track, details = agent.is_on_track("Улучшить продукт")
        assert on_track is False

    def test_llm_error_propagates(self):
        agent = make_claude_agent()
        agent._call_llm = MagicMock(side_effect=Exception("API Error"))
        with pytest.raises(Exception):
            agent.is_on_track("Тест")


# ===========================================================================
# 9. analyze_okr
# ===========================================================================

class TestAnalyzeOKR:
    def test_empty_history_returns_message(self):
        agent = make_claude_agent()
        result = agent.analyze_okr()
        assert result != ""  # returns some non-empty message when history is empty

    def test_returns_llm_response(self):
        agent = make_claude_agent()
        agent.add_to_history("Увеличить выручку на 30% к Q3")
        agent._call_llm = MagicMock(return_value="Хороший OKR, но добавьте метрики")
        result = agent.analyze_okr()
        assert "Хороший OKR" in result

    def test_llm_error_returns_error_string(self):
        agent = make_claude_agent()
        agent.add_to_history("Тест")
        agent._call_llm = MagicMock(side_effect=Exception("API down"))
        result = agent.analyze_okr()
        assert "API down" in result  # error message is included in output


# ===========================================================================
# 10. validate_existing_okr
# ===========================================================================

class TestValidateExistingOKR:
    def test_returns_llm_response(self):
        agent = make_claude_agent()
        agent._call_llm = MagicMock(return_value="OKR корректен")
        result = agent.validate_existing_okr("Увеличить NPS до 70 к Q2")
        assert "OKR корректен" in result

    def test_llm_error_returns_error_string(self):
        agent = make_claude_agent()
        agent._call_llm = MagicMock(side_effect=Exception("API Error"))
        result = agent.validate_existing_okr("Тест OKR")
        assert "Error" in result


# ===========================================================================
# 11. process_file
# ===========================================================================

class TestProcessFile:
    def test_txt_file(self, tmp_path):
        agent = make_claude_agent()
        f = tmp_path / "test.txt"
        f.write_text("OKR текст", encoding="utf-8")
        ok, content = agent.process_file(str(f))
        assert ok is True
        assert "OKR текст" in content

    def test_missing_file(self, tmp_path):
        agent = make_claude_agent()
        ok, content = agent.process_file(str(tmp_path / "missing.txt"))
        assert ok is False
        assert "not found" in content.lower()

    def test_unsupported_extension(self, tmp_path):
        agent = make_claude_agent()
        f = tmp_path / "file.xyz"
        f.write_bytes(b"data")
        ok, content = agent.process_file(str(f))
        assert ok is False
        assert "Unsupported" in content

    def test_csv_file(self, tmp_path):
        agent = make_claude_agent()
        f = tmp_path / "test.csv"
        f.write_text("col1,col2\nval1,val2", encoding="utf-8")
        ok, content = agent.process_file(str(f))
        assert ok is True
        assert "val1" in content

    def test_md_file(self, tmp_path):
        agent = make_claude_agent()
        f = tmp_path / "test.md"
        f.write_text("# OKR Header", encoding="utf-8")
        ok, content = agent.process_file(str(f))
        assert ok is True
        assert "OKR Header" in content


# ===========================================================================
# 12. analyze_okr_files
# ===========================================================================

class TestAnalyzeOKRFiles:
    def test_returns_analysis_for_txt(self, tmp_path):
        agent = make_claude_agent()
        f = tmp_path / "okr.txt"
        f.write_text("Увеличить продажи на 20%", encoding="utf-8")
        agent._call_llm = MagicMock(return_value="Хороший файл OKR")
        result = agent.analyze_okr_files([str(f)])
        assert "Хороший файл OKR" in result

    def test_no_files_returns_message(self):
        agent = make_claude_agent()
        result = agent.analyze_okr_files([])
        assert "Не удалось" in result or "No files" in result

    def test_llm_error_returns_error_string(self, tmp_path):
        agent = make_claude_agent()
        f = tmp_path / "okr.txt"
        f.write_text("OKR", encoding="utf-8")
        agent._call_llm = MagicMock(side_effect=Exception("API Error"))
        result = agent.analyze_okr_files([str(f)])
        assert "Error" in result


# ===========================================================================
# 13. recognize_speech
# ===========================================================================

_FAKE_DEVICE = {
    'name': 'Test Mic', 'max_input_channels': 1,
    'hostapi': 0, 'default_samplerate': 16000,
}
_FAKE_HOSTAPI = [{'name': 'Windows WASAPI'}]
_FAKE_PCM = b'\x00\x00' * 8192  # 8192 silent int16 samples


class TestRecognizeSpeech:
    def _sd_patches(self, devices=None, hostapis=None):
        """Return context-manager patches for sounddevice enumeration."""
        if devices is None:
            devices = [_FAKE_DEVICE]
        if hostapis is None:
            hostapis = _FAKE_HOSTAPI
        return (
            patch("sounddevice.query_devices", return_value=devices),
            patch("sounddevice.query_hostapis", return_value=hostapis),
        )

    def test_successful_recognition(self):
        agent = make_claude_agent()

        p1, p2 = self._sd_patches()
        with p1, p2, \
             patch("okr_agent_core.OKRAgentCore._record_with_sounddevice",
                   return_value=(_FAKE_PCM, 16000, 1)), \
             patch.object(agent.recognizer, "recognize_google", return_value="Привет"):
            ok, text = agent.recognize_speech(device_index=0)

        assert ok is True
        assert text == "Привет"

    def test_microphone_attribute_error(self):
        """When all devices fail, recognize_speech returns (False, error_message)."""
        agent = make_claude_agent()

        p1, p2 = self._sd_patches(devices=[])
        with p1, p2:
            ok, text = agent.recognize_speech()

        assert ok is False
        assert text

    def test_unknown_value_error(self):
        import speech_recognition as sr_lib
        agent = make_claude_agent()

        p1, p2 = self._sd_patches()
        with p1, p2, \
             patch("okr_agent_core.OKRAgentCore._record_with_sounddevice",
                   return_value=(_FAKE_PCM, 16000, 1)), \
             patch.object(agent.recognizer, "recognize_google",
                          side_effect=sr_lib.UnknownValueError()):
            ok, text = agent.recognize_speech(device_index=0)

        assert ok is False
        assert "распознана" in text.lower()

    def test_request_error(self):
        import speech_recognition as sr_lib
        agent = make_claude_agent()

        p1, p2 = self._sd_patches()
        with p1, p2, \
             patch("okr_agent_core.OKRAgentCore._record_with_sounddevice",
                   return_value=(_FAKE_PCM, 16000, 1)), \
             patch.object(agent.recognizer, "recognize_google",
                          side_effect=sr_lib.RequestError("service error")):
            ok, text = agent.recognize_speech(device_index=0)

        assert ok is False
        assert "распознавания" in text.lower()

    def test_passes_device_index(self):
        """When device_index=2 is given, _record_with_sounddevice is called with 2 first."""
        agent = make_claude_agent()
        record_calls = []

        def fake_record(dev_idx, max_duration=10.0, stop_event=None):
            record_calls.append(dev_idx)
            return _FAKE_PCM, 16000, 1

        p1, p2 = self._sd_patches()
        with p1, p2, \
             patch("okr_agent_core.OKRAgentCore._record_with_sounddevice",
                   side_effect=fake_record), \
             patch.object(agent.recognizer, "recognize_google", return_value="OK"):
            agent.recognize_speech(device_index=2)

        assert record_calls[0] == 2

    def test_default_device_index_not_passed(self):
        """device_index=None → enumerates all devices, never passes None to _record."""
        agent = make_claude_agent()
        record_calls = []

        def fake_record(dev_idx, max_duration=10.0, stop_event=None):
            record_calls.append(dev_idx)
            return _FAKE_PCM, 16000, 1

        p1, p2 = self._sd_patches()
        with p1, p2, \
             patch("okr_agent_core.OKRAgentCore._record_with_sounddevice",
                   side_effect=fake_record), \
             patch.object(agent.recognizer, "recognize_google", return_value="OK"):
            agent.recognize_speech(device_index=None)

        assert None not in record_calls
        assert len(record_calls) >= 1
