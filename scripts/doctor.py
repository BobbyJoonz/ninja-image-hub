"""Preflight doctor: tell the operator exactly what is missing before starting the Hub.

Run it before the first start, and again whenever a worker will not come online:

    python scripts/doctor.py

Exit code 0 means "ready to start"; 1 means at least one check failed. Every failure
line carries the command that fixes it.
"""
import argparse
import json
import mimetypes
import os
import shutil
import socket
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RESULTS = []
QUIET = False
PORT_HINT = None


def _console(text):
    """Console-safe text: the Windows console may be cp1252 while files hold Unicode."""
    return str(text).encode("ascii", "replace").decode("ascii")


def record(level, name, detail, fix=""):
    RESULTS.append({"level": level, "name": name, "detail": detail, "fix": fix})
    if QUIET:
        return
    tag = {"ok": "PASS", "warn": "WARN", "fail": "FAIL"}[level]
    print(f"[{tag}] {_console(name)}: {_console(detail)}")
    if fix and level != "ok":
        print(f"       -> {_console(fix)}")


def load_env():
    try:
        from dotenv import load_dotenv
    except ImportError:
        return False
    load_dotenv(ROOT / ".env", override=False)
    return True


def check_python():
    if sys.version_info < (3, 11):
        record("fail", "python", f"{sys.version.split()[0]} (need 3.11+)", "install Python 3.11+")
        return
    record("ok", "python", sys.version.split()[0])
    missing = []
    for module in ("fastapi", "starlette", "sqlalchemy", "httpx", "PIL", "dotenv", "psutil", "itsdangerous"):
        try:
            __import__(module)
        except ImportError:
            missing.append(module)
    if missing:
        record(
            "fail",
            "python packages",
            "missing: " + ", ".join(missing),
            "pip install -r requirements.txt",
        )
    else:
        record("ok", "python packages", "all runtime imports available")


def check_node():
    node = shutil.which("node")
    if not node:
        record("fail", "node", "not found on PATH", "install Node.js 22+")
        return
    try:
        version = os.popen("node --version").read().strip()
    except OSError:
        version = "?"
    major = version.lstrip("v").split(".")[0]
    if major.isdigit() and int(major) < 22:
        record("fail", "node", f"{version} (need 22+)", "upgrade Node.js to 22 or newer")
    else:
        record("ok", "node", version)
    for tool in ("npm", "git"):
        if shutil.which(tool):
            record("ok", tool, "available")
        else:
            record("fail", tool, "not found on PATH", "install it and reopen the terminal")


def check_secrets():
    env_file = ROOT / ".env"
    if not env_file.exists():
        record(
            "fail",
            "env file",
            ".env is missing",
            "python scripts/bootstrap.py  (creates unique secrets once)",
        )
    else:
        record("ok", "env file", str(env_file))
    secret = os.getenv("APP_SECRET", "")
    password = os.getenv("ADMIN_PASSWORD", "")
    agent = os.getenv("AGENT_API_KEY", "")
    if len(secret) < 32:
        record("fail", "APP_SECRET", f"length {len(secret)} (need 32+)", "python scripts/bootstrap.py")
    else:
        record("ok", "APP_SECRET", "strong")
    if len(password) < 12:
        record("fail", "ADMIN_PASSWORD", f"length {len(password)} (need 12+)", "python scripts/bootstrap.py")
    else:
        record("ok", "ADMIN_PASSWORD", "strong")
    record("ok" if agent else "warn", "AGENT_API_KEY", "set" if agent else "empty (agent API disabled)",
           "set AGENT_API_KEY in .env if an external agent should read images")


def check_mime():
    guessed = mimetypes.guess_type("app.js")[0]
    if guessed in ("text/javascript", "application/javascript"):
        record("ok", "javascript MIME", guessed)
    else:
        # The panel pins this mapping at startup, so it is not a blocker for the Hub
        # itself; a hand-rolled static server (python -m http.server) would still break.
        record(
            "warn",
            "javascript MIME",
            f".js resolves to {guessed!r}; the panel overrides this at startup",
            "for a hand-served preview use: python scripts/serve_preview.py",
        )
    css = mimetypes.guess_type("style.css")[0]
    record("ok" if css == "text/css" else "warn", "css MIME", str(css))


def check_data_dir():
    data = Path(os.getenv("DATA_DIR", str(ROOT / "data"))).resolve()
    try:
        data.mkdir(parents=True, exist_ok=True)
        probe = data / ".doctor-probe"
        probe.write_bytes(b"ok")
        probe.unlink()
        record("ok", "data directory", str(data))
    except OSError as exc:
        record("fail", "data directory", f"not writable: {exc}", "choose a writable DATA_DIR")
        return None
    free_gb = shutil.disk_usage(data).free / (1024**3)
    if free_gb < 5:
        record("warn", "free disk", f"{free_gb:.1f} GB", "free space: images and ZIP exports need room")
    else:
        record("ok", "free disk", f"{free_gb:.1f} GB")
    return data


