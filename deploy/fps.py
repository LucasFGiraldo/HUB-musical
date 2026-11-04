"""Mede os quadros por segundo da tela do quiosque durante as transições (rodar no Dell).
Pré-requisito: o quiosque aberto com HUB_KIOSK_FLAGS="--remote-debugging-port=9222" (em ~/.config/hub-kiosk.env).
Uso: ~/hub-musical/.venv/bin/python ~/hub-musical/deploy/fps.py
"""
import asyncio
import json
import sys

import httpx
import websockets

JS = """
(async () => {
  const frames = []; let last = performance.now(), run = true;
  const tick = (t) => { frames.push(t - last); last = t; if (run) requestAnimationFrame(tick); };
  requestAnimationFrame(tick);
  const wait = (ms) => new Promise((r) => setTimeout(r, ms));
  // a tela de espera troca entre música e leituras; a capa acende o "halo" (desfoque grande)
  document.body.classList.add('playing');
  document.querySelectorAll('.halo').forEach((h) => h.classList.add('on'));
  for (let i = 0; i < 5; i++) { showPage('books'); await wait(1800); showPage('music'); await wait(1800); }
  run = false; await wait(100);
  frames.shift(); frames.sort((a, b) => a - b);
  const avg = frames.reduce((s, x) => s + x, 0) / frames.length;
  return JSON.stringify({ frames: frames.length, fps: +(1000 / avg).toFixed(1), p95_ms: +frames[Math.floor(frames.length * .95)].toFixed(1),
                          worst_ms: +frames.at(-1).toFixed(1), slow_over_50ms: frames.filter((x) => x > 50).length });
})()
"""


async def main():
    tabs = httpx.get("http://127.0.0.1:9222/json").json()
    page = next(t for t in tabs if t["type"] == "page")
    async with websockets.connect(page["webSocketDebuggerUrl"], max_size=None) as ws:
        await ws.send(json.dumps({"id": 1, "method": "Runtime.evaluate",
                                  "params": {"expression": JS, "awaitPromise": True, "returnByValue": True}}))
        while True:
            msg = json.loads(await ws.recv())
            if msg.get("id") == 1:
                print(msg["result"]["result"].get("value") or msg)
                return


asyncio.run(main())
