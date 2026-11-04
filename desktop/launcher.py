"""The Stop Press desktop app. Runs the server in a background thread, puts an icon in the tray (menu bar on a Mac),
and opens the settings page the first time. Settings and the day's data live in one per-user folder, so the app can
be updated or moved without losing them. Run from source with `python desktop/launcher.py` (needs the server's
requirements and desktop/requirements.txt), or as the built StopPress app (see desktop/stop-press.spec)."""
import ctypes
import json
import os
import plistlib
import shlex
import shutil
import socket
import subprocess
import sys
import threading
import time
import urllib.request
import webbrowser
from pathlib import Path

PORTS = range(8000, 8020)  # the first free one is used; 8000 unless something else has it
LABEL = "org.stop-press.app"
ES_CONTINUOUS, ES_SYSTEM_REQUIRED = 0x80000000, 0x00000001


def data_dir() -> Path:
    """Where settings, the archive and the reading state live. Kept out of the app's own folder."""
    if sys.platform == "win32":
        return Path(os.environ.get("APPDATA") or Path.home() / "AppData" / "Roaming") / "StopPress"
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "StopPress"
    return Path.home() / ".local" / "share" / "stop-press"


DATA = data_dir()
DATA.mkdir(parents=True, exist_ok=True)
# The server reads these when paper.config is imported, so they are set before anything from paper is imported.
os.environ.setdefault("STOP_PRESS_CONFIG", str(DATA / "stop-press.toml"))
os.environ.setdefault("PAPER_DATA_DIR", str(DATA / "data"))
if getattr(sys, "frozen", False):
    if sys.stdout is None or sys.stderr is None:  # a windowed build has no console: keep the log in the data folder
        sys.stdout = sys.stderr = open(DATA / "stop-press.log", "a", encoding="utf-8", buffering=1)
    sys.path.insert(0, sys._MEIPASS)  # the bundled `paper` package
else:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "server"))

SETTINGS_FILE = DATA / "launcher.json"
RUNNING_FILE = DATA / "running.json"


def load_state() -> dict:
    try:
        return json.loads(SETTINGS_FILE.read_text("utf-8"))
    except (OSError, ValueError):
        return {}


def save_state(state: dict) -> None:
    SETTINGS_FILE.write_text(json.dumps(state, indent=2), "utf-8")


def free_port() -> int:
    for port in PORTS:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            try:
                s.bind(("0.0.0.0", port))
                return port
            except OSError:
                continue
    raise RuntimeError(f"No free port between {PORTS[0]} and {PORTS[-1]}.")


def healthy(port: int) -> bool:
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/healthz", timeout=2) as r:
            return json.loads(r.read()).get("ok") is True
    except Exception:
        return False


def command() -> list[str]:
    """The command that starts this app, for the start-with-computer entry."""
    if getattr(sys, "frozen", False):
        return [sys.executable]
    exe = sys.executable
    if sys.platform == "win32" and Path(exe).name.lower() == "python.exe":
        exe = str(Path(exe).with_name("pythonw.exe"))  # no console window at log-in
    return [exe, str(Path(__file__).resolve())]


# Start with my computer. The entry is a file, so the tick in the menu is simply whether the file exists.

def autostart_path() -> Path:
    if sys.platform == "win32":
        return Path(os.environ["APPDATA"]) / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Startup" / "Stop Press.cmd"
    if sys.platform == "darwin":
        return Path.home() / "Library" / "LaunchAgents" / f"{LABEL}.plist"
    return Path.home() / ".config" / "autostart" / "stop-press.desktop"


def set_autostart(on: bool) -> None:
    path = autostart_path()
    if not on:
        path.unlink(missing_ok=True)
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    cmd = command()
    if sys.platform == "win32":
        quoted = " ".join(f'"{c}"' for c in cmd)
        path.write_text(f'@echo off\r\nstart "" {quoted}\r\n', "utf-8")
    elif sys.platform == "darwin":
        path.write_bytes(plistlib.dumps({"Label": LABEL, "ProgramArguments": cmd, "RunAtLoad": True}))
    else:
        path.write_text("[Desktop Entry]\nType=Application\nName=Stop Press\n"
                        f"Exec={' '.join(shlex.quote(c) for c in cmd)}\nX-GNOME-Autostart-enabled=true\n", "utf-8")


