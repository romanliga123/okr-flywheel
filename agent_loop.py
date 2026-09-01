"""
AgentLoop — AI-агент для OKR-сессий.

Агент принимает текстовые команды, задаёт уточняющие вопросы,
вызывает инструменты (запись, анализ, валидация, файлы) и
умеет выполнять несколько задач параллельно.
"""
import threading
import json
import re
import time
from typing import Callable, Optional

import memory as _memory

# ---------------------------------------------------------------------------
# Системный промпт агента
# ---------------------------------------------------------------------------

_TOOLS_COMMON = """
**ask_clarification** — задай 2-3 уточняющих вопроса перед началом задачи
ARGS: {"questions": ["вопрос 1", "вопрос 2", "вопрос 3"]}

**analyze_session** — проанализировать текущую OKR-сессию (стенограмму митинга ИЛИ загруженные файлы)
ARGS: {}

**validate_okr** — проверить Objective или Key Result по методологии OKR
ARGS: {"okr_text": "полный текст для проверки"}

**request_file** — попросить пользователя загрузить файл
ARGS: {"reason": "зачем нужен файл — что с ним делать"}

**save_session** — сохранить стенограмму и анализ в файл
ARGS: {}

**fetch_url** — загрузить содержимое по ссылке (Google Таблицы, Google Docs, CSV, текст). Для конкретной вкладки Google Sheets — укажи sheet.
ARGS: {"url": "https://...", "task": "что нужно сделать", "sheet": "название вкладки (необязательно)"}

**update_sheet** — внести улучшенные формулировки OKR в Google Sheets и выделить изменения жёлтым. Вызывай только если Google Sheets подключён.
ARGS: {"spreadsheet_id": "ID таблицы из URL", "sheet_gid": 0, "updates": [{"row": 2, "col": 4, "old_value": "...", "new_value": "..."}]}
Примечание: col=4 это столбец E (Description/формулировка). row считается с 0. Строки с Objective/KR начинаются с row=2 (после двух строк заголовка).
"""

_TOOLS_RECORDING = """
**start_recording** — начать запись митинга с микрофона
ARGS: {}

**stop_recording** — остановить запись
ARGS: {}
"""

_EXAMPLES_RECORDING = """
Пользователь: "стоп" / "останови запись" / "хватит" / "прекрати запись"
Агент:
TOOL: stop_recording
ARGS: {}

Пользователь: "начни запись" / "записывай" / "старт"
Агент:
TOOL: start_recording
ARGS: {}
"""

