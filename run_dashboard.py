"""
Streamlit 대시보드 런처 (IDE에서 ▶(세모)로 실행하기용)

사용:
  1) IDE에서 이 파일(`run_dashboard.py`)을 실행(▶)
  2) 브라우저가 자동으로 열리며 대시보드가 뜹니다.

터미널에서도 가능:
  .\.venv\Scripts\python.exe run_dashboard.py
"""

from __future__ import annotations

import os
import socket
import subprocess
import sys
import time
import webbrowser
from pathlib import Path


def _pick_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def _wait_for_server(host: str, port: int, timeout_s: float = 30.0) -> bool:
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        try:
            with socket.create_connection((host, port), timeout=0.5):
                return True
        except OSError:
            time.sleep(0.2)
    return False


def main() -> int:
    dashboard_path = Path(__file__).with_name("dashboard.py")
    if not dashboard_path.exists():
        print(f"dashboard.py not found: {dashboard_path}")
        return 1

    port = _pick_free_port()
    url = f"http://localhost:{port}"

    # Streamlit이 처음 실행될 때 "Email:" 프롬프트를 띄우는 경우가 있어,
    # 통계 수집을 끄고(stdin 차단) 자동 실행되게 합니다.
    env = os.environ.copy()
    env.setdefault("STREAMLIT_BROWSER_GATHER_USAGE_STATS", "false")
    env.setdefault("STREAMLIT_SUPPRESS_EMAIL_PROMPT", "true")

    cmd = [
        sys.executable,
        "-m",
        "streamlit",
        "run",
        str(dashboard_path),
        "--server.port",
        str(port),
        "--server.headless",
        "true",
        "--server.showEmailPrompt",
        "false",
        "--browser.gatherUsageStats",
        "false",
    ]

    proc = subprocess.Popen(cmd, stdin=subprocess.DEVNULL, env=env)

    host = "127.0.0.1"
    if _wait_for_server(host, port, timeout_s=30.0):
        webbrowser.open(url)
    else:
        code = proc.poll()
        if code is not None:
            print(f"Streamlit exited early (code={code}). 콘솔 로그를 확인하세요.")
            return code
        print("서버가 30초 내에 준비되지 않았습니다. 잠시 후 새로고침 해보세요.")

    print(f"Dashboard: {url}")
    print("종료: 이 창에서 Ctrl+C")

    try:
        return proc.wait()
    except KeyboardInterrupt:
        proc.terminate()
        try:
            return proc.wait(timeout=5)
        except Exception:
            proc.kill()
            return proc.wait()


if __name__ == "__main__":
    raise SystemExit(main())
