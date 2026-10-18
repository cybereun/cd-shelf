"""CD Shelf 데스크톱 런처: 내장 서버를 띄우고 전용 창으로 연다."""
import os
import socket
import sys
import threading
import time
import urllib.request
import webbrowser

if getattr(sys, "frozen", False):
    _base = getattr(sys, "_MEIPASS", os.path.dirname(sys.executable))
    os.environ["PATH"] = _base + os.pathsep + os.environ.get("PATH", "")
    if hasattr(os, "add_dll_directory"):
        os.add_dll_directory(_base)

if os.name == "nt":
    try:  # 작업 표시줄에서 파이썬이 아닌 이 앱의 아이콘으로 묶이게 함
        import ctypes

        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("CDShelf.App")
    except Exception:
        pass

import app as cdapp  # noqa: E402

PREFERRED_PORT = 5000


def _alive(port):
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/ping", timeout=1) as r:
            return r.read().strip() == b"cdshelf"
    except Exception:
        return False


def _pick_port():
    for port in (PREFERRED_PORT, 0):
        s = socket.socket()
        try:
            s.bind(("0.0.0.0", port))
            chosen = s.getsockname()[1]
            s.close()
            return chosen
        except OSError:
            s.close()
    raise RuntimeError("사용 가능한 포트가 없습니다.")


def main():
    if _alive(PREFERRED_PORT):  # 이미 실행 중이면 새 서버를 만들지 않고 브라우저로 연다
        webbrowser.open(f"http://127.0.0.1:{PREFERRED_PORT}/")
        return
    cdapp.init_db()
    port = _pick_port()
    cdapp.PORT = port
    threading.Thread(
        target=lambda: cdapp.app.run(host="0.0.0.0", port=port, threaded=True, use_reloader=False),
        daemon=True,
    ).start()
    for _ in range(50):
        if _alive(port):
            break
        time.sleep(0.1)
    url = f"http://127.0.0.1:{port}/"
    try:
        import webview

        webview.create_window("CD 보관함", url, width=1200, height=820, min_size=(480, 600))
        webview.start()
    except Exception:  # WebView2가 없거나 창 생성 실패 시 기본 브라우저로 대체
        webbrowser.open(url)
        while True:
            time.sleep(3600)


if __name__ == "__main__":
    main()
