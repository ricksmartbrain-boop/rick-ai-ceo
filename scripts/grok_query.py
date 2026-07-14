#!/usr/bin/env python3
"""
grok_query.py - Query Grok via Chrome CDP with live web search
Usage: python3 grok_query.py "your question"
"""

import json, urllib.request, socket, time, os, base64, struct, sys

PORT = 9222
QUERY = sys.argv[1] if len(sys.argv) > 1 else "What is today's top AI news?"

def get_tab():
    tabs = json.loads(urllib.request.urlopen(f'http://localhost:{PORT}/json').read())
    return next((t for t in tabs if t['type'] == 'page' and 'x.com' in t.get('url', '')), None)

def make_ws(ws_url):
    ws_url = ws_url.replace('ws://', '')
    parts = ws_url.split('/', 1)
    host, port_s = parts[0].split(':')
    path = '/' + parts[1]
    sock = socket.socket()
    sock.connect((host, int(port_s)))
    sock.settimeout(30)
    key = base64.b64encode(os.urandom(16)).decode()
    sock.send(f"GET {path} HTTP/1.1\r\nHost: {parts[0]}\r\nUpgrade: websocket\r\nConnection: Upgrade\r\nSec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n\r\n".encode())
    sock.recv(4096)
    return sock

def ws_send(sock, cmd):
    msg = json.dumps(cmd).encode()
    ln = len(msg)
    mask = os.urandom(4)
    masked = bytes(msg[i] ^ mask[i%4] for i in range(ln))
    if ln < 126: frame = bytes([0x81, ln | 0x80]) + mask + masked
    elif ln < 65536: frame = bytes([0x81, 126]) + struct.pack('>H', ln) + mask + masked
    sock.send(frame)

def ws_recv(sock):
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

def cdp(sock, cmd_id, method, params, drain=True):
    ws_send(sock, {"id": cmd_id, "method": method, "params": params})
    time.sleep(0.1)
    if drain:
        try:
            sock.settimeout(1)
            return ws_recv(sock)
        except:
            pass
        finally:
            sock.settimeout(30)
    return {}

# Step 1: Navigate to fresh Grok
tab = get_tab()
sock = make_ws(tab['webSocketDebuggerUrl'])
cdp(sock, 1, "Page.navigate", {"url": "https://x.com/i/grok"})
sock.close()
time.sleep(5)

# Step 2: Get fresh tab reference after navigation
tab = get_tab()
sock = make_ws(tab['webSocketDebuggerUrl'])

# Step 3: Type query
cdp(sock, 10, "Input.dispatchMouseEvent", {"type": "mousePressed", "x": 760, "y": 750, "button": "left", "clickCount": 1})
time.sleep(0.5)
cdp(sock, 11, "Input.dispatchMouseEvent", {"type": "mouseReleased", "x": 760, "y": 750, "button": "left", "clickCount": 1})
time.sleep(0.5)

for i, ch in enumerate(QUERY):
    cdp(sock, 100+i, "Input.dispatchKeyEvent", {"type": "char", "text": ch})
    time.sleep(0.015)

time.sleep(0.3)
cdp(sock, 999, "Input.dispatchKeyEvent", {"type": "keyDown", "key": "Enter", "code": "Enter", "text": "\r"})
cdp(sock, 1000, "Input.dispatchKeyEvent", {"type": "keyUp", "key": "Enter", "code": "Enter"})
sock.close()

print("Query submitted. Waiting 25s for Grok response...", file=sys.stderr)
time.sleep(25)

# Step 4: Read response with fresh connection
tab = get_tab()
sock = make_ws(tab['webSocketDebuggerUrl'])
ws_send(sock, {"id": 2000, "method": "Runtime.evaluate", "params": {"expression": "document.body.innerText", "returnByValue": True}})
time.sleep(3)
try:
    resp = ws_recv(sock)
    text = resp.get('result',{}).get('result',{}).get('value','')
    
    # Extract Grok's answer (skip UI chrome, find content after query)
    idx = text.find(QUERY[:40])
    if idx > -1:
        answer = text[idx + len(QUERY):].strip()
        # Remove trailing UI elements
        for stop in ['Think Harder', 'Auto\n', '14 posts', '23 web']:
            if stop in answer:
                answer = answer[:answer.index(stop)]
        print(answer.strip()[:2000])
    else:
        print(text[300:2500])
except Exception as e:
    print(f"Error reading response: {e}", file=sys.stderr)
finally:
    sock.close()
