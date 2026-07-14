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
        
        await asyncio.sleep(1)
        
        # Check login status first
        result = await send("Runtime.evaluate", {
            "expression": """
            (() => {
                try {
                    const cache = window.apolloClient.cache.extract();
                    return cache['Viewer'] ? cache['Viewer']['isLoggedIn'] : 'no viewer';
                } catch(e) { return 'ERROR: ' + e.message; }
            })()
            """,
            "returnByValue": True
        })
        logged_in = result.get("result", {}).get("result", {}).get("value")
        print(f"isLoggedIn: {logged_in}")
        
        if not logged_in:
            print("NOT LOGGED IN — aborting")
            return
        
        # Delete both duplicate comments via GraphQL
        COMMENT_IDS = ["5296663", "5297039"]
        
        for comment_id in COMMENT_IDS:
            print(f"\nDeleting comment {comment_id}...")
            result = await send("Runtime.evaluate", {
                "expression": f"""
                fetch('/frontend/graphql', {{
                    method: 'POST',
                    headers: {{'content-type': 'application/json'}},
                    credentials: 'include',
                    body: JSON.stringify({{
                        operationName: 'deleteComment',
                        query: 'mutation deleteComment($id: ID!) {{ deleteComment(id: $id) {{ id }} }}',
                        variables: {{ id: '{comment_id}' }}
                    }})
                }}).then(r => r.json()).then(d => JSON.stringify(d)).catch(e => 'ERR: ' + e.message)
                """,
                "returnByValue": False,
                "awaitPromise": True
            })
            resp = result.get("result", {}).get("result", {}).get("value", "")
            print(f"  Response: {resp[:200]}")
            await asyncio.sleep(1)

asyncio.run(run())
