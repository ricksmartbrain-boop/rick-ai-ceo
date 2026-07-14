#!/bin/bash
# grok-search.sh — Query Grok with live web search via Chrome CDP
# Usage: grok-search.sh "your question here"
# Returns: Grok's answer with live web results

QUERY="${1:-What is today's date and top AI news?}"
PORT=9222

# Check if Chrome debug instance is running
if ! curl -s "http://localhost:$PORT/json" > /dev/null 2>&1; then
  # Launch Chrome with the rick profile (has X session cookies)
  mkdir -p /tmp/chrome-rick-profile/Default
  
  # Copy cookies from Default profile
  cp "/Users/rickthebot/Library/Application Support/Google/Chrome/Default/Cookies" /tmp/chrome-rick-profile/Default/ 2>/dev/null
  
  # Write prefs with JS Apple Events enabled
  python3 -c "
import json
prefs = {'allow_js_apple_events': True, 'profile': {'name': 'Rick'}}
json.dump(prefs, open('/tmp/chrome-rick-profile/Default/Preferences', 'w'))
"
  
  /Applications/Google\ Chrome.app/Contents/MacOS/Google\ Chrome \
    --remote-debugging-port=$PORT \
    --user-data-dir=/tmp/chrome-rick-profile \
    --no-first-run \
    --no-default-browser-check \
    "https://x.com/i/grok" > /tmp/chrome-grok.log 2>&1 &
  
  sleep 8
fi

python3 << PYEOF
import json, urllib.request, socket, time, os, base64, struct

PORT = $PORT
QUERY = """$QUERY"""

tabs = json.loads(urllib.request.urlopen(f'http://localhost:{PORT}/json').read())
tab = next((t for t in tabs if t['type'] == 'page' and 'x.com' in t.get('url','')), None)
if not tab:
    print("ERROR: No X.com tab found")
    exit(1)

ws_url = tab['webSocketDebuggerUrl'].replace('ws://', '')
parts = ws_url.split('/', 1)
host, port_s = parts[0].split(':')
path = '/' + parts[1]
sock = socket.socket()
sock.connect((host, int(port_s)))
sock.settimeout(60)
key = base64.b64encode(os.urandom(16)).decode()
sock.send(f"GET /{parts[1]} HTTP/1.1\r\nHost: {parts[0]}\r\nUpgrade: websocket\r\nConnection: Upgrade\r\nSec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n\r\n".encode())
sock.recv(4096)

def ws_send(cmd):
    msg = json.dumps(cmd).encode()
    ln = len(msg)
    mask = os.urandom(4)
    masked = bytes(msg[i] ^ mask[i%4] for i in range(ln))
    if ln < 126: frame = bytes([0x81, ln | 0x80]) + mask + masked
    elif ln < 65536: frame = bytes([0x81, 126]) + struct.pack('>H', ln) + mask + masked
    sock.send(frame)

def ws_recv():
    h = b''
    while len(h) < 2: h += sock.recv(2 - len(h))
    ln = h[1] & 0x7F
    if ln == 126:
        b = b''
        while len(b) < 2: b += sock.recv(2-len(b))
        ln = struct.unpack('>H', b)[0]
    elif ln == 127:
        b = b''
        while len(b) < 8: b += sock.recv(8-len(b))
        ln = struct.unpack('>Q', b)[0]
    data = b''
    while len(data) < ln:
        data += sock.recv(min(65536, ln - len(data)))
    return json.loads(data.decode())

# Navigate to fresh Grok
ws_send({"id": 1, "method": "Page.navigate", "params": {"url": "https://x.com/i/grok"}})
time.sleep(5)
ws_recv()

# Click input and type query
ws_send({"id": 2, "method": "Input.dispatchMouseEvent", "params": {"type": "mousePressed", "x": 760, "y": 750, "button": "left", "clickCount": 1}})
ws_recv()
time.sleep(0.5)
ws_send({"id": 3, "method": "Input.dispatchMouseEvent", "params": {"type": "mouseReleased", "x": 760, "y": 750, "button": "left", "clickCount": 1}})
ws_recv()
time.sleep(0.5)

for i, ch in enumerate(QUERY):
    ws_send({"id": 100+i, "method": "Input.dispatchKeyEvent", "params": {"type": "char", "text": ch}})
    ws_recv()
    time.sleep(0.01)

time.sleep(0.3)
ws_send({"id": 999, "method": "Input.dispatchKeyEvent", "params": {"type": "keyDown", "key": "Enter", "code": "Enter", "text": "\r"}})
ws_recv()
time.sleep(0.3)
ws_send({"id": 1000, "method": "Input.dispatchKeyEvent", "params": {"type": "keyUp", "key": "Enter", "code": "Enter"}})
ws_recv()

# Wait for response
print("Waiting for Grok response...", flush=True)
time.sleep(25)

ws_send({"id": 2000, "method": "Runtime.evaluate", "params": {"expression": "document.body.innerText", "returnByValue": True}})
resp = ws_recv()
text = resp.get('result',{}).get('result',{}).get('value','')

# Extract just the Grok response (skip nav chrome)
lines = text.split('\n')
answer_start = False
answer_lines = []
for line in lines:
    if QUERY[:30] in line:
        answer_start = True
        continue
    if answer_start and line.strip() and 'keyboard shortcuts' not in line:
        answer_lines.append(line)
    if answer_start and len(answer_lines) > 50:
        break

print('\n'.join(answer_lines[:40]))
sock.close()
PYEOF
