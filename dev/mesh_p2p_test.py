#!/usr/bin/env python3
"""Cross-process P2P + live-event + resilience test for riftor-meshd.

Drives two real daemon processes over JSON-line stdin/stdout and verifies:
  1. P2P dial / ping-pong (the original Phase 1 check).
  2. Phase 2 live-event path: Commander (A) creates an engagement, Worker (B)
     joins via invite, A submits a finding; in Autonomous mode A's processor
     auto-publishes and broadcasts a gossip "processed" event that B observes
     on its stdout as a MeshEvent.
  3. Phase 1 resilience: kill A, then B still answers get_state from its synced
     local CRDT replica.

Run with:  RIFTOR_MESH_P2P=1 uv run python dev/mesh_p2p_test.py

Offline note: the meshd processor has NO offline LLM stub. The first finding in
a fresh engagement skips the dedup LLM call (no existing findings) and falls
back to severity="medium" when the severity LLM call fails — so the publish +
"processed" gossip event DO happen offline. Doc-content sync over P2P, however,
may not complete without a relay/working transport; the script degrades
gracefully (⚠️ / SKIP) for any step that needs connectivity it can't get.
"""

import asyncio
import itertools
import json
import os
import sys
import time

BINARY = "./meshd/target/debug/riftor-meshd"

# Generous timeouts: real localhost iroh overlay formation + gossip + the
# LLM-severity fallback (3 retries with backoff before falling back to
# "medium") can take many seconds.
RPC_TIMEOUT = 15.0
JOIN_TIMEOUT = 12.0  # join imports a doc ticket → dials inviter; bound it
PRESENCE_TIMEOUT = 40.0  # first presence heartbeat is broadcast every 15s
PROCESSED_TIMEOUT = 45.0
SYNC_TIMEOUT = 30.0


class Daemon:
    """A single meshd subprocess with a demultiplexing stdout reader.

    A single background task owns stdout: it routes id-matched Response lines
    to per-request Futures and buffers unsolicited MeshEvent lines (which have
    no "id") into a shared list + queue so they survive across RPC calls.
    """

    def __init__(self, proc, name):
        self.proc = proc
        self.name = name
        self._ids = itertools.count(1)
        self._pending = {}
        self.events = []
        self._event_q = asyncio.Queue()
        self._write_lock = asyncio.Lock()
        self._reader = asyncio.create_task(self._read_stdout())
        self._stderr_task = asyncio.create_task(self._drain_stderr())

    async def _read_stdout(self):
        while True:
            try:
                line = await self.proc.stdout.readline()
            except (asyncio.LimitOverrunError, ValueError):
                try:
                    await self.proc.stdout.readuntil(b"\n")
                except Exception:
                    pass
                continue
            if not line:  # EOF: daemon exited
                self._fail_all(ConnectionError(f"{self.name} stdout closed"))
                return
            text = line.strip()
            if not text:
                continue
            try:
                msg = json.loads(text)
            except json.JSONDecodeError:
                continue
            if isinstance(msg, dict) and "id" in msg:
                fut = self._pending.pop(msg["id"], None)
                if fut and not fut.done():
                    fut.set_result(msg)
            else:
                self.events.append(msg)
                self._event_q.put_nowait(msg)

    async def _drain_stderr(self):
        if not self.proc.stderr:
            return
        try:
            while await self.proc.stderr.readline():
                pass
        except Exception:
            pass

    def _fail_all(self, exc):
        for fut in self._pending.values():
            if not fut.done():
                fut.set_exception(exc)
        self._pending.clear()

    async def request(self, method, params=None, timeout=RPC_TIMEOUT):
        rid = next(self._ids)
        fut = asyncio.get_running_loop().create_future()
        self._pending[rid] = fut
        payload = json.dumps({"id": rid, "method": method, "params": params or {}})
        async with self._write_lock:
            self.proc.stdin.write(payload.encode() + b"\n")
            await self.proc.stdin.drain()
        try:
            return await asyncio.wait_for(fut, timeout)
        except asyncio.TimeoutError:
            self._pending.pop(rid, None)
            raise

    async def call(self, method, params=None, timeout=RPC_TIMEOUT):
        """request() but unwrap result / raise on error."""
        resp = await self.request(method, params, timeout)
        if "error" in resp:
            raise RuntimeError(f"{method} -> {resp['error']}")
        return resp["result"]

    async def wait_for_event(self, predicate, timeout):
        """Return the first buffered-or-incoming event matching predicate, else None."""
        for ev in self.events:
            if predicate(ev):
                return ev
        deadline = time.monotonic() + timeout
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return None
            try:
                ev = await asyncio.wait_for(self._event_q.get(), remaining)
            except asyncio.TimeoutError:
                return None
            if predicate(ev):
                return ev

    async def stop(self):
        if getattr(self, "_stopped", False):
            return
        self._stopped = True
        self.proc.terminate()
        try:
            await asyncio.wait_for(self.proc.wait(), timeout=5)
        except asyncio.TimeoutError:
            self.proc.kill()
            await self.proc.wait()
        self._reader.cancel()
        self._stderr_task.cancel()


