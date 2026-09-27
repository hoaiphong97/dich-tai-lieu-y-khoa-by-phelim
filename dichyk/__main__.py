"""Khởi động app: chạy server nội bộ rồi mở cửa sổ app (pywebview).

    python -m dichyk            # mở cửa sổ app
    python -m dichyk --browser  # mở bằng trình duyệt thay cho cửa sổ app
"""

from __future__ import annotations

import argparse
import socket
import sys
import threading
import time
import urllib.request
import webbrowser

from . import paths


def _free_port(preferred: int = 8765) -> int:
    for port in (preferred, 0):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            try:
                sock.bind(("127.0.0.1", port))
                return sock.getsockname()[1]
            except OSError:
                continue
    raise RuntimeError("Không tìm được cổng trống")


def _ensure_streams() -> None:
    """App đóng gói không có console: sys.stdout/stderr là None, ghi log ra file thay thế."""
    if sys.stdout is None or sys.stderr is None:
        log = open(paths.data_dir() / "app.log", "a", encoding="utf-8", buffering=1)
        sys.stdout = sys.stdout or log
        sys.stderr = sys.stderr or log


def _start_server(port: int) -> None:
    import uvicorn

    from .server import app

    config = uvicorn.Config(
        app, host="127.0.0.1", port=port, log_level="warning", access_log=False, log_config=None,
    )
    server = uvicorn.Server(config)
    threading.Thread(target=server.run, daemon=True, name="server").start()
    url = f"http://127.0.0.1:{port}/api/health"
    for _ in range(100):
        try:
            with urllib.request.urlopen(url, timeout=1):
                return
        except OSError:
            time.sleep(0.1)
    raise RuntimeError("Server không khởi động được")


class DesktopApi:
    """Hàm gọi được từ giao diện khi chạy trong cửa sổ app."""

    def __init__(self):
        self.window = None

    def pick_pdf(self):
        import webview

        dialog = getattr(webview, "FileDialog", None)
        kind = dialog.OPEN if dialog else webview.OPEN_DIALOG
        result = self.window.create_file_dialog(kind, allow_multiple=False, file_types=("PDF (*.pdf)",))
        return result[0] if result else None


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="dichyk", description=paths.APP_TITLE)
    parser.add_argument("--browser", action="store_true", help="mở bằng trình duyệt")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--server-only", action="store_true", help="chỉ chạy server (để phát triển)")
    args = parser.parse_args(argv)

    _ensure_streams()
    port = _free_port(args.port)
    _start_server(port)
    url = f"http://127.0.0.1:{port}/"

    if args.server_only:
        print(f"{paths.APP_TITLE} đang chạy tại {url}")
        try:
            while True:
                time.sleep(3600)
        except KeyboardInterrupt:
            return 0

    if not args.browser:
        try:
            import webview

            api = DesktopApi()
            window = webview.create_window(
                paths.APP_TITLE, url, js_api=api, width=1280, height=840, min_size=(980, 640)
            )
            api.window = window
            webview.start()
            return 0
        except Exception as exc:  # không có pywebview / WebView2: dùng trình duyệt
            print(f"Không mở được cửa sổ app ({exc}); chuyển sang trình duyệt.", file=sys.stderr)

    webbrowser.open(url)
    print(f"{paths.APP_TITLE} đang chạy tại {url} — đóng cửa sổ này để thoát.")
    try:
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        return 0


if __name__ == "__main__":
    sys.exit(main())