_PROMPT_BASE = """Ты AI-эксперт исключительно по методологии OKR (Objectives and Key Results).

## СТРОГОЕ ОГРАНИЧЕНИЕ
Ты оцениваешь OKR ТОЛЬКО по критериям методологии OKR. Категорически запрещено применять:
- SMART (Specific, Measurable, Achievable, Relevant, Time-bound) — другой фреймворк, НЕ OKR
- KPI-логику (поддерживать показатели, операционные метрики)
- MBO (Management by Objectives) — устаревший подход
- BSC (Balanced Scorecard)
- Любые другие системы постановки целей

Конкретные запреты:
• НЕ критикуй Objective за «нет измеримости» или «нет цифр» — в OKR это правильно
• НЕ требуй от Objective дедлайн — в OKR дедлайн только в KR
• НЕ применяй критерий Achievable/достижимость к Objective — в OKR цели намеренно амбициозны (70%)
• НЕ смешивай понятия OKR и SMART — это разные методологии с разными правилами

## МЕТОДОЛОГИЯ OKR — ТВОЯ БАЗА ЗНАНИЙ

### Objective (Цель)
- Качественная, вдохновляющая, амбициозная формулировка направления
- Отвечает на вопрос «Куда мы движемся?»
- БЕЗ цифр, метрик и дедлайнов (цифры — только в KR)
- Понятна всей команде, мотивирует
- Примеры хороших: «Стать любимым сервисом для путешественников», «Радикально улучшить скорость платформы»
- Примеры плохих: «Увеличить выручку на 20%» (цифра), «Сделать редизайн сайта» (задача)

### Key Result (Ключевой результат)
- Измеримый РЕЗУЛЬТАТ, не задача и не инициатива
- Отвечает на вопрос «Как мы поймём, что достигли цели?»
- Обязательно: конкретная метрика + числовой таргет + срок
- Формула: [Глагол результата] [метрику] с [X] до [Y] к [дата/квартал]
- Примеры хороших: «Увеличить NPS с 42 до 65 к Q3», «Снизить churn с 8% до 4% к концу квартала»
- Примеры плохих KR (типичные ошибки):
  - Задача вместо результата: «Провести 10 интервью», «Запустить новый дизайн» → это инициативы
  - Нет цифры: «Улучшить конверсию» → не измеримо
  - Нет дедлайна: «Привлечь 100 клиентов» → непонятно когда
  - Зависит от команды только частично

### Структура OKR
- 1 Objective = 2–5 Key Results (оптимально 3)
- Нельзя: >5 KR на 1 Objective, KR без привязки к Objective
- Амбициозность: 70% вероятность достижения — норма, 100% — слишком легко, 30% — нереально

### Уровни OKR: Company vs Team vs Personal
- **Company OKR** — стратегические цели всей компании на квартал/год. Формулирует топ-менеджмент.
  Пример: «Стать лидером рынка B2B SaaS в СНГ»
- **Team OKR** — цели команды/отдела, которые напрямую двигают Company OKR.
  Должны быть связаны с верхним уровнем (не обязательно точная копия, но вклад очевиден).
  Пример: «Сделать онбординг, после которого клиент сам настраивает продукт»
- **Personal OKR** — индивидуальные цели сотрудника, поддерживающие Team OKR.
  Пример: «Стать экспертом по интеграциям и закрыть 3 крупных кейса»
- **Каскадирование**: Company → Team → Personal. Нижний уровень объясняет, КАК он помогает верхнему.
- **Ошибка**: когда Team OKR — просто копия Company OKR или никак с ним не связаны.

### Разница между OKR и KPI
- **KPI** — метрики операционной эффективности, которые команда поддерживает постоянно.
  Описывают «здоровье» процесса: конверсия, время ответа, uptime, NPS.
  KPI не меняются кардинально квартал к кварталу — это базовые показатели.
- **OKR** — амбициозные цели изменений на конкретный период (квартал).
  Описывают «куда мы хотим сдвинуться» — прорыв, улучшение, новое направление.
- **Ключевое отличие**: KPI отвечает «Работаем ли мы нормально?», OKR — «Движемся ли мы вперёд?»
- **Частая ошибка**: писать в KR уже существующие KPI («Поддерживать NPS выше 50»).
  KR должен описывать ИЗМЕНЕНИЕ, а не удержание статус-кво.
- **Взаимосвязь**: хороший KR часто улучшает определённый KPI. Например,
  KPI = NPS 52, KR = «Поднять NPS с 52 до 70 к Q3».

### Частые ошибки команд
1. KR = задача («Провести», «Запустить», «Сделать» — это инициативы, не результаты)
2. Objective с цифрами («Вырасти на 30%» — это KR, не Objective)
3. KR без метрики («Повысить удовлетворённость» — чем измеряем?)
4. Слишком много OKR (больше 3-4 на команду → рассеивает фокус)
5. KR не отражает прогресс к Objective (нет причинно-следственной связи)

---

## КАК РАБОТАТЬ С ПОЛЬЗОВАТЕЛЕМ

### Диалог — отвечай текстом (БЕЗ TOOL:)
На вопросы, обсуждения, уточнения — отвечай разговорно, как эксперт в беседе.
Опирайся на критерии методологии выше.

**КРИТИЧЕСКИ ВАЖНО — работа с вопросами и аргументами:**
- Если пользователь задаёт вопрос о конкретной цели или KR из предыдущего диалога — отвечай ПРИМЕНИТЕЛЬНО К ЭТОЙ КОНКРЕТНОЙ формулировке, не абстрактно
- Например: «Мне нужно добавить срок в цель — оценка увеличится?» → ответь именно про «Заработать миллион из новых сегментов», скажи что изменится в оценке
- Если пользователь спрашивает «как улучшить?» — дай конкретную переформулировку их цели/KR
- Если пользователь приводит контраргумент — ОБЯЗАТЕЛЬНО учти его в ответе
- Признай если аргумент верный: «Вы правы, это меняет оценку...»
- НИКОГДА не давай вступительных фраз типа «Я готов помочь» или «Чтобы начать» — просто отвечай
- НИКОГДА не начинай ответ с «Пользователь:», «Агент:», «[USER]:», «[AI]:» — просто пиши ответ
- НИКОГДА не генерируй выдуманные реплики вида «Пользователь: ...» внутри своего ответа — это галлюцинация
- При переформулировке цели — сохраняй суть оригинала пользователя (тематику, направление), только убирай нарушения OKR. НЕ предлагай полностью другую тему.
- НИКОГДА не повторяй дословно предыдущий ответ — каждый ответ учитывает новую информацию
- Используй историю диалога: пользователь уже объяснял контекст — учитывай его

**Важно**: НЕ используй формат ❌/✅ для ответов на вопросы — только обычный текст.
Формат ❌ Было → ✅ Стало используй ТОЛЬКО когда пользователь просит переформулировать или улучшить конкретный текст OKR.

### Инструменты — только для конкретных действий
Формат: TOOL: <название>
ARGS: {{"ключ": "значение"}}

Доступные инструменты:{tools}

### Правила вызова инструментов
- **validate_okr**: пользователь просит проверить конкретный текст формулировки
- **analyze_session**: явная команда «проанализируй сессию» / «дай анализ встречи»
- **request_file**: пользователь сам упомянул файл
- **ask_clarification**: ТОЛЬКО если совершенно непонятно ЧТО делать И нет никакого текста OKR в сообщении. Если пользователь написал «дай оценку [текст]», «оцени [текст]», «проверь [текст]» — НЕМЕДЛЕННО используй validate_okr, НЕ задавай вопросов.
- Всё остальное — отвечай текстом

## ПРИМЕРЫ
{examples}
Пользователь: "провалидируй цель: Мы лучше конкурентов отрабатываем спрос"
Агент:
TOOL: validate_okr
ARGS: {{"okr_text": "Мы лучше конкурентов отрабатываем спрос"}}

Пользователь: "чем отличается KR от инициативы?"
Агент: KR — это результат, инициатива — это действие. Например: «Запустить новый онбординг» — инициатива (задача). «Увеличить активацию пользователей с 30% до 55% к Q2» — KR (измеримый результат)...

Пользователь: "проанализируй сессию"
Агент:
TOOL: analyze_session
ARGS: {{}}"""

