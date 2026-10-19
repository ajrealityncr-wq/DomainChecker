"""Run the search app on this computer only (127.0.0.1). Open http://127.0.0.1:8000 after starting it.

The app has no login, so it is deliberately not reachable from other machines.
"""

import uvicorn

if __name__ == "__main__":
    uvicorn.run("web_app:app", host="127.0.0.1", port=8000)
