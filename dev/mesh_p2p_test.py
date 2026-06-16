#!/usr/bin/env python3
"""Cross-process P2P test. Needs: RIFTOR_MESH_P2P=1 cargo build"""

import asyncio, json, os

async def send(proc, payload):
    proc.stdin.write((json.dumps(payload) + "\n").encode())
    await proc.stdin.drain()
    line = await proc.stdout.readline()
    return json.loads(line.decode())

async def main():
    os.environ["RIFTOR_MESH_P2P"] = "1"
    binary = "./meshd/target/debug/riftor-meshd"

    print("Starting daemon A (P2P listener)...")
    proc_a = await asyncio.create_subprocess_exec(
        binary, stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
    )
    a = await send(proc_a, {"id":1,"method":"get_node_addr","params":{}})
    nid = a["result"]["node_id"]; addrs = a["result"]["direct_addresses"]
    print(f"  A: {nid[:16]}... addrs={addrs}")
    proc_a.stdin.close()

    print("Starting daemon B (dialer)...")
    proc_b = await asyncio.create_subprocess_exec(
        binary, stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
    )
    r = await send(proc_b, {"id":1,"method":"p2p_dial","params":{"node_id":nid,"addresses":addrs}})
    proc_b.stdin.close()

    if "result" in r:
        pong = r["result"].get("remote_response",{}).get("result",{}).get("pong")
        print(f"\n{'✅ P2P WORKS! (pong={pong})' if pong else f'⚠️ {r}'}")
    else:
        print(f"\n❌ {r.get('error',{}).get('message','?')}")

    proc_a.terminate(); proc_b.terminate()
    await asyncio.wait([proc_a.wait(), proc_b.wait()], timeout=2)

asyncio.run(main())
