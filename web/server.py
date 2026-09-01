"""
OKR Agent — Web Server (FastAPI) v2

Запуск:
    uvicorn web.server:app --host 0.0.0.0 --port 8000 --reload

Каждый пользователь получает изолированную сессию со своим AgentLoop.
"""
import os
import sys
import uuid
import json
import time
import asyncio
import tempfile
import threading
import queue as _queue
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, UploadFile, File, Form, HTTPException
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse, JSONResponse

# ── Добавляем корень проекта в sys.path ─────────────────────────────────────
ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from okr_agent_core import OKRAgentCore
from agent_loop import AgentLoop
import memory as _memory

_memory.init_db()

# ── Конфигурация из переменных окружения ────────────────────────────────────
PROVIDER    = os.getenv("OKR_PROVIDER", "gemini")
API_KEY     = os.getenv("OKR_API_KEY", "")
OLLAMA_MODEL = os.getenv("OKR_OLLAMA_MODEL", "neural-chat")
LANGUAGE    = os.getenv("OKR_LANGUAGE", "ru-RU")
ACCESS_PASSWORD = os.getenv("OKR_PASSWORD", "")

# ── Ключи для каждого провайдера ─────────────────────────────────────────────
# Каждый провайдер может иметь свой ключ. Если не задан отдельный ключ,
# используется OKR_API_KEY (для провайдера по умолчанию).
_PROVIDER_KEYS: dict[str, str] = {
    "groq":    os.getenv("GROQ_API_KEY")    or (API_KEY if PROVIDER == "groq"    else ""),
    "gemini":  os.getenv("GEMINI_API_KEY")  or (API_KEY if PROVIDER == "gemini"  else ""),
    "claude":  os.getenv("CLAUDE_API_KEY")  or os.getenv("ANTHROPIC_API_KEY") or (API_KEY if PROVIDER == "claude"  else ""),
    "mistral": os.getenv("MISTRAL_API_KEY") or (API_KEY if PROVIDER == "mistral" else ""),
    "ollama":  os.getenv("OLLAMA_HOST", ""),  # только если явно настроен
}

PROVIDER_LABELS = {
    "groq": "Groq", "gemini": "Gemini", "claude": "Claude",
    "mistral": "Mistral", "ollama": "Ollama (local)",
}

def _available_providers() -> list[dict]:
    result = []
    for p, key in _PROVIDER_KEYS.items():
        if key:
            result.append({"id": p, "label": PROVIDER_LABELS.get(p, p)})
    return result

# Google OAuth credentials (из переменных окружения Railway)
GOOGLE_CLIENT_ID     = os.getenv("GOOGLE_CLIENT_ID", "")
GOOGLE_CLIENT_SECRET = os.getenv("GOOGLE_CLIENT_SECRET", "")
GOOGLE_REDIRECT_URI  = os.getenv(
    "GOOGLE_REDIRECT_URI",
    "https://web-production-16b46e.up.railway.app/auth/google/callback"
)
GOOGLE_SCOPES = "https://www.googleapis.com/auth/spreadsheets"

app = FastAPI(title="OKR Agent")

# ── Статические файлы ────────────────────────────────────────────────────────
STATIC_DIR = Path(__file__).parent / "static"
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

# ── Хранилище сессий: session_id → {"loop": AgentLoop, "ws": WebSocket} ─────
_sessions: dict[str, dict] = {}
_sessions_lock = threading.Lock()


def _make_core(provider: str = None, api_key: str = None) -> OKRAgentCore:
    """Создаёт новый OKRAgentCore. provider/api_key переопределяют значения из env."""
    p = provider or PROVIDER
    k = api_key or _PROVIDER_KEYS.get(p) or API_KEY
    if not k and p != "ollama":
        raise ValueError(f"API-ключ для провайдера '{p}' не задан.")
    return OKRAgentCore(
        provider=p,
        api_key=k or None,
        ollama_model=OLLAMA_MODEL,
        language=LANGUAGE,
    )


def _get_or_create_session(session_id: str) -> dict:
    with _sessions_lock:
        if session_id not in _sessions:
            core = _make_core()
            loop = AgentLoop(core=core, web_mode=True, session_id=session_id)
            _sessions[session_id] = {
                "loop": loop,
                "created": time.time(),
                "ws": None,
                "provider": PROVIDER,
            }
        return _sessions[session_id]


# ── HTTP endpoints ───────────────────────────────────────────────────────────

@app.get("/", response_class=HTMLResponse)
async def index():
    html = (STATIC_DIR / "index.html").read_text(encoding="utf-8")
    return HTMLResponse(content=html, headers={"Cache-Control": "no-store"})


