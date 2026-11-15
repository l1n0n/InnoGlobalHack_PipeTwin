import socket
import subprocess
import sys
from pathlib import Path
from time import monotonic, sleep
from urllib.error import URLError
from urllib.request import urlopen

import webview


def wait_for_server(server, url):
    deadline = monotonic() + 30
    while monotonic() < deadline and server.poll() is None:
        try:
            with urlopen(f"{url}/_stcore/health", timeout=1) as response:
                if response.status == 200:
                    return
        except (URLError, TimeoutError):
            sleep(0.2)
    raise RuntimeError("Streamlit did not start. See the logs above.")


def main():
    root = Path(__file__).resolve().parent
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]

    url = f"http://127.0.0.1:{port}"
    server = subprocess.Popen(
        [
            sys.executable, "-m", "streamlit", "run", str(root / "app.py"),
            "--server.address", "127.0.0.1", "--server.port", str(port),
            "--server.headless", "true", "--browser.gatherUsageStats", "false",
        ],
        cwd=root,
    )
    try:
        wait_for_server(server, url)
        webview.settings["ALLOW_DOWNLOADS"] = True
        webview.create_window("PipeTwin", url, width=1280, height=850)
        webview.start(gui="edgechromium")
    finally:
        server.terminate()
        server.wait()


if __name__ == "__main__":
    main()