def check_database(data):
    if not data:
        return
    url = os.getenv("DATABASE_URL", "")
    if url.startswith("postgresql"):
        record("warn", "database", "PostgreSQL configured (not verified by this script)",
               "start PostgreSQL and check psycopg is installed")
        return
    db_path = data / "hub.db"
    if not db_path.exists():
        record("ok", "database", "SQLite will be created on first start")
        return
    try:
        with sqlite3.connect(str(db_path)) as conn:
            mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
    except sqlite3.Error as exc:
        record("fail", "database", f"cannot open {db_path}: {exc}", "restore from backup")
        return
    if str(mode).lower() == "wal":
        record("ok", "database", f"SQLite WAL ({db_path.name})")
    else:
        record("warn", "database", f"SQLite journal_mode={mode}", "restart the panel once to apply WAL")


def _playwright_chromium():
    """Downloaded Chromium builds only — ffmpeg/winldd folders do not count."""
    roots = []
    local = os.getenv("LOCALAPPDATA", "")
    for candidate in (
        Path(local) / "ms-playwright" if local else None,
        Path.home() / "AppData" / "Local" / "ms-playwright",
        Path.home() / ".cache" / "ms-playwright",
    ):
        if candidate and candidate.is_dir():
            roots.append(candidate)
    for root in roots:
        for child in root.iterdir():
            if child.is_dir() and child.name.startswith("chromium"):
                return child
    return None


def _chrome_binaries():
    program_files = os.getenv("PROGRAMFILES", r"C:\Program Files")
    program_files_x86 = os.getenv("PROGRAMFILES(X86)", r"C:\Program Files (x86)")
    candidates = [
        Path(program_files) / "Google" / "Chrome" / "Application" / "chrome.exe",
        Path(program_files_x86) / "Google" / "Chrome" / "Application" / "chrome.exe",
        Path.home() / "AppData" / "Local" / "Google" / "Chrome" / "Application" / "chrome.exe",
    ]
    for command in ("google-chrome", "google-chrome-stable", "chromium", "chromium-browser"):
        found = shutil.which(command)
        if found:
            candidates.append(Path(found))
    return [path for path in candidates if path.is_file()]


def check_gateway():
    vendor = ROOT / "vendor" / "gpt-web-gateway"
    if not (vendor / "src" / "server.js").exists():
        record(
            "fail",
            "gateway",
            "not installed (no vendor/gpt-web-gateway/src/server.js)",
            "python scripts/prepare_gateway.py",
        )
        return
    record("ok", "gateway", str(vendor))
    if (vendor / "node_modules").is_dir():
        record("ok", "gateway dependencies", "node_modules present")
    else:
        record("fail", "gateway dependencies", "node_modules missing", "cd vendor/gpt-web-gateway && npm ci")
    pin = ROOT / "vendor" / "PINNED_COMMIT.txt"
    if pin.exists():
        # utf-8-sig: a Windows editor may add a BOM, which used to crash the console print.
        revision = pin.read_text(encoding="utf-8-sig").strip().splitlines()[0][:32]
        record("ok", "gateway revision", revision)
    else:
        record("warn", "gateway revision", "not recorded", "prefer a pinned commit; keep PINNED_COMMIT.txt")
    downloaded = _playwright_chromium()
    chrome = _chrome_binaries()
    if downloaded:
        record("ok", "playwright chromium", str(downloaded.name))
    elif chrome:
        # The gateway defaults to patchright channel="chrome", which drives the installed
        # Google Chrome; a downloaded build is optional on such hosts.
        record(
            "ok",
            "playwright chromium",
            "not downloaded; the worker uses installed Google Chrome instead",
        )
    else:
        record(
            "warn",
            "playwright chromium",
            "no downloaded build and no system Chrome",
            "cd vendor/gpt-web-gateway && npx playwright install chromium",
        )
    if chrome:
        record("ok", "google chrome", str(chrome[0]))
    else:
        record(
            "fail",
            "google chrome",
            "not found (patchright drives the real Chrome build)",
            "install Google Chrome, then: cd vendor/gpt-web-gateway && npx patchright install chrome",
        )


def _free_port(start):
    for candidate in range(start, start + 40):
        with socket.socket() as sock:
            if sock.connect_ex(("127.0.0.1", candidate)) != 0:
                return candidate
    return None


def check_memory():
    try:
        import psutil
    except ImportError:
        record("warn", "memory", "psutil not installed", "pip install -r requirements.txt")
        return
    memory = psutil.virtual_memory()
    total_gb = memory.total / (1024**3)
    available_gb = memory.available / (1024**3)
    record("ok", "memory", f"{total_gb:.1f} GB total, {available_gb:.1f} GB available")
    wanted = int(os.getenv("MAX_RUNNING_ACCOUNTS", "2") or 2)
    need_gb = wanted * 1.3
    if available_gb < need_gb:
        record(
            "warn",
            "ram budget",
            f"max_running_accounts={wanted} needs ~{need_gb:.1f} GB, {available_gb:.1f} GB available",
            "lower max_running_accounts, lower idle_worker_shutdown_minutes, or add RAM",
        )
    else:
        record("ok", "ram budget", f"{wanted} live workers fit in {available_gb:.1f} GB")


