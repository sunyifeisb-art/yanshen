"""macOS desktop launcher for 研申 — pywebview (WKWebView) host.

Replaces the Windows C# WebView2 host (launcher.py + desktop_host/Program.cs)
with pywebview's native WKWebView window on macOS.  Startup flow mirrors the
Windows launcher:

  1. configure logging and prepare the user data directory
  2. show a lightweight in-window loading page immediately
  3. start the local HTTP service in a background thread
  4. wait for /health, then navigate the window to the workbench

The frontend talks to the desktop host via `window.chrome.webview.postMessage`
(see static/js/core.js sendDesktopDisplayCommand).  pywebview does not provide
that object, so once the app page loads we install a small shim that routes the
same commands into the Python js_api, mapping `zoom:` onto CSS zoom and
`display:` onto native fullscreen.  In a plain browser the app already falls
back to CSS zoom, so this bridge is purely additive.
"""

import logging
import re
import threading
import time
import urllib.request
from pathlib import Path
from urllib.parse import unquote

import webview

from gongkao.paths import log_path, user_data_dir

APP_NAME = "研申"
START_PATH = "/home"
HEALTH_PATH = "/health"
SERVER_READY_TIMEOUT_SECONDS = 90
SERVER_PORT_FILE = "server-port.txt"
WINDOW_WIDTH = 1220
WINDOW_HEIGHT = 820
WINDOW_MIN_WIDTH = 900
WINDOW_MIN_HEIGHT = 620
BACKGROUND_COLOR = "#f7faf8"

# Installed on every page load (idempotent) so the app's
# `window.chrome.webview.postMessage` calls reach the pywebview js_api.
HOST_BRIDGE_SHIM = """(() => {
  if (window.chrome && window.chrome.webview && window.chrome.webview.postMessage) return;
  const postMessage = (command) => {
    if (window.pywebview && window.pywebview.api && window.pywebview.api.handle_command) {
      window.pywebview.api.handle_command(String(command));
    }
  };
  if (!window.chrome) window.chrome = {};
  if (!window.chrome.webview) window.chrome.webview = {};
  window.chrome.webview.postMessage = postMessage;
})();
"""

LOADING_HTML = """<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<style>
  html, body { height: 100%; margin: 0; }
  body { background: #f7faf8; display: flex; align-items: center; justify-content: center;
         font-family: -apple-system, "PingFang SC", "Helvetica Neue", sans-serif; }
  .card { width: 480px; background: #fff; border: 1px solid #dce8e3; border-radius: 12px;
          padding: 36px 40px 32px; box-shadow: 0 12px 32px rgba(32, 38, 34, 0.06); }
  .accent { width: 7px; height: 7px; border-radius: 50%; background: #35695c; margin-bottom: 18px; }
  h1 { font-size: 26px; font-weight: 700; margin: 0 0 6px; color: #202622; }
  p { margin: 0 0 24px; color: #6a746f; font-size: 14px; }
  .bar { height: 8px; background: #dbeae4; border-radius: 4px; overflow: hidden; }
  .fill { height: 100%; width: 12%; background: #35695c; border-radius: 4px;
          transition: width .4s ease; }
  #status { margin-top: 12px; font-size: 12px; color: #33433d; }
</style>
</head>
<body>
  <div class="card">
    <div class="accent"></div>
    <h1>研申</h1>
    <p>把资料留在本地，把时间用在作答上。</p>
    <div class="bar"><div class="fill" id="fill"></div></div>
    <p id="status">正在准备本地数据…</p>
  </div>
  <script>
    window.__yanshenSetStatus = function (text, pct) {
      var s = document.getElementById('status');
      if (s && text) s.textContent = text;
      var f = document.getElementById('fill');
      if (f && typeof pct === 'number') f.style.width = pct + '%';
    };
  </script>
</body>
</html>
"""


class HostBridge:
    """Receives desktop-window commands forwarded by the JS shim."""

    def __init__(self, window=None):
        self.window = window

    def handle_command(self, command):
        command = str(command or "")
        try:
            if command.startswith("zoom:"):
                try:
                    zoom = float(command[len("zoom:"):])
                except ValueError:
                    zoom = 1.0
                zoom = max(0.5, min(2.0, zoom))
                self.window.evaluate_js(f"document.documentElement.style.zoom = {zoom:.2f};")
                logging.info("Interface zoom changed to %.2f", zoom)
            elif command == "display:fullscreen":
                self.window.set_fullscreen(True)
                logging.info("Display profile changed to fullscreen")
            elif command == "display:window":
                self.window.set_fullscreen(False)
                logging.info("Display profile changed to window")
        except Exception:
            logging.exception("Desktop host command failed: %s", command)

    def download_file(self, url):
        """Fetch a local server URL (e.g. /attempts/1/package.zip) and save it to
        ~/Downloads. WKWebView does not trigger downloads from anchor links, so
        the JS layer forwards the click here and we save the file natively."""
        try:
            url = str(url or "").strip()
            if not url.startswith("/"):
                url = "/" + url
            with urllib.request.urlopen(f"http://127.0.0.1:{self._local_server_port()}{url}", timeout=120) as response:
                data = response.read()
                disposition = response.headers.get("Content-Disposition") or ""
            filename = self._filename_from_disposition(disposition) or "研申批改包.zip"
            stem, dot, ext = filename.rpartition(".")
            filename = f"{stem}-{time.strftime('%Y%m%d-%H%M%S')}{dot if dot else '.zip'}{ext if dot else ''}"
            downloads = Path.home() / "Downloads"
            try:
                downloads.mkdir(parents=True, exist_ok=True)
            except OSError:
                downloads = Path.home()
            target = downloads / filename
            target.write_bytes(data)
            return {"ok": True, "path": str(target)}
        except Exception as exc:
            logging.exception("Host download failed for %s", url)
            return {"ok": False, "error": str(exc)}

    @staticmethod
    def _filename_from_disposition(disposition):
        match = re.search(r"filename\*=UTF-8''([^;]+)", disposition, re.IGNORECASE)
        if match:
            return unquote(match.group(1))
        match = re.search(r'filename="?([^";]+)"?', disposition, re.IGNORECASE)
        return match.group(1) if match else ""

    @staticmethod
    def _local_server_port():
        try:
            port = int((user_data_dir() / SERVER_PORT_FILE).read_text(encoding="utf-8").strip())
            if 1024 <= port <= 65535:
                return port
        except Exception:
            pass
        return 5000