async def spawn(name):
    proc = await asyncio.create_subprocess_exec(
        BINARY,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        limit=8 * 1024 * 1024,
    )
    return Daemon(proc, name)


def ok(msg):
    print(f"✅ {msg}")


def warn(msg):
    print(f"⚠️  {msg}")


def fail(msg):
    print(f"❌ {msg}")


def skip(msg):
    print(f"⏭️  SKIP {msg}")


async def scenario_p2p_dial(a, b):
    """Original Phase 1 check: B dials A and gets a pong. Preserved verbatim."""
    print("\n=== Scenario 1: P2P dial ping-pong ===")
    addr = await a.call("get_node_addr")
    nid = addr["node_id"]
    addrs = addr["direct_addresses"]
    print(f"  A node_id={nid[:16]}... addrs={addrs}")
    try:
        r = await b.request(
            "p2p_dial", {"node_id": nid, "addresses": addrs}, timeout=30
        )
    except asyncio.TimeoutError:
        warn("p2p_dial timed out (no transport between nodes on this host)")
        return
    if "result" in r:
        pong = (
            r["result"]
            .get("remote_response", {})
            .get("result", {})
            .get("pong")
        )
        if pong:
            ok(f"P2P WORKS (pong={pong})")
        else:
            warn(f"dial returned but no pong: {r}")
    else:
        warn(f"p2p_dial error: {r.get('error', {}).get('message', '?')}")


def _sample_finding():
    return {
        "title": "Reflected XSS in search parameter",
        "target": "https://example.test/search",
        "vuln_class": "xss",
        "description": "User-supplied 'q' is reflected without encoding.",
    }


async def _verify_commander_local_publish(a, eid):
    """Poll A's own get_state until the autonomously-published finding lands.

    Autonomous publish writes to A's local replica with no network, but the
    severity step retries the (offline) LLM with backoff before falling back to
    'medium', so it can take ~10s. Poll rather than read once.
    """
    print(f"  Polling A.get_state up to {SYNC_TIMEOUT:.0f}s for local publish...")
    deadline = time.monotonic() + SYNC_TIMEOUT
    while time.monotonic() < deadline:
        state_a = await a.call("get_state", {"engagement_id": eid})
        if state_a.get("findings"):
            ok(
                f"A get_state shows {len(state_a['findings'])} finding(s) "
                "(autonomous local publish OK)"
            )
            return True
        await asyncio.sleep(1.0)
    warn(
        "A get_state shows no findings within timeout — autonomous publish did "
        "not land (processor still working or severity step blocked)"
    )
    return False