class KeepAwake:
    """Stops the computer going to sleep while the paper runs. Windows: a thread that re-asks every minute (the
    request belongs to the thread that makes it). Mac: caffeinate, which ends with this process. Linux: systemd."""

    def __init__(self):
        self.stop_event = threading.Event()
        self.thread = None
        self.proc = None

    @staticmethod
    def supported() -> bool:
        return sys.platform in ("win32", "darwin") or shutil.which("systemd-inhibit") is not None

    def set(self, on: bool) -> None:
        if on:
            if sys.platform == "win32":
                if self.thread is None:
                    self.stop_event.clear()
                    self.thread = threading.Thread(target=self._windows_loop, daemon=True, name="keep-awake")
                    self.thread.start()
            elif self.proc is None:
                if sys.platform == "darwin":
                    args = ["caffeinate", "-i", "-w", str(os.getpid())]
                else:
                    args = ["systemd-inhibit", "--what=sleep", "--who=Stop Press", "--why=The paper is updating",
                            "sleep", "infinity"]
                self.proc = subprocess.Popen(args, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                             stderr=subprocess.DEVNULL)
        else:
            if sys.platform == "win32" and self.thread is not None:
                self.stop_event.set()
                self.thread.join(5)
                self.thread = None
                ctypes.windll.kernel32.SetThreadExecutionState(ES_CONTINUOUS)
            if self.proc is not None:
                self.proc.terminate()
                self.proc = None

    def _windows_loop(self) -> None:
        while True:
            ctypes.windll.kernel32.SetThreadExecutionState(ES_CONTINUOUS | ES_SYSTEM_REQUIRED)
            if self.stop_event.wait(60):
                break
        ctypes.windll.kernel32.SetThreadExecutionState(ES_CONTINUOUS)


def icon_image():
    from PIL import Image, ImageDraw, ImageFont

    img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle((2, 2, 62, 62), radius=10, fill=(245, 240, 230, 255), outline=(20, 20, 20, 255), width=3)
    try:
        font = ImageFont.load_default(size=28)
    except TypeError:
        font = ImageFont.load_default()
    d.text((32, 33), "SP", fill=(20, 20, 20, 255), anchor="mm", font=font)
    return img


def main() -> None:
    state = load_state()

    # A second copy just shows the settings page of the one already running.
    try:
        running = json.loads(RUNNING_FILE.read_text("utf-8"))
        if healthy(running["port"]):
            webbrowser.open(f"http://127.0.0.1:{running['port']}/setup")
            return
    except (OSError, ValueError, KeyError):
        pass

    port = free_port()
    import uvicorn

    from paper.app import app

    server = uvicorn.Server(uvicorn.Config(app, host="0.0.0.0", port=port, workers=1))
    thread = threading.Thread(target=server.run, daemon=True, name="stop-press-server")
    thread.start()
    RUNNING_FILE.write_text(json.dumps({"port": port, "pid": os.getpid()}), "utf-8")

    base = f"http://127.0.0.1:{port}"

    def wait_then_open():
        """The settings page opens once, on the first run (after that, from the tray menu)."""
        for _ in range(120):
            if healthy(port):
                break
            time.sleep(0.5)
        if not state.get("welcomed"):
            state["welcomed"] = True
            save_state(state)
            webbrowser.open(f"{base}/setup")

    threading.Thread(target=wait_then_open, daemon=True, name="first-run").start()

    keep = KeepAwake()
    if state.get("keep_awake") and keep.supported():
        keep.set(True)

    def open_path(path: str):
        return lambda icon=None, item=None: webbrowser.open(base + path)

    def toggle_autostart(icon, item):
        set_autostart(not autostart_path().exists())
        icon.update_menu()

    def toggle_awake(icon, item):
        on = keep.proc is None and keep.thread is None
        keep.set(on)
        state["keep_awake"] = on
        save_state(state)
        icon.update_menu()

    def quit_app(icon, item):
        keep.set(False)
        server.should_exit = True
        icon.stop()

    try:
        import pystray

        items = [
            pystray.MenuItem("Open settings", open_path("/setup"), default=True),
            pystray.MenuItem("Open the paper", open_path("/view")),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Start with my computer", toggle_autostart,
                             checked=lambda item: autostart_path().exists()),
        ]
        if keep.supported():
            items.append(pystray.MenuItem("Keep computer awake", toggle_awake,
                                          checked=lambda item: keep.proc is not None or keep.thread is not None))
        items += [pystray.Menu.SEPARATOR, pystray.MenuItem("Quit", quit_app)]
        icon = pystray.Icon("stop-press", icon_image(), "Stop Press", pystray.Menu(*items))
        icon.run()  # blocks until Quit, on the main thread
    except Exception:
        # No tray available (e.g. a Linux desktop without one): keep serving until interrupted.
        try:
            while thread.is_alive():
                thread.join(1)
        except KeyboardInterrupt:
            pass
    finally:
        keep.set(False)
        server.should_exit = True
        thread.join(15)
        RUNNING_FILE.unlink(missing_ok=True)


if __name__ == "__main__":
    main()
