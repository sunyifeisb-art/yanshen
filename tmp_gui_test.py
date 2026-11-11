"""Throwaway GUI smoke test for the macOS launcher.

Opens a pywebview window, starts the local service, navigates to the
workbench, then a background thread closes the window after a few seconds.
Progress is logged to the user data dir log so a killed process can still
tell us how far it got.
"""

import os
import threading
import time

import webview

import launcher_mac

os.environ.setdefault("GONGKAO_DATA_DIR", "/tmp/yanshen-guitest")


def log(msg):
    print(msg, flush=True)


launcher = launcher_mac.Launcher()
launcher.configure_logging()


def _run():
    try:
        launcher.set_loading_status("正在初始化应用环境…", 15)
        launcher.start_server()
        log("SERVER_READY " + launcher.start_url)
        launcher.set_loading_status("服务就绪，载入主页", 90)
        launcher.window.load_url(launcher.start_url)
        log("NAVIGATED")
    except Exception as exc:
        log("RUN_ERROR %r" % (exc,))


def _closer():
    time.sleep(8)
    log("CLOSER_FIRING")
    try:
        launcher.window.destroy()
        log("DESTROY_CALLED")
    except Exception as exc:
        log("DESTROY_ERROR %r" % (exc,))


launcher.bridge = launcher_mac.HostBridge()
launcher.window = webview.create_window(
    "研申测试",
    html=launcher_mac.LOADING_HTML,
    js_api=launcher.bridge,
    width=1000,
    height=700,
    background_color=launcher_mac.BACKGROUND_COLOR,
)
launcher.bridge.window = launcher.window
launcher.window.events.loaded += launcher._install_host_bridge

threading.Thread(target=_closer, daemon=True).start()
log("STARTING_WEBVIEW")
webview.start(_run, debug=False)
log("WEBVIEW_RETURNED")
launcher.stop()
log("TEST_LAUNCHER_OK")
