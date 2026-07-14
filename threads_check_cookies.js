const { chromium } = require('playwright');
(async()=>{
  const browser = await chromium.connectOverCDP('http://127.0.0.1:9222');
  const page = browser.contexts()[0].pages()[0];
  const cookies = await page.context().cookies('https://www.threads.com');
  console.log('Total threads cookies:', cookies.length);
  console.log('Auth cookies:', JSON.stringify(cookies.filter(c => ['sessionid','csrftoken','ds_user_id','mid','ig_did','datr','wd'].includes(c.name)).map(c=>({name:c.name, value: c.value.slice(0,30)+'...', domain:c.domain})), null, 2));
  // Also get user from current session
  await page.goto('https://www.threads.com/', {waitUntil:'domcontentloaded', timeout:30000}).catch(()=>{});
  await page.waitForTimeout(3000);
  const text = await page.textContent('body').catch(()=>'');
  console.log('Logged in?', text.includes('meet_rick_ai') ? 'YES - meet_rick_ai' : text.includes('Login') ? 'NO - Login page' : 'UNKNOWN');
  await browser.close();
})();