@app.post("/api/session")
async def create_session(password: str = Form(default="")):
    """Создать новую сессию. Возвращает session_id."""
    if ACCESS_PASSWORD and password != ACCESS_PASSWORD:
        raise HTTPException(status_code=403, detail="Неверный пароль")
    session_id = str(uuid.uuid4())
    _get_or_create_session(session_id)
    return {"session_id": session_id}


@app.get("/api/providers")
async def list_providers():
    """Вернуть список доступных провайдеров и текущий по умолчанию."""
    return {"providers": _available_providers(), "default": PROVIDER}


@app.get("/api/test/{provider}")
async def test_provider(provider: str):
    """Тестовый вызов провайдера — возвращает OK или точную ошибку по каждой модели."""
    available = [p["id"] for p in _available_providers()]
    if provider not in available:
        return {"ok": False, "error": f"Провайдер '{provider}' не настроен (нет ключа)"}
    try:
        core = await asyncio.to_thread(_make_core, provider)
    except Exception as e:
        return {"ok": False, "provider": provider, "error": f"Ошибка инициализации: {e}"}

    if provider == "gemini":
        import google.generativeai as genai
        results = []
        all_models = [core.gemini_model] + [m for m in core.GEMINI_FALLBACK_MODELS if m != core.gemini_model]
        for model in all_models:
            try:
                m = genai.GenerativeModel(model)
                resp = m.generate_content("Скажи: работаю")
                results.append({"model": model, "ok": True, "response": resp.text.strip()[:100]})
                break
            except Exception as e:
                results.append({"model": model, "ok": False, "error": str(e)[:200]})
        return {"provider": "gemini", "models": results}

    try:
        result = await asyncio.to_thread(core._call_llm, "Ответь одним словом: работаю", 50)
        return {"ok": True, "provider": provider, "response": result}
    except Exception as e:
        return {"ok": False, "provider": provider, "error": str(e)}


@app.post("/api/{session_id}/provider")
async def switch_provider(session_id: str, body: dict):
    """Сменить LLM-провайдер для конкретной сессии."""
    provider = body.get("provider", "").strip()
    available = [p["id"] for p in _available_providers()]
    if provider not in available:
        raise HTTPException(status_code=400, detail=f"Провайдер '{provider}' недоступен. Доступны: {available}")
    sess = _get_or_create_session(session_id)
    try:
        new_core = _make_core(provider=provider)
        sess["loop"].set_core(new_core)
        sess["provider"] = provider
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    return {"ok": True, "provider": provider, "label": PROVIDER_LABELS.get(provider, provider)}


@app.get("/api/{session_id}/provider")
async def get_provider(session_id: str):
    """Вернуть текущий провайдер сессии."""
    sess = _get_or_create_session(session_id)
    p = sess.get("provider", PROVIDER)
    return {"provider": p, "label": PROVIDER_LABELS.get(p, p)}


# ── Маховик: команды и метрики ───────────────────────────────────────────────

@app.post("/api/{session_id}/team")
async def set_team(session_id: str, body: dict):
    """Зарегистрировать команду для сессии. Вызывается сразу после создания сессии."""
    name = str(body.get("name", "")).strip()
    if not name:
        raise HTTPException(status_code=400, detail="name обязателен")
    industry = str(body.get("industry", "")).strip()
    sess = _get_or_create_session(session_id)
    agent: AgentLoop = sess["loop"]
    agent.set_team(name, industry)
    return {"ok": True, "team_id": agent.ctx["team_id"], "team_name": agent.ctx["team_name"]}


@app.get("/api/{session_id}/team/history")
async def team_history(session_id: str):
    """История валидаций команды для этой сессии."""
    sess = _get_or_create_session(session_id)
    agent: AgentLoop = sess["loop"]
    team_id = agent.ctx.get("team_id")
    if not team_id:
        return {"team_name": None, "history": None}
    ctx = _memory.get_team_context(team_id)
    return {"team_name": agent.ctx.get("team_name"), "history": ctx}


@app.get("/api/admin/metrics")
async def admin_metrics():
    """Сводные метрики маховика для Roman."""
    return _memory.get_metrics()


@app.post("/api/{session_id}/validate")
async def validate_okr(session_id: str, body: dict):
    """Валидация OKR по методологии."""
    sess = _get_or_create_session(session_id)
    text = body.get("text", "").strip()
    if not text:
        raise HTTPException(status_code=400, detail="Текст не задан")
    result = await asyncio.to_thread(sess["loop"].core.validate_existing_okr, text)
    return {"result": result}


