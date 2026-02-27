#!/usr/bin/env python3
"""
run_app.py — Launch the IF Panel Designer in your browser.

Run from VS Code terminal:
    python run_app.py

What it does:
    1. Finds a free port (default 8501)
    2. Starts the Streamlit server as a subprocess
    3. Opens Chrome (or your default browser) to the app URL
    4. Keeps running until you press Ctrl+C

Options:
    python run_app.py --port 8502          # Use a specific port
    python run_app.py --no-browser         # Don't open browser automatically
    python run_app.py --browser firefox    # Use a specific browser
    python run_app.py --chrome-profile     # Reuse your existing Chrome profile
                                           # (so you stay logged into Biocompare, etc.)
"""

import os
import platform
import shutil
import signal
import socket
import subprocess
import sys
import time
import webbrowser


# ─── Configuration ──────────────────────────────────────────────────────────

DEFAULT_PORT = 8501
APP_ENTRY = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                         "gui_streamlit", "app.py")


def find_free_port(start: int = DEFAULT_PORT, end: int = 8599) -> int:
    """Find a free port in the given range."""
    for port in range(start, end):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            try:
                s.bind(("127.0.0.1", port))
                return port
            except OSError:
                continue
    raise RuntimeError(f"No free port found in range {start}-{end}")


def find_chrome() -> str | None:
    """Find Chrome/Chromium executable path."""
    system = platform.system()

    if system == "Darwin":  # macOS
        candidates = [
            "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
            "/Applications/Chromium.app/Contents/MacOS/Chromium",
            shutil.which("google-chrome"),
            shutil.which("chromium"),
        ]
    elif system == "Windows":
        candidates = [
            os.path.expandvars(r"%ProgramFiles%\Google\Chrome\Application\chrome.exe"),
            os.path.expandvars(r"%ProgramFiles(x86)%\Google\Chrome\Application\chrome.exe"),
            os.path.expandvars(r"%LocalAppData%\Google\Chrome\Application\chrome.exe"),
            shutil.which("chrome"),
        ]
    else:  # Linux
        candidates = [
            shutil.which("google-chrome"),
            shutil.which("google-chrome-stable"),
            shutil.which("chromium"),
            shutil.which("chromium-browser"),
            "/usr/bin/google-chrome",
            "/usr/bin/chromium",
        ]

    for path in candidates:
        if path and os.path.isfile(path):
            return path
    return None


def open_in_chrome(url: str, chrome_path: str = None, use_profile: bool = False):
    """
    Open URL in Chrome specifically (not just any browser).
    Falls back to default browser if Chrome isn't found.
    """
    if chrome_path is None:
        chrome_path = find_chrome()

    if chrome_path:
        args = [chrome_path]
        if use_profile:
            # Reuse the user's existing Chrome profile so they keep cookies,
            # saved passwords, Biocompare logins, etc.
            pass  # Chrome opens with default profile by default
        else:
            # Open with a clean app-like window
            args.append(f"--app={url}")
        try:
            subprocess.Popen(args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            return True
        except Exception as e:
            print(f"  Chrome launch failed ({e}), falling back to default browser")

    # Fallback: system default browser
    webbrowser.open(url)
    return True


def wait_for_server(port: int, timeout: float = 30.0):
    """Wait until the Streamlit server is accepting connections."""
    start = time.time()
    while time.time() - start < timeout:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            try:
                s.connect(("127.0.0.1", port))
                return True
            except ConnectionRefusedError:
                time.sleep(0.3)
    return False


def main():
    import argparse

    parser = argparse.ArgumentParser(description="Launch IF Panel Designer")
    parser.add_argument("--port", type=int, default=0,
                        help=f"Port (default: auto-find from {DEFAULT_PORT})")
    parser.add_argument("--no-browser", action="store_true",
                        help="Don't open browser automatically")
    parser.add_argument("--browser", type=str, default="chrome",
                        choices=["chrome", "default"],
                        help="Which browser to open (default: chrome)")
    parser.add_argument("--chrome-profile", action="store_true",
                        help="Reuse your existing Chrome user profile")
    args = parser.parse_args()

    # ── Find port ──
    port = args.port or find_free_port()
    url = f"http://localhost:{port}"

    # ── Check streamlit is installed ──
    streamlit_bin = shutil.which("streamlit")
    if not streamlit_bin:
        print("❌ Streamlit not found. Run: pip install streamlit")
        sys.exit(1)

    # ── Start Streamlit ──
    print(f"\n{'=' * 50}")
    print(f"  🔬 IF Panel Designer")
    print(f"  URL: {url}")
    print(f"  Press Ctrl+C to stop")
    print(f"{'=' * 50}\n")

    streamlit_args = [
        streamlit_bin, "run", APP_ENTRY,
        "--server.port", str(port),
        "--server.headless", "true",          # Don't let Streamlit open its own browser
        "--server.address", "127.0.0.1",
        "--browser.gatherUsageStats", "false",
        "--theme.base", "light",
    ]

    # Start Streamlit as a subprocess (cwd = project root for correct imports)
    project_root = os.path.dirname(os.path.abspath(__file__))
    proc = subprocess.Popen(
        streamlit_args,
        cwd=project_root,
        stdout=sys.stdout,
        stderr=sys.stderr,
    )

    # ── Open browser once server is ready ──
    if not args.no_browser:
        print("  Waiting for server to start...")
        if wait_for_server(port):
            print(f"  ✅ Server ready — opening {'Chrome' if args.browser == 'chrome' else 'browser'}...")
            if args.browser == "chrome":
                open_in_chrome(url, use_profile=args.chrome_profile)
            else:
                webbrowser.open(url)
        else:
            print("  ⚠️  Server didn't start in time. Open manually:", url)

    # ── Keep running until Ctrl+C ──
    def handle_signal(sig, frame):
        print("\n\n  Shutting down...")
        proc.terminate()
        proc.wait(timeout=5)
        sys.exit(0)

    signal.signal(signal.SIGINT, handle_signal)
    signal.signal(signal.SIGTERM, handle_signal)

    try:
        proc.wait()
    except KeyboardInterrupt:
        proc.terminate()
        proc.wait(timeout=5)


if __name__ == "__main__":
    main()
