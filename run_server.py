"""Run the search app on this computer only (127.0.0.1) and open it in the browser.

The app has no login, so it is deliberately not reachable from other machines.
"""

import threading
import webbrowser

import uvicorn

URL = "http://127.0.0.1:8000"

if __name__ == "__main__":
    # Give the server a moment to start before the browser asks for the page.
    threading.Timer(1.5, webbrowser.open, args=(URL,)).start()
    uvicorn.run("web_app:app", host="127.0.0.1", port=8000)