@app.post("/api/{session_id}/analyze")
async def analyze_session(session_id: str, body: dict):
    """Анализ всей сессии."""
    sess = _get_or_create_session(session_id)
    use_prev = body.get("use_previous_context", False)
    result = await asyncio.to_thread(sess["loop"].core.analyze_okr, use_prev)
    return {"result": result}


@app.post("/api/{session_id}/upload")
async def upload_file(session_id: str, file: UploadFile = File(...)):
    """Загрузка файла в контекст агента."""
    sess = _get_or_create_session(session_id)
    suffix = Path(file.filename).suffix.lower()
    allowed = {".txt", ".md", ".csv", ".xlsx", ".xls", ".pdf"}
    if suffix not in allowed:
        raise HTTPException(status_code=400, detail=f"Тип файла не поддерживается: {suffix}")

    content_bytes = await file.read()
    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
        tmp.write(content_bytes)
        tmp_path = tmp.name

    try:
        ok, content = sess["loop"].core.process_file(tmp_path)
    finally:
        os.unlink(tmp_path)

    if not ok:
        raise HTTPException(status_code=422, detail=content)

    sess["loop"].provide_file(tmp_path, content, file.filename)
    return {"filename": file.filename, "chars": len(content)}


@app.post("/api/{session_id}/audio")
async def transcribe_audio(session_id: str, file: UploadFile = File(...)):
    """Распознавание голосового фрагмента через Google STT."""
    import speech_recognition as sr

    sess = _get_or_create_session(session_id)
    data = await file.read()

    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
        tmp.write(data)
        tmp_path = tmp.name

    try:
        recognizer = sr.Recognizer()
        with sr.AudioFile(tmp_path) as source:
            audio = recognizer.record(source)
        text = recognizer.recognize_google(audio, language=LANGUAGE)
        sess["loop"].core.add_to_history(text)
        return {"text": text}
    except sr.UnknownValueError:
        return {"text": ""}
    except sr.RequestError as e:
        raise HTTPException(status_code=503, detail=f"Google STT недоступен: {e}")
    finally:
        os.unlink(tmp_path)


@app.post("/api/{session_id}/url")
async def fetch_url(session_id: str, body: dict):
    """Загрузить содержимое по URL и добавить в контекст сессии."""
    sess = _get_or_create_session(session_id)
    url = body.get("url", "").strip()
    task = body.get("task", "проанализируй содержимое").strip()
    if not url:
        raise HTTPException(status_code=400, detail="URL не указан")
    ok, content = await asyncio.to_thread(sess["loop"].core.fetch_url_content, url)
    if not ok:
        raise HTTPException(status_code=422, detail=content)
    import re as _re
    name = _re.sub(r'https?://', '', url).split('/')[0][:40]
    sess["loop"].ctx["files"][name] = content[:3000]
    return {"name": name, "chars": len(content), "task": task}


@app.post("/api/{session_id}/chat_test")
async def chat_test(session_id: str, body: dict):
    """Диагностика: тестирует агента через HTTP (без WebSocket)."""
    sess = _get_or_create_session(session_id)
    text = body.get("text", "привет")
    result_q: _queue.Queue = _queue.Queue()
    orig = sess["loop"].on_message

    def _capture(t, k):
        result_q.put({"text": t, "kind": k})

    sess["loop"].on_message = _capture
    sess["loop"].send(text)
    try:
        result = await asyncio.wait_for(
            asyncio.to_thread(result_q.get, True, 40),
            timeout=45,
        )
        return result
    except (asyncio.TimeoutError, Exception):
        return {"error": "timeout — agent did not respond"}
    finally:
        sess["loop"].on_message = orig


@app.post("/api/{session_id}/transcript")
async def add_transcript(session_id: str, body: dict):
    """Добавить распознанный текст в историю сессии (отправляется браузером после Web Speech API)."""
    sess = _get_or_create_session(session_id)
    text = body.get("text", "").strip()
    if text:
        sess["loop"].core.add_to_history(text)
    return {"ok": True}


@app.get("/api/{session_id}/history")
async def get_history(session_id: str):
    sess = _get_or_create_session(session_id)
    return {"history": sess["loop"].core.history}


@app.delete("/api/{session_id}/history")
async def clear_history(session_id: str):
    sess = _get_or_create_session(session_id)
    sess["loop"].reset_session()
    return {"ok": True}


# ── WebSocket — агент-чат ───────────────────────────────────────────────────

