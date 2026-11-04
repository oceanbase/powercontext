"""Drive the UI in headless Chrome over CDP and capture screenshots.

Usage:  python scripts/capture_ui.py [base_url]
Requires Chrome. The script talks the DevTools protocol directly, so no extra
Python package is needed.
"""

from __future__ import annotations

import base64
import json
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace") if hasattr(sys.stdout, "reconfigure") else None

from websocket import create_connection  # noqa: E402  (websocket-client)

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8788"
OUT = Path(__file__).resolve().parents[1] / "docs" / "screenshots"
CHROME = r"C:\Program Files\Google\Chrome\Application\chrome.exe"


class Session:
    def __init__(self, url: str, width: int = 1440, height: int = 1300) -> None:
        self.profile = tempfile.mkdtemp(prefix="sr-chrome-")
        self.process = subprocess.Popen(
            [
                CHROME,
                "--headless=new",
                "--disable-gpu",
                "--hide-scrollbars",
                "--no-first-run",
                "--remote-allow-origins=*",
                f"--user-data-dir={self.profile}",
                f"--window-size={width},{height}",
                "--remote-debugging-port=9101",
                "about:blank",
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        target = None
        for attempt in range(80):
            try:
                with urllib.request.urlopen("http://127.0.0.1:9101/json", timeout=2) as response:
                    pages = json.load(response)
                target = next((page for page in pages if page.get("type") == "page"), None)
                if target:
                    break
            except Exception:  # noqa: BLE001 - wait for devtools
                if attempt % 10 == 0:
                    print("waiting for devtools…", flush=True)
            time.sleep(0.5)
        if not target:
            raise RuntimeError("Chrome DevTools endpoint did not come up")
        self.ws = create_connection(
            target["webSocketDebuggerUrl"], timeout=60, suppress_origin=True
        )
        self.msg_id = 0
        self.send("Page.enable")
        self.send("Runtime.enable")
        self.send("Emulation.setDeviceMetricsOverride", {
            "width": width, "height": height, "deviceScaleFactor": 1, "mobile": False,
        })
        self.send("Page.navigate", {"url": url})
        time.sleep(4)

    def send(self, method: str, params: dict | None = None) -> dict:
        self.msg_id += 1
        self.ws.send(json.dumps({"id": self.msg_id, "method": method, "params": params or {}}))
        while True:
            message = json.loads(self.ws.recv())
            if message.get("id") == self.msg_id:
                return message

    def eval(self, expression: str) -> dict:
        result = self.send("Runtime.evaluate", {"expression": expression, "awaitPromise": True, "returnByValue": True})
        return result.get("result", {}).get("result", {}).get("value")

    def shot(self, name: str, full_page: bool = True) -> Path:
        if full_page:
            height = self.eval("Math.min(document.body.scrollHeight, 2200)")
            self.send("Emulation.setDeviceMetricsOverride", {
                "width": 1440, "height": int(height or 1300), "deviceScaleFactor": 1, "mobile": False,
            })
            time.sleep(0.6)
        data = self.send("Page.captureScreenshot", {"format": "png", "captureBeyondViewport": True})
        path = OUT / name
        path.write_bytes(base64.b64decode(data["result"]["data"]))
        return path

    def close(self) -> None:
        try:
            self.ws.close()
        finally:
            self.process.terminate()
            self.process.wait(timeout=20)


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    session = Session(BASE + "/")
    try:
        # 1. reset the profile to a wide scope, so the "before" state is neutral
        session.eval("""
            fetch('/api/profile', {method:'POST', headers:{'Content-Type':'application/json'},
              body: JSON.stringify({user:'demo-user', courses:[], source_types:[],
                keyword_weight:0.6, vector_weight:0.4, top_k:8, tag:'', reason:'截图准备'})})
              .then(r => r.json())
        """)
        time.sleep(1)

        # 2. run a recall and show the result
        session.eval("""
            (async () => {
              await api('/api/recall', {method:'POST', headers:{'Content-Type':'application/json'},
                body: JSON.stringify({user:'demo-user', question:'Raft 怎么保证不丢已提交的日志'})});
              location.reload();
            })()
        """)
        time.sleep(1)
        session.send("Page.navigate", {"url": BASE + "/"})
        time.sleep(3)
        session.eval("""
            (async () => {
              const data = await api('/api/recall', {method:'POST', headers:{'Content-Type':'application/json'},
                body: JSON.stringify({user:'demo-user', question:'Raft 怎么保证不丢已提交的日志'})});
              renderRecall(data);
            })()
        """)
        time.sleep(1)
        session.shot("ui-recall.png")

        # 3. remember a preference, then recall the same question again
        session.eval("""
            (async () => {
              await api('/api/profile', {method:'POST', headers:{'Content-Type':'application/json'},
                body: JSON.stringify({user:'demo-user', courses:['分布式系统'], source_types:['note','paper'],
                  keyword_weight:0.7, vector_weight:0.3, top_k:6, tag:'', reason:'截图准备：只看分布式系统'})});
              const data = await api('/api/recall', {method:'POST', headers:{'Content-Type':'application/json'},
                body: JSON.stringify({user:'demo-user', question:'Raft 怎么保证不丢已提交的日志'})});
              renderRecall(data);
              const memory = await api('/api/profile?user=demo-user');
              renderProfileState(memory);
            })()
        """)
        time.sleep(2)
        session.shot("ui-recall-after-memory.png")
    finally:
        session.close()

    print("wrote:", sorted(p.name for p in OUT.glob("*.png")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
