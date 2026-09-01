'use strict';

const App = (() => {

  // ── Per-agent state ────────────────────────────────────────────────
  const state = {
    text:   { sessionId: null, ws: null },
    sheets: { sessionId: null, ws: null },
    voice:  { sessionId: null, ws: null },
  };
  const LS_KEY = { text: 'okr_session_text', sheets: 'okr_session_sheets', voice: 'okr_session_voice' };

  let needsPassword = false;
  let currentAgent = 'text';
  let _teamName = '';

  // ── Team helpers ─────────────────────────────────────────────────
  async function _registerTeam(sessionId, name) {
    try {
      await fetch(`/api/${sessionId}/team`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ name }),
      });
    } catch (_) {}
  }

  function _showTeamBadge() {
    const badge = document.getElementById('team-badge');
    if (!badge) return;
    const name = _teamName || localStorage.getItem('okr_team_name') || '';
    if (name) {
      badge.textContent = '👥 ' + name;
      badge.style.display = 'inline-flex';
    } else {
      badge.style.display = 'none';
    }
  }

  // ── Init ─────────────────────────────────────────────────────────
  async function init() {
    // Restore team name
    _teamName = localStorage.getItem('okr_team_name') || '';
    if (_teamName) {
      const el = document.getElementById('team-name-input');
      if (el) el.value = _teamName;
    }

    // Try to restore text-agent session (default agent on load)
    const saved = localStorage.getItem(LS_KEY.text);
    if (saved) {
      state.text.sessionId = saved;
      if (_teamName) await _registerTeam(saved, _teamName);
      showApp();
      _showTeamBadge();
      await connectWS('text');
      return;
    }
    try {
      const r = await fetch('/api/session', { method: 'POST', body: new FormData() });
      if (r.status === 403) {
        needsPassword = true;
        document.getElementById('password-row').style.display = 'block';
      } else if (r.ok) {
        const d = await r.json();
        state.text.sessionId = d.session_id;
        localStorage.setItem(LS_KEY.text, d.session_id);
        showApp();
        await connectWS('text');
        return;
      }
    } catch (_) {}
    document.getElementById('login-screen').style.display = 'flex';
  }

  document.getElementById('enter-btn').addEventListener('click', async () => {
    const teamName = (document.getElementById('team-name-input').value || '').trim();
    const pwd = document.getElementById('password-input').value;
    const form = new FormData();
    if (needsPassword) form.append('password', pwd);
    const r = await fetch('/api/session', { method: 'POST', body: form });
    if (r.status === 403) { showLoginError('Неверный пароль'); return; }
    if (!r.ok) { showLoginError('Ошибка сервера'); return; }
    const d = await r.json();
    state.text.sessionId = d.session_id;
    localStorage.setItem(LS_KEY.text, d.session_id);
    if (teamName) {
      _teamName = teamName;
      localStorage.setItem('okr_team_name', teamName);
      await _registerTeam(d.session_id, teamName);
    }
    showApp();
    _showTeamBadge();
    await connectWS('text');
  });

  document.getElementById('password-input').addEventListener('keydown', e => {
    if (e.key === 'Enter') document.getElementById('enter-btn').click();
  });

  document.getElementById('new-session-btn').addEventListener('click', async () => {
    if (!confirm('Сбросить сессию текущего агента? История будет очищена.')) return;
    const agent = currentAgent;
    const old = state[agent];
    if (old.ws) { old.ws.close(); old.ws = null; }
    old.sessionId = null;
    localStorage.removeItem(LS_KEY[agent]);
    // clear UI for that agent
    if (agent === 'text') {
      document.getElementById('t-chat-messages').innerHTML = '';
      document.getElementById('t-transcript-box').textContent = '';
      document.getElementById('t-analysis-box').textContent = '';
    } else if (agent === 'sheets') {
      document.getElementById('s-chat-messages').innerHTML = '';
      document.getElementById('s-analysis-box').textContent = '';
    } else if (agent === 'voice') {
      voice._clearState();
    }
    await ensureSession(agent);
    await connectWS(agent);
  });

  // ── Session helpers ───────────────────────────────────────────────
  async function ensureSession(agent) {
    if (state[agent].sessionId) return state[agent].sessionId;
    const saved = localStorage.getItem(LS_KEY[agent]);
    if (saved) { state[agent].sessionId = saved; return saved; }
    const r = await fetch('/api/session', { method: 'POST', body: new FormData() });
    if (!r.ok) return null;
    const d = await r.json();
    state[agent].sessionId = d.session_id;
    localStorage.setItem(LS_KEY[agent], d.session_id);
    return d.session_id;
  }

  // ── WebSocket ─────────────────────────────────────────────────────
  async function connectWS(agent) {
    const sid = state[agent].sessionId || await ensureSession(agent);
    if (!sid) return;
    if (state[agent].ws && state[agent].ws.readyState === WebSocket.OPEN) return;
    const proto = location.protocol === 'https:' ? 'wss' : 'ws';
    const ws = new WebSocket(`${proto}://${location.host}/ws/${sid}`);
    state[agent].ws = ws;
    ws.onmessage = e => handleServerMsg(agent, JSON.parse(e.data));
    ws.onclose = () => {
      setTimeout(() => { if (state[agent].sessionId) connectWS(agent); }, 3000);
    };
  }

  function handleServerMsg(agent, { type, text }) {
    if (type === 'connected') {
      document.getElementById('provider-badge').textContent = '🟢 ' + (text.split('Провайдер: ')[1] || text);
      return;
    }
    if (type === 'transcript') {
      if (agent === 'text') _appendContent('t-transcript-box', text);
      return;
    }
    if (type === 'file_needed') {
      _addMsg(agent, 'Пожалуйста, загрузите файл.\nПричина: ' + text, 'question');
      return;
    }
    if (type === 'save_needed') {
      if (agent === 'text') text_obj.saveSession();
      else if (agent === 'sheets') sheets_obj.saveSession();
      return;
    }
    if (['agent', 'question', 'tool', 'error'].includes(type)) {
      if (text) _addMsg(agent, text, type);
      if (type === 'agent' && text && text.length > 200) {
        if (agent === 'text') { _appendContent('t-analysis-box', text); text_obj.showTab('analysis'); }
        else if (agent === 'sheets') { _appendContent('s-analysis-box', text); }
      }
    }
  }

  function sendWS(agent, text) {
    const ws = state[agent].ws;
    if (!ws || ws.readyState !== WebSocket.OPEN) {
      _addMsg(agent, 'Нет соединения с сервером. Подождите…', 'error');
      return;
    }
    ws.send(JSON.stringify({ text }));
  }

  // ── Shared UI helpers ─────────────────────────────────────────────
  function _msgBoxId(agent) {
    return agent === 'text' ? 't-chat-messages' : agent === 'sheets' ? 's-chat-messages' : null;
  }

  function _addMsg(agent, text, kind) {
    const boxId = _msgBoxId(agent);
    if (!boxId) return;
    const box = document.getElementById(boxId);
    const div = document.createElement('div');
    div.className = `msg ${kind}`;
    const labels = { agent: 'Агент', user: 'Вы', question: 'Агент — уточнение', tool: '', error: 'Ошибка' };
    const label = labels[kind] || '';
    if (label) {
      const lbl = document.createElement('div');
      lbl.className = 'msg-label';
      lbl.textContent = label;
      div.appendChild(lbl);
    }
    const content = document.createElement('div');
    content.textContent = text;
    div.appendChild(content);
    box.appendChild(div);
    box.scrollTop = box.scrollHeight;
  }

  function _appendContent(boxId, text) {
    const box = document.getElementById(boxId);
    const sep = box.textContent ? '\n\n── ' + new Date().toLocaleTimeString('ru') + ' ──\n' : '';
    box.textContent += sep + text;
    box.scrollTop = box.scrollHeight;
  }

  function _escapeHtml(s) {
    return s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
  }

  function showLoginError(msg) {
    const el = document.getElementById('login-error');
    el.textContent = msg;
    el.style.display = 'block';
  }

  // ── Provider selector ─────────────────────────────────────────────
  async function _loadProviders() {
    try {
      const r = await fetch('/api/providers');
      if (!r.ok) return;
      const d = await r.json();
      const sel = document.getElementById('provider-select');
      sel.innerHTML = '';
      d.providers.forEach(p => {
        const opt = document.createElement('option');
        opt.value = p.id;
        opt.textContent = p.label;
        if (p.id === d.default) opt.selected = true;
        sel.appendChild(opt);
      });
    } catch (_) {}
  }

  async function switchProvider(providerId) {
    if (!providerId) return;
    const dot = document.getElementById('provider-dot');
    dot.textContent = '🟡';
    const agents = ['text', 'sheets', 'voice'];
    let ok = false;
    for (const agent of agents) {
      const sid = state[agent].sessionId;
      if (!sid) continue;
      try {
        const r = await fetch(`/api/${sid}/provider`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ provider: providerId }),
        });
        if (r.ok) ok = true;
      } catch (_) {}
    }
    dot.textContent = ok ? '🟢' : '🔴';
    if (ok) {
      const label = document.getElementById('provider-select').selectedOptions[0]?.textContent || providerId;
      _addMsg('text', `✅ Провайдер переключён на ${label}`, 'tool');
    }
  }

  // ── App show ──────────────────────────────────────────────────────
  function showApp() {
    document.getElementById('login-screen').style.display = 'none';
    document.getElementById('app-screen').style.display = 'flex';
    _loadProviders();
    _addMsg('text', 'Привет! Я ваш OKR-агент. Просто напишите мне — я помогу разобраться с вашими OKR.\n\nЧто можно сделать прямо в чате:\n• «Помоги сформулировать OKR для команды разработки»\n• «Проверь наши OKR — вот они: …»\n• «Что такое хороший Key Result?»\n• «Разбери мой Objective и найди слабые места»\n\nДополнительные возможности:\n• Загрузите файл (.txt .csv .xlsx .pdf) через кнопку слева\n• Загрузите аудио встречи — расшифрую и проанализирую\n• Вкладка «Таблицы» — анализ Google Sheets и внесение изменений\n• Вкладка «Голосовой» — проверка OKR вживую во время митинга', 'agent');
    document.getElementById('voice-transcript').addEventListener('mouseup', voice._onTranscriptSelect);
    document.getElementById('voice-transcript').addEventListener('keyup', voice._onTranscriptSelect);
  }

  // ── Agent switcher ────────────────────────────────────────────────
  async function switchAgent(name) {
    currentAgent = name;
    document.getElementById('text-screen').style.display   = name === 'text'   ? 'grid' : 'none';
    document.getElementById('sheets-screen').style.display = name === 'sheets' ? 'grid' : 'none';
    document.getElementById('voice-screen').style.display  = name === 'voice'  ? 'flex' : 'none';
    document.getElementById('switch-text').classList.toggle('active', name === 'text');
    document.getElementById('switch-sheets').classList.toggle('active', name === 'sheets');
    document.getElementById('switch-voice').classList.toggle('active', name === 'voice');

    // Ensure session + WS for this agent
    await ensureSession(name);
    await connectWS(name);

    // First visit to sheets: check google status
    if (name === 'sheets') sheets_obj._checkGoogleStatus();
  }

  // ════════════════════════════════════════════════════════════════
  // АГЕНТ 1: ТЕКСТОВЫЙ
  // ════════════════════════════════════════════════════════════════
  const text_obj = {

    sendMessage() {
      const input = document.getElementById('t-chat-input');
      const txt = input.value.trim();
      if (!txt) return;
      input.value = '';
      _addMsg('text', txt, 'user');
      sendWS('text', txt);
    },

    onChatKey(e) {
      if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); text_obj.sendMessage(); }
    },

    async validateOKR() {
      const txt = document.getElementById('t-validate-input').value.trim();
      if (!txt) { alert('Введите текст для проверки'); return; }
      _addMsg('text', `Проверяю OKR:\n«${txt}»`, 'user');
      _addMsg('text', '⏳ Анализирую по методологии OKR…', 'tool');
      try {
        const r = await fetch(`/api/${state.text.sessionId}/validate`, {
          method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ text: txt }),
        });
        const d = await r.json();
        _addMsg('text', d.result, 'agent');
      } catch (e) { _addMsg('text', 'Ошибка: ' + e.message, 'error'); }
    },

    async uploadFile(input) {
      const file = input.files[0];
      if (!file) return;
      const status = document.getElementById('t-file-status');
      status.textContent = '⏳ Загружаю…';
      const form = new FormData();
      form.append('file', file);
      try {
        const r = await fetch(`/api/${state.text.sessionId}/upload`, { method: 'POST', body: form });
        if (!r.ok) { const d = await r.json(); throw new Error(d.detail); }
        const d = await r.json();
        status.textContent = `✓ ${d.filename} (${d.chars} символов)`;
        _addMsg('text', `📎 Файл загружен: ${d.filename}\nАгент получил содержимое.`, 'tool');
      } catch (e) {
        status.textContent = '⚠ ' + e.message;
        _addMsg('text', 'Ошибка загрузки: ' + e.message, 'error');
      }
      input.value = '';
    },

    async uploadAudio(input) {
      const file = input.files[0];
      if (!file) return;
      const status = document.getElementById('t-file-status');
      status.textContent = '⏳ Загружаю аудио…';
      const form = new FormData();
      form.append('file', file);
      try {
        const r = await fetch(`/api/${state.text.sessionId}/upload`, { method: 'POST', body: form });
        if (!r.ok) { const d = await r.json(); throw new Error(d.detail); }
        const d = await r.json();
        status.textContent = `✓ Аудио загружено: ${d.filename}`;
        _addMsg('text', `🎵 Аудио загружено: ${d.filename}\nНачинаю транскрипцию…`, 'tool');
        sendWS('text', 'расшифруй загруженное аудио и проанализируй OKR');
      } catch (e) {
        status.textContent = '⚠ ' + e.message;
        _addMsg('text', 'Ошибка загрузки аудио: ' + e.message, 'error');
      }
      input.value = '';
    },

    async analyzeSession() {
      _addMsg('text', 'Анализирую всю сессию…', 'tool');
      const usePrev = document.getElementById('t-use-prev-ctx').checked;
      try {
        const r = await fetch(`/api/${state.text.sessionId}/analyze`, {
          method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ use_previous_context: usePrev }),
        });
        const d = await r.json();
        _addMsg('text', d.result, 'agent');
        _appendContent('t-analysis-box', d.result);
        text_obj.showTab('analysis');
      } catch (e) { _addMsg('text', 'Ошибка: ' + e.message, 'error'); }
    },

    showTab(name) {
      const btns = document.querySelectorAll('#text-screen .tab-btn');
      btns.forEach((b, i) => b.classList.toggle('active', ['transcript', 'analysis'][i] === name));
      document.getElementById('t-tab-transcript').style.display = name === 'transcript' ? 'flex' : 'none';
      document.getElementById('t-tab-analysis').style.display = name === 'analysis' ? 'flex' : 'none';
    },

    async clearHistory() {
      if (!confirm('Очистить историю текстового агента?')) return;
      await fetch(`/api/${state.text.sessionId}/history`, { method: 'DELETE' }).catch(() => {});
      document.getElementById('t-transcript-box').textContent = '';
      document.getElementById('t-analysis-box').textContent = '';
      _addMsg('text', 'История очищена.', 'tool');
    },

    saveSession() {
      const transcript = document.getElementById('t-transcript-box').textContent;
      const analysis = document.getElementById('t-analysis-box').textContent;
      if (!transcript && !analysis) { alert('Нечего сохранять'); return; }
      const ts = new Date().toLocaleString('ru');
      const content = `OKR Сессия (Текстовый агент) ${ts}\n${'='.repeat(60)}\n\nСТЕНОГРАММА\n${transcript}\n\nАНАЛИЗ\n${analysis}`;
      _downloadText(content, `okr_text_${Date.now()}.txt`);
    },
  };

  // ════════════════════════════════════════════════════════════════
  // АГЕНТ 2: ТАБЛИЦЫ
  // ════════════════════════════════════════════════════════════════
  const sheets_obj = {

    sendMessage() {
      const input = document.getElementById('s-chat-input');
      const txt = input.value.trim();
      if (!txt) return;
      input.value = '';
      _addMsg('sheets', txt, 'user');
      sendWS('sheets', txt);
    },

    onChatKey(e) {
      if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); sheets_obj.sendMessage(); }
    },

    async analyzeUrl() {
      const url = document.getElementById('s-url-input').value.trim();
      const task = document.getElementById('s-url-task').value.trim() || 'Проанализируй OKR в таблице';
      if (!url) { alert('Вставьте ссылку на таблицу'); return; }
      const status = document.getElementById('s-file-status');
      status.textContent = '⏳ Загружаю…';
      _addMsg('sheets', `🌐 Загружаю таблицу:\n${url}\nЗадача: ${task}`, 'user');
      try {
        const r = await fetch(`/api/${state.sheets.sessionId}/url`, {
          method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ url, task }),
        });
        if (!r.ok) { const d = await r.json(); throw new Error(d.detail); }
        const d = await r.json();
        status.textContent = `✓ ${d.name} (${d.chars} символов)`;
        _addMsg('sheets', `✅ Загружено: ${d.name}\nОтправляю на анализ…`, 'tool');
        sendWS('sheets', task + ' (файл: ' + d.name + ')');
        document.getElementById('s-url-input').value = '';
        document.getElementById('s-url-task').value = '';
      } catch (e) {
        status.textContent = '⚠ ' + e.message;
        _addMsg('sheets', 'Ошибка загрузки: ' + e.message, 'error');
      }
    },

    async uploadFile(input) {
      const file = input.files[0];
      if (!file) return;
      const status = document.getElementById('s-file-status');
      status.textContent = '⏳ Загружаю…';
      const form = new FormData();
      form.append('file', file);
      try {
        const r = await fetch(`/api/${state.sheets.sessionId}/upload`, { method: 'POST', body: form });
        if (!r.ok) { const d = await r.json(); throw new Error(d.detail); }
        const d = await r.json();
        status.textContent = `✓ ${d.filename} (${d.chars} символов)`;
        _addMsg('sheets', `📊 Таблица загружена: ${d.filename}\nАгент готов к анализу.`, 'tool');
      } catch (e) {
        status.textContent = '⚠ ' + e.message;
        _addMsg('sheets', 'Ошибка загрузки: ' + e.message, 'error');
      }
      input.value = '';
    },

    connectGoogle() {
      const sid = state.sheets.sessionId;
      if (!sid) { _addMsg('sheets', 'Сначала дождитесь инициализации сессии.', 'error'); return; }
      window.open(`/auth/google?session_id=${sid}`, '_blank');
      _addMsg('sheets', 'Откроется окно Google. После входа вернитесь сюда — статус обновится автоматически.', 'tool');
      const check = setInterval(async () => {
        const r = await fetch(`/api/${sid}/google/status`).catch(() => null);
        if (r && r.ok) {
          const d = await r.json();
          if (d.connected) {
            clearInterval(check);
            sheets_obj._checkGoogleStatus();
            _addMsg('sheets', '✅ Google Sheets подключён! Теперь после анализа нажмите «Внести изменения».', 'agent');
          }
        }
      }, 2000);
      setTimeout(() => clearInterval(check), 180000);
    },

    async _checkGoogleStatus() {
      const sid = state.sheets.sessionId;
      if (!sid) return;
      try {
        const r = await fetch(`/api/${sid}/google/status`);
        const d = await r.json();
        const btn = document.getElementById('s-google-connect-btn');
        const status = document.getElementById('s-google-status');
        if (d.connected) {
          btn.textContent = '✅ Google Sheets подключён';
          btn.style.background = 'linear-gradient(to bottom,#16a34a,#15803d)';
          btn.onclick = null;
          status.textContent = 'Можно вносить изменения в таблицы';
        } else {
          status.textContent = 'Не подключён — нажмите кнопку выше';
        }
      } catch (_) {}
    },

    async applyToSheet() {
      try {
        const r = await fetch(`/api/${state.sheets.sessionId}/google/status`);
        const d = await r.json();
        if (!d.connected) {
          _addMsg('sheets', 'Сначала нажмите «🔗 Подключить Google Sheets» и войдите в Google аккаунт.', 'question');
          return;
        }
      } catch (_) {}
      _addMsg('sheets', 'Вношу улучшенные формулировки OKR в таблицу…', 'user');
      sendWS('sheets', 'внеси изменения в таблицу');
    },

    showTab(name) {
      document.querySelectorAll('#sheets-screen .tab-btn').forEach(b =>
        b.classList.toggle('active', b.textContent.includes('Анализ')));
      document.getElementById('s-tab-analysis').style.display = 'flex';
    },

    async clearHistory() {
      if (!confirm('Очистить историю агента таблиц?')) return;
      await fetch(`/api/${state.sheets.sessionId}/history`, { method: 'DELETE' }).catch(() => {});
      document.getElementById('s-analysis-box').textContent = '';
      _addMsg('sheets', 'История очищена.', 'tool');
    },

    saveSession() {
      const analysis = document.getElementById('s-analysis-box').textContent;
      if (!analysis) { alert('Нечего сохранять'); return; }
      const ts = new Date().toLocaleString('ru');
      const content = `OKR Сессия (Агент таблиц) ${ts}\n${'='.repeat(60)}\n\n${analysis}`;
      _downloadText(content, `okr_sheets_${Date.now()}.txt`);
    },
  };

  // ════════════════════════════════════════════════════════════════
  // АГЕНТ 3: ГОЛОСОВОЙ
  // ════════════════════════════════════════════════════════════════
  const voice = {
    _isRecording: false,
    _speechRec: null,
    _recInterval: null,
    _recSeconds: 0,
    _valCount: 0,

    toggleRecording() {
      if (voice._isRecording) voice._stop(); else voice._start();
    },

    _start() {
      const SR = window.SpeechRecognition || window.webkitSpeechRecognition;
      if (!SR) {
        alert('Ваш браузер не поддерживает голосовой ввод. Используйте Chrome или Edge.');
        return;
      }
      voice._isRecording = true;
      voice._recSeconds = 0;
      const btn = document.getElementById('voice-rec-btn');
      btn.textContent = '⏹ Остановить сессию';
      btn.classList.add('recording');
      document.getElementById('voice-timer').style.display = 'inline';
      document.getElementById('voice-status').textContent = '🎙 Говорите — каждая фраза будет проверена…';
      voice._recInterval = setInterval(() => {
        voice._recSeconds++;
        const m = String(Math.floor(voice._recSeconds / 60)).padStart(2, '0');
        const s = String(voice._recSeconds % 60).padStart(2, '0');
        document.getElementById('voice-timer').textContent = `${m}:${s}`;
      }, 1000);

      voice._speechRec = new SR();
      voice._speechRec.lang = 'ru-RU';
      voice._speechRec.continuous = true;
      voice._speechRec.interimResults = false;
      voice._speechRec.onresult = async (e) => {
        for (let i = e.resultIndex; i < e.results.length; i++) {
          if (e.results[i].isFinal) {
            const txt = e.results[i][0].transcript.trim();
            if (!txt) continue;
            const ts = new Date().toLocaleTimeString('ru');
            voice._appendPhrase(txt, ts);
            // Save to voice session history if session exists
            const sid = state.voice.sessionId;
            if (sid) fetch(`/api/${sid}/transcript`, {
              method: 'POST', headers: { 'Content-Type': 'application/json' },
              body: JSON.stringify({ text: txt }),
            }).catch(() => {});
            voice._validateCard(txt, ts);
          }
        }
      };
      voice._speechRec.onerror = (e) => {
        if (e.error === 'not-allowed') {
          alert('Нет доступа к микрофону. Разрешите доступ в браузере.');
          voice._stop();
        } else if (e.error !== 'no-speech') {
          document.getElementById('voice-status').textContent = `⚠ ${e.error}`;
        }
      };
      voice._speechRec.onend = () => { if (voice._isRecording) voice._speechRec.start(); };
      voice._speechRec.start();
    },

    _stop() {
      voice._isRecording = false;
      if (voice._speechRec) { voice._speechRec.onend = null; voice._speechRec.stop(); }
      clearInterval(voice._recInterval);
      const btn = document.getElementById('voice-rec-btn');
      btn.textContent = '🎙 Начать сессию';
      btn.classList.remove('recording');
      document.getElementById('voice-timer').style.display = 'none';
      document.getElementById('voice-status').textContent = '⏹ Запись остановлена';
    },

    _appendPhrase(txt, ts) {
      const box = document.getElementById('voice-transcript');
      const p = document.createElement('div');
      p.className = 'voice-phrase';
      p.innerHTML = `<span class="v-ts">${ts}</span>${_escapeHtml(txt)}`;
      box.appendChild(p);
      box.scrollTop = box.scrollHeight;
    },

    async _validateCard(txt, ts) {
      const results = document.getElementById('voice-results');
      const empty = results.querySelector('.voice-results-empty');
      if (empty) empty.remove();

      const card = document.createElement('div');
      card.className = 'val-card validating';
      card.innerHTML = `
        <div class="val-meta">
          <span class="val-time">${ts}</span>
          <span class="val-badge other">⏳ Проверяю…</span>
        </div>
        <div class="val-phrase">${_escapeHtml(txt)}</div>`;
      results.insertBefore(card, results.firstChild);

      // Use voice session if available, else text session
      const sid = state.voice.sessionId || state.text.sessionId;
      try {
        const r = await fetch(`/api/${sid}/validate`, {
          method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ text: txt }),
        });
        const d = await r.json();
        voice._fillCard(card, txt, ts, d.result || '');
      } catch (e) {
        card.className = 'val-card';
        card.querySelector('.val-badge').textContent = '⚠ Ошибка';
        const res = document.createElement('div');
        res.className = 'val-result';
        res.textContent = e.message;
        card.appendChild(res);
      }

      voice._valCount++;
      document.getElementById('voice-val-count').textContent = `${voice._valCount} проверено`;
    },

    _fillCard(card, txt, ts, result) {
      const lower = result.toLowerCase();
      let badgeClass = 'other', badgeText = 'Не OKR';
      if (lower.includes('objective') || (lower.includes('тип:') && lower.includes('цель'))) { badgeClass = 'objective'; badgeText = 'OBJECTIVE'; }
      if (lower.includes('key result') || (lower.includes('тип:') && lower.includes('key'))) { badgeClass = 'kr'; badgeText = 'KEY RESULT'; }
      if (lower.includes('инициатива') || lower.includes('задача')) { badgeClass = 'initiative'; badgeText = 'ИНИЦИАТИВА'; }

      const scoreMatch = result.match(/(\d+)\/10/);
      let scoreClass = 'ok', scoreText = '';
      if (scoreMatch) {
        const s = parseInt(scoreMatch[1]);
        scoreText = `${s}/10`;
        scoreClass = s >= 7 ? 'good' : s >= 4 ? 'ok' : 'bad';
      }

      card.className = 'val-card';
      card.innerHTML = `
        <div class="val-meta">
          <span class="val-time">${ts}</span>
          <span class="val-badge ${badgeClass}">${badgeText}</span>
          ${scoreText ? `<span class="val-score ${scoreClass}">${scoreText}</span>` : ''}
        </div>
        <div class="val-phrase">${_escapeHtml(txt)}</div>
        <div class="val-result">${_escapeHtml(result)}</div>`;
    },

    _onTranscriptSelect() {
      const sel = window.getSelection();
      const btn = document.getElementById('validate-sel-btn');
      btn.style.display = sel && sel.toString().trim().length > 3 ? 'inline-block' : 'none';
    },

    async validateSelection() {
      const sel = window.getSelection();
      if (!sel || !sel.toString().trim()) { alert('Выделите текст в стенограмме для проверки'); return; }
      const txt = sel.toString().trim();
      sel.removeAllRanges();
      document.getElementById('validate-sel-btn').style.display = 'none';
      const ts = new Date().toLocaleTimeString('ru') + ' (выделено)';
      await voice._validateCard(txt, ts);
    },

    clearSession() {
      if (!confirm('Очистить стенограмму и результаты голосовой сессии?')) return;
      voice._clearState();
    },

    _clearState() {
      document.getElementById('voice-transcript').innerHTML = '';
      document.getElementById('voice-results').innerHTML =
        '<div class="voice-results-empty">Результаты появятся здесь.<br>Каждая фраза проверяется автоматически.</div>';
      voice._valCount = 0;
      document.getElementById('voice-val-count').textContent = '';
    },
  };

  // ── Utilities ─────────────────────────────────────────────────────
  function _downloadText(content, filename) {
    const blob = new Blob([content], { type: 'text/plain;charset=utf-8' });
    const a = document.createElement('a');
    a.href = URL.createObjectURL(blob);
    a.download = filename;
    a.click();
  }

  // ── Boot ──────────────────────────────────────────────────────────
  document.addEventListener('DOMContentLoaded', init);

  return {
    switchAgent,
    switchProvider,
    text:   text_obj,
    sheets: sheets_obj,
    voice:  voice,
  };
})();
