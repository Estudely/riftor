#!/usr/bin/env python3
"""Cross-machine P2P test: Linux listens, Mac dials."""

import subprocess, json, time, sys

LINUX_BIN = "./meshd/target/debug/riftor-meshd"
MAC_BIN = "./meshd/target/release/riftor-meshd"
MAC_SSH = ["sshpass", "-p", "3141592", "ssh", "-o", "StrictHostKeyChecking=no", "-o", "ConnectTimeout=5", "amanverasia@macbook-old"]

print("Starting Linux daemon (listener)...")
lin = subprocess.Popen([LINUX_BIN], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    env={"RIFTOR_MESH_P2P": "1", "PATH": "/home/linuxbrew/.linuxbrew/Cellar/rustup/1.29.0/bin:/usr/bin"})
time.sleep(0.5)

# Get Linux addresses
lin.stdin.write(b'{"id":1,"method":"get_node_addr","params":{}}\n')
lin.stdin.flush()
linfo = json.loads(lin.stdout.readline().decode())
nid = linfo["result"]["node_id"]
addrs = json.dumps(linfo["result"]["direct_addresses"])
print(f"  Linux: {nid} @ {addrs}")

print("Dialing from Mac...")
cmd = f'export PATH="$HOME/.cargo/bin:$HOME/.local/bin:$PATH" && export RIFTOR_MESH_P2P=1 && cd ~/iroh-work/riftor && (echo \'{{"id":1,"method":"p2p_dial","params":{{"node_id":"{nid}","addresses":{addrs}}}}}}\'; sleep 2) | timeout 8 {MAC_BIN} 2>/dev/null'
out = subprocess.run([*MAC_SSH, cmd], capture_output=True, text=True, timeout=15)
resp = out.stdout.strip()
if resp:
    r = json.loads(resp)
    pong = r.get("result",{}).get("remote_response",{}).get("result",{}).get("pong")
    if pong:
        print(f"\n✅ CROSS-MACHINE P2P WORKS! (pong={pong})")
    else:
        print(f"\n⚠️  {r}")
else:
    print(f"\n❌ No response (stderr: {out.stderr[:200]})")

lin.terminate(); lin.wait()
