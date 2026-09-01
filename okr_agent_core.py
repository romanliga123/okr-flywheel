import speech_recognition as sr
import anthropic
import os
import json
import base64
from typing import Tuple, Dict, List
from pathlib import Path

class OKRAgentCore:
    """Core logic for OKR validation agent"""

    def __init__(self, provider: str = "claude", api_key: str = None,
                 ollama_model: str = "neural-chat", language: str = "ru-RU"):
        self.provider = provider
        self.ollama_model = ollama_model
        self.language = language
        self.recognizer = sr.Recognizer()
        self.history = []
        self.previous_okrs_file = 'previous_okrs.json'
        self.previous_okrs = self.load_previous_okrs()

        if provider == "claude":
            if not api_key:
                api_key = os.getenv('ANTHROPIC_API_KEY')
            if not api_key:
                raise ValueError("ANTHROPIC_API_KEY not provided and not set in environment")
            import httpx
            http_client = httpx.Client(verify=False)
            self.anthropic_client = anthropic.Anthropic(api_key=api_key, http_client=http_client)

        elif provider == "gemini":
            if not api_key:
                api_key = os.getenv('GEMINI_API_KEY')
            if not api_key:
                raise ValueError("GEMINI_API_KEY not provided and not set in environment")
            try:
                import google.generativeai as genai
                genai.configure(api_key=api_key.strip())
                self.gemini_api_key = api_key.strip()
                self.gemini_model = 'gemini-2.0-flash-lite'
            except ImportError:
                raise ImportError("google-generativeai package not installed. Run: pip install google-generativeai")

        elif provider == "groq":
            if not api_key:
                api_key = os.getenv('GROQ_API_KEY')
            if not api_key:
                raise ValueError("GROQ_API_KEY not provided and not set in environment")
            try:
                from groq import Groq
                self.groq_client = Groq(api_key=api_key.strip())
                self.groq_model = "llama-3.1-8b-instant"
            except ImportError:
                raise ImportError("groq package not installed. Run: uv add groq")

        elif provider == "mistral":
            if not api_key:
                api_key = os.getenv('MISTRAL_API_KEY')
            if not api_key:
                raise ValueError("MISTRAL_API_KEY not provided and not set in environment")
            try:
                from mistralai.client import Mistral
            except ImportError:
                try:
                    from mistralai import Mistral
                except ImportError:
                    raise ImportError("mistralai package not installed. Run: uv add mistralai")
            self.mistral_client = Mistral(api_key=api_key.strip())
            self.mistral_model = "open-mistral-nemo"

        elif provider == "kimi":
            if not api_key:
                api_key = os.getenv('KIMI_API_KEY')
            if not api_key:
                raise ValueError("KIMI_API_KEY not provided and not set in environment")
            import httpx
            self.kimi_api_key = api_key.strip()
            self.kimi_model = "moonshot-v1-8k"
            self.kimi_http = httpx.Client(verify=False, timeout=60)

        elif provider == "ollama":
            try:
                import ollama
                self.ollama = ollama
            except ImportError:
                raise ImportError("ollama package not installed. Run: uv add ollama")
        else:
            raise ValueError("Provider must be 'claude', 'gemini', 'groq', 'mistral', 'kimi' or 'ollama'")

    # ------------------------------------------------------------------
    # OKR storage
    # ------------------------------------------------------------------

    def load_previous_okrs(self) -> Dict[str, List[str]]:
        if os.path.exists(self.previous_okrs_file):
            with open(self.previous_okrs_file, 'r', encoding='utf-8') as f:
                return json.load(f)
        return {}

    def save_previous_okrs(self, okrs: Dict[str, List[str]]) -> None:
        with open(self.previous_okrs_file, 'w', encoding='utf-8') as f:
            json.dump(okrs, f, ensure_ascii=False, indent=4)

    def get_previous_context(self) -> str:
        if not self.previous_okrs:
            return ""
        context = "OKR предыдущих кварталов:\n"
        for quarter, okrs in self.previous_okrs.items():
            context += f"{quarter}: {', '.join(okrs)}\n"
        return context

    # ------------------------------------------------------------------
    # Microphone helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _decode_device_name(raw) -> str:
        """Return the most human-readable form of a PyAudio device name."""
        import locale

        def _is_readable(s: str) -> bool:
            """True if string consists mainly of printable ASCII or Cyrillic."""
            if not s:
                return False
            ok = sum(
                1 for c in s
                if ord(c) < 128 or '\u0400' <= c <= '\u04FF'
                or c in ' ()[]._-/\\,;:0123456789'
            )
            return ok >= len(s) * 0.75

        if isinstance(raw, bytes):
            sys_enc = locale.getpreferredencoding(False) or 'cp1251'
            for enc in (sys_enc, 'utf-8', 'cp1251', 'cp866', 'latin-1'):
                try:
                    decoded = raw.decode(enc)
                    if _is_readable(decoded):
                        return decoded
                except Exception:
                    pass
            return raw.decode('ascii', errors='replace')

        if isinstance(raw, str):
            if _is_readable(raw):
                return raw
            # Try mojibake fix: string was decoded as latin-1 but bytes are UTF-8
            try:
                fixed = raw.encode('latin-1').decode('utf-8')
                if _is_readable(fixed):
                    return fixed
            except Exception:
                pass
            # Try: bytes are cp1251
            try:
                fixed = raw.encode('latin-1').decode('cp1251')
                if _is_readable(fixed):
                    return fixed
            except Exception:
                pass
            # Try: bytes are cp866 (DOS Russian)
            try:
                fixed = raw.encode('latin-1').decode('cp866')
                if _is_readable(fixed):
                    return fixed
            except Exception:
                pass
            return raw

        return str(raw)

    @staticmethod
    def repair_windows_audio() -> tuple:
        """Restart the Windows Audio service to fix AUDCLNT_E_DEVICE_INVALIDATED.

        Returns (success: bool, message: str).
        """
        import ctypes
        import subprocess
        import time

        is_admin = bool(ctypes.windll.shell32.IsUserAnAdmin())
        if is_admin:
            try:
                subprocess.run(
                    ['net', 'stop', 'audiosrv', '/y'],
                    capture_output=True, timeout=20,
                    creationflags=subprocess.CREATE_NO_WINDOW,
                )
                time.sleep(2)
                subprocess.run(
                    ['net', 'start', 'audiosrv'],
                    capture_output=True, timeout=20,
                    creationflags=subprocess.CREATE_NO_WINDOW,
                )
                time.sleep(2)
                return True, "Служба Windows Audio перезапущена. Нажмите '🔍 Авто' и повторите запись."
            except Exception as e:
                return False, f"Ошибка при перезапуске службы: {e}"

        # Not admin — try UAC elevation via ShellExecuteW runas
        try:
            ret = ctypes.windll.shell32.ShellExecuteW(
                None, "runas", "cmd.exe",
                '/c net stop audiosrv && net start audiosrv && echo. && echo Готово! Закройте окно. && timeout /t 5',
                None, 1,
            )
            if ret > 32:
                return True, (
                    "UAC-запрос отправлен. Разрешите выполнение в окне контроля учётных записей.\n"
                    "После перезапуска службы закройте cmd-окно и нажмите '🔍 Авто'."
                )
        except Exception:
            pass

        # UAC failed or was declined — open Services GUI instead
        try:
            subprocess.Popen(
                ['mmc', 'services.msc'],
                creationflags=subprocess.CREATE_NO_WINDOW,
            )
            return False, (
                "Не удалось автоматически перезапустить службу.\n\n"
                "Открыто окно «Службы Windows»:\n"
                "1. Найдите «Windows Audio»\n"
                "2. Правая кнопка → Перезапустить\n"
                "Или просто перезагрузите компьютер."
            )
        except Exception:
            pass

        return False, (
            "Требуется перезагрузка компьютера.\n\n"
            "Все пути захвата аудио недоступны (AUDCLNT_E_DEVICE_INVALIDATED).\n"
            "После перезагрузки микрофон заработает."
        )

    @staticmethod
    def auto_detect_microphone() -> tuple:
        """Return (sd_device_index, name) for the best available microphone."""
        try:
            import sounddevice as _sd

            # Prefer default input device
            try:
                default_idx = _sd.default.device[0]
                if isinstance(default_idx, int) and default_idx >= 0:
                    d = _sd.query_devices(default_idx)
                    if d.get('max_input_channels', 0) > 0:
                        return (default_idx, d.get('name', 'Микрофон'))
            except Exception:
                pass

            # Fall back: WDM-KS first (bypasses WavesAudioService), then WASAPI, then others
            devices = list(_sd.query_devices())
            hostapis = list(_sd.query_hostapis())
            api_priority = {}
            for i, api in enumerate(hostapis):
                name = api.get('name', '')
                if 'WDM' in name or 'KS' in name:
                    api_priority[i] = 0
                elif 'WASAPI' in name:
                    api_priority[i] = 1
                else:
                    api_priority[i] = 2

            inputs = [
                (i, d) for i, d in enumerate(devices)
                if d.get('max_input_channels', 0) > 0
            ]
            inputs.sort(key=lambda x: api_priority.get(x[1].get('hostapi', 0), 99))
            if inputs:
                i, d = inputs[0]
                return (i, d.get('name', f'Device {i}'))
        except Exception:
            pass
        return (-1, 'Системный микрофон')

    @classmethod
    def list_microphones(cls) -> list:
        """Return deduplicated list of (sd_device_index, display_name) for real input devices.

        Uses sounddevice, preferring WDM-KS (bypasses WavesAudioService on Windows).
        Falls back to speech_recognition enumeration if sounddevice is unavailable.
        """
        result = []
        try:
            import sounddevice as _sd

            devices = list(_sd.query_devices())
            hostapis = list(_sd.query_hostapis())

            api_priority = {}
            for i, api in enumerate(hostapis):
                name = api.get('name', '')
                if 'WDM' in name or 'KS' in name:
                    api_priority[i] = 0
                elif 'WASAPI' in name:
                    api_priority[i] = 1
                elif 'DirectSound' in name:
                    api_priority[i] = 2
                else:
                    api_priority[i] = 3

            seen: dict = {}
            for i, d in enumerate(devices):
                if d.get('max_input_channels', 0) <= 0:
                    continue
                name = d.get('name', f'Device {i}')
                if cls._is_virtual_device(name):
                    continue
                hostapi = d.get('hostapi', 0)
                priority = api_priority.get(hostapi, 99)
                base = name.split('(')[0].strip()
                if base not in seen or priority < seen[base][2]:
                    seen[base] = (i, name, priority)

            for base, (idx, name, _) in sorted(seen.items(), key=lambda x: (x[1][2], x[1][0])):
                result.append((idx, name))

        except Exception:
            try:
                for i, name in enumerate(sr.Microphone.list_microphone_names()):
                    result.append((i, cls._decode_device_name(name)))
            except Exception:
                pass
        return result

    # Device names that indicate virtual/loopback (used only in recognize_speech fallback)
    _VIRTUAL_PATTERNS = (
        'переназначение', 'первичный драйвер', 'динамик', 'стерео микшер',
        'stereo mix', 'wave out', 'loopback', 'what u hear', 'output',
        'voicemeeter', 'virtual cable', 'vb-audio',
    )

    @classmethod
    def _is_virtual_device(cls, name: str) -> bool:
        n = name.lower()
        return any(p in n for p in cls._VIRTUAL_PATTERNS)

    @staticmethod
    def _record_with_sounddevice(dev_idx: int, max_duration: float = 10.0,
                                  stop_event=None) -> tuple:
        """Record audio via sounddevice callback API (works with all PortAudio host APIs).

        Uses callback mode so it works with WDM-KS (which blocks the PortAudio
        blocking API on Windows). Only honours stop_event after the first audio
        callback fires — avoids a race where the user presses Stop before recording
        actually starts.

        Returns (pcm_bytes, sample_rate, channels).
        Raises OSError if the device fails to open or produces no data.
        """
        import sounddevice as _sd
        import queue as _queue
        import time as _time

        d = _sd.query_devices(dev_idx)
        sr_rate = int(d.get('default_samplerate', 44100))
        # Always record mono — sr.AudioData has no channel parameter; stereo bytes
        # fed as mono cause Google STT to receive audio at half speed (garbled text).
        ch = 1

        q = _queue.Queue()

        def _cb(indata, frames, time_info, status):
            q.put(bytes(indata))

        try:
            stream = _sd.InputStream(
                samplerate=sr_rate, blocksize=2048,
                device=dev_idx, channels=ch,
                dtype='int16', callback=_cb,
            )
        except Exception as e:
            raise OSError(f"Открытие устройства {dev_idx} ({d.get('name', '?')}): {e}")

        with stream:
            t0 = _time.time()
            data_seen = False
            while _time.time() - t0 < max_duration:
                _time.sleep(0.05)
                if not data_seen and not q.empty():
                    data_seen = True
                if data_seen and stop_event is not None and stop_event.is_set():
                    break
                if not data_seen and _time.time() - t0 > 1.5:
                    break

        chunks = []
        while not q.empty():
            try:
                chunks.append(q.get_nowait())
            except Exception:
                break
        raw = b''.join(chunks)

        if len(raw) < 128:
            raise OSError(
                f"Устройство {d.get('name', dev_idx)}: нет данных ({len(raw)} байт)"
            )

        return raw, sr_rate, ch

    def recognize_speech(self, device_index: int = None,
                          stop_event=None) -> Tuple[bool, str]:
        """Record and recognise speech using sounddevice exclusively.

        Tries the selected device first (if any), then all other input devices
        in order: WDM-KS first (bypasses WavesAudioService), then WASAPI, then
        other APIs. stop_event stops recording gracefully once audio is flowing.
        """
        try:
            import sounddevice as _sd
        except ImportError:
            return False, (
                "Библиотека sounddevice не установлена.\n"
                "Запустите в терминале: uv add sounddevice"
            )

        # Build ordered list of sounddevice device indices to try
        devices_to_try: list = []
        if device_index is not None and device_index >= 0:
            devices_to_try.append(device_index)

        try:
            all_devices = list(_sd.query_devices())
            hostapis = list(_sd.query_hostapis())

            api_priority: dict = {}
            for i, api in enumerate(hostapis):
                name = api.get('name', '')
                if 'WDM' in name or 'KS' in name:
                    api_priority[i] = 0
                elif 'WASAPI' in name:
                    api_priority[i] = 1
                else:
                    api_priority[i] = 2

            others = [
                i for i, d in enumerate(all_devices)
                if d.get('max_input_channels', 0) > 0
                and i not in devices_to_try
                and not self._is_virtual_device(d.get('name', ''))
            ]
            others.sort(key=lambda i: (
                api_priority.get(all_devices[i].get('hostapi', 0), 99), i
            ))
            devices_to_try.extend(others)
        except Exception:
            pass

        last_error = (
            "Микрофон недоступен. Проверьте:\n"
            "1. Настройки Windows → Конфиденциальность → Микрофон → разрешить классическим приложениям\n"
            "2. Физическое подключение гарнитуры\n"
            "3. Попробуйте стандартный Диктофон Windows — если там тоже нет записи, проблема в системе"
        )

        audio = None
        for dev_idx in devices_to_try:
            try:
                raw, sr_rate, ch = self._record_with_sounddevice(
                    dev_idx, max_duration=10.0, stop_event=stop_event
                )
                audio = sr.AudioData(raw, sr_rate, 2)
                break
            except Exception as e:
                last_error = str(e)
                continue

        if audio is None:
            return False, last_error

        try:
            text = self.recognizer.recognize_google(audio, language=self.language)
            return True, text
        except sr.UnknownValueError:
            return False, "Речь не распознана. Говорите чётче или ближе к микрофону."
        except sr.RequestError as e:
            return False, f"Ошибка сервиса распознавания речи: {e}"
        except Exception as e:
            return False, f"Ошибка распознавания: {e}"

    def transcribe_meeting(self, device_index=None, stop_event=None,
                           on_text=None, on_status=None,
                           chunk_seconds: int = 10) -> None:
        """Continuously record meeting audio and transcribe in chunks via Google STT.

        Uses _record_with_sounddevice for each chunk — the same proven path
        as recognize_speech on the main screen.
        """
        import sounddevice as _sd
        import time as _time
        import threading as _threading

        def _status(msg: str):
            if on_status:
                on_status(msg)

        # Build device list — same logic as recognize_speech
        devices_to_try: list = []
        if device_index is not None and device_index >= 0:
            devices_to_try.append(device_index)
        try:
            all_devices = list(_sd.query_devices())
            hostapis  = list(_sd.query_hostapis())
            api_priority: dict = {}
            for i, api in enumerate(hostapis):
                name = api.get('name', '')
                if 'WDM' in name or 'KS' in name:
                    api_priority[i] = 0
                elif 'WASAPI' in name:
                    api_priority[i] = 1
                else:
                    api_priority[i] = 2
            others = [
                i for i, d in enumerate(all_devices)
                if d.get('max_input_channels', 0) > 0
                and i not in devices_to_try
                and not self._is_virtual_device(d.get('name', ''))
            ]
            others.sort(key=lambda i: (api_priority.get(all_devices[i].get('hostapi', 0), 99), i))
            devices_to_try.extend(others)
            all_devices_ref = all_devices
        except Exception:
            all_devices_ref = []

        if not devices_to_try:
            raise OSError("Нет доступных устройств записи. Подключите микрофон.")

        # Find first device that actually produces audio — same probe as recognize_speech
        working_dev: int | None = None
        working_rate: int = 44100
        for dev_idx in devices_to_try:
            try:
                raw, rate, _ = self._record_with_sounddevice(dev_idx, max_duration=2.0)
                if len(raw) >= 128:
                    working_dev = dev_idx
                    working_rate = rate
                    break
            except Exception:
                continue

        if working_dev is None:
            raise OSError(
                "Ни одно устройство записи не передаёт аудио.\n\n"
                "• Настройки → выберите микрофон вручную или нажмите '🔍 Авто'\n"
                "• Проверьте, что микрофон не заглушён в микшере Windows\n"
                "• Закройте другие программы, использующие микрофон\n"
                "• Нажмите '🔧 Починить' в Настройках"
            )

        dev_name = (all_devices_ref[working_dev].get('name', str(working_dev))
                    if working_dev < len(all_devices_ref) else str(working_dev))
        _status(f"🎙 Запись: {dev_name}  {working_rate} Гц  •  фрагменты по {chunk_seconds} сек")

        orig_timeout = self.recognizer.operation_timeout
        self.recognizer.operation_timeout = 15
        chunk_num = 0
        pending: list = []

        def _send_chunk(raw: bytes, n: int, rate: int):
            _status(f"⏱ Отправляю фрагмент {n} в Google STT…")
            try:
                audio = sr.AudioData(raw, rate, 2)
                text = self.recognizer.recognize_google(audio, language=self.language)
                if text:
                    if on_text:
                        on_text(text, _time.strftime('%H:%M:%S'))
                    _status(f"✅ Фрагмент {n} распознан")
                else:
                    _status(f"🔇 Фрагмент {n}: пусто")
            except sr.UnknownValueError:
                _status(f"🔇 Фрагмент {n}: тишина")
            except sr.RequestError as e:
                _status(f"⚠️ Google STT недоступен (фрагмент {n}): {e}")
                if on_text:
                    on_text(f"[Google STT недоступен: {e}]", _time.strftime('%H:%M:%S'))
            except Exception as e:
                _status(f"⚠️ Ошибка фрагмента {n}: {e}")
                if on_text:
                    on_text(f"[Ошибка: {e}]", _time.strftime('%H:%M:%S'))

        try:
            while not (stop_event and stop_event.is_set()):
                try:
                    # Record one chunk — exactly like recognize_speech, same device, same path
                    raw, rate, _ = self._record_with_sounddevice(
                        working_dev,
                        max_duration=float(chunk_seconds),
                        stop_event=stop_event,
                    )
                except OSError as e:
                    _status(f"⚠️ Ошибка записи: {e}")
                    _time.sleep(1)
                    continue

                if len(raw) < 128:
                    continue  # empty chunk (e.g. stop pressed immediately)

                chunk_num += 1
                n = chunk_num
                t = _threading.Thread(
                    target=_send_chunk, args=(raw, n, rate), daemon=True
                )
                t.start()
                pending.append(t)
                pending = [p for p in pending if p.is_alive()]
        finally:
            self.recognizer.operation_timeout = orig_timeout

        # Wait for in-flight transcriptions (max 30 s)
        deadline = _time.time() + 30
        for t in pending:
            rem = deadline - _time.time()
            if rem > 0:
                t.join(timeout=rem)

    # ------------------------------------------------------------------
    # LLM routing
    # ------------------------------------------------------------------

    def _call_llm(self, prompt: str, max_tokens: int = 500, num_ctx: int = 1024) -> str:
        if self.provider == "claude":
            return self._call_claude(prompt, max_tokens)
        elif self.provider == "gemini":
            return self._call_gemini(prompt, max_tokens)
        elif self.provider == "groq":
            return self._call_groq(prompt, max_tokens)
        elif self.provider == "mistral":
            return self._call_mistral(prompt, max_tokens)
        elif self.provider == "kimi":
            return self._call_kimi(prompt, max_tokens)
        else:
            return self._call_ollama(prompt, max_tokens, num_ctx)

    def _call_claude(self, prompt: str, max_tokens: int = 500) -> str:
        try:
            response = self.anthropic_client.messages.create(
                model="claude-opus-4-6",
                max_tokens=max_tokens,
                system="Всегда отвечай на русском языке. Давай развёрнутые ответы.",
                messages=[{"role": "user", "content": prompt}]
            )
            for block in response.content:
                if block.type == "text":
                    return block.text.strip()
            return ""
        except Exception as e:
            raise Exception(f"Anthropic API Error: {e}")

    # Gemini 2.0 models only — 1.5 deprecated from API as of 2026
    GEMINI_FALLBACK_MODELS = [
        'gemini-2.0-flash-lite',
        'gemini-2.0-flash',
        'gemini-2.0-flash-exp',
        'gemini-2.0-flash-thinking-exp-01-21',
        'gemini-2.0-pro-exp-02-05',
    ]

    def _call_gemini(self, prompt: str, max_tokens: int = 500) -> str:
        import google.generativeai as genai
        import re, time as _time
        models = [self.gemini_model] + [m for m in self.GEMINI_FALLBACK_MODELS if m != self.gemini_model]
        last_error = None
        for model in models:
            try:
                m = genai.GenerativeModel(model)
                response = m.generate_content(prompt)
                if model != self.gemini_model:
                    self.gemini_model = model
                return response.text.strip()
            except Exception as e:
                error_str = str(e)
                if '429' in error_str or 'RESOURCE_EXHAUSTED' in error_str \
                        or '404' in error_str or 'NOT_FOUND' in error_str:
                    last_error = e
                    if '429' in error_str or 'RESOURCE_EXHAUSTED' in error_str:
                        _time.sleep(2)
                    continue
                raise Exception(f"Gemini API Error: {e}")
        error_str = str(last_error)
        retry_match = re.search(r"'retryDelay':\s*'(\d+)s'", error_str)
        retry_info = f" Повторите через {retry_match.group(1)} секунд." if retry_match else " Попробуйте через минуту."
        raise Exception(f"Превышен лимит запросов Gemini.{retry_info}")

    # Groq fallback model chain (all free-tier)
    GROQ_FALLBACK_MODELS = [
        "llama-3.1-8b-instant",
        "llama-3.3-70b-versatile",
        "meta-llama/llama-4-scout-17b-16e-instruct",
        "gemma2-9b-it",
        "mixtral-8x7b-32768",
        "llama3-8b-8192",
        "llama3-70b-8192",
        "gemma-7b-it",
    ]

    def _call_groq(self, prompt: str, max_tokens: int = 500) -> str:
        models = [self.groq_model] + [m for m in self.GROQ_FALLBACK_MODELS if m != self.groq_model]
        last_error = None
        for model in models:
            try:
                response = self.groq_client.chat.completions.create(
                    model=model,
                    messages=[
                        {"role": "system", "content": "Всегда отвечай на русском языке. Давай развёрнутые ответы."},
                        {"role": "user", "content": prompt},
                    ],
                    max_tokens=max_tokens,
                    temperature=0.1,
                )
                if model != self.groq_model:
                    self.groq_model = model
                return response.choices[0].message.content.strip()
            except Exception as e:
                error_str = str(e).lower()
                if any(k in error_str for k in ('rate', '429', '503', 'capacity', 'model', 'overloaded', 'unavailable')):
                    last_error = e
                    continue
                raise Exception(f"Groq API Error: {e}")
        raise Exception(f"Groq: все модели недоступны. {last_error}")

    # Mistral fallback model chain (all free/open)
    MISTRAL_FALLBACK_MODELS = [
        "open-mistral-nemo",
        "mistral-small-latest",
        "open-mixtral-8x7b",
        "open-mistral-7b",
    ]

    def _call_mistral(self, prompt: str, max_tokens: int = 500) -> str:
        models = [self.mistral_model] + [m for m in self.MISTRAL_FALLBACK_MODELS if m != self.mistral_model]
        last_error = None
        for model in models:
            try:
                response = self.mistral_client.chat.complete(
                    model=model,
                    messages=[
                        {"role": "system", "content": "Всегда отвечай на русском языке. Давай развёрнутые ответы."},
                        {"role": "user", "content": prompt},
                    ],
                    max_tokens=max_tokens,
                    temperature=0.1,
                )
                if model != self.mistral_model:
                    self.mistral_model = model
                return response.choices[0].message.content.strip()
            except Exception as e:
                error_str = str(e).lower()
                if 'rate' in error_str or '429' in error_str or 'model' in error_str:
                    last_error = e
                    continue
                raise Exception(f"Mistral API Error: {e}")
        raise Exception(f"Mistral: все модели недоступны. {last_error}")

    def _call_kimi(self, prompt: str, max_tokens: int = 500) -> str:
        payload = {
            "model": self.kimi_model,
            "max_tokens": max_tokens,
            "messages": [
                {"role": "system", "content": "Всегда отвечай на русском языке. Давай развёрнутые ответы."},
                {"role": "user", "content": prompt},
            ],
        }
        try:
            response = self.kimi_http.post(
                "https://api.moonshot.cn/v1/chat/completions",
                headers={
                    "Authorization": f"Bearer {self.kimi_api_key}",
                    "Content-Type": "application/json",
                },
                json=payload,
            )
            response.raise_for_status()
            data = response.json()
            return data["choices"][0]["message"]["content"].strip()
        except Exception as e:
            raise Exception(f"Kimi API Error: {e}")

    @staticmethod
    def list_ollama_models() -> list:
        try:
            import ollama
            response = ollama.list()
            models = getattr(response, 'models', None)
            if models:
                return [getattr(m, 'model', str(m)) for m in models]
            if isinstance(response, dict):
                return [m.get('model', m.get('name', '')) for m in response.get('models', [])]
        except Exception:
            pass
        return []

    def _call_ollama(self, prompt: str, max_tokens: int = 500, num_ctx: int = 1024) -> str:
        def _generate(model: str) -> str:
            response = self.ollama.generate(
                model=model,
                prompt=prompt,
                stream=False,
                options={"num_predict": max_tokens, "num_ctx": num_ctx, "temperature": 0.1},
            )
            if isinstance(response, dict):
                return response['response'].strip()
            return response.response.strip()

        try:
            return _generate(self.ollama_model)
        except Exception as e:
            error_str = str(e)
            if 'connection' in error_str.lower() or 'refused' in error_str.lower():
                raise Exception("Ollama не запущена. Откройте приложение Ollama и попробуйте снова.")
            if '404' in error_str or 'not found' in error_str.lower():
                available = self.list_ollama_models()
                if available:
                    self.ollama_model = available[0]
                    try:
                        return _generate(self.ollama_model)
                    except Exception as e2:
                        raise Exception(f"Ollama Error: {e2}")
                raise Exception("Нет установленных моделей Ollama.\nУстановите: ollama pull mistral")
            raise Exception(f"Ollama Error: {e}")

    # ------------------------------------------------------------------
    # Core logic
    # ------------------------------------------------------------------

    def is_on_track(self, message: str) -> Tuple[bool, str]:
        prev_context = self.get_previous_context()

        binary_prompt = f"""Ты строгий фильтр OKR. Отвечай ТОЛЬКО "ДА" или "НЕТ".

БИЗНЕС-ЦЕЛЬ = напрямую влияет на выручку, клиентов, продукт, рост или KPI компании.

ВСЕГДА НЕТ:
- Предметы: "сапоги", "телефон", "кофе", "стол"
- Личные желания: "хочу...", "нравится...", "нужны мне"
- Быт: погода, еда, покупки, хобби
- Одно слово без бизнес-контекста

ВСЕГДА ДА:
- Метрики: NPS, ARR, MAU, конверсия, выручка, отток
- Сроки + цифры: "увеличить на X% к Q2", "снизить до Y"
- Продукт/команда: "запустить фичу", "нанять 5 инженеров"

Сообщение: "{message}"
Это бизнес-цель? (ДА/НЕТ):"""

        try:
            binary_result = self._call_llm(binary_prompt, max_tokens=5).strip().upper()
            is_business = "НЕТ" not in binary_result

            if not is_business:
                reasons_prompt = f"""Сообщение: "{message}"
Объясни кратко (2-3 предложения), почему это НЕ подходит для OKR компании. Отвечай на русском."""
                reasons = self._call_llm(reasons_prompt, max_tokens=200)
                return False, reasons

            structure_prompt = f"""Отвечай ТОЛЬКО "ДА" или "НЕТ".
По методологии OKR:
- Objective (цель): качественная, без цифр, вдохновляющая — ДА если это чёткий Objective
- Key Result (ключевой результат): содержит измеримый результат с цифрой И сроком — ДА если это чёткий KR
Является ли сообщение корректным Objective ИЛИ корректным Key Result по методологии OKR?
{prev_context}
Сообщение: "{message}"
Ответ (ДА/НЕТ):"""
            structure_result = self._call_llm(structure_prompt, max_tokens=5).strip().upper()

            if "НЕТ" not in structure_result:
                return True, ""
            reasons_prompt = f"""Сообщение: "{message}"
По методологии OKR — что не так и что добавить?
Objective должен быть качественным (без цифр), Key Result — измеримым результатом с цифрой и сроком (не задачей).
Объясни кратко (2-3 предложения) на русском, чего конкретно не хватает."""
            reasons = self._call_llm(reasons_prompt, max_tokens=200)
            return False, reasons

        except Exception:
            raise

    def analyze_okr(self, use_previous_context: bool = True) -> str:
        if not self.history:
            return "История обсуждений пуста. Добавьте сообщения через ручной ввод или запись."

        prev_context = self.get_previous_context() if use_previous_context else ""

        # Full session transcript — keep head + tail when very long
        full_text = '\n'.join(self.history)
        total_chunks = len(self.history)
        MAX = 2000
        if len(full_text) <= MAX:
            history_text = full_text
        else:
            head = full_text[:800]
            tail = full_text[-(MAX - 800):]
            history_text = f"{head}\n...[середина сессии сокращена]...\n{tail}"

        prev_note = (
            "Учитывай OKR предыдущих кварталов при анализе.\n" if prev_context else ""
        )

        prompt = f"""Ты эксперт по методологии OKR. Проанализируй стенограмму митинга ({total_chunks} фрагментов) и выдай анализ СТРОГО в указанном формате.

Критерии оценки OKR:
• Objective — качественная вдохновляющая цель БЕЗ цифр («Куда идём?»)
• Key Result — измеримый РЕЗУЛЬТАТ с цифрой и сроком, НЕ задача («Как узнаем что достигли?»)
• KR-задача («сделать», «запустить», «провести») = нарушение методологии
• Амбициозность: 70% вероятность достижения — оптимально

{prev_context}{prev_note}Стенограмма:
{history_text}

Формат ответа — для каждого найденного Objective и Key Result используй ЭТОТ шаблон:

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
🎯 OBJECTIVE [номер]
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Текущая формулировка:
  [текст Objective]

Оценка: [X]/10

Проблема с формулировкой:
  [что именно нарушает методологию OKR]

Как улучшить формулировку:
  [конкретные изменения текста]

❌ Было (текущая формулировка):
  [точный текущий текст]
✅ Стало (улучшенная формулировка):
  [новый текст — качественная цель без цифр]

  ┈ KR[N] ┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈
  Текущая формулировка:
    [текст KR]

  Оценка: [X]/10

  Проблема с формулировкой:
    [что не так — задача вместо результата, нет цифры, нет срока и т.д.]

  Как улучшить формулировку:
    [конкретные изменения текста KR]

  ❌ Было (текущая формулировка):
    [точный текущий KR]
  ✅ Стало (улучшенная формулировка):
    [новый KR — измеримый результат с цифрой и сроком]

В конце добавь итог:
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
📊 ИТОГ
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Средняя оценка Objectives: [X]/10
Средняя оценка Key Results: [X]/10
Главные системные проблемы:
  • [проблема 1]
  • [проблема 2]"""

        try:
            result = self._call_llm(prompt, max_tokens=900, num_ctx=2048)
            if not result or not result.strip():
                simple = (
                    f"Оцени по методологии OKR ({total_chunks} фрагм.): {full_text[:500]}.\n"
                    "Objective — цель без цифр, Key Result — измеримый результат с цифрами.\n"
                    "Дай 2 примера: ❌ как сейчас → ✅ по методологии OKR. Ответ на русском."
                )
                result = self._call_llm(simple, max_tokens=600, num_ctx=1024)
            if not result or not result.strip():
                return "Модель не вернула ответ. Попробуйте Gemini или Groq вместо Ollama."
            return result
        except Exception as e:
            return f"Ошибка анализа OKR: {e}"

    def analyze_selected_text(self, text: str) -> str:
        if not text or not text.strip():
            return "Нет выделенного текста для анализа."
        prompt = f"""Ты эксперт по методологии OKR. Оцени фрагмент по критериям OKR и дай ответ на русском.

Методология OKR:
• Objective — качественная вдохновляющая цель БЕЗ цифр («Стать лидером рынка», «Сделать продукт любимым»)
• Key Result — измеримый РЕЗУЛЬТАТ с цифрой и сроком, НЕ задача («Увеличить NPS с 42 до 65 к Q3»)
• Инициатива — задача/активность («Запустить фичу», «Провести исследование») — это НЕ KR

Фрагмент:
"{text.strip()}"

Структура ответа:
1. ТИП: Objective / Key Result / Инициатива (не KR) / Смешанный — объясни почему по методологии OKR
2. ОЦЕНКА: X/10 — с конкретным обоснованием
3. НАРУШЕНИЯ МЕТОДОЛОГИИ OKR (если есть):
   • (что именно нарушено и почему)
4. ПЕРЕФОРМУЛИРОВКА:
   ❌ Сейчас: (цитата)
   ✅ По методологии OKR: (конкретная версия — Objective без цифр ИЛИ KR с цифрой и сроком)"""
        try:
            result = self._call_llm(prompt, max_tokens=500, num_ctx=1024)
            if not result or not result.strip():
                return "Модель не вернула ответ. Попробуйте ещё раз."
            return result
        except Exception as e:
            return f"Ошибка анализа: {e}"

    def validate_existing_okr(self, okr_text: str) -> str:
        prev_context = self.get_previous_context()

        # Берём только строки похожие на OKR-элементы (O1/KR1/Objective/KR и т.п.)
        # Фильтруем инструкции и лишний текст который мог попасть из запроса пользователя
        import re as _re
        _okr_line = _re.compile(
            r'^(O\d|KR\d|Objective|Key\s*Result|Цель|KR\s|O\s|\d+[\.\)]\s)',
            _re.IGNORECASE
        )
        # Признаки строки-инструкции (не OKR-элемента)
        _instruction = _re.compile(
            r'\bи\s+krs?\b|\bдай\s|\bмакс[иу]м|\bразвернут|\bпожалуйст|\bчтобы\s+ты\b|\bпровалидируй\b|\bоцен[ии]\b',
            _re.IGNORECASE
        )
        all_lines = [l.strip() for l in okr_text.split('\n') if l.strip()]
        items = [
            l for l in all_lines
            if _okr_line.match(l) and not _instruction.search(l)
        ]
        # Если OKR-строк не нашлось — весь текст считаем одним элементом
        if not items:
            items = [l for l in all_lines if not _instruction.search(l)] or all_lines
        item_count = len(items)
        # ~700 токенов на элемент (6 критериев с развёрнутыми объяснениями) + 400 базовых; диапазон 1400–3000
        max_tok = max(1400, min(3000, item_count * 700 + 400))
        num_ctx = max(2048, item_count * 200 + 1024)

        # Нумерованный список элементов
        elements_list = "\n".join(f"{i+1}. {el}" for i, el in enumerate(items))

        # Если есть Objective — извлекаем его текст для явной привязки KR
        import re as _re2
        objective_text = ""
        for it in items:
            if _re2.match(r'^(O\d|Objective)', it, _re2.IGNORECASE):
                # Убираем префикс O1/Objective и берём суть
                objective_text = _re2.sub(r'^(O\d+|Objective)\s*[:\s]*', '', it, flags=_re2.IGNORECASE).strip()
                break
        obj_context = f"\nOBJECTIVE ДЛЯ ЭТИХ KR: «{objective_text}»" if objective_text else ""

        prompt = f"""Ты эксперт исключительно по методологии OKR. Оцени элементы ТОЛЬКО по критериям OKR.{obj_context}

═══ СПИСОК ЭЛЕМЕНТОВ ДЛЯ АНАЛИЗА ({item_count} шт.) ═══
{elements_list}
═══════════════════════════════════════════════════════

ЖЁСТКИЕ ПРАВИЛА:
1. Твой ответ должен содержать РОВНО {item_count} блоков анализа — по одному на каждый элемент из списка.
   После блока №{item_count} — немедленно СТОП. Не добавляй никаких дополнительных блоков.
2. НЕ анализируй текст из своего раздела «КАК ИСПРАВИТЬ» — это твоё предложение, не новый элемент.
3. НЕ анализируй один и тот же элемент дважды под разными названиями или типами.
4. Оценка должна быть логически согласована с разбором: если критерий отмечен ✅ — не снижай за него оценку.
5. Только методология OKR. Запрещены SMART, KPI, MBO и любые другие фреймворки.
6. ЗАПРЕЩЕНО снижать оценку Objective за отсутствие цифр/метрик — по OKR это правильно.
7. Переформулировка Objective в разделе «КАК ИСПРАВИТЬ» должна быть КАЧЕСТВЕННОЙ — без цифр, без метрик, без финансовых таргетов. Если исходная цель содержит «миллион» — в переформулировке этого слова быть не должно.

Критерии оценки Objective (6 критериев, каждый = 1.67 балла, итого 10):
  О1. Вдохновляющая — мотивирует команду
  О2. Качественная — без конкретных чисел («миллион», «30%», «x2» = ❌)
      НЕ нарушение: «значительный», «высокий», «лучший» — прилагательные, не числа
      НЕ нарушение: «в 2026», «в Q3» — временная привязка, нарушением НЕ является
  О3. Амбициозная — ПРАВИЛО: всегда ✅ если нет очевидно невозможного (агент не знает контекст компании)
  О4. Понятная — любой поймёт с первого прочтения
  О5. Ограниченная по времени — содержит «в 2026», «в Q3», «к концу года» и т.п.
  О6. Ориентированная на результат — «достичь»/«стать», не «делать»/«провести»
  Формула: число ✅ × 1.67 + число ⚠️ × 0.83 = итог, округли до 0.5
  Примеры: 6✅=10, 5✅=8.5, 4✅=6.5, 4✅+1⚠️=7.5, 3✅=5

Критерии оценки Key Result (6 критериев, каждый = 1.67 балла, итого 10):
  KR1. Измеримый — конкретная цифра (%, руб., шт., дни, NPS)
  KR2. Верифицируемый — однозначно: выполнен или нет
  KR3. Результатный — ЧТО достигнем, не ЧТО делаем
  KR4. Связан с Objective — достижение KR приближает к цели.
       Если в начале промпта есть OBJECTIVE ДЛЯ ЭТИХ KR — оценивай связь с ним по смыслу.
       Если Objective нигде не указан — ставь ⚠️ и пиши «Objective не указан»
  KR5. Амбициозный — 70% вероятность выполнения = норма
  KR6. Количество KR на цель ≤5. Если анализируешь KR без Objective — ставь ✅ (нет данных для подсчёта)
  Формула та же: число ✅ × 1.67 + число ⚠️ × 0.83, округли до 0.5

Общие критерии для связки Objective + KR (проверяй если есть и цель, и KR):
  С1. Каскадируемость — OKR нижнего уровня вытекают из OKR верхнего уровня
  С2. Отсутствие задач — OKR описывает желаемое состояние, не список дел
  С3. Независимость от одного человека — KR не должен зависеть только от одного исполнителя

{prev_context}
ПРАВИЛА ПЕРЕФОРМУЛИРОВКИ (прочитай до начала анализа):
Для Objective переформулировка ОБЯЗАНА содержать ВСЕ 6 элементов:
  [1] вдохновляющее направление + [2] без чисел/% + [3] амбициозно + [4] понятно + [5] ПЕРИОД («в 2026»/«в Q3»/«к концу года») + [6] результат не действие
  Шаблон: «[Стать/Завоевать/Создать] [что] [где] [ПЕРИОД]»
  Пример: «Стать лидером в новых рыночных сегментах в 2026 году»

Для Key Result переформулировка ОБЯЗАНА быть KR-форматом (НЕ Objective!):
  [1] глагол РЕЗУЛЬТАТА (не действия) + [2] метрика + [3] конкретное число + [4] период
  Шаблон: «[Достичь/Увеличить/Снизить] [метрику] [до Y / с X до Y] к [ПЕРИОД]»
  Пример: «Достичь 200 offer sent от УК и сетевиков к концу Q3»
  ЗАПРЕЩЕНО: начинать с «Внедрить», «Сделать», «Запустить», «Провести» — это задачи, не результаты
  ЗАПРЕЩЕНО: терять метрики из оригинала — если в KR было 2 метрики, в переформулировке тоже 2
  ЗАПРЕЩЕНО: предлагать Objective вместо KR

Для каждого из {item_count} элементов используй СТРОГО этот формат (КАК ИСПРАВИТЬ — внутри блока сразу после оценки):

━━━━━━━━━━━━━━━━━━━━━━━
**[текст элемента]**
ТИП: [Objective / Key Result / Инициатива / Смешанный]

[Если Objective — КРИТЕРИИ ОЦЕНКИ ЦЕЛИ:]
• 1. Вдохновляющая: ✅/❌ — [объяснение]
• 2. Качественная (без цифр): ✅/❌ — [если ❌ укажи какое слово нарушает]
• 3. Амбициозная: ✅ — [всегда ✅, агент не знает контекст компании]
• 4. Понятная: ✅/❌ — [объяснение]
• 5. Ограниченная по времени: ✅/❌ — [объяснение]
• 6. Ориентированная на результат: ✅/❌ — [объяснение]

[Если Key Result — КРИТЕРИИ ОЦЕНКИ KEY RESULT:]
• 1. Измеримый: ✅/❌ — [объяснение]
• 2. Верифицируемый: ✅/❌ — [объяснение]
• 3. Результатный (не активностный): ✅/❌ — [объяснение]
• 4. Связан с Objective: ✅ прямая / ⚠️ слабая / ❌ нет — [объяснение]
• 5. Амбициозный (70% комфорт): ✅/❌ — [объяснение]
• 6. Количество KR в норме (≤5): ✅/❌ — [объяснение]

ОЦЕНКА: [X]/10 — [главная причина. ПРАВИЛО: причина должна точно отражать ❌ критерии выше. Если критерий ❌ из-за НАЛИЧИЯ числа — пиши «содержит число», НЕ «отсутствует число». Не противоречь своим же критериям.]

КАК ИСПРАВИТЬ: [только если ОЦЕНКА < 10/10 И есть ❌ в критериях. Если все ✅ и оценка 10/10 — НЕ ПИШИ этот раздел вообще]
❌ Сейчас: «[цитата]»
✅ По OKR: «[для Objective: шаблон из правил выше с обязательным ПЕРИОДОМ]»
━━━━━━━━━━━━━━━━━━━━━━━

[После {item_count} блоков, если есть и Objective и KR:]
ОБЩАЯ ОЦЕНКА СВЯЗКИ ЦЕЛЬ + KR:
• 1. Каскадируемость: ✅/❌ — [2-3 предложения: если выполним все KR — приблизит ли это к Objective? Связь по СМЫСЛУ: KR про офферы/продажи → логично ведут к цели роста/дохода = ✅. Каскадируемость ❌ только если KRs явно про другую область, никак не связанную с Objective.]
• 2. Отсутствие задач: ✅/❌ — [2-3 предложения: OKR описывает желаемое состояние («стать», «достичь»), а не список дел («провести», «сделать»). Есть ли здесь задачи вместо результатов?]
• 3. Независимость от одного человека: ✅/❌ — [2-3 предложения: может ли один человек заблокировать KR? Зависит ли результат от команды или от одного исполнителя? Почему это важно?]

[СТОП — больше ничего не добавляй]"""
        try:
            result = self._call_llm(prompt, max_tokens=max_tok, num_ctx=num_ctx)
            if not result or not result.strip():
                return "Модель не вернула ответ. Попробуйте ещё раз."
            result = self._fix_scores(result)
            return result
        except Exception as e:
            return f"Ошибка валидации OKR: {e}"

    @staticmethod
    def _extract_score(text: str) -> float:
        """Парсит первый 'ОЦЕНКА: X/10' из текста. Возвращает 0.0 если не найдено."""
        import re as _re
        m = _re.search(r'ОЦЕНКА:\s*(\d+(?:[.,]\d+)?)/10', text)
        if m:
            return float(m.group(1).replace(',', '.'))
        return 0.0

    @staticmethod
    def _extract_errors(text: str) -> list:
        """Возвращает список кодов критериев с ❌ (например ['O2', 'KR3'])."""
        import re as _re
        errors = []
        for line in text.split('\n'):
            if '❌' in line:
                m = _re.search(r'\b(O\d+|KR?\d+)\b', line, _re.IGNORECASE)
                if m:
                    errors.append(m.group(1).upper())
        seen = set()
        return [e for e in errors if not (e in seen or seen.add(e))]

    @staticmethod
    def _fix_scores(text: str) -> str:
        """Пересчитывает ОЦЕНКА: X/10 на основе ✅/⚠️/❌ в предшествующих критериях."""
        import re
        lines = text.split('\n')
        fixed = []
        for i, line in enumerate(lines):
            if re.match(r'\s*ОЦЕНКА:', line):
                # Берём до 14 строк до ОЦЕНКА чтобы найти критерии
                chunk = '\n'.join(lines[max(0, i - 14):i])
                marks = re.findall(r'[•\-]\s*\d+\.[^:]+:\s*([✅⚠️❌]+)', chunk)
                if marks:
                    criteria = marks[:6]
                    score = sum(
                        1.67 if '✅' in m else (0.83 if '⚠' in m else 0.0)
                        for m in criteria
                    )
                    score = round(score * 2) / 2  # округление до 0.5
                    # Извлекаем только текст после "—", без вложенных "ОЦЕНКА:"
                    if '—' in line:
                        reason = line.split('—', 1)[1].strip()
                        # Убираем задвоение если reason содержит "ОЦЕНКА:"
                        if 'ОЦЕНКА:' in reason:
                            reason = reason[:reason.index('ОЦЕНКА:')].strip().rstrip('—').strip()
                    else:
                        reason = ''
                    suffix = f' — {reason}' if reason else ''
                    fixed.append(f'ОЦЕНКА: {score}/10{suffix}')
                    continue
            fixed.append(line)
        return '\n'.join(fixed)

    def add_okr_to_previous(self, quarter: str, okr: str) -> None:
        if quarter not in self.previous_okrs:
            self.previous_okrs[quarter] = []
        self.previous_okrs[quarter].append(okr)
        self.save_previous_okrs(self.previous_okrs)

    def add_to_history(self, text: str) -> None:
        self.history.append(text)

    def clear_history(self) -> None:
        self.history = []

    def get_history(self) -> List[str]:
        return self.history.copy()

    def process_file(self, file_path: str) -> Tuple[bool, str]:
        try:
            file_path = Path(file_path)
            if not file_path.exists():
                return False, "File not found"
            suffix = file_path.suffix.lower()

            if suffix in ['.txt', '.md', '.log']:
                with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
                    return True, f.read()

            elif suffix == '.csv':
                try:
                    import pandas as pd
                    return True, pd.read_csv(file_path).to_string()
                except ImportError:
                    return False, "pandas not installed"

            elif suffix in ['.xlsx', '.xls']:
                try:
                    import pandas as pd
                    xls = pd.ExcelFile(file_path)
                    content = ""
                    for sheet in xls.sheet_names:
                        df = pd.read_excel(file_path, sheet_name=sheet)
                        content += f"Sheet: {sheet}\n{df.to_string()}\n\n"
                    return True, content
                except ImportError:
                    return False, "openpyxl/pandas not installed"

            elif suffix in ['.png', '.jpg', '.jpeg', '.gif', '.bmp']:
                return True, self._encode_image_as_base64(str(file_path))

            elif suffix == '.pdf':
                try:
                    import PyPDF2
                    content = ""
                    with open(file_path, 'rb') as f:
                        reader = PyPDF2.PdfReader(f)
                        for i, page in enumerate(reader.pages):
                            content += f"Страница {i+1}:\n{page.extract_text()}\n\n"
                    return True, content
                except ImportError:
                    return False, "PyPDF2 not installed"

            return False, f"Unsupported file type: {suffix}"
        except Exception as e:
            return False, f"Error: {e}"

    @staticmethod
    def _parse_okr_csv(csv_text: str) -> str:
        """Парсит CSV из Google Sheets с OKR-структурой в читаемый формат.

        Определяет строки заголовков, Objective (столбец KR = O1/O2/...)
        и Key Results (KR1/KR2/...) и форматирует их структурированно.
        """
        import csv as _csv, io as _io

        try:
            reader = _csv.reader(_io.StringIO(csv_text))
            rows = list(reader)
        except Exception:
            return csv_text

        if not rows:
            return csv_text

        # Найти строку заголовков (содержит KR, Description, Owner и т.д.)
        header_idx = None
        for i, row in enumerate(rows[:5]):
            row_str = ' '.join(row).lower()
            if 'description' in row_str or ('owner' in row_str and 'kr' in row_str):
                header_idx = i
                break

        if header_idx is None:
            # Нет явных заголовков — возвращаем raw CSV
            return csv_text

        headers = rows[header_idx]

        def col(name_variants):
            for i, h in enumerate(headers):
                if any(v.lower() in h.lower() for v in name_variants):
                    return i
            return None

        c_type    = col(['KR']) or 2
        c_desc    = col(['Description', 'Описание', 'Название']) or 4
        c_owner   = col(['Owner', 'Владелец']) or 3
        c_pct     = 0  # % выполнения — обычно первый столбец
        c_checkin = col(['Check-in', 'Checkin', 'Current']) or 6
        c_target  = col(['Target', 'Цель']) or 7
        c_metric  = col(['Metric', 'Метрика']) or 8
        c_status  = col(['Status', 'Статус']) or 10
        c_blocker = col(['Blocker', 'Action', 'Комментарий']) or 11

        def cell(row, idx):
            if idx is not None and idx < len(row):
                return row[idx].strip()
            return ''

        lines = []
        lines.append(f"Вкладка: структура OKR\n{'='*60}")

        for row in rows[header_idx + 1:]:
            if not any(c.strip() for c in row[:6]):
                continue

            type_val = cell(row, c_type)
            desc     = cell(row, c_desc)

            if not type_val or not desc:
                continue

            # Objective: O1, O2, O3...
            import re as _re
            if _re.match(r'^O\d+', type_val):
                pct     = cell(row, c_pct)
                owner   = cell(row, c_owner)
                status  = cell(row, c_status)
                lines.append(f"\n{'─'*60}")
                lines.append(f"OBJECTIVE {type_val}: {desc}")
                lines.append(f"  Прогресс: {pct}% | Владелец: {owner} | Статус: {status}")

            # Key Result: KR1, KR2, KR1 ⚙ и т.д.
            elif 'KR' in type_val:
                pct     = cell(row, c_pct)
                owner   = cell(row, c_owner)
                checkin = cell(row, c_checkin)
                target  = cell(row, c_target)
                metric  = cell(row, c_metric)
                status  = cell(row, c_status)
                blocker = cell(row, c_blocker)
                lines.append(f"  {type_val}: {desc}")
                lines.append(f"    Факт: {checkin} / Цель: {target} {metric} ({pct}%) | Владелец: {owner} | Статус: {status}")
                if blocker:
                    lines.append(f"    Блокер/Комментарий: {blocker[:120]}")

        return '\n'.join(lines) if len(lines) > 2 else csv_text

    @staticmethod
    def extract_okr_cells(csv_text: str) -> list:
        """Возвращает список ячеек с OKR-данными: {type, row, col, text}.

        row/col — нумерация с 0 для Google Sheets API.
        col=4 (столбец E) — Description/формулировка.
        """
        import csv as _csv, io as _io, re as _re

        try:
            rows = list(_csv.reader(_io.StringIO(csv_text)))
        except Exception:
            return []

        # Найти строку заголовков
        header_idx = None
        for i, row in enumerate(rows[:5]):
            row_str = ' '.join(row).lower()
            if 'description' in row_str or ('owner' in row_str and 'kr' in row_str):
                header_idx = i
                break

        if header_idx is not None:
            headers = rows[header_idx]

            def col_idx(variants):
                for i, h in enumerate(headers):
                    if any(v.lower() in h.lower() for v in variants):
                        return i
                return None

            c_type = col_idx(['KR']) or 2
            c_desc = col_idx(['Description', 'Описание']) or 4
            data_start = header_idx + 1
        else:
            # Нет строки заголовков — используем стандартные позиции OKR-таблицы
            c_type, c_desc = 2, 4
            data_start = 1  # пропускаем строку-заголовок таблицы (row 0)

        cells = []
        for row_num, row in enumerate(rows[data_start:], start=data_start):
            if not any(c.strip() for c in row[:6]):
                continue
            type_val = row[c_type].strip() if c_type < len(row) else ''
            desc = row[c_desc].strip() if c_desc < len(row) else ''
            if not type_val or not desc:
                continue
            if _re.match(r'^O\d+', type_val) or 'KR' in type_val:
                cells.append({
                    "type": type_val.split()[0],  # убираем ⚙ и др.
                    "row": row_num,
                    "col": c_desc,
                    "text": desc,
                })
        return cells

    def fetch_url_content(self, url: str, sheet_name: str = '') -> tuple:
        """Загружает содержимое по URL. Поддерживает Google Таблицы (все вкладки), Docs, CSV.

        sheet_name: название конкретной вкладки (для Google Sheets).
        Returns (success: bool, content: str).
        """
        import re as _re
        import httpx as _httpx
        import urllib.parse as _up

        headers = {"User-Agent": "Mozilla/5.0 (compatible; OKRAgent/1.0)"}

        # ── Google Sheets ────────────────────────────────────────────────
        sheets_m = _re.search(r'docs\.google\.com/spreadsheets/d/([a-zA-Z0-9_-]+)', url)
        if sheets_m:
            sheet_id = sheets_m.group(1)
            gid_m = _re.search(r'[?&#]gid=(\d+)', url)
            gid = gid_m.group(1) if gid_m else None

            def _fetch_csv(candidates):
                for export in candidates:
                    try:
                        r = _httpx.get(export, follow_redirects=True, timeout=30, headers=headers)
                        if r.status_code == 200 and r.text.strip():
                            return r.text
                    except Exception:
                        pass
                return None

            # Список всех вкладок через gviz JSON
            def _list_sheets():
                try:
                    import json as _json
                    r = _httpx.get(
                        f"https://docs.google.com/spreadsheets/d/{sheet_id}/gviz/tq?tqx=out:json",
                        follow_redirects=True, timeout=15, headers=headers)
                    m = _re.search(r'setResponse\((.*)\)', r.text, _re.DOTALL)
                    if m:
                        data = _json.loads(m.group(1))
                        return data  # содержит info о таблице
                except Exception:
                    pass
                return None

            # Получить gid'ы всех вкладок.
            # Google Sheets HTML обфусцирован — ищем gid в мета-тегах og:url/canonical
            # и в data-атрибутах. Также пробуем экспорт с gid=0,1,2...
            def _get_all_gids():
                result = []
                seen_content = set()
                try:
                    page = _httpx.get(
                        f"https://docs.google.com/spreadsheets/d/{sheet_id}",
                        follow_redirects=True, timeout=20, headers=headers)
                    html = page.text

                    # Паттерн 1: gid в мета og:url или canonical
                    meta_gids = _re.findall(r'[?&#]gid[=:](\d+)', html)

                    # Паттерн 2: большие числа рядом с именами вкладок в JS-блоках
                    # Ищем пары [число, "строка"] — типичная структура для вкладок
                    tab_pairs = _re.findall(r'\[(\d{7,12}),\d*,"([^"]{1,60})"', html)

                    all_gid_candidates = list(dict.fromkeys(meta_gids))
                    for g, name in tab_pairs:
                        if g not in [x[0] for x in result]:
                            all_gid_candidates.append(g)

                    # Проверяем каждый gid — берём только те что дают уникальный контент
                    baseline = _fetch_csv([
                        f"https://docs.google.com/spreadsheets/d/{sheet_id}/gviz/tq?tqx=out:csv"
                    ]) or ''
                    if baseline:
                        seen_content.add(baseline[:200])
                        result.append(('', 'Вкладка 1'))  # первая вкладка без gid

                    for g in all_gid_candidates:
                        csv = _fetch_csv([
                            f"https://docs.google.com/spreadsheets/d/{sheet_id}/gviz/tq?tqx=out:csv&gid={g}",
                            f"https://docs.google.com/spreadsheets/d/{sheet_id}/export?format=csv&gid={g}",
                        ])
                        if csv and csv[:200] not in seen_content:
                            seen_content.add(csv[:200])
                            # Попробуем найти имя вкладки из первой строки CSV
                            first_row = csv.split('\n')[0][:50]
                            result.append((g, first_row or f'Вкладка gid={g}'))
                except Exception:
                    pass
                return result

            # Если задана конкретная вкладка по имени — грузим только её
            if sheet_name.strip():
                encoded = _up.quote(sheet_name.strip())
                candidates = [
                    f"https://docs.google.com/spreadsheets/d/{sheet_id}/gviz/tq?tqx=out:csv&sheet={encoded}",
                    f"https://docs.google.com/spreadsheets/d/{sheet_id}/export?format=csv&sheet={encoded}",
                ]
                csv_text = _fetch_csv(candidates)
                if csv_text:
                    parsed = self._parse_okr_csv(csv_text)
                    return True, f"[Вкладка: {sheet_name}]\n{parsed}"
                return False, f"Вкладка «{sheet_name}» не найдена или недоступна."

            # Если в URL есть конкретный #gid= — загружаем именно его, без поиска других вкладок
            if gid and gid != '0':
                specific = _fetch_csv([
                    f"https://docs.google.com/spreadsheets/d/{sheet_id}/gviz/tq?tqx=out:csv&gid={gid}",
                    f"https://docs.google.com/spreadsheets/d/{sheet_id}/export?format=csv&gid={gid}",
                ])
                if specific:
                    return True, self._parse_okr_csv(specific)

            # Без указания вкладки — грузим ВСЕ вкладки
            all_gids = _get_all_gids()

            if all_gids:
                sections = []
                for tab_gid, tab_name in all_gids:
                    tab_candidates = [
                        f"https://docs.google.com/spreadsheets/d/{sheet_id}/gviz/tq?tqx=out:csv&gid={tab_gid}",
                        f"https://docs.google.com/spreadsheets/d/{sheet_id}/export?format=csv&gid={tab_gid}",
                    ]
                    csv_text = _fetch_csv(tab_candidates)
                    if csv_text:
                        parsed = self._parse_okr_csv(csv_text)
                        # Включаем только вкладки с OKR-данными (есть Objective или KR)
                        if 'OBJECTIVE' in parsed or 'KR' in parsed:
                            sections.append(f"{'='*60}\nВКЛАДКА: {tab_name}\n{'='*60}\n{parsed}")
                if sections:
                    return True, "\n\n".join(sections)

            # Фолбэк — только первая вкладка (или по gid из URL)
            candidates = [
                f"https://docs.google.com/spreadsheets/d/{sheet_id}/gviz/tq?tqx=out:csv",
                f"https://docs.google.com/spreadsheets/d/{sheet_id}/export?format=csv",
            ]
            if gid and gid != '0':
                candidates = [
                    f"https://docs.google.com/spreadsheets/d/{sheet_id}/gviz/tq?tqx=out:csv&gid={gid}",
                    f"https://docs.google.com/spreadsheets/d/{sheet_id}/export?format=csv&gid={gid}",
                ] + candidates

            csv_text = _fetch_csv(candidates)
            if csv_text:
                parsed = self._parse_okr_csv(csv_text)
                hint = (
                    "\n\n💡 Чтобы загрузить конкретную вкладку: "
                    "откройте нужную вкладку в Google Sheets → скопируйте URL из адресной строки "
                    "(он будет содержать #gid=ЧИСЛО) → вставьте этот URL в запрос."
                )
                return True, parsed + hint

            return False, (
                "Таблица недоступна.\n"
                "Убедитесь что доступ открыт: Файл → Поделиться → «Все у кому есть ссылка»."
            )

        # ── Google Docs ──────────────────────────────────────────────────
        docs_m = _re.search(r'docs\.google\.com/document/d/([a-zA-Z0-9_-]+)', url)
        if docs_m:
            doc_id = docs_m.group(1)
            export = f"https://docs.google.com/document/d/{doc_id}/export?format=txt"
            try:
                r = _httpx.get(export, follow_redirects=True, timeout=30, headers=headers)
                if r.status_code == 200:
                    return True, r.text
                return False, (
                    "Google Документ недоступен.\n"
                    "Откройте доступ: Настройки доступа → Все у кого есть ссылка."
                )
            except Exception as e:
                return False, f"Ошибка загрузки Google Документа: {e}"

        # ── Произвольный URL ─────────────────────────────────────────────
        try:
            r = _httpx.get(url, follow_redirects=True, timeout=30, headers=headers)
            ct = r.headers.get("content-type", "")
            if any(t in ct for t in ("text/plain", "text/csv", "application/csv")):
                return True, r.text
            if "text/html" in ct:
                # Простая очистка HTML → текст
                text = _re.sub(r'<style[^>]*>[\s\S]*?</style>', '', r.text, flags=_re.I)
                text = _re.sub(r'<script[^>]*>[\s\S]*?</script>', '', text, flags=_re.I)
                text = _re.sub(r'<[^>]+>', ' ', text)
                text = _re.sub(r'\s+', ' ', text).strip()
                return True, text[:6000]
            return False, f"Неподдерживаемый тип содержимого: {ct}"
        except Exception as e:
            return False, f"Ошибка загрузки URL: {e}"

    def _encode_image_as_base64(self, image_path: str) -> str:
        with open(image_path, 'rb') as f:
            return base64.b64encode(f.read()).decode('utf-8')

    def analyze_okr_files(self, file_paths: List[str]) -> str:
        prev_context = self.get_previous_context()
        files_content = ""

        for file_path in file_paths:
            success, content = self.process_file(file_path)
            if success:
                name = Path(file_path).name
                if content.startswith('/9j') or content.startswith('iVBOR'):
                    files_content += f"Изображение: {name}\n[Визуальный контент]\n\n"
                else:
                    files_content += f"Файл: {name}\n{content}\n\n"
            else:
                files_content += f"Ошибка ({Path(file_path).name}): {content}\n"

        if not files_content:
            return "Не удалось обработать ни один файл."

        # Truncate to keep prompt size reasonable
        if len(files_content) > 8000:
            files_content = files_content[:8000] + "\n...[обрезано]"

        prompt = f"""Ты эксперт по OKR. Проанализируй документ и дай развёрнутый ответ на русском.

{prev_context}{files_content}

Справка по методологии OKR:
• Objective — качественная вдохновляющая цель БЕЗ цифр
• Key Result — измеримый результат (не задача) С цифрами и сроком
• KR не должен быть задачей/инициативой («сделать», «запустить»)
• Один Objective → 3-5 Key Results

Структура анализа:
1. СТРУКТУРА OKR: есть ли Objective + Key Results? Правильно ли разделены?
2. КАЧЕСТВО OBJECTIVE: качественная ли цель, нет ли цифр, вдохновляет ли?
3. КАЧЕСТВО KEY RESULTS: измеримые ли, результаты или задачи, есть ли цифры и сроки?
4. ПРОБЛЕМЫ ПО МЕТОДОЛОГИИ OKR (до 3 конкретных)
5. ПЕРЕФОРМУЛИРОВКА:
   ❌ Сейчас: (цитата)
   ✅ По OKR: (конкретная версия с цифрами и сроком)"""

        try:
            result = self._call_llm(prompt, max_tokens=600, num_ctx=1024)
            if not result or not result.strip():
                return "Модель не вернула ответ. Проверьте что файл содержит текст с OKR."
            return result
        except Exception as e:
            return f"Ошибка анализа файлов: {e}"