class Launcher:
    def __init__(self):
        self.server = None
        self.server_thread = None
        self.url = ""
        self.start_url = ""
        self.window = None
        self.bridge = None

    def configure_logging(self):
        user_data_dir().mkdir(parents=True, exist_ok=True)
        logging.basicConfig(
            filename=log_path(),
            level=logging.INFO,
            format="%(asctime)s %(levelname)s %(message)s",
            encoding="utf-8",
            force=True,
        )

    def preferred_server_port(self):
        try:
            port = int((user_data_dir() / SERVER_PORT_FILE).read_text(encoding="utf-8").strip())
            return port if 1024 <= port <= 65535 else 0
        except (OSError, ValueError):
            return 0

    def remember_server_port(self, port):
        try:
            (user_data_dir() / SERVER_PORT_FILE).write_text(str(port), encoding="utf-8")
        except OSError:
            logging.warning("Could not persist local server port %s", port)

    def start_server(self):
        # Lazy import: gongkao.web.application pulls in langgraph / langchain
        # and friends, which is the slow part of a frozen startup.  Importing
        # here (after the loading window is already on screen) keeps the app
        # window responsive while those modules load in the background.
        from gongkao.web.application import create_server

        preferred_port = self.preferred_server_port()
        try:
            self.server = create_server(port=preferred_port)
        except OSError:
            if not preferred_port:
                raise
            logging.warning("Preferred local port %s is unavailable; choosing a new port", preferred_port)
            self.server = create_server(port=0)
        self.remember_server_port(self.server.server_address[1])
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"
        self.start_url = f"{self.url}{START_PATH}"
        self.server_thread = threading.Thread(
            target=self.server.serve_forever,
            name="gongkao-local-server",
            daemon=True,
        )
        self.server_thread.start()
        self.wait_for_server_ready()
        logging.info("Gongkao Shenlun local service started at %s", self.url)

    def wait_for_server_ready(self):
        deadline = time.monotonic() + SERVER_READY_TIMEOUT_SECONDS
        last_error = None
        health_url = f"{self.url}{HEALTH_PATH}"
        while time.monotonic() < deadline:
            try:
                with urllib.request.urlopen(health_url, timeout=2.0) as response:
                    if 200 <= response.status < 500:
                        return
            except OSError as exc:
                last_error = exc
            time.sleep(0.2)
        raise RuntimeError(f"本地服务未能在规定时间内启动：{last_error}")

    def set_loading_status(self, text, pct):
        if self.window is None:
            return
        try:
            self.window.evaluate_js(
                "window.__yanshenSetStatus && window.__yanshenSetStatus("
                f"{text!r}, {pct!r});"
            )
        except Exception:
            pass

    def _install_host_bridge(self):
        try:
            self.window.evaluate_js(HOST_BRIDGE_SHIM)
        except Exception:
            logging.exception("Could not install host bridge shim")

    def _run(self):
        try:
            self.set_loading_status("正在初始化应用环境…", 15)
            self.set_loading_status("正在载入本地题库与模型，首次可能稍久…", 30)
            self.start_server()
            self.set_loading_status("本地服务已就绪，正在打开研申…", 95)
            time.sleep(0.4)
            self.window.load_url(self.start_url)
        except Exception as exc:
            logging.exception("Failed to start")
            self.show_error(exc)
            # Never destroy the window from the GUI thread while the event
            # loop is mid-event; schedule the close on a worker thread.
            threading.Thread(
                target=self.window.destroy,
                name="gongkao-error-close",
                daemon=True,
            ).start()

    def start(self):
        self.bridge = HostBridge()
        self.window = webview.create_window(
            APP_NAME,
            html=LOADING_HTML,
            js_api=self.bridge,
            width=WINDOW_WIDTH,
            height=WINDOW_HEIGHT,
            min_size=(WINDOW_MIN_WIDTH, WINDOW_MIN_HEIGHT),
            background_color=BACKGROUND_COLOR,
        )
        self.bridge.window = self.window
        self.window.events.loaded += self._install_host_bridge
        try:
            self.configure_logging()
            webview.start(self._run, debug=False)
        except Exception as exc:
            logging.exception("Failed to start")
            self.show_error(exc)
        finally:
            self.stop()

    def show_error(self, exc):
        try:
            import AppKit

            alert = AppKit.NSAlert.alloc().init()
            alert.setMessageText_("研申无法启动")
            alert.setInformativeText_(f"{exc}\n\n日志：{log_path()}")
            alert.runModal()
        except Exception:
            pass

    def stop(self):
        if self.server:
            try:
                self.server.shutdown()
                self.server.server_close()
            except Exception:
                pass
            self.server = None
        if self.server_thread and self.server_thread.is_alive():
            self.server_thread.join(timeout=5)
        self.server_thread = None


if __name__ == "__main__":
    Launcher().start()