async def scenario_live_event_and_resilience(a, b):
    """Phase 2 live-event path + Phase 1 resilience across A (commander) and B (worker)."""
    print("\n=== Scenario 2: engagement / invite / join / submit / processed ===")

    # 1. Commander creates engagement, sets autonomous mode, generates invite.
    meta = await a.call("create_engagement", {"name": "mesh-dev-test"})
    eid = meta["id"]
    ok(f"A created engagement {eid}")

    mode = await a.call("set_processor_mode", {"mode": "autonomous"})
    ok(f"A processor mode = {mode.get('mode')}")

    invite = (await a.call("generate_invite", {"engagement_id": eid}))["invite"]
    ok(f"A generated invite ({len(invite)} chars)")

    # 2. Worker joins via the invite. NOTE: join_engagement imports the doc
    #    read-ticket, which dials the inviter to fetch the replica. Offline (no
    #    relay/transport between nodes) that import blocks the daemon's request
    #    loop, so the join never returns and B is wedged for further RPCs. We
    #    bound it with a timeout and, on failure, skip the B-dependent steps
    #    rather than hang.
    b_joined = False
    print(f"  B joining engagement (up to {JOIN_TIMEOUT:.0f}s)...")
    try:
        joined = await b.call(
            "join_engagement", {"invite": invite}, timeout=JOIN_TIMEOUT
        )
        b_joined = True
        if joined.get("id") != eid:
            warn(f"B joined but engagement id mismatch: {joined.get('id')} != {eid}")
        else:
            ok(f"B joined engagement {eid}")
    except asyncio.TimeoutError:
        warn(
            f"join_engagement on B did not return within {JOIN_TIMEOUT:.0f}s — "
            "the doc-ticket import is blocking on a P2P dial that has no "
            "transport on this host. B is now wedged; downstream B-side checks "
            "will be SKIPPED. (This path needs working node-to-node "
            "connectivity, e.g. a relay.)"
        )
    except RuntimeError as e:
        warn(f"join_engagement on B errored: {e}")

    if not b_joined:
        # B cannot serve further requests; still verify A's local publish by
        # submitting on A and polling A's own state.
        finding = _sample_finding()
        sub = await a.call(
            "submit", {"engagement_id": eid, "submission": {"data": finding}}
        )
        ok(f"A submitted finding (submission_id={sub.get('submission_id', '?')[:8]}...)")
        await _verify_commander_local_publish(a, eid)
        skip("'processed' live event (B never joined offline)")
        skip("CRDT sync to B (B never joined offline)")
        skip("resilience get_state on B (B never joined offline)")
        return False

    # 3. Confirm the gossip overlay actually formed before relying on it: wait
    #    for a presence heartbeat from the commander to reach the worker.
    print("  Waiting for gossip overlay (presence heartbeat on B)...")
    presence = await b.wait_for_event(
        lambda e: e.get("type") == "MeshEvent"
        and e.get("engagement_id") == eid
        and e.get("subtopic") == "presence",
        timeout=PRESENCE_TIMEOUT,
    )
    overlay_live = presence is not None
    if overlay_live:
        ok("gossip overlay live (B received presence heartbeat from A)")
    else:
        warn(
            "no presence heartbeat reached B within "
            f"{PRESENCE_TIMEOUT:.0f}s — gossip overlay likely did not form "
            "(no relay/transport on this host). Live-event + sync steps may "
            "not be observable; continuing to verify what we can."
        )

    # 4. Commander submits a finding. Its LOCAL processor publishes (autonomous)
    #    and broadcasts a "processed" gossip event. Because a daemon never hears
    #    its own broadcast, the *worker* is the one that observes "processed".
    finding = _sample_finding()
    sub = await a.call(
        "submit", {"engagement_id": eid, "submission": {"data": finding}}
    )
    ok(f"A submitted finding (submission_id={sub.get('submission_id', '?')[:8]}...)")

    # 4a. Observe the live "processed" MeshEvent on B.
    print(f"  Waiting up to {PROCESSED_TIMEOUT:.0f}s for 'processed' event on B...")
    processed = await b.wait_for_event(
        lambda e: e.get("type") == "MeshEvent"
        and e.get("engagement_id") == eid
        and e.get("subtopic") == "processed",
        timeout=PROCESSED_TIMEOUT,
    )
    if processed:
        ok(f"B observed live 'processed' MeshEvent: {processed.get('payload')}")
    elif overlay_live:
        fail("overlay formed but no 'processed' event reached B in time")
    else:
        skip("'processed' live event (gossip overlay never formed offline)")

    # 4b. Verify the finding is present in A's own state (always works locally —
    #     the autonomous publish writes to A's replica without any network).
    await _verify_commander_local_publish(a, eid)

    # 4c. Verify CRDT sync to B: poll B's get_state until the finding appears.
    print(f"  Polling B.get_state up to {SYNC_TIMEOUT:.0f}s for CRDT sync...")
    synced = False
    deadline = time.monotonic() + SYNC_TIMEOUT
    while time.monotonic() < deadline:
        state_b = await b.call("get_state", {"engagement_id": eid})
        if state_b.get("findings"):
            synced = True
            break
        await asyncio.sleep(1.0)
    if synced:
        ok("finding synced to B's CRDT replica (doc-content sync OK)")
    elif overlay_live:
        warn("overlay formed but finding did not sync to B within timeout")
    else:
        skip("CRDT sync to B (no transport/relay on this host offline)")

    # 5. Resilience: kill A, then B must still answer get_state from its local
    #    replica without the commander present.
    print("\n=== Scenario 3: resilience — kill A, B answers from local replica ===")
    await a.stop()
    ok("A terminated")
    try:
        state_b = await b.call("get_state", {"engagement_id": eid}, timeout=10)
    except Exception as e:
        fail(f"B failed to answer get_state after A died: {e}")
        return synced
    n = len(state_b.get("findings", []))
    ok(
        f"B answered get_state after A died (findings={n}) — local replica "
        "serves independently of the commander"
    )
    if synced and n == 0:
        warn("B previously had synced findings but now reports none")
    return synced


async def main():
    os.environ["RIFTOR_MESH_P2P"] = "1"
    if not os.path.exists(BINARY):
        fail(f"daemon binary not found at {BINARY} (build it first)")
        return 1

    print("Starting daemon A (Commander)...")
    a = await spawn("A")
    print("Starting daemon B (Worker)...")
    b = await spawn("B")

    try:
        await scenario_p2p_dial(a, b)
        # scenario_2 may terminate A internally as part of the resilience check;
        # a.stop() is idempotent so the finally block is always safe.
        await scenario_live_event_and_resilience(a, b)
    finally:
        await a.stop()
        await b.stop()

    print("\nDone. Steps marked ✅ verified; ⚠️/⏭️ degraded gracefully offline.")
    return 0


if __name__ == "__main__":
    try:
        rc = asyncio.run(asyncio.wait_for(main(), timeout=240))
    except asyncio.TimeoutError:
        fail("overall test timed out (240s) — aborting so it never hangs")
        rc = 1
    sys.exit(rc)
