"""Start Local Chat:  python run.py

Environment variables:
  PORT        port to listen on (default 8000)
  NO_BROWSER  set to 1 to skip opening the browser automatically
"""
import os
import threading
import webbrowser

import uvicorn

HOST = "127.0.0.1"  # never bind anything else: this app has no authentication
PORT = int(os.environ.get("PORT", "8000"))


def main() -> None:
    url = f"http://{HOST}:{PORT}"
    if os.environ.get("NO_BROWSER") != "1":
        threading.Timer(1.2, lambda: webbrowser.open(url)).start()
    print(f"\n  Local Chat running at {url}  (Ctrl+C to stop)\n")
    uvicorn.run("backend.main:app", host=HOST, port=PORT, log_level="warning")


if __name__ == "__main__":
    main()
