"""
Запуск OKR Agent с публичным тоннелем.
Запустите: python start_web.py
"""
import os
import sys
import time
import threading
import subprocess
from pathlib import Path

ROOT = Path(__file__).parent
os.chdir(ROOT)

# Загрузить .env
env_file = ROOT / ".env"
if env_file.exists():
    for line in env_file.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip())

PORT = 8000

def start_server():
    uvicorn = ROOT / ".venv" / "Scripts" / "uvicorn.exe"
    subprocess.Popen(
        [str(uvicorn), "web.server:app", "--host", "127.0.0.1", "--port", str(PORT)],
        cwd=str(ROOT),
    )

def wait_for_server():
    import urllib.request
    for _ in range(30):
        try:
            urllib.request.urlopen(f"http://127.0.0.1:{PORT}", timeout=1)
            return True
        except Exception:
            time.sleep(1)
    return False

def get_tunnel_url():
    # Вариант 1: pyngrok (устанавливается автоматически)
    try:
        from pyngrok import ngrok, conf
        tunnel = ngrok.connect(PORT, "http")
        return tunnel.public_url.replace("http://", "https://")
    except ImportError:
        pass

    # Вариант 2: SSH через serveo.net
    try:
        import socket
        sock = socket.create_connection(("serveo.net", 22), timeout=5)
        sock.close()

        result = subprocess.Popen(
            ["ssh", "-o", "StrictHostKeyChecking=no",
             "-R", f"80:localhost:{PORT}", "serveo.net"],
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, creationflags=subprocess.CREATE_NO_WINDOW
        )
        for line in result.stdout:
            if "https://" in line or "http://" in line:
                for word in line.split():
                    if word.startswith("http"):
                        return word.strip()
    except Exception:
        pass

    return None

if __name__ == "__main__":
    print("🚀 Запускаю OKR Agent Web Server...")

    # Установить pyngrok если нет
    try:
        import pyngrok
    except ImportError:
        print("📦 Устанавливаю pyngrok...")
        subprocess.check_call(
            [sys.executable, "-m", "pip", "install", "pyngrok", "-q"]
        )

    print(f"⚙️  Запускаю сервер на порту {PORT}...")
    start_server()

    if not wait_for_server():
        print("❌ Сервер не запустился. Проверьте .env файл.")
        sys.exit(1)

    print("✅ Сервер запущен")
    print("🌐 Создаю публичный тоннель...")

    url = get_tunnel_url()

    if url:
        print("\n" + "="*60)
        print(f"🔗 ССЫЛКА ДЛЯ КОЛЛЕГ:")
        print(f"   {url}")
        print("="*60)
        print("\nСсылка работает пока это окно открыто.")
        print("Нажмите Ctrl+C чтобы остановить.\n")
        try:
            while True:
                time.sleep(60)
        except KeyboardInterrupt:
            print("\nОстановлено.")
    else:
        print("\n⚠️  Не удалось создать публичный тоннель автоматически.")
        print(f"\nДля коллег в вашей сети:")
        import socket
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.connect(("8.8.8.8", 80))
            local_ip = s.getsockname()[0]
            s.close()
        except Exception:
            local_ip = "ВАШ_IP"
        print(f"   http://{local_ip}:{PORT}")
        print(f"\nДля интернета — см. web/README.md (Railway)")
        try:
            while True:
                time.sleep(60)
        except KeyboardInterrupt:
            pass
