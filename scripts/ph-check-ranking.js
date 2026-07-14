#!/usr/bin/env node
const CDP = require('chrome-remote-interface');
const CDP_PORT = 9223;
async function sleep(ms) { return new Promise(r => setTimeout(r, ms)); }

async function run() {
  let client;
  try {
    const targets = await CDP.List({ port: CDP_PORT });
    const target = targets.find(t => t.type === 'page') || targets[0];
    client = await CDP({ port: CDP_PORT, target: target.id });
    const { Page, Runtime, Network } = client;
    await Network.enable();
    await Page.enable();

    // Check today's PH page
    console.log('Checking PH today page...');
    await Page.navigate({ url: 'https://www.producthunt.com' });
    await sleep(5000);

    const r = await Runtime.evaluate({
      expression: `
        (function() {
          const text = document.body.innerText;
          // Find Rick in text
          const rickIdx = text.toLowerCase().indexOf('rick');
          const context = rickIdx >= 0 ? text.substring(Math.max(0,rickIdx-200), rickIdx+500) : 'NOT FOUND';
          
          // Get first 3000 chars to see today's products
          return JSON.stringify({
            rickFound: rickIdx >= 0,
            rickContext: context,
            pageStart: text.substring(0, 3000)
          });
        })()
      `,
      returnByValue: true
    });
    
    const data = JSON.parse(r.result.value);
    console.log('Rick found on homepage:', data.rickFound);
    if (data.rickFound) {
      console.log('Context around Rick:');
      console.log(data.rickContext);
    }
    console.log('\nPage start:');
    console.log(data.pageStart.substring(0, 2000));

  } catch(err) {
    console.error(err.message);
    process.exit(1);
  } finally {
    if (client) await client.close();
  }
}
run();
