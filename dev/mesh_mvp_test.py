#!/usr/bin/env python3
"""MVP test: Commander listens P2P, Worker submits finding over iroh QUIC."""

import asyncio, json, os, subprocess, sys, time

BINARY = "./meshd/target/debug/riftor-meshd"
ENV = {"RIFTOR_MESH_P2P": "1", "PATH": "/home/linuxbrew/.linuxbrew/Cellar/rustup/1.29.0/bin:/usr/bin"}

async def send(proc, payload):
    proc.stdin.write((json.dumps(payload) + "\n").encode())
    await proc.stdin.drain()
    line = await proc.stdout.readline()
    return json.loads(line.decode())

async def main():
    # 1. Start Commander (P2P listener + processor)
    print("=== COMMANDER ===")
    cmd = await asyncio.create_subprocess_exec(
        BINARY, stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE, env=ENV)
    
    # Create identity and engagement
    ident = await send(cmd, {"id":1,"method":"create_identity","params":{}})
    eng = await send(cmd, {"id":2,"method":"create_engagement","params":{"name":"MVP Test"}})
    eng_id = eng["result"]["id"]
    addrs = (await send(cmd, {"id":3,"method":"get_node_addr","params":{}}))["result"]
    nid = addrs["node_id"]; addr_list = addrs["direct_addresses"]
    print(f"  Engagement: {eng_id}")
    print(f"  P2P addr: {nid[:16]}...")
    cmd.stdin.close()  # Commander stays alive for P2P

    # 2. Start Worker (dialer)
    print("\n=== WORKER ===")
    worker = await asyncio.create_subprocess_exec(
        BINARY, stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE, env=ENV)

    # Worker submits finding to Commander over P2P
    r = await send(worker, {
        "id": 1, "method": "p2p_dial",
        "params": {"node_id": nid, "addresses": addr_list}
    })
    pong = r.get("result",{}).get("remote_response",{}).get("result",{}).get("pong")
    print(f"  P2P ping: {'✅' if pong else '❌ ' + str(r)[:80]}")

    # Now actually submit a finding over P2P
    print("\n  Submitting finding over P2P...")
    # Use p2p_submit_remote if available, otherwise dial and send manually
    r = await send(worker, {
        "id": 2, "method": "p2p_submit_remote",
        "params": {
            "node_id": nid, "addresses": addr_list,
            "engagement_id": eng_id,
            "submission": {"type":"finding", "data":{
                "title": "SQLi in /login (from P2P)",
                "severity": "critical",
                "target": "10.0.0.5",
                "vuln_class": "sqli",
                "description": "Blind SQL injection via P2P worker"
            }}
        }
    })
    print(f"  Submit result: {r.get('result',{}).get('status','?')} / {r.get('error',{}).get('message','?')}")
    worker.stdin.close()

    # 3. Check Commander state (new daemon to query)
    print("\n=== VERIFY ===")
    await asyncio.sleep(2)  # Let processor work
    verify = await asyncio.create_subprocess_exec(
        BINARY, stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE, env=ENV)
    state = await send(verify, {"id":1,"method":"get_state","params":{"engagement_id":eng_id}})
    findings = state.get("result",{}).get("findings",[])
    print(f"  Findings in docs: {len(findings)}")
    for f in findings:
        if isinstance(f, dict):
            print(f"  ✅ {f.get('title','?')} [{f.get('severity','?')}] (decision: {f.get('decision','?')})")
    verify.stdin.close()
    verify.terminate()

    cmd.terminate(); worker.terminate()
    await asyncio.wait([cmd.wait(), worker.wait(), verify.wait()], timeout=2)

    if findings:
        print(f"\n{'='*50}\n MVP WORKS! Finding synced over P2P.\n{'='*50}")
    else:
        print(f"\n⚠️  No findings synced (processor may need more time or LLM key)")

asyncio.run(main())
