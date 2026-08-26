#!/usr/bin/env python3
"""Capture golden JSON-RPC frames from an ISOLATED Hermes gateway.

Safety properties (brief §6/§12):
  * HERMES_HOME points at a throwaway directory — nothing touches any
    profile's sessions, config, or credentials.
  * Dedicated loopback port, --isolated (never attaches to the machine-level
    server other projects may be running).
  * Only self-generated traffic is recorded; sanitizer scrubs absolute paths.
"""
from __future__ import annotations

import asyncio
import json
import os
import pathlib
import socket
import subprocess
import sys
import time

PORT = 19767
CAP_HOME = pathlib.Path("/tmp/talaria-golden-capture")
RAW = CAP_HOME / "raw_frames.jsonl"
HERMES = pathlib.Path.home() / ".hermes" / "hermes-agent" / "venv" / "bin" / "hermes"
if not HERMES.exists():
    HERMES = pathlib.Path("hermes")  # fall back to PATH


def wait_port(port: int, timeout: float = 90.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        with socket.socket() as s:
            s.settimeout(1.0)
            try:
                s.connect(("127.0.0.1", port))
                return True
            except OSError:
                time.sleep(1.0)
    return False


async def drive() -> int:
    import websockets

    uri = f"ws://127.0.0.1:{PORT}/api/ws"
    frames: list[dict] = []

    def record(direction: str, line: str) -> None:
        frames.append({"dir": direction, "line": line})

    async with websockets.connect(uri, max_size=None) as ws:
        # --- handshake: server pushes gateway.ready -------------------------
        raw_ready = await asyncio.wait_for(ws.recv(), timeout=30)
        record("in", raw_ready if isinstance(raw_ready, str) else raw_ready.decode())
        ready = json.loads(raw_ready)
        print("handshake:", json.dumps(ready)[:200])

        async def send(obj: dict) -> None:
            line = json.dumps(obj, separators=(",", ":"))
            record("out", line)
            await ws.send(line)

        async def recv_until_id(want_id: int, timeout: float = 60.0):
            """Collect notifications until the response for want_id arrives."""
            notes = []
            deadline = time.time() + timeout
            while True:
                remain = deadline - time.time()
                if remain <= 0:
                    raise TimeoutError(f"no response for id={want_id}")
                raw = await asyncio.wait_for(ws.recv(), timeout=remain)
                record("in", raw if isinstance(raw, str) else raw.decode())
                msg = json.loads(raw)
                params = msg.get("params") or {}
                if msg.get("method") == "event":
                    notes.append(params.get("type"))
                if msg.get("id") == want_id and ("result" in msg or "error" in msg):
                    return msg, notes

        rid = 0
        next_id = lambda: (rid := rid + 1)  # noqa: E731

        # heartbeat
        i = next_id()
        await send({"jsonrpc": "2.0", "id": i, "method": "gateway.ping"})
        await recv_until_id(i)

        # session lifecycle
        i = next_id()
        await send({"jsonrpc": "2.0", "id": i, "method": "session.list",
                    "params": {"limit": 5}})
        list_resp, notes = await recv_until_id(i)
        print("session.list:", json.dumps(list_resp)[:200])

        i = next_id()
        await send({"jsonrpc": "2.0", "id": i, "method": "session.create",
                    "params": {"title": "talaria golden capture"}})
        created, _ = await recv_until_id(i)
        print("session.create:", json.dumps(created)[:200])

        sid = None
        result = created.get("result") or {}
        if isinstance(result, dict):
            sid = result.get("session_id") or result.get("id") \
                or (result.get("session") or {}).get("id")
        if sid:
            i = next_id()
            await send({"jsonrpc": "2.0", "id": i, "method": "session.activate",
                        "params": {"session_id": sid}})
            try:
                _, _ = await recv_until_id(i, timeout=30)
            except TimeoutError:
                print("session.activate: no direct response (noted)")
        else:
            print("no session id parsed; skipping activate")

        # one tiny real turn — the core streaming shape
        i = next_id()
        await send({"jsonrpc": "2.0", "id": i, "method": "prompt.submit",
                    "params": {"text": "Reply with exactly: OK"}})
        try:
            resp, stream_notes = await recv_until_id(i, timeout=120)
            print("prompt.submit done; stream events seen:",
                  sorted(set(n for n in stream_notes if n))[:12])
        except TimeoutError:
            print("prompt.submit: timed out (streaming shape deferred)")

        await ws.close()

    RAW.write_text("\n".join(json.dumps(f) for f in frames) + "\n")
    print(f"\nrecorded {len(frames)} frames -> {RAW}")
    return 0


def main() -> int:
    import shutil
    shutil.rmtree(CAP_HOME, ignore_errors=True)
    CAP_HOME.mkdir(parents=True)

    env = dict(os.environ)
    env["HERMES_HOME"] = str(CAP_HOME / "hermes-home")

    proc = subprocess.Popen(
        [str(HERMES), "serve", "--host", "127.0.0.1", "--port", str(PORT),
         "--skip-build", "--isolated"],
        env=env, stdout=open(CAP_HOME / "serve.log", "w"),
        stderr=subprocess.STDOUT,
    )
    try:
        if not wait_port(PORT):
            print("server never came up; log tail:")
            print("\n".join((CAP_HOME / "serve.log").read_text().splitlines()[-20:]))
            return 1
        rc = asyncio.run(drive())
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=15)
        except subprocess.TimeoutExpired:
            proc.kill()
    return rc


if __name__ == "__main__":
    sys.exit(main())
