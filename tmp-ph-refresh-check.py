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
        
        # Hard reload the page
        print("Reloading page...")
        await send("Page.reload", {"ignoreCache": True})
        await asyncio.sleep(6)
        
        # Check login status
        result = await send("Runtime.evaluate", {
            "expression": """
            (() => {
                try {
                    const cache = window.apolloClient?.cache?.extract();
                    if (!cache) return 'no apollo';
                    const viewer = cache['Viewer'];
                    if (!viewer) return 'no viewer';
                    return JSON.stringify({isLoggedIn: viewer.isLoggedIn, username: cache[viewer.user?.['__ref']]?.username});
                } catch(e) { return 'ERROR: ' + e.message; }
            })()
            """,
            "returnByValue": True
        })
        print("Login status:", result.get("result", {}).get("result", {}).get("value"))

asyncio.run(run())