@app.websocket("/ws/{session_id}")
async def websocket_endpoint(websocket: WebSocket, session_id: str):
    await websocket.accept()
    sess = _get_or_create_session(session_id)
    agent: AgentLoop = sess["loop"]
    sess["ws"] = websocket

    # Обычная thread-safe очередь из стандартной библиотеки.
    # Фоновые потоки кладут сюда напрямую (без event loop).
    send_q: _queue.Queue = _queue.Queue()

    def _send(text: str, kind: str):
        send_q.put(json.dumps({"type": kind, "text": text}))

    agent.on_message      = lambda t, k: _send(t, k)
    agent.on_transcript   = lambda t, ts: _send(f"[{ts}] {t}", "transcript")
    agent.on_request_file = lambda r: _send(r, "file_needed")
    agent.on_save         = lambda: _send("save", "save_needed")
    # Даём агенту доступ к Google OAuth токену сессии
    agent.get_google_token = lambda: sess.get("google_token", {}).get("access_token")

    # Приветствие
    _send(f"Сессия {session_id[:8]}… Провайдер: {PROVIDER}", "connected")

    async def _send_loop():
        """Отправляет накопленные сообщения в WebSocket (poll каждые 50 мс)."""
        while True:
            try:
                while not send_q.empty():
                    await websocket.send_text(send_q.get_nowait())
            except Exception:
                return
            await asyncio.sleep(0.05)

    async def _recv_loop():
        """Принимает сообщения от браузера и передаёт агенту."""
        try:
            while True:
                raw = await websocket.receive_text()
                data = json.loads(raw)
                user_text = data.get("text", "").strip()
                if user_text:
                    agent.send(user_text)
        except WebSocketDisconnect:
            sess["ws"] = None
        except Exception:
            pass

    send_task = asyncio.create_task(_send_loop())
    recv_task = asyncio.create_task(_recv_loop())
    try:
        await asyncio.gather(recv_task, send_task, return_exceptions=True)
    finally:
        send_task.cancel()
        recv_task.cancel()


# ── Google OAuth ─────────────────────────────────────────────────────────────

from fastapi.responses import RedirectResponse

@app.get("/auth/google")
async def auth_google(session_id: str):
    """Шаг 1: перенаправляем пользователя на страницу согласия Google."""
    if not GOOGLE_CLIENT_ID:
        raise HTTPException(status_code=503, detail="Google OAuth не настроен. Добавьте GOOGLE_CLIENT_ID в переменные окружения.")
    import urllib.parse
    params = urllib.parse.urlencode({
        "client_id":     GOOGLE_CLIENT_ID,
        "redirect_uri":  GOOGLE_REDIRECT_URI,
        "response_type": "code",
        "scope":         GOOGLE_SCOPES,
        "access_type":   "offline",
        "prompt":        "consent",
        "state":         session_id,
    })
    return RedirectResponse(f"https://accounts.google.com/o/oauth2/v2/auth?{params}")


@app.get("/auth/google/callback")
async def auth_google_callback(code: str = "", state: str = "", error: str = ""):
    """Шаг 2: Google вернул code — обмениваем на токен."""
    if error:
        return HTMLResponse(f"<h3>Ошибка авторизации: {error}</h3><script>window.close()</script>")
    if not code or not state:
        return HTMLResponse("<h3>Неверный запрос</h3>")

    import httpx as _hx
    resp = _hx.post("https://oauth2.googleapis.com/token", data={
        "code":          code,
        "client_id":     GOOGLE_CLIENT_ID,
        "client_secret": GOOGLE_CLIENT_SECRET,
        "redirect_uri":  GOOGLE_REDIRECT_URI,
        "grant_type":    "authorization_code",
    }, timeout=15)

    if resp.status_code != 200:
        return HTMLResponse(f"<h3>Ошибка получения токена: {resp.text[:200]}</h3>")

    tokens = resp.json()
    sess = _get_or_create_session(state)
    sess["google_token"] = tokens  # содержит access_token и refresh_token

    return HTMLResponse("""
        <html><body style="font-family:sans-serif;background:#0d0d1a;color:#4ade80;padding:40px;text-align:center">
        <h2>✅ Google Sheets подключён!</h2>
        <p>Можно закрыть это окно и вернуться в агент.</p>
        <script>
          if (window.opener) { window.opener.postMessage('google_connected', '*'); }
          setTimeout(() => window.close(), 2000);
        </script>
        </body></html>
    """)


