#!/bin/bash
# MVP: Full P2P flow with Commander running in background
set -e
BIN=./meshd/target/debug/riftor-meshd
export PATH="/home/linuxbrew/.linuxbrew/Cellar/rustup/1.29.0/bin:$HOME/.cargo/bin:$PATH"
export DEEPSEEK_API_KEY="sk-a1a063c2ec174ffb9f0869a3dc012dc4"

# Create a FIFO for sending commands to the Commander
FIFO=/tmp/mvp_fifo
rm -f $FIFO && mkfifo $FIFO

# Start Commander with P2P, input from FIFO, output to a file
RIFTOR_MESH_P2P=1 $BIN < $FIFO > /tmp/cmdout 2>/tmp/cmderr &
CMD_PID=$!
sleep 1

# Helper: send command via FIFO, read from output file
send_cmd() {
    echo "$1" > $FIFO
    # Wait a bit for response, then read last line
    sleep 0.2
    tail -1 /tmp/cmdout
}

echo "=== SETUP ==="
ENG=$(send_cmd '{"id":1,"method":"create_engagement","params":{"name":"mvp"}}')
ENG_ID=$(echo "$ENG" | python3 -c "import sys,json; print(json.load(sys.stdin)['result']['id'])")
echo "  Engagement: $ENG_ID"

ADDR=$(send_cmd '{"id":2,"method":"get_node_addr","params":{}}')
NID=$(echo "$ADDR" | python3 -c "import sys,json; print(json.load(sys.stdin)['result']['node_id'])")
ADDRS=$(echo "$ADDR" | python3 -c "import sys,json; print(json.dumps(json.load(sys.stdin)['result']['direct_addresses']))")
echo "  P2P: ${NID:0:16}... @ $ADDRS"

echo ""
echo "=== WORKER DIALS + SUBMITS ==="
# Worker is a SEPARATE daemon process that dials the Commander
SUB=$(echo "{\"id\":1,\"method\":\"p2p_submit_remote\",\"params\":{\"node_id\":\"$NID\",\"addresses\":$ADDRS,\"engagement_id\":\"$ENG_ID\",\"submission\":{\"type\":\"finding\",\"data\":{\"title\":\"SQLi over P2P\",\"severity\":\"critical\",\"target\":\"10.0.0.5\",\"vuln_class\":\"sqli\",\"description\":\"Blind SQLi via iroh P2P\"}}}}" | $BIN 2>/dev/null)
echo "  Submit result: $SUB"

echo ""
echo "=== WAITING FOR LLM PROCESSOR ==="
sleep 5

echo ""
echo "=== CHECK COMMANDER STATE ==="
STATE=$(send_cmd '{"id":3,"method":"get_state","params":{"engagement_id":"'$ENG_ID'"}}')
echo "$STATE" | python3 -c "
import sys, json
r = json.load(sys.stdin)
fs = r.get('result',{}).get('findings',[])
print(f'  Findings in Commander: {len(fs)}')
for f in fs:
    if isinstance(f,dict):
        print(f'  ✅ {f.get(\"title\",\"?\")} [{f.get(\"severity\",\"?\")}] (decision: {f.get(\"decision\",\"?\")})')
if len(fs) > 0:
    print()
    print('='*50)
    print(' MVP WORKS!')
    print(' Finding → P2P → Commander → AI → Docs')
    print('='*50)
"

kill $CMD_PID 2>/dev/null
rm -f $FIFO