def check_linux_host():
    """A headed browser on a server needs a virtual display and Chrome's shared libraries."""
    if not sys.platform.startswith("linux"):
        record("ok", "browser display", "desktop session")
        return
    display = os.getenv("DISPLAY", "")
    if display:
        record("ok", "browser display", f"X11 DISPLAY={display}")
    elif shutil.which("xvfb-run"):
        record("ok", "browser display", "xvfb-run available (one virtual display per worker)")
    else:
        record(
            "fail",
            "browser display",
            "no DISPLAY and no xvfb-run",
            "sudo apt install -y xvfb  (or run deploy/install_linux.sh)",
        )
    try:
        libraries = subprocess.run(
            ["ldconfig", "-p"], capture_output=True, text=True
        ).stdout
    except OSError:
        libraries = ""
    packages = {
        "libnss3": "libnss3",
        "libatk-1.0": "libatk1.0-0",
        "libatk-bridge": "libatk-bridge2.0-0",
        "libgbm": "libgbm1",
        "libasound": "libasound2",
        "libxkbcommon": "libxkbcommon0",
        "libcups": "libcups2",
    }
    missing = [pkg for lib, pkg in packages.items() if lib not in libraries]
    if missing:
        record(
            "warn",
            "chrome libraries",
            "missing: " + ", ".join(missing),
            "sudo apt install -y " + " ".join(missing),
        )
    else:
        record("ok", "chrome libraries", "present")
    if shutil.which("fc-list"):
        fonts = subprocess.run(["fc-list"], capture_output=True, text=True).stdout
        if not fonts.strip():
            record("warn", "fonts", "no fonts installed", "sudo apt install -y fonts-liberation fonts-noto-color-emoji")
        else:
            record("ok", "fonts", f"{len(fonts.splitlines())} font files")
    else:
        record("warn", "fonts", "fontconfig not installed", "sudo apt install -y fontconfig fonts-liberation")


def check_ports():
    global PORT_HINT
    port = int(os.getenv("PORT", "8080"))
    with socket.socket() as sock:
        busy = sock.connect_ex(("127.0.0.1", port)) == 0
    if busy:
        # A completely free port cannot be promised (the probe only proves "nothing is
        # listening right now"), but the next candidate is a useful, concrete hint.
        alternative = _free_port(port + 1)
        PORT_HINT = alternative or port
        record(
            "warn",
            "panel port",
            f"127.0.0.1:{port} is already in use"
            + (f"; {alternative} looks free" if alternative else ""),
            f"start with --port {alternative} (or stop whatever owns {port}); "
            "only one Uvicorn process may run",
        )
    else:
        PORT_HINT = port
        record("ok", "panel port", f"127.0.0.1:{port} free")
    free = 0
    for candidate in range(41000, 41010):
        with socket.socket() as sock:
            if sock.connect_ex(("127.0.0.1", candidate)) != 0:
                free += 1
    record("ok" if free else "fail", "worker ports", f"{free}/10 free in 41000-41009",
           "stop stray gateway processes" if not free else "")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true", help="machine-readable output")
    args = parser.parse_args()
    global QUIET
    QUIET = args.json

    if not args.json:
        print("Ninja Image Hub - preflight doctor\n" + "=" * 34)
    loaded = load_env()
    if not loaded and not args.json:
        print("[WARN] python-dotenv missing: reading process environment only")

    check_python()
    check_node()
    check_secrets()
    check_mime()
    data = check_data_dir()
    check_database(data)
    check_memory()
    check_gateway()
    check_linux_host()
    check_ports()

    failures = [r for r in RESULTS if r["level"] == "fail"]
    warnings = [r for r in RESULTS if r["level"] == "warn"]
    if args.json:
        print(json.dumps({"ok": not failures, "failures": failures, "warnings": warnings}, indent=2))
    else:
        print("\n" + "-" * 34)
        print(f"{len(RESULTS) - len(failures) - len(warnings)} passed, "
              f"{len(warnings)} warnings, {len(failures)} failures")
        if failures:
            print("\nNot ready yet. Fix the FAIL lines above, then run this again.")
        else:
            port = PORT_HINT or 8080
            print("\nReady. Start the panel with:")
            print(
                f"  python -m uvicorn app:app --host 127.0.0.1 --port {port} --workers 1"
            )
            print(f"Then open http://127.0.0.1:{port}")
            print("Or double-click start_windows.bat (Windows) which uses port 8081.")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
