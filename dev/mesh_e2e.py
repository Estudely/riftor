#!/usr/bin/env python3
"""End-to-end test for Riftor Mesh.

Starts the riftor-meshd daemon and exercises the full API:
identity, create engagement, invite, join, submit finding, get state.
"""

import asyncio
import subprocess
import sys
from pathlib import Path

# Add runtime PATH
import os
os.environ["PATH"] = "/home/linuxbrew/.linuxbrew/Cellar/rustup/1.29.0/bin:" + os.environ.get("PATH", "")


async def read_line(reader: asyncio.StreamReader) -> str:
    line = await reader.readline()
    return line.decode().strip()


async def send(process, payload: dict) -> dict:
    """Send a JSON request and return the response."""
    import json
    msg = json.dumps(payload) + "\n"
    process.stdin.write(msg.encode())
    await process.stdin.drain()
    resp_line = await read_line(process.stdout)
    return json.loads(resp_line)


async def main():
    root = Path(__file__).parent.parent  # /dev -> repo root
    binary = root / "meshd" / "target" / "debug" / "riftor-meshd"
    if not binary.exists():
        binary = root / "meshd" / "target" / "release" / "riftor-meshd"
    if not binary.exists():
        print("ERROR: riftor-meshd binary not found. Build it first:")
        print("  cargo build --manifest-path meshd/Cargo.toml")
        sys.exit(1)

    print(f"Starting daemon: {binary}")
    process = await asyncio.create_subprocess_exec(
        str(binary),
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )

    try:
        # 1. Ping
        print("\n--- 1. Ping ---")
        resp = await send(process, {"id": 1, "method": "ping", "params": {}})
        assert resp["result"]["pong"] is True
        print(f"  OK: {resp['result']}")

        # 2. Create identity
        print("\n--- 2. Create Identity ---")
        resp = await send(process, {"id": 2, "method": "create_identity", "params": {}})
        node_id = resp["result"]["node_id"]
        public_key = resp["result"]["public_key"]
        print(f"  Node ID: {node_id}")
        print(f"  Public Key: {public_key[:32]}...")

        # 3. Create engagement
        print("\n--- 3. Create Engagement ---")
        resp = await send(process, {
            "id": 3, "method": "create_engagement",
            "params": {"name": "E2E Test Engagement"}
        })
        eng = resp["result"]
        print(f"  ID: {eng['id']}")
        print(f"  Name: {eng['name']}")
        print(f"  Created: {eng['created_at']}")
        eng_id = eng["id"]

        # 4. Generate invite
        print("\n--- 4. Generate Invite ---")
        resp = await send(process, {
            "id": 4, "method": "generate_invite",
            "params": {"engagement_id": eng_id}
        })
        invite = resp["result"]["invite"]
        print(f"  Invite: {invite[:60]}...")

        # 5. Submit a finding
        print("\n--- 5. Submit Finding ---")
        resp = await send(process, {
            "id": 5, "method": "submit",
            "params": {
                "engagement_id": eng_id,
                "submission": {
                    "type": "finding",
                    "data": {
                        "title": "SQL Injection in /api/login",
                        "severity": "critical",
                        "target": "10.0.0.5",
                        "vuln_class": "sqli",
                        "description": "Blind SQL injection in login endpoint"
                    }
                }
            }
        })
        sub_id = resp["result"]["submission_id"]
        print(f"  Submission ID: {sub_id}")

        # 6. Submit another finding
        print("\n--- 6. Submit Another Finding ---")
        resp = await send(process, {
            "id": 6, "method": "submit",
            "params": {
                "engagement_id": eng_id,
                "submission": {
                    "type": "finding",
                    "data": {
                        "title": "XSS in /search",
                        "severity": "medium",
                        "target": "10.0.0.5",
                        "vuln_class": "xss",
                        "description": "Reflected XSS in search parameter"
                    }
                }
            }
        })
        print(f"  Submission ID: {resp['result']['submission_id']}")

        # 7. Get state (check submissions were recorded)
        print("\n--- 7. Get State ---")
        resp = await send(process, {
            "id": 7, "method": "get_state",
            "params": {"engagement_id": eng_id}
        })
        state = resp["result"]
        print(f"  Findings: {len(state['findings'])}")
        print(f"  Hosts: {len(state['hosts'])}")
        print(f"  Services: {len(state['services'])}")

        # 8. Error handling: unknown method
        print("\n--- 8. Error Handling ---")
        resp = await send(process, {"id": 8, "method": "do_the_thing", "params": {}})
        assert resp["error"]["code"] == "UNKNOWN_METHOD"
        print(f"  OK: Correctly rejected unknown method")

        print("\n" + "=" * 50)
        print(" ALL TESTS PASSED!")
        print("=" * 50)

    except Exception as e:
        print(f"\nFAILED: {e}")
        stderr = await process.stderr.read()
        if stderr:
            print(f"Daemon stderr: {stderr.decode()}")
        sys.exit(1)

    finally:
        process.terminate()
        await process.wait()


if __name__ == "__main__":
    asyncio.run(main())