async def _refresh_google_token(sess: dict) -> str:
    """Обновляет access_token если истёк, возвращает актуальный."""
    import httpx as _hx, time as _t
    token_data = sess.get("google_token", {})
    access_token = token_data.get("access_token", "")
    refresh_token = token_data.get("refresh_token", "")
    expires_at = token_data.get("expires_at", 0)

    if _t.time() < expires_at - 60:
        return access_token  # ещё действует

    if not refresh_token:
        raise HTTPException(status_code=401, detail="Требуется повторная авторизация Google.")

    resp = _hx.post("https://oauth2.googleapis.com/token", data={
        "refresh_token": refresh_token,
        "client_id":     GOOGLE_CLIENT_ID,
        "client_secret": GOOGLE_CLIENT_SECRET,
        "grant_type":    "refresh_token",
    }, timeout=15)
    if resp.status_code != 200:
        raise HTTPException(status_code=401, detail="Не удалось обновить Google токен. Авторизуйтесь снова.")

    new_tokens = resp.json()
    new_tokens["refresh_token"] = refresh_token
    new_tokens["expires_at"] = _t.time() + new_tokens.get("expires_in", 3600)
    sess["google_token"] = new_tokens
    return new_tokens["access_token"]


@app.get("/api/{session_id}/google/status")
async def google_status(session_id: str):
    """Проверить подключён ли Google Sheets для этой сессии."""
    sess = _get_or_create_session(session_id)
    connected = bool(sess.get("google_token", {}).get("access_token"))
    return {"connected": connected}


@app.post("/api/{session_id}/sheets/update")
async def sheets_update(session_id: str, body: dict):
    """Записать улучшенные OKR в Google Sheets и выделить изменения жёлтым.

    body:
      spreadsheet_id: str
      sheet_gid: int  (0 для первого листа)
      updates: list[{row: int, col: int, old_value: str, new_value: str}]
    """
    import httpx as _hx

    sess = _get_or_create_session(session_id)
    if not sess.get("google_token"):
        raise HTTPException(status_code=401, detail="Сначала подключите Google Sheets через кнопку 🔗.")

    access_token = await _refresh_google_token(sess)
    spreadsheet_id = body.get("spreadsheet_id", "")
    sheet_gid = body.get("sheet_gid", 0)
    updates = body.get("updates", [])

    if not spreadsheet_id or not updates:
        raise HTTPException(status_code=400, detail="spreadsheet_id и updates обязательны.")

    headers = {"Authorization": f"Bearer {access_token}", "Content-Type": "application/json"}
    base = f"https://sheets.googleapis.com/v4/spreadsheets/{spreadsheet_id}"

    # 1. Записываем новые значения
    value_requests = []
    for u in updates:
        row, col = u["row"], u["col"]
        col_letter = chr(ord('A') + col)
        range_notation = f"{col_letter}{row + 1}"
        value_requests.append({
            "range": range_notation,
            "values": [[u["new_value"]]],
        })

    if value_requests:
        r = _hx.post(f"{base}/values:batchUpdate", headers=headers, json={
            "valueInputOption": "USER_ENTERED",
            "data": value_requests,
        }, timeout=30)
        if r.status_code not in (200, 201):
            raise HTTPException(status_code=502, detail=f"Ошибка записи значений: {r.text[:200]}")

    # 2. Красим изменённые ячейки в жёлтый
    format_requests = []
    for u in updates:
        row, col = u["row"], u["col"]
        format_requests.append({
            "repeatCell": {
                "range": {
                    "sheetId":          sheet_gid,
                    "startRowIndex":    row,
                    "endRowIndex":      row + 1,
                    "startColumnIndex": col,
                    "endColumnIndex":   col + 1,
                },
                "cell": {
                    "userEnteredFormat": {
                        "backgroundColor": {"red": 1.0, "green": 0.95, "blue": 0.0}
                    }
                },
                "fields": "userEnteredFormat.backgroundColor",
            }
        })

    if format_requests:
        r = _hx.post(f"{base}:batchUpdate", headers=headers,
                     json={"requests": format_requests}, timeout=30)
        if r.status_code not in (200, 201):
            raise HTTPException(status_code=502, detail=f"Ошибка форматирования: {r.text[:200]}")

    return {"updated": len(updates), "message": f"Обновлено {len(updates)} ячеек, выделены жёлтым."}


# ── Очистка старых сессий (>4 часов) ────────────────────────────────────────

async def _cleanup_task():
    while True:
        await asyncio.sleep(3600)
        cutoff = time.time() - 4 * 3600
        with _sessions_lock:
            old = [sid for sid, s in _sessions.items() if s["created"] < cutoff]
            for sid in old:
                _sessions[sid]["loop"].stop_recording_if_active()
                del _sessions[sid]


@app.on_event("startup")
async def startup():
    asyncio.create_task(_cleanup_task())
