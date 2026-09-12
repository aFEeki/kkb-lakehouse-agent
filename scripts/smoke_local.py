"""Opt-in browser integration check; run after npm run build in web/."""

import os
import socket
import subprocess
import sys
import time
from pathlib import Path
from tempfile import TemporaryDirectory
from urllib.error import URLError
from urllib.request import urlopen

from playwright.sync_api import expect, sync_playwright

ROOT = Path(__file__).resolve().parents[1]


def wait_ready(url: str, process: subprocess.Popen) -> None:
    for _ in range(100):
        if process.poll() is not None:
            raise RuntimeError(f"Local server exited before {url} became ready")
        try:
            with urlopen(url, timeout=1) as response:
                if response.status == 200:
                    return
        except (URLError, TimeoutError):
            time.sleep(0.2)
    raise RuntimeError(f"Timed out waiting for {url}")


def main() -> None:
    # Refuse to test unrelated developer services already using these ports.
    for port in (8000, 3000):
        with socket.socket() as probe:
            probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            probe.bind(("127.0.0.1", port))
    processes = []
    with TemporaryDirectory(prefix="kkb-smoke-") as directory:
        env = {
            **os.environ,
            "MIA_API_KEY": "",
            "EVDS_API_KEY": "",
            "DATA_DIR": directory,
            "DUCKDB_PATH": str(Path(directory) / "smoke.duckdb"),
            "LANCEDB_PATH": str(Path(directory) / "lance"),
            "CORS_ORIGINS": '["http://127.0.0.1:3000"]',
        }
        try:
            processes.append(
                subprocess.Popen(
                    [
                        sys.executable,
                        "-m",
                        "uvicorn",
                        "kkb_agent.api.main:app",
                        "--host",
                        "127.0.0.1",
                        "--port",
                        "8000",
                    ],
                    cwd=ROOT,
                    env=env,
                )
            )
            wait_ready("http://127.0.0.1:8000/health", processes[-1])
            processes.append(
                subprocess.Popen(
                    ["npm", "start", "--", "--hostname", "127.0.0.1", "--port", "3000"],
                    cwd=ROOT / "web",
                    env=env,
                    start_new_session=True,
                )
            )
            wait_ready("http://127.0.0.1:3000", processes[-1])
            with sync_playwright() as playwright:
                browser = playwright.chromium.launch()
                try:
                    page = browser.new_page()
                    errors = []
                    page.on("pageerror", lambda error: errors.append(str(error)))
                    page.goto("http://127.0.0.1:3000")
                    expect(page.get_by_text("Sistem hazır", exact=True)).to_be_visible()
                    expect(page.get_by_text("Hazır", exact=True)).to_have_count(3)
                    assert (
                        page.locator("main").evaluate(
                            "element => getComputedStyle(element).backgroundColor"
                        )
                        != "rgba(0, 0, 0, 0)"
                    )
                    page.route("**/health", lambda route: route.abort())
                    page.get_by_role("button", name="Yeniden kontrol et").click()
                    expect(page.locator("main").get_by_role("alert")).to_be_visible()
                    page.unroute("**/health")
                    page.get_by_role("button", name="Yeniden kontrol et").click()
                    expect(page.get_by_text("Sistem hazır", exact=True)).to_be_visible()
                    assert not errors, errors
                    print("Browser/backend health, Tailwind and retry smoke checks passed.")
                finally:
                    browser.close()
        finally:
            for process in reversed(processes):
                if process.poll() is None:
                    # npm owns a child Next process; terminate its isolated process group.
                    if process is processes[-1] and len(processes) == 2:
                        import signal

                        os.killpg(process.pid, signal.SIGTERM)
                    else:
                        process.terminate()
                    process.wait(timeout=10)


if __name__ == "__main__":
    main()
