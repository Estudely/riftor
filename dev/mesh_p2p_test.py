#!/usr/bin/env python3
"""Cross-machine P2P test: keeps daemon A alive for P2P connections."""

import asyncio
import json

async def send(proc, payload):
    proc.stdin.write((json.dumps(payload) + "\n").encode())
    await proc.stdin.drain()
    line = await proc.stdout.readline()
    return json.loads(line.decode())

async def main():
    print("Starting daemon A (listener with P2P router)...")
    proc_a = await asyncio.create_subprocess_exec(
        "./meshd/target/debug/riftor-meshd",
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )

    # Get A's NodeId + addresses
    a_info = await send(proc_a, {"id": 1, "method": "get_node_addr", "params": {}})
    node_id_a = a_info["result"]["node_id"]
    addresses_a = a_info["result"]["direct_addresses"]
    print(f"  A NodeId: {node_id_a[:16]}...")
    print(f"  A Addrs: {addresses_a}")

    print("Starting daemon B (dialer)...")
    proc_b = await asyncio.create_subprocess_exec(
        "./meshd/target/debug/riftor-meshd",
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )

    # B dials A with addresses
    result = await send(proc_b, {
        "id": 1,
        "method": "p2p_dial",
        "params": {"node_id": node_id_a, "addresses": addresses_a}
    })

    if "result" in result and "remote_response" in result["result"]:
        pong = result["result"]["remote_response"].get("result", {}).get("pong")
        if pong:
            print("\n✅ P2P WORKS! Remote responded with pong=true")
        else:
            print(f"\n⚠️  Connected but: {pong}")
    elif "error" in result:
        print(f"\n❌ Failed: {result['error']['message']}")
    else:
        print(f"\n⚠️  Unexpected: {json.dumps(result, indent=2)}")

    proc_a.terminate()
    proc_b.terminate()
    await proc_a.wait()
    await proc_b.wait()

if __name__ == "__main__":
    asyncio.run(main())
