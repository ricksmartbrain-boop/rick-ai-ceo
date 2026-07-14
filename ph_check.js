const CDP = require('chrome-remote-interface');

(async () => {
  const browser = await CDP();
  const { Target } = browser;
  const { targetId } = await Target.createTarget({ url: 'about:blank' });
  await browser.close();

  const client = await CDP({ target: targetId });
  const { Page, Runtime } = client;
  await Page.enable();
  await Runtime.enable();
  await Page.navigate({ url: 'https://www.producthunt.com/products/rick?launch=rick' });
  await Page.loadEventFired();
  await new Promise(r => setTimeout(r, 8000));

  const { result } = await Runtime.evaluate({
    expression: `(() => {
      const bodyText = document.body.innerText || '';
      const title = document.title;
      const url = location.href;
      const comments = [...document.querySelectorAll('[data-test="comment"], [data-test*="comment"], article')]
        .map((el) => (el.innerText || '').trim())
        .filter(Boolean)
        .filter((t, i, arr) => t.length > 8 && arr.indexOf(t) === i)
        .slice(0, 20);
      const buttons = [...document.querySelectorAll('button, a')]
        .map(el => (el.innerText || el.getAttribute('aria-label') || '').trim())
        .filter(Boolean)
        .filter((t, i, arr) => arr.indexOf(t) === i)
        .slice(0, 50);
      return { title, url, bodyText: bodyText.slice(0, 5000), comments, buttons };
    })()`,
    returnByValue: true,
  });

  console.log(JSON.stringify(result.value, null, 2));
  await client.close();
})().catch(err => {
  console.error(err && err.stack ? err.stack : String(err));
  process.exit(1);
});