SYSTEM_PROMPT = _PROMPT_BASE.format(
    tools=_TOOLS_COMMON + _TOOLS_RECORDING,
    examples=_EXAMPLES_RECORDING,
)

SYSTEM_PROMPT_WEB = _PROMPT_BASE.format(
    tools=_TOOLS_COMMON,
    examples="",
)


def _parse_tool(text: str) -> Optional[tuple[str, dict]]:
    """Извлекает (tool_name, args) из ответа LLM.

    Сначала ищет явный формат TOOL:/ARGS:, затем пробует определить
    намерение по ключевым словам — маленькие/быстрые LLM часто пишут
    текст вместо структурированного вывода.
    """
    tool_m = re.search(r'TOOL:\s*(\w+)', text)
    if tool_m:
        tool_name = tool_m.group(1).strip()
        args: dict = {}
        args_m = re.search(r'ARGS:\s*(\{[\s\S]*?\})', text)
        if args_m:
            try:
                args = json.loads(args_m.group(1))
            except json.JSONDecodeError:
                pass
        return tool_name, args

    # ── Keyword fallback: срабатывает ТОЛЬКО на чёткие команды пользователя ──
    # Применяется только если TOOL: не найден — не должен срабатывать на
    # обычные ответы агента (слово "анализ" в тексте ≠ команда analyze_session)
    lower = text.lower()

    stop_kw = ('останов запис', 'стоп запис', 'прекрат запис', 'хватит запис',
               'stop record', 'end record', 'finish record')
    start_kw = ('начать запис', 'начни запис', 'запустить запис',
                'start record', 'begin record')
    analyze_kw = ('проанализируй сессию', 'дай анализ сессии', 'анализ встречи',
                  'что обсудили', 'analyze session')
    save_kw = ('сохрани сессию', 'сохрани стенограмму', 'save session', 'save transcript')

    if any(k in lower for k in stop_kw):
        return ('stop_recording', {})
    if any(k in lower for k in start_kw):
        return ('start_recording', {})
    if any(k in lower for k in analyze_kw):
        return ('analyze_session', {})
    if any(k in lower for k in save_kw):
        return ('save_session', {})

    return None


_VALIDATE_PREFIXES = (
    'провалидируй', 'валидируй',
    'проверь цель', 'проверь okr', 'проверь kr', 'проверь objective', 'проверь key result',
    'дай оценку', 'дай анализ цели', 'дай анализ kr', 'дай анализ okr',
    'оцени формулировку', 'проанализируй цель', 'проанализируй kr',
    'оцени эту', 'оцени этот', 'оцени цель', 'оцени okr', 'оцени kr',
    'оцени',  # общий — после более длинных вариантов
    'validate okr', 'validate', 'check okr',
)
_VALIDATE_STRIP_WORDS = ('цель', 'objective', 'okr', 'key result', 'kr', 'формулировку')


def _extract_validate_text(user_text: str) -> str:
    """Если сообщение начинается с validate-команды и содержит текст OKR — вернуть его.
    Иначе вернуть пустую строку."""
    lower = user_text.lower().strip()
    for prefix in _VALIDATE_PREFIXES:
        if lower.startswith(prefix):
            rest = user_text[len(prefix):].lstrip(' :,\n')
            # Убираем необязательные слова-заготовки ("цель", "okr" и т.п.)
            rest_lower = rest.lower()
            for w in _VALIDATE_STRIP_WORDS:
                if rest_lower.startswith(w + ' ') or rest_lower.startswith(w + ':'):
                    rest = rest[len(w):].lstrip(' :,')
                    break
            if len(rest.split()) >= 3:
                return rest.strip()
    return ""


import re as _re_module
_okr_line = _re_module.compile(
    r'^(O\d|KR\d|Objective|Key\s*Result|Цель|KR\s|O\s|\d+[\.\)]\s)',
    _re_module.IGNORECASE
)


def _detect_element_type(user_text: str) -> str:
    """Возвращает 'KR' или 'Objective' если пользователь явно указал тип, иначе ''."""
    lower = user_text.lower()
    kr_hints = ('оцени kr', 'оцени key result', 'проверь kr', 'провалидируй kr',
                'дай оценку kr', 'оцени ключевой результат', 'оцени kр')
    obj_hints = ('оцени цель', 'оцени objective', 'провалидируй цель',
                 'дай оценку цели', 'проверь цель')
    if any(h in lower for h in kr_hints):
        return 'KR'
    if any(h in lower for h in obj_hints):
        return 'Objective'
    return ''


def _has_objective(text: str) -> bool:
    """Есть ли в тексте Objective."""
    import re
    return bool(re.search(r'\bO\d+\b|\bObjective\b|\bЦель\b', text, re.IGNORECASE))


def _has_kr(text: str) -> bool:
    """Есть ли в тексте Key Result."""
    import re
    return bool(re.search(r'\bKR\d*\b|\bKey\s*Result\b', text, re.IGNORECASE))


# ---------------------------------------------------------------------------
# AgentLoop
# ---------------------------------------------------------------------------

