"""Explicit install step for the third-party browser gateway.

Never overwrites or updates a vendor checkout silently, and never runs the gateway
itself. Installing the Node dependencies is not enough: the upstream project also ships
its browser binaries through Playwright/patchright, and the Hub cannot start a worker
without them. Both steps run here so `python scripts/prepare_gateway.py` is a complete
install.
"""
import argparse
import os
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
VENDOR = ROOT / "vendor" / "gpt-web-gateway"
UPSTREAM = "https://github.com/stufently/gpt-web-gateway.git"
BROWSER_STEPS = (
    ["npx", "--yes", "playwright", "install", "chromium"],
    ["npx", "--yes", "patchright", "install", "chrome"],
)


def system_chrome():
    """Path of the installed Google Chrome, or empty string when there is none."""
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
    for candidate in candidates:
        if candidate.is_file():
            return str(candidate)
    return ""


def run(command, cwd=None):
    print("+", " ".join(command), flush=True)
    subprocess.run(command, cwd=cwd, check=True)


def install_from_tarball():
    """Download refs/heads/main from codeload when git://github.com is unreachable."""
    import tarfile
    import tempfile
    import urllib.request

    url = (
        "https://codeload.github.com/stufently/gpt-web-gateway/tar.gz/refs/heads/main"
    )
    with tempfile.TemporaryDirectory() as tmp:
        archive = Path(tmp) / "gateway.tar.gz"
        print("+ download", url, flush=True)
        urllib.request.urlretrieve(url, archive)
        with tarfile.open(archive) as tar:
            tar.extractall(tmp, filter="data")
        roots = [p for p in Path(tmp).iterdir() if p.is_dir()]
        if len(roots) != 1:
            sys.exit("Unexpected tarball layout; review it manually and install by hand.")
        shutil.copytree(roots[0], VENDOR, dirs_exist_ok=True)
    return (
        "tarball refs/heads/main "
        f"(github.com blocked; fetched {datetime.now(timezone.utc).isoformat(timespec='seconds')})"
    )


def already_installed(vendor):
    return (vendor / "src" / "server.js").exists() and (vendor / "node_modules").is_dir()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--yes",
        action="store_true",
        help="skip the interactive INSTALL confirmation",
    )
    parser.add_argument(
        "--skip-browsers",
        action="store_true",
        help="only install Node dependencies (browser binaries must be installed manually)",
    )
    args = parser.parse_args()

    if not (shutil.which("git") and shutil.which("npm") and shutil.which("node")):
        sys.exit("Git, Node.js >=22, and npm must be available on PATH.")
    node_major = subprocess.check_output(["node", "--version"], text=True).strip()
    print("Node.js:", node_major)

    if VENDOR.exists():
        if already_installed(VENDOR):
            print("Gateway already installed. Leave it unchanged.")
            print("Browser binaries are not checked here; run python scripts/doctor.py.")
            sys.exit(0)
        sys.exit(
            "Incomplete existing vendor directory: remove it manually after reviewing, "
            "then retry."
        )

    print(
        "Installing third-party gpt-web-gateway from GitHub. Review its licensing/security "
        "and the OpenAI Terms of Use before proceeding."
    )
    print("Upstream:", UPSTREAM)
    print("This is NOT an official OpenAI product; UI automation may lead to account suspension.")
    if not args.yes:
        choice = input("Type INSTALL to proceed: ").strip()
        if choice != "INSTALL":
            sys.exit("Cancelled.")

    revision = ""
    try:
        run(["git", "clone", "--depth", "1", UPSTREAM, str(VENDOR)])
        revision = subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=VENDOR, text=True
        ).strip()
    except subprocess.CalledProcessError:
        # github.com:443 is blocked on some networks while codeload/npm still work, so
        # fall back to the source tarball instead of leaving the user with no gateway.
        print("git clone failed; falling back to the codeload tarball ...")
        revision = install_from_tarball()

    pin = ROOT / "vendor" / "PINNED_COMMIT.txt"
    pin.parent.mkdir(parents=True, exist_ok=True)
    pin.write_text(revision + "\n", encoding="utf-8")
    print("Gateway revision:", revision, "(recorded in vendor/PINNED_COMMIT.txt)")

    if not (VENDOR / "package-lock.json").exists():
        sys.exit(
            "Upstream has no package-lock.json; refusing npm install with unverified "
            "dependency resolution. Review upstream and pin a commit that has a lockfile."
        )
    run(["npm", "ci"], cwd=VENDOR)

    if args.skip_browsers:
        print("Skipped browser binaries (--skip-browsers).")
    else:
        browser_failure = None
        for step in BROWSER_STEPS:
            try:
                run(step, cwd=VENDOR)
            except subprocess.CalledProcessError as exc:
                browser_failure = exc
                print("browser download failed:", step[2] if len(step) > 2 else step)
                break
        if browser_failure is not None:
            # Playwright's CDN is geo-blocked on some networks. The gateway's default
            # engine (patchright) drives the *installed* Google Chrome, so a downloaded
            # build is optional — only fail when there is no Chrome at all.
            if system_chrome():
                print(
                    "WARNING: browser binaries could not be downloaded. The gateway defaults "
                    "to patchright channel='chrome', which uses the installed Google Chrome at "
                    f"{system_chrome()}. Workers can still start."
                )
            else:
                sys.exit(
                    "No browser available: the download failed and Google Chrome is not "
                    "installed. Install Chrome, or run the failed command manually inside "
                    f"{VENDOR}: {' '.join(step)}"
                )

    print("Installed gateway revision:", revision)
    print("Install Chrome if patchright reports it missing, then restart the panel.")
    print("Next: python scripts/doctor.py  ->  python -m uvicorn app:app --workers 1")


if __name__ == "__main__":
    main()
