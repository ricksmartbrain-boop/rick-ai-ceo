import asyncio, json, websockets, urllib.request

tabs = json.loads(urllib.request.urlopen("http://localhost:9222/json").read())
ph_tab = next((t for t in tabs if "producthunt.com" in t.get("url", "") and "webSocketDebuggerUrl" in t), None)

async def run():
    ws_url = ph_tab["webSocketDebuggerUrl"]
    async with websockets.connect(ws_url, max_size=10_000_000) as ws:
        msg_id = 1
        async def send(method, params={}):
            nonlocal msg_id
            await ws.send(json.dumps({"id": msg_id, "method": method, "params": params}))
            msg_id += 1
            while True:
                r = json.loads(await ws.recv())
                if r.get("id") == msg_id - 1:
                    return r
        
        await send("Page.navigate", {"url": "https://www.producthunt.com/login"})
        await asyncio.sleep(3)
        result = await send("Runtime.evaluate", {"expression": "window.location.href", "returnByValue": True})
        print("Navigated to:", result.get("result", {}).get("result", {}).get("value"))

asyncio.run(run())
