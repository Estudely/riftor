#!/usr/bin/env python3
"""Live test: submit finding and watch the processor pipeline with real LLM."""

import asyncio
import json
import os
import sys
from pathlib import Path

os.environ["PATH"] = "/home/linuxbrew/.linuxbrew/Cellar/rustup/1.29.0/bin:" + os.environ.get("PATH", "")


async def read_line(reader: asyncio.StreamReader) -> str:
    line = await reader.readline()
    return line.decode().strip()


async def send(process, payload: dict) -> dict:
    msg = json.dumps(payload) + "\n"
    process.stdin.write(msg.encode())
    await process.stdin.drain()
    resp_line = await read_line(process.stdout)
    return json.loads(resp_line)


async def main():
    binary = Path(__file__).parent.parent / "meshd" / "target" / "debug" / "riftor-meshd"
    if not binary.exists():
        print("ERROR: Build first: cargo build --manifest-path meshd/Cargo.toml")
        sys.exit(1)

    print(f"Starting daemon with DeepSeek LLM...")
    process = await asyncio.create_subprocess_exec(
        str(binary),
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )

    try:
        # 1. Ping
        resp = await send(process, {"id": 1, "method": "ping", "params": {}})
        assert resp["result"]["pong"], "Ping failed"

        # 2. Create identity
        resp = await send(process, {"id": 2, "method": "create_identity", "params": {}})
        node_id = resp["result"]["node_id"]
        print(f"Identity: {node_id}")

        # 3. Create engagement
        resp = await send(process, {
            "id": 3, "method": "create_engagement",
            "params": {"name": "Live LLM Test"}
        })
        eng_id = resp["result"]["id"]
        print(f"Engagement: {eng_id}")

        # 4. Check queue (should be empty)
        resp = await send(process, {
            "id": 4, "method": "get_queue_stats",
            "params": {"engagement_id": eng_id}
        })
        stats = resp.get("result", {})
        print(f"Queue before: pending={stats.get('pending', '?')}, completed={stats.get('completed', '?')}")

        # 5. Submit a finding (this triggers the processor)
        print("\nSubmitting finding: 'SQL Injection in /api/login'...")
        resp = await send(process, {
            "id": 5, "method": "submit",
            "params": {
                "engagement_id": eng_id,
                "submission": {
                    "type": "finding",
                    "data": {
                        "title": "SQL Injection in /api/login",
                        "severity": "high",
                        "target": "10.0.0.5",
                        "vuln_class": "sqli",
                        "description": "Blind SQL injection in login endpoint allows data extraction via boolean-based timing attacks"
                    }
                }
            }
        })
        sub_id = resp["result"]["submission_id"]
        print(f"Submission ID: {sub_id}")

        # 6. Wait for processor to work (LLM call takes ~1-3s)
        print("Waiting for processor to process...")
        await asyncio.sleep(5)

        # 7. Check queue stats
        resp = await send(process, {
            "id": 6, "method": "get_queue_stats",
            "params": {"engagement_id": eng_id}
        })
        stats = resp.get("result", {})
        print(f"Queue after: pending={stats.get('pending', '?')}, completed={stats.get('completed', '?')}, failed={stats.get('failed', '?')}")

        # 8. Check state - findings should be published
        resp = await send(process, {
            "id": 7, "method": "get_state",
            "params": {"engagement_id": eng_id}
        })
        state = resp.get("result", {})
        findings = state.get("findings", [])
        print(f"Published findings: {len(findings)}")
        if findings:
            for f in findings:
                if isinstance(f, dict):
                    print(f"  - {f.get('title', 'N/A')} [{f.get('severity', 'N/A')}] (decision: {f.get('decision', 'N/A')})")

        # 9. Submit a second, similar finding (test dedup)
        print("\nSubmitting finding: 'SQL Injection in /api/admin'...")
        resp = await send(process, {
            "id": 8, "method": "submit",
            "params": {
                "engagement_id": eng_id,
                "submission": {
                    "type": "finding",
                    "data": {
                        "title": "SQL Injection in /api/admin",
                        "severity": "high",
                        "target": "10.0.0.5",
                        "vuln_class": "sqli",
                        "description": "Error-based SQL injection in admin panel search"
                    }
                }
            }
        })
        sub_id2 = resp["result"]["submission_id"]
        print(f"Submission ID: {sub_id2}")
        print("Waiting for processor...")
        await asyncio.sleep(5)

        resp = await send(process, {
            "id": 9, "method": "get_state",
            "params": {"engagement_id": eng_id}
        })
        state = resp.get("result", {})
        findings = state.get("findings", [])
        print(f"Published findings: {len(findings)}")
        for f in findings:
            if isinstance(f, dict):
                print(f"  - {f.get('title', 'N/A')} [{f.get('severity', 'N/A')}] (decision: {f.get('decision', 'N/A')})")

        # 10. Submit an XSS (should be clearly new)
        print("\nSubmitting finding: 'XSS in /search'...")
        resp = await send(process, {
            "id": 10, "method": "submit",
            "params": {
                "engagement_id": eng_id,
                "submission": {
                    "type": "finding",
                    "data": {
                        "title": "Reflected XSS in /search",
                        "severity": "medium",
                        "target": "10.0.0.5",
                        "vuln_class": "xss",
                        "description": "Reflected XSS via search query parameter"
                    }
                }
            }
        })
        print(f"Submission ID: {resp['result']['submission_id']}")
        print("Waiting for processor...")
        await asyncio.sleep(5)

        resp = await send(process, {
            "id": 11, "method": "get_state",
            "params": {"engagement_id": eng_id}
        })
        state = resp.get("result", {})
        findings = state.get("findings", [])
        print(f"Published findings: {len(findings)}")
        for f in findings:
            if isinstance(f, dict):
                print(f"  - {f.get('title', 'N/A')} [{f.get('severity', 'N/A')}] (decision: {f.get('decision', 'N/A')})")

        print("\n" + "=" * 50)
        print(" LIVE TEST COMPLETE")
        print("=" * 50)

    except Exception as e:
        print(f"\nFAILED: {e}")
        stderr = await process.stderr.read()
        if stderr:
            print(f"\nDaemon stderr:\n{stderr.decode()[-500:]}")
        sys.exit(1)

    finally:
        process.terminate()
        await process.wait()


if __name__ == "__main__":
    asyncio.run(main())