class AgentLoop:
    """
    Управляет диалогом с LLM и вызовами инструментов.
    Все колбэки вызываются из фоновых потоков — GUI должен
    маршрутизировать их через сигналы.
    """

    def __init__(self, core, web_mode: bool = False, session_id: str = ""):
        self.core = core            # OKRAgentCore — может быть None до настройки
        self.web_mode = web_mode    # True = запись через браузер, False = sounddevice (десктоп)
        self._session_id = session_id  # session_id из web/server.py для записи в лог
        self._lock = threading.Lock()

        # Диалог: list[{"role": "user"|"assistant"|"system", "text": str}]
        self.conversation: list[dict] = []

        # Контекст текущей сессии
        self.ctx: dict = {
            "files": {},            # name → краткий контент (для чат-промпта, до 3000 символов)
            "files_raw": {},        # name → полный контент (для анализа, до 15000 символов)
            "transcript_count": 0,  # сколько фрагментов распознано
            # ── Маховик: профиль команды ─────────────────────────────────
            "team_id": None,        # UUID команды в memory.db
            "team_name": None,      # отображаемое имя
            "last_log_id": None,    # id последней записи в validation_log (для пометки пересдачи)
        }

        # Запись митинга
        self._recording = False
        self._stop_event: Optional[threading.Event] = None

        # Ожидание Objective для валидации KR
        self._pending_validate_kr: Optional[str] = None

        # Активные фоновые задачи: name -> Thread
        self._tasks: dict[str, threading.Thread] = {}

        # ── Колбэки, устанавливает GUI ──────────────────────────────────
        # (text: str, kind: str) → None
        # kind: "agent" | "question" | "tool" | "error"
        self.on_message: Optional[Callable] = None
        # () → str | None  — возвращает Google access_token если подключён
        self.get_google_token: Optional[Callable] = None

        # (text: str, timestamp: str) → None  — новый фрагмент стенограммы
        self.on_transcript: Optional[Callable] = None

        # (reason: str) → None  — агент просит открыть файл
        self.on_request_file: Optional[Callable] = None

        # () → None  — агент просит сохранить сессию
        self.on_save: Optional[Callable] = None

        # (device_index: int) → None  — текущий индекс микрофона из настроек
        self._mic_index: int = -1

    # ------------------------------------------------------------------
    # Публичный API
    # ------------------------------------------------------------------

    def set_core(self, core) -> None:
        """Обновить ядро (после смены провайдера в настройках)."""
        self.core = core

    def set_mic_index(self, idx: int) -> None:
        self._mic_index = idx

    def send(self, user_text: str) -> None:
        """Принять сообщение от пользователя и запустить шаг агента."""
        if not self.core:
            self._emit("Агент не настроен — выберите провайдера в настройках.", "error")
            return

        lower = user_text.lower().strip()

        # Команда "внеси изменения в таблицу" — вызываем напрямую без LLM
        if any(kw in lower for kw in ['внеси', 'update_sheet', 'внести изменения']):
            self.conversation.append({"role": "user", "text": user_text})
            self._spawn("apply_sheet", self._apply_sheet_improvements)
            return

        # Если в сообщении есть URL — перехватываем и грузим напрямую
        url_m = re.search(r'https?://[^\s]+', user_text)
        if url_m:
            url = url_m.group(0).rstrip('.,;)')
            task = user_text.replace(url_m.group(0), '').strip() or "проанализируй содержимое"
            self.conversation.append({"role": "user", "text": user_text})
            self._spawn("fetch_url", self._run_fetch_url, url, task, "")
            return

        # Прямая валидация только при явной команде: "провалидируй <текст>" и т.д.
        okr_text = _extract_validate_text(user_text)
        if okr_text:
            # Определяем тип элемента из запроса пользователя
            type_hint = _detect_element_type(user_text)
            if type_hint and not _okr_line.match(okr_text):
                okr_text = f"{type_hint}: {okr_text}"
            self.conversation.append({"role": "user", "text": user_text})
            self._spawn("validate", self._run_validate, okr_text)
            return

        self.conversation.append({"role": "user", "text": user_text})
        threading.Thread(target=self._step, daemon=True).start()

    def provide_file(self, path: str, content: str, name: str) -> None:
        """Вызывается GUI после того как пользователь выбрал файл."""
        self.ctx["files_raw"][name] = content[:15000]
        self.ctx["files"][name] = content[:3000]
        self.conversation.append({
            "role": "user",
            "text": f"[Файл загружен: {name}] — содержимое включено в контекст. Жди дальнейших инструкций.",
        })
        self._emit(f"📄 Файл принят: {name} ({len(content)} символов)", "tool")
        threading.Thread(target=self._step, daemon=True).start()

    @property
    def is_recording(self) -> bool:
        return self._recording

    def stop_recording_if_active(self) -> None:
        if self._stop_event:
            self._stop_event.set()

    def set_team(self, name: str, industry: str = "") -> None:
        """Зарегистрировать команду для этой сессии. Вызывается из web/server.py."""
        team = _memory.get_or_create_team(name, industry)
        self.ctx["team_id"] = team["team_id"]
        self.ctx["team_name"] = team["name"]
        _memory.increment_session_count(team["team_id"])

    def reset_session(self) -> None:
        """Сбросить контекст сессии (но не диалог). Команда сохраняется."""
        team_id   = self.ctx.get("team_id")
        team_name = self.ctx.get("team_name")
        self.ctx = {
            "files": {}, "files_raw": {}, "transcript_count": 0,
            "team_id": team_id, "team_name": team_name, "last_log_id": None,
        }
        self.core.clear_history() if self.core else None

    # ------------------------------------------------------------------
    # Внутренние методы
    # ------------------------------------------------------------------

    def _emit(self, text: str, kind: str = "agent") -> None:
        if self.on_message:
            self.on_message(text, kind)

    def _build_prompt(self) -> str:
        """Собирает полный промпт для LLM."""
        # Статус сессии
        ctx_lines = []
        if self._recording:
            ctx_lines.append(f"• Запись митинга: ИДЁТ ({self.ctx['transcript_count']} фрагментов)")
        if self.ctx["files"]:
            ctx_lines.append(f"• Загруженные файлы/ссылки: {', '.join(self.ctx['files'].keys())}")
        if self.core and self.core.history:
            ctx_lines.append(f"• Стенограмма: {len(self.core.history)} фрагментов в истории")
        ctx_block = ("\n## ТЕКУЩИЙ КОНТЕКСТ\n" + "\n".join(ctx_lines) + "\n") if ctx_lines else ""

        # Профиль команды — инжектируется если команда зарегистрирована
        team_block = ""
        if self.ctx.get("team_id"):
            try:
                td = _memory.get_team_context(self.ctx["team_id"])
                if td["history_count"] > 0:
                    errs = ", ".join(td["top_errors"]) if td["top_errors"] else "нет"
                    team_block = (
                        f"\n## ПРОФИЛЬ КОМАНДЫ: {self.ctx['team_name']}\n"
                        f"• Прошлых валидаций: {td['history_count']}\n"
                        f"• Средний балл: {td['avg_score']}/10\n"
                        f"• Частые ошибки: {errs}\n"
                        f"• Последние OKR: {td['last_okrs_preview']}\n"
                    )
                else:
                    team_block = f"\n## КОМАНДА: {self.ctx['team_name']} (первая валидация)\n"
            except Exception:
                pass

        # Содержимое файлов — включается в каждый промпт, не вытесняется историей
        files_block = ""
        if self.ctx["files"]:
            parts = []
            for name, content in self.ctx["files"].items():
                parts.append(f"### {name}\n{content}")
            files_block = "\n## ЗАГРУЖЕННЫЕ ФАЙЛЫ И ССЫЛКИ\n" + "\n\n".join(parts) + "\n"

        # История диалога без последнего сообщения (оно идёт отдельно)
        msgs = self.conversation[-12:]
        last_user = msgs[-1]["text"] if msgs and msgs[-1]["role"] == "user" else ""
        history_msgs = msgs[:-1] if last_user else msgs
        history = "\n".join(
            f"{'[USER]' if m['role'] == 'user' else '[AI]'}: {m['text']}"
            for m in history_msgs
        )

        history_block = ("\n## ИСТОРИЯ ДИАЛОГА\n" + history + "\n") if history else ""
        current_block = f"\n## ТЕКУЩИЙ ВОПРОС\n[USER]: {last_user}\n" if last_user else ""

        sys = SYSTEM_PROMPT_WEB if self.web_mode else SYSTEM_PROMPT
        return f"{sys}{team_block}{ctx_block}{files_block}{history_block}{current_block}\n[AI]:"

    def _step(self) -> None:
        """Один шаг агента: вызов LLM → разбор → выполнение."""
        try:
            prompt = self._build_prompt()
            response = self.core._call_llm(prompt, max_tokens=1200)
            self.conversation.append({"role": "assistant", "text": response})

            parsed = _parse_tool(response)
            if parsed:
                tool_name, args = parsed
                self._execute(tool_name, args)
            else:
                self._emit(response.strip(), "agent")

        except Exception as e:
            self._emit(f"Ошибка агента: {e}", "error")

    def _execute(self, tool: str, args: dict) -> None:
        """Диспетчер инструментов."""
        self._emit(f"▶ {tool}", "tool")

        if tool == "ask_clarification":
            questions = args.get("questions", [])
            text = "\n".join(f"{i + 1}. {q}" for i, q in enumerate(questions))
            self._emit(text, "question")

        elif tool == "start_recording":
            self._start_recording()

        elif tool == "stop_recording":
            self._stop_recording()

        elif tool == "analyze_session":
            self._spawn("analyze", self._run_analysis)

        elif tool == "validate_okr":
            okr = args.get("okr_text", "")
            self._spawn("validate", self._run_validate, okr)

        elif tool == "request_file":
            reason = args.get("reason", "")
            if self.on_request_file:
                self.on_request_file(reason)

        elif tool == "save_session":
            if self.on_save:
                self.on_save()

        elif tool == "update_sheet":
            spreadsheet_id = args.get("spreadsheet_id", "")
            sheet_gid = args.get("sheet_gid", 0)
            updates = args.get("updates", [])
            if spreadsheet_id and updates:
                self._spawn("update_sheet", self._run_update_sheet,
                            spreadsheet_id, sheet_gid, updates)
            else:
                self._emit("Укажите spreadsheet_id и список updates.", "error")

        elif tool == "fetch_url":
            url = args.get("url", "")
            task = args.get("task", "проанализируй содержимое")
            sheet = args.get("sheet", "")
            if url:
                self._spawn("fetch_url", self._run_fetch_url, url, task, sheet)
            else:
                self._emit("Укажите URL для загрузки.", "error")

        else:
            self._emit(f"Неизвестный инструмент: {tool}", "error")

    def _spawn(self, name: str, fn: Callable, *args) -> None:
        """Запустить задачу в фоновом потоке (параллельно)."""
        t = threading.Thread(target=fn, args=args, daemon=True)
        with self._lock:
            self._tasks[name] = t
        t.start()

    # ------------------------------------------------------------------
    # Реализации инструментов
    # ------------------------------------------------------------------

    def _start_recording(self) -> None:
        if self.web_mode:
            # Веб: запись через браузер (Web Speech API) — сигнал в JS
            self._emit("", "start_recording")
            self._emit("Запись голоса запущена в браузере. Говорите — текст появится в стенограмме.", "tool")
            return

        if self._recording:
            self._emit("Запись уже идёт.", "agent")
            return

        self._recording = True
        self._stop_event = threading.Event()
        dev = self._mic_index if self._mic_index >= 0 else None

        def _on_text(text: str, ts: str) -> None:
            self.ctx["transcript_count"] += 1
            if self.core:
                self.core.add_to_history(text)
            if self.on_transcript:
                self.on_transcript(text, ts)

        def _record() -> None:
            try:
                self.core.transcribe_meeting(
                    device_index=dev,
                    stop_event=self._stop_event,
                    on_text=_on_text,
                    on_status=lambda m: self._emit(m, "tool"),
                    chunk_seconds=10,
                )
            except Exception as e:
                self._emit(f"Ошибка записи: {e}", "error")
            finally:
                self._recording = False
                self._emit("⏹ Запись завершена.", "tool")

        self._emit("🎙 Запись запущена — говорите!", "tool")
        self._spawn("recording", _record)

    def _stop_recording(self) -> None:
        # Останавливаем серверную запись (десктоп)
        if self._recording and self._stop_event:
            self._stop_event.set()
            self._emit("⏹ Запись остановлена.", "tool")
        elif not self._recording:
            # Веб-версия: запись идёт в браузере — всё равно шлём сигнал
            self._emit("⏹ Останавливаю запись.", "tool")
        # Сигнал браузеру остановить Web Speech API
        self._emit("", "stop_recording")

    def _analyze_file_directly(self, file_name: str, task: str) -> None:
        """Анализирует загруженный файл напрямую через LLM без вызова инструментов.

        Это исключает бесконечный цикл fetch_url: вместо _step() (который видит URL
        в истории и снова вызывает загрузку) — идёт прямой вызов LLM с содержимым файла.
        """
        self._emit(f"🔍 Анализирую «{file_name}»…", "tool")
        content = self.ctx["files_raw"].get(file_name) or self.ctx["files"].get(file_name, "")
        if not content:
            self._emit("Содержимое файла не найдено в контексте.", "error")
            return

        clean_task = task or "Проанализируй содержимое файла с точки зрения методологии OKR."
        prev_context = self.core.get_previous_context() if self.core else ""

        prompt = f"""Ты эксперт по методологии OKR. Проанализируй содержимое файла и выполни задачу.

Методология OKR:
• Objective (O) — качественная вдохновляющая цель БЕЗ цифр. Отвечает «Куда идём?»
• Key Result (KR) — измеримый РЕЗУЛЬТАТ с цифрой и дедлайном, НЕ задача. Отвечает «Как узнаем что достигли?»
• KR не должен быть задачей («сделать X») — только результатом («достичь X%»)

{prev_context}Задача: {clean_task}

Данные из файла «{file_name}»:
{content}

Формат ответа — для каждого Objective и каждого Key Result используй ЭТОТ шаблон блоками (НЕ таблицу):

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
🎯 OBJECTIVE O[N]
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Текущая формулировка:
  [текст Objective]

Оценка: [X]/10

Проблема с формулировкой:
  [что именно в тексте Objective нарушает методологию — есть цифры где не должно быть, слишком размыто, не вдохновляет и т.д.]

Как улучшить формулировку:
  [конкретные изменения текста Objective]

❌ Было (текущая формулировка из таблицы):
  [скопируй точный текущий Objective из данных]
✅ Стало (улучшенная формулировка):
  [новый текст Objective — качественная, вдохновляющая цель без цифр]

  ┈ KR[N] ┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈
  Текущая формулировка:
    [текст KR]

  Оценка: [X]/10

  Проблема с формулировкой:
    [что именно в тексте KR нарушает методологию OKR — задача вместо результата, нет цифры, нет срока и т.д.]

  Как улучшить формулировку:
    [конкретные изменения в тексте — что добавить, убрать, переформулировать]

  ❌ Было (текущая формулировка из таблицы):
    [скопируй точный текущий KR из данных]
  ✅ Стало (улучшенная формулировка):
    [новый текст KR — измеримый результат с цифрой, метрикой и сроком]

Повтори этот блок для каждого O и каждого KR из данных.

В конце добавь:
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
📊 ИТОГ
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Средняя оценка Objectives: [X]/10
Средняя оценка Key Results: [X]/10
Главные системные проблемы:
  • [проблема 1]
  • [проблема 2]

Отвечай на русском языке."""

        try:
            result = self.core._call_llm(prompt, max_tokens=7000)
            self._emit(result, "agent")
            # Добавляем результат в историю для follow-up вопросов
            self.conversation.append({"role": "assistant", "text": result})
        except Exception as e:
            self._emit(f"Ошибка анализа: {e}", "error")

    def _run_analysis(self) -> None:
        self._emit("🔍 Анализирую…", "tool")
        # Если есть загруженные файлы — используем тот же блочный формат что и после загрузки
        if self.ctx.get("files"):
            for name in self.ctx["files"]:
                task = "Проанализируй все Objectives и Key Results по методологии OKR."
                self._analyze_file_directly(name, task)
            return
        # Без файлов — стандартный анализ митинга
        try:
            result = self.core.analyze_okr()
            self._emit(result, "agent")
        except Exception as e:
            self._emit(f"Ошибка анализа: {e}", "error")

    def _apply_sheet_improvements(self) -> None:
        """Автоматически улучшает OKR в Google Sheets без участия LLM-инструментов."""
        spreadsheet_id = self.ctx.get("last_spreadsheet_id", "")
        sheet_gid = self.ctx.get("last_sheet_gid", 0)

        if not spreadsheet_id:
            self._emit("Сначала загрузите Google Таблицу через ссылку.", "error")
            return

        # Загружаем сырой CSV чтобы получить точные номера строк
        import httpx as _hx
        self._emit("📥 Загружаю структуру таблицы…", "tool")
        candidates = [
            f"https://docs.google.com/spreadsheets/d/{spreadsheet_id}/gviz/tq?tqx=out:csv&gid={sheet_gid}",
            f"https://docs.google.com/spreadsheets/d/{spreadsheet_id}/gviz/tq?tqx=out:csv",
        ]
        raw_csv = None
        for url in candidates:
            try:
                r = _hx.get(url, follow_redirects=True, timeout=20,
                            headers={"User-Agent": "Mozilla/5.0"})
                if r.status_code == 200 and r.text.strip():
                    raw_csv = r.text
                    break
            except Exception:
                pass

        if not raw_csv:
            self._emit("Не удалось загрузить таблицу для обновления. Убедитесь что доступ открыт.", "error")
            return

        cells = self.core.extract_okr_cells(raw_csv)
        if not cells:
            self._emit("Не найдены Objectives или Key Results в таблице.", "error")
            return

        token = self.get_google_token() if self.get_google_token else None
        if not token:
            self._emit("Подключите Google Sheets через кнопку 🔗 в боковой панели.", "error")
            return

        self._emit(f"🔍 Генерирую улучшения для {len(cells)} ячеек…", "tool")

        # Просим LLM улучшить формулировки — ответ строго в JSON
        cell_lines = "\n".join(f'{c["type"]}: {c["text"]}' for c in cells)
        prompt = f"""Улучши формулировки OKR строго по методологии. Ответь ТОЛЬКО JSON-массивом без пояснений.

Текущие формулировки:
{cell_lines}

Правила:
- Objective (Oн): качественная вдохновляющая цель БЕЗ цифр
- Key Result (KRн): измеримый результат С цифрой и сроком, не задача

Формат ответа (только JSON, без markdown):
[{{"type": "O1", "improved": "новая формулировка"}}, {{"type": "KR1", "improved": "новая формулировка"}}, ...]"""

        try:
            result = self.core._call_llm(prompt, max_tokens=2000)
        except Exception as e:
            self._emit(f"Ошибка LLM: {e}", "error")
            return

        # Парсим JSON из ответа
        import json as _json, re as _re
        m = _re.search(r'\[[\s\S]*\]', result)
        if not m:
            self._emit("Модель не вернула JSON. Попробуйте ещё раз.", "error")
            return
        try:
            improvements = _json.loads(m.group(0))
        except Exception:
            self._emit("Не удалось разобрать JSON ответа.", "error")
            return

        imp_map = {item["type"]: item.get("improved", "") for item in improvements if "type" in item}

        updates = []
        for cell in cells:
            cell_type = cell["type"].split()[0]  # убираем ⚙
            new_text = imp_map.get(cell_type, "")
            if new_text and new_text.strip() != cell["text"].strip():
                updates.append({
                    "row": cell["row"],
                    "col": cell["col"],
                    "old_value": cell["text"],
                    "new_value": new_text,
                })

        if not updates:
            self._emit("Улучшений нет — все формулировки уже оптимальны.", "agent")
            return

        # Находим первый пустой столбец после всех данных — туда запишем оригиналы (красный)
        import csv as _csv2, io as _io2
        max_col = 0
        for _row in _csv2.reader(_io2.StringIO(raw_csv)):
            if any(c.strip() for c in _row):
                max_col = max(max_col, len(_row))
        orig_col = max_col  # индекс первого пустого столбца (0-based)

        self._emit(f"✏️ Вношу {len(updates)} изменений в таблицу…", "tool")
        self._run_update_sheet(spreadsheet_id, sheet_gid, updates, orig_col=orig_col)

    def _run_update_sheet(self, spreadsheet_id: str, sheet_gid: int, updates: list,
                          orig_col: int = None) -> None:
        """Записывает улучшенные OKR в Google Sheets через Sheets API v4.

        Использует spreadsheets:batchUpdate с updateCells + repeatCell —
        оба работают через sheetId, без необходимости знать имя листа.
        orig_col — столбец для оригинальных формулировок (красный). None = не сохранять.
        """
        import httpx as _hx

        token = self.get_google_token() if self.get_google_token else None
        if not token:
            self._emit(
                "Google Sheets не подключён. Нажмите кнопку '🔗 Подключить Google Sheets' в боковой панели.",
                "error"
            )
            return

        self._emit(f"✏️ Вношу {len(updates)} изменений в таблицу…", "tool")
        headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
        base = f"https://sheets.googleapis.com/v4/spreadsheets/{spreadsheet_id}"

        requests = []

        # Записываем оригинальные формулировки в orig_col (красный фон)
        if orig_col is not None:
            for u in updates:
                requests.append({
                    "updateCells": {
                        "range": {
                            "sheetId": sheet_gid,
                            "startRowIndex": u["row"], "endRowIndex": u["row"] + 1,
                            "startColumnIndex": orig_col, "endColumnIndex": orig_col + 1,
                        },
                        "rows": [{"values": [{"userEnteredValue": {"stringValue": u["old_value"]},
                                              "userEnteredFormat": {"backgroundColor": {"red": 1.0, "green": 0.4, "blue": 0.4}}}]}],
                        "fields": "userEnteredValue,userEnteredFormat.backgroundColor",
                    }
                })

        # Записываем улучшенные формулировки (жёлтый фон)
        for u in updates:
            requests.append({
                "updateCells": {
                    "range": {
                        "sheetId": sheet_gid,
                        "startRowIndex": u["row"], "endRowIndex": u["row"] + 1,
                        "startColumnIndex": u["col"], "endColumnIndex": u["col"] + 1,
                    },
                    "rows": [{"values": [{"userEnteredValue": {"stringValue": u["new_value"]},
                                          "userEnteredFormat": {"backgroundColor": {"red": 1.0, "green": 0.95, "blue": 0.0}}}]}],
                    "fields": "userEnteredValue,userEnteredFormat.backgroundColor",
                }
            })

        r = _hx.post(f"{base}:batchUpdate", headers=headers,
                     json={"requests": requests}, timeout=30)

        if r.status_code not in (200, 201):
            self._emit(f"Ошибка записи в таблицу: {r.text[:250]}", "error")
            return

        orig_note = (
            f" Оригиналы сохранены в столбце {chr(ord('A') + orig_col)} (красный фон)."
            if orig_col is not None else ""
        )
        self._emit(
            f"✅ Готово! Внесено {len(updates)} изменений.{orig_note} "
            f"Новые формулировки — жёлтый фон, исходные — красный.",
            "agent"
        )

    def _run_validate(self, okr_text: str) -> None:

        preview = okr_text[:60] + ("…" if len(okr_text) > 60 else "")
        self._emit(f"🔍 Проверяю: {preview}", "tool")
        try:
            result = self.core.validate_existing_okr(okr_text)
            self._emit(result, "agent")

            # ── Маховик: записать валидацию в лог ────────────────────────
            team_id = self.ctx.get("team_id")
            if team_id:
                try:
                    score  = self.core._extract_score(result)
                    errors = self.core._extract_errors(result)
                    log_id = _memory.log_validation(
                        team_id=team_id,
                        session_id=self._session_id,
                        input_okr=okr_text,
                        score=score,
                        top_errors=errors,
                        suggestions=result,
                    )
                    self.ctx["last_log_id"] = log_id
                    # Паттерн: 3+ раза одна ошибка → агент сам отмечает
                    td = _memory.get_team_context(team_id)
                    if td["top_errors"] and td["history_count"] >= 3:
                        top_err = td["top_errors"][0]
                        self._emit(
                            f"📌 Замечаю паттерн: критерий {top_err} нарушается "
                            f"чаще всего ({td['history_count']} валидаций). "
                            f"Хотите разберём его подробнее?",
                            "agent"
                        )
                except Exception:
                    pass
        except Exception as e:
            self._emit(f"Ошибка валидации: {e}", "error")

    def _run_fetch_url(self, url: str, task: str, sheet: str = "") -> None:
        short = url[:60] + ("…" if len(url) > 60 else "")
        self._emit(f"🌐 Загружаю: {short}" + (f" (вкладка: {sheet})" if sheet else ""), "tool")
        try:
            ok, content = self.core.fetch_url_content(url, sheet_name=sheet)
        except Exception as e:
            self._emit(f"Ошибка загрузки: {e}", "error")
            return

        if not ok:
            self._emit(f"❌ {content}", "error")
            return

        # Имя для хранения в контексте — включаем gid чтобы разные вкладки не перезаписывали друг друга
        import re as _re
        gid_m2 = _re.search(r'gid=(\d+)', url)
        gid_suffix = f"_gid{gid_m2.group(1)}" if gid_m2 else ""
        name = _re.sub(r'https?://', '', url).split('/')[0][:30] + gid_suffix
        self.ctx["files_raw"][name] = content[:15000]
        self.ctx["files"][name] = content[:3000]
        # Сохраняем spreadsheet_id и gid для последующей записи
        import re as _re2
        _sid_m = _re2.search(r'spreadsheets/d/([a-zA-Z0-9_-]+)', url)
        _gid_m = _re2.search(r'gid=(\d+)', url)
        if _sid_m:
            self.ctx["last_spreadsheet_id"] = _sid_m.group(1)
            self.ctx["last_sheet_gid"] = int(_gid_m.group(1)) if _gid_m else 0
        self._emit(f"✅ Загружено: {name} ({len(content)} символов)", "tool")

        # Анализируем НАПРЯМУЮ через LLM — без _step() и без выбора инструментов.
        # _step() вызывал бы LLM который снова видел URL и снова вызывал fetch_url → бесконечный цикл.
        self._spawn("analyze_file", self._analyze_file_directly, name, task)
