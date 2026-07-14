import asyncio, json, base64
import websockets

TITLE = "run your SaaS product launch across 10 platforms in 48 hours"
PAGE_ID = "96308E6628F8A9CB72A0922B5430D810"

async def s(ws, msg, wid, timeout=25):
    await ws.send(json.dumps(msg))
    while True:
        r = json.loads(await asyncio.wait_for(ws.recv(), timeout=timeout))
        if r.get('id') == wid: return r

async def js(ws, expr, wid):
    r = await s(ws, {'id':wid,'method':'Runtime.evaluate','params':{'expression':expr}}, wid)
    return r['result'].get('result',{}).get('value','')

async def click(ws, x, y, wid):
    await s(ws, {'id':wid,'method':'Input.dispatchMouseEvent','params':{'type':'mousePressed','x':float(x),'y':float(y),'button':'left','clickCount':1}}, wid)
    await s(ws, {'id':wid+1,'method':'Input.dispatchMouseEvent','params':{'type':'mouseReleased','x':float(x),'y':float(y),'button':'left','clickCount':1}}, wid+1)

async def pick_select(ws, input_id, pref_text, base):
    await js(ws, 'var i=document.getElementById("' + input_id + '");if(i){i.focus();i.click();}', base)
    await asyncio.sleep(0.5)
    await s(ws, {'id':base+2,'method':'Input.dispatchKeyEvent','params':{'type':'keyDown','key':'ArrowDown','windowsVirtualKeyCode':40}}, base+2)
    await s(ws, {'id':base+3,'method':'Input.dispatchKeyEvent','params':{'type':'keyUp','key':'ArrowDown','windowsVirtualKeyCode':40}}, base+3)
    await asyncio.sleep(1.5)
    prefix = input_id.replace("-input","")
    raw = await js(ws, '''
        var opts=Array.from(document.querySelectorAll("[id^='''' + prefix + '''-option']")).filter(e=>!e.innerText?.includes("SELECT")&&!e.innerText?.includes("CHOOSE..."));
        var el=opts.find(e=>e.innerText?.toUpperCase().includes("''' + pref_text.upper() + '''"));
        if(!el)el=opts[0];
        if(el){el.scrollIntoView({block:"center",behavior:"instant"});var r=el.getBoundingClientRect();
          JSON.stringify({x:r.left+r.width/2,y:r.top+r.height/2,text:el.innerText?.trim().substring(0,50),
          inView:r.top>30&&r.top<window.innerHeight-30,all:opts.map(o=>o.innerText?.trim().substring(0,30)).slice(0,8)});}
        else JSON.stringify({notfound:true,all:opts.map(o=>o.innerText?.trim()).slice(0,8)})
    ''', base+4)
    try:
        c = json.loads(raw)
        if c.get('inView'):
            await click(ws, c['x'], c['y'], base+5)
            await asyncio.sleep(1.2)
            return c.get('text',''), c.get('all',[])
        return "not_inview", c.get('all',[])
    except:
        return "error", []

async def screenshot(ws, path, wid):
    r = await s(ws, {'id':wid,'method':'Page.captureScreenshot','params':{'format':'jpeg','quality':55}}, wid)
    with open(path,'wb') as f: f.write(base64.b64decode(r['result'].get('data','')))

async def run():
    uri = 'ws://localhost:9224/devtools/page/' + PAGE_ID
    async with websockets.connect(uri, max_size=10_000_000) as ws:
        # NAVIGATE
        await s(ws, {'id':1,'method':'Page.navigate','params':{'url':'https://www.fiverr.com/users/rick_smartest/manage_gigs/new?wizard=0&tab=general'}}, 1)
        await asyncio.sleep(9)
        print('✓ Loaded')

        # TITLE
        await js(ws, 'var ta=document.querySelector("textarea[placeholder]");Object.getOwnPropertyDescriptor(window.HTMLTextAreaElement.prototype,"value").set.call(ta,' + json.dumps(TITLE) + ');ta.dispatchEvent(new Event("input",{bubbles:true}));', 10)
        print('✓ Title')

        # CATEGORY (react-select-2-option-5 = PROGRAMMING & TECH)
        await js(ws, 'document.getElementById("react-select-2-input").focus();document.getElementById("react-select-2-input").click();', 20)
        await asyncio.sleep(0.5)
        await s(ws, {'id':21,'method':'Input.dispatchKeyEvent','params':{'type':'keyDown','key':'ArrowDown','windowsVirtualKeyCode':40}}, 21)
        await s(ws, {'id':22,'method':'Input.dispatchKeyEvent','params':{'type':'keyUp','key':'ArrowDown','windowsVirtualKeyCode':40}}, 22)
        await asyncio.sleep(1.5)
        c2_raw = await js(ws, 'var e=document.getElementById("react-select-2-option-5");e.scrollIntoView({block:"center",behavior:"instant"});var r=e.getBoundingClientRect();JSON.stringify({x:r.left+r.width/2,y:r.top+r.height/2,inView:r.top>0&&r.top<window.innerHeight})', 23)
        c2 = json.loads(c2_raw)
        await click(ws, c2['x'], c2['y'], 24)
        await asyncio.sleep(2.5)
        cat_val = await js(ws, 'document.querySelector("input[name*=category_id]")?.value', 25)
        print('✓ Category id=' + str(cat_val))

        # SUBCATEGORY (react-select-3-option-5 = AI DEVELOPMENT)
        await js(ws, 'document.getElementById("react-select-3-input").focus();document.getElementById("react-select-3-input").click();', 30)
        await asyncio.sleep(0.5)
        await s(ws, {'id':31,'method':'Input.dispatchKeyEvent','params':{'type':'keyDown','key':'ArrowDown','windowsVirtualKeyCode':40}}, 31)
        await s(ws, {'id':32,'method':'Input.dispatchKeyEvent','params':{'type':'keyUp','key':'ArrowDown','windowsVirtualKeyCode':40}}, 32)
        await asyncio.sleep(1.5)
        c3_raw = await js(ws, 'var e=document.getElementById("react-select-3-option-5");e.scrollIntoView({block:"center",behavior:"instant"});var r=e.getBoundingClientRect();JSON.stringify({x:r.left+r.width/2,y:r.top+r.height/2,inView:r.top>0&&r.top<window.innerHeight})', 33)
        c3 = json.loads(c3_raw)
        await click(ws, c3['x'], c3['y'], 34)
        await asyncio.sleep(2.5)
        sub_val = await js(ws, 'document.querySelector("input[name*=sub_category_id]")?.value', 35)
        print('✓ Subcat id=' + str(sub_val))

        # SERVICE TYPE (react-select-4)
        svc, svc_opts = await pick_select(ws, 'react-select-4-input', '', 40)
        print('✓ Service type: ' + str(svc) + ' | opts: ' + str(svc_opts[:4]))
        await asyncio.sleep(1)

        # Discover all remaining inputs
        all_inputs_raw = await js(ws, 'JSON.stringify(Array.from(document.querySelectorAll("input[id*=react-select]")).filter(i=>i.getBoundingClientRect().y>0).map(i=>({id:i.id,y:Math.round(i.getBoundingClientRect().y)})))', 50)
        all_inputs = json.loads(all_inputs_raw)
        print('All inputs now: ' + str(all_inputs))

        done_ids = {'react-select-2-input','react-select-3-input','react-select-4-input'}
        remaining = [i for i in all_inputs if i['id'] not in done_ids]

        for idx, inp in enumerate(remaining):
            res, opts = await pick_select(ws, inp['id'], '', 60+idx*20)
            print('  ' + inp['id'] + ': ' + str(res) + ' | ' + str(opts[:3]))
            await asyncio.sleep(1)

            # Re-discover after each selection (new inputs may appear)
            new_inputs_raw = await js(ws, 'JSON.stringify(Array.from(document.querySelectorAll("input[id*=react-select]")).filter(i=>i.getBoundingClientRect().y>0).map(i=>({id:i.id,y:Math.round(i.getBoundingClientRect().y)})))', 65+idx*20)
            new_inputs = json.loads(new_inputs_raw)
            new_remaining = [i for i in new_inputs if i['id'] not in done_ids]
            if len(new_remaining) > len(remaining) - idx:
                remaining = new_remaining[idx+1:]
                print('  New inputs discovered: ' + str([i['id'] for i in new_remaining]))

        # RADIOS / CHECKBOXES
        checks_raw = await js(ws, 'JSON.stringify(Array.from(document.querySelectorAll("input[type=radio],input[type=checkbox]")).filter(i=>i.getBoundingClientRect().y>0&&!i.checked).map(i=>({id:i.id,name:i.name,val:i.value,y:Math.round(i.getBoundingClientRect().y)})).slice(0,15))', 70)
        checks = json.loads(checks_raw)
        print('Unchecked radios/checks: ' + str(checks))

        clicked_names = set()
        for ch_idx, ch in enumerate(checks):
            if ch.get('name') and ch['name'] not in clicked_names:
                sel = 'input[name="' + ch['name'] + '"]'
                ch_raw = await js(ws, 'var el=document.querySelector(\'' + sel + '\');if(el){el.scrollIntoView({block:"center",behavior:"instant"});var r=el.getBoundingClientRect();JSON.stringify({x:r.left+r.width/2,y:r.top+r.height/2,inView:r.top>30&&r.top<window.innerHeight-30});}else "nf"', 80+ch_idx*3)
                try:
                    cc = json.loads(ch_raw)
                    if cc.get('inView'):
                        await click(ws, cc['x'], cc['y'], 81+ch_idx*3)
                        clicked_names.add(ch['name'])
                        print('  Checked: ' + ch['name'])
                        await asyncio.sleep(0.5)
                except: pass

        # TAGS — find the search tags input (lowest on page = highest y)
        await asyncio.sleep(1)
        tag_inputs_raw = await js(ws, 'JSON.stringify(Array.from(document.querySelectorAll("input[id*=react-select]")).filter(i=>i.getBoundingClientRect().y>0).map(i=>({id:i.id,y:Math.round(i.getBoundingClientRect().y)})).sort((a,b)=>b.y-a.y))', 90)
        tag_inputs = json.loads(tag_inputs_raw)
        print('Tag input candidates (by y desc): ' + str(tag_inputs))

        # The tags/positive keywords field is the bottommost react-select
        if tag_inputs:
            tag_id = tag_inputs[0]['id']
            tags = ["productlaunch", "saaslaunch", "aiautomation", "growthhacking", "producthunt"]
            print('Using ' + tag_id + ' for tags')
            for j, tag in enumerate(tags):
                await js(ws, 'var i=document.getElementById("' + tag_id + '");i.scrollIntoView({block:"center",behavior:"instant"});i.focus();i.click();', 100+j*25)
                await asyncio.sleep(0.4)
                for k, char in enumerate(tag):
                    await s(ws, {'id':101+j*25+k,'method':'Input.dispatchKeyEvent','params':{'type':'keyDown','key':char,'text':char}}, 101+j*25+k)
                    await asyncio.sleep(0.07)
                await asyncio.sleep(1.2)
                prefix2 = tag_id.replace('-input','')
                opt_raw = await js(ws, 'var opts=Array.from(document.querySelectorAll("[id^=\'' + prefix2 + '-option\']")).filter(e=>!e.innerText?.includes("SELECT"));if(opts.length){var r=opts[0].getBoundingClientRect();if(r.top>0&&r.top<window.innerHeight)JSON.stringify({x:r.left+r.width/2,y:r.top+r.height/2,text:opts[0].innerText?.trim()});else "notinview";}else "noopts"', 110+j*25)
                if opt_raw not in ('noopts','notinview',''):
                    try:
                        oc = json.loads(opt_raw)
                        await click(ws, oc['x'], oc['y'], 111+j*25)
                        print('  Tag ' + str(j) + ' picked: ' + oc.get('text', tag))
                    except:
                        await s(ws, {'id':112+j*25,'method':'Input.dispatchKeyEvent','params':{'type':'keyDown','key':'Enter','windowsVirtualKeyCode':13}}, 112+j*25)
                        print('  Tag ' + str(j) + ' enter: ' + tag)
                else:
                    await s(ws, {'id':113+j*25,'method':'Input.dispatchKeyEvent','params':{'type':'keyDown','key':'Enter','windowsVirtualKeyCode':13}}, 113+j*25)
                    print('  Tag ' + str(j) + ' fallback-enter: ' + tag + ' (' + opt_raw + ')')
                await asyncio.sleep(0.8)

        # SCREENSHOT pre-save
        await asyncio.sleep(1)
        await screenshot(ws, '/Users/rickthebot/.openclaw/workspace/fv-presave.jpg', 200)

        # SAVE & CONTINUE — scroll to button then click
        await js(ws, 'window.scrollTo(0,0);', 201)
        await asyncio.sleep(0.5)
        save_raw = await js(ws, 'var btn=Array.from(document.querySelectorAll("button")).find(b=>b.innerText?.includes("Save & Continue"));if(btn){btn.scrollIntoView({block:"center",behavior:"instant"});var r=btn.getBoundingClientRect();JSON.stringify({x:r.left+r.width/2,y:r.top+r.height/2,disabled:btn.disabled});}else "notfound"', 202)
        print('Save button: ' + str(save_raw))
        save = json.loads(save_raw)
        await click(ws, save['x'], save['y'], 203)
        await asyncio.sleep(7)

        final_url = await js(ws, 'document.URL', 210)
        title = await js(ws, 'document.title', 211)
        print('FINAL URL: ' + str(final_url))
        print('FINAL TITLE: ' + str(title))
        await screenshot(ws, '/Users/rickthebot/.openclaw/workspace/fv-done.jpg', 212)
        print('✓ Complete')

asyncio.run(run())
