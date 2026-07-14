#!/usr/bin/env node
/**
 * PH Comment Monitor + Reply via chrome-remote-interface
 * Port: 9222
 */

const CDP = require('/opt/homebrew/lib/node_modules/chrome-remote-interface');
const PH_POST_URL = 'https://www.producthunt.com/posts/rick';
const MAKER_HANDLE = 'meetrickai';

function sleep(ms) { return new Promise(r => setTimeout(r, ms)); }

function craftReply(commentText, authorHandle) {
  const text = (commentText || '').toLowerCase();

  if (text.includes('how') && (text.includes('work') || text.includes('does it') || text.includes('set up') || text.includes('setup'))) {
    return `It runs as your AI CEO — monitors revenue, executes tasks, posts to socials, and reviews what happened each night. You connect your tools (Stripe, GitHub, email, X) and give it a mandate. After that it operates mostly autonomously with Telegram as the control channel. Happy to dig into specifics!`;
  }
  if (text.includes('price') || text.includes('cost') || text.includes('paid') || text.includes('free') || text.includes('pricing')) {
    return `Live at meetrick.ai — early access pricing is designed to be reasonable relative to the time and revenue it handles for you. Would love to get you set up!`;
  }
  if (text.includes('open source') || text.includes('self.host') || text.includes('self host') || text.includes('github')) {
    return `Not open source yet — still in early access. Self-hosting is worth thinking about down the line. What's driving the interest?`;
  }
  if (text.includes('ai ceo') || text.includes('autonomous') || text.includes('agentic') || (text.includes('agent') && text.length < 200)) {
    return `Exactly the distinction we're going for. Most AI tools wait for prompts — Rick has a standing mandate and executes against it. The difference in practice: you wake up and things got done, not just suggested.`;
  }
  if (text.includes('stripe') || text.includes('revenue') || text.includes('mrr') || text.includes('money') || text.includes('sales')) {
    return `Stripe monitoring is one of the most immediately useful parts. Rick flags anomalies, tracks MRR daily, and surfaces revenue context before you need to ask. One less dashboard to babysit.`;
  }
  if (text.includes('telegram')) {
    return `Telegram is the control channel — Rick sends updates, asks for approvals on high-stakes moves, and reports what it shipped. Surprisingly natural command interface once you're in the flow.`;
  }
  if (text.includes('congrat') || text.includes('well done') || text.includes('amazing') || text.includes('love this') || text.includes('love it') || text.includes('impressive') || text.includes('great work') || text.includes('great job') || text.includes('awesome')) {
    return `Thank you, really appreciate it! If you try it out, would love to hear what you think 🙏`;
  }
  if (text.includes('when') || text.includes('waitlist') || text.includes('available') || text.includes('sign up') || text.includes('get access') || text.includes('get start')) {
    return `Available now at meetrick.ai! We're onboarding early users and iterating fast. Would love to get you in.`;
  }
  if (text.includes('versus') || text.includes(' vs ') || text.includes('differ') || text.includes('compar')) {
    return `The key difference from copilots or chat assistants: Rick operates at the CEO layer, not the task layer. It owns outcomes rather than responding to prompts. Different abstraction entirely.`;
  }
  if (text.includes('sleep') || text.includes('overnight') || text.includes('while you') || text.includes('24/7') || text.includes('always on')) {
    return `That's the core of it — Rick runs nightly reviews, monitors what broke, and queues next-day work while you sleep. The goal: every morning feels like you had an EA working overnight.`;
  }
  if (text.includes('what is') || text.includes('what does') || text.includes('explain') || text.includes('tell me more')) {
    return `Rick is an AI CEO agent — it has a mandate ($100K MRR) and executes autonomously: monitoring revenue, running nightly ops reviews, posting to socials, managing tasks. You stay in the loop via Telegram. meetrick.ai if you want to see it in action.`;
  }
  // Generic
  return `Appreciate you checking it out! Happy to answer questions. Rick is live at meetrick.ai — always good to hear what resonates with folks seeing it fresh.`;
}

async function main() {
  console.log('=== PH Comment Monitor + Reply ===');
  console.log(`Time: ${new Date().toISOString()}\n`);

  // Get page list to find a suitable tab
  const targets = await CDP.List({ port: 9222 });
  const phTarget = targets.find(t => t.type === 'page' && t.url.includes('producthunt.com'))
    || targets.find(t => t.type === 'page');

  if (!phTarget) {
    console.error('No suitable Chrome page found');
    process.exit(1);
  }

  console.log(`Attaching to: ${phTarget.url}`);
  const client = await CDP({ target: phTarget.id, port: 9222 });
  const { Page, Runtime, Input, DOM } = client;

  await Page.enable();
  await Runtime.enable();

  // Navigate to PH post
  console.log(`Navigating to ${PH_POST_URL}...`);
  await Page.navigate({ url: PH_POST_URL });
  
  // Wait for load
  await new Promise(resolve => {
    Page.loadEventFired(resolve);
    sleep(12000).then(resolve);
  });
  await sleep(5000); // extra for React

  // Get page text
  const pageTextResult = await Runtime.evaluate({
    expression: 'document.body.innerText',
    returnByValue: true,
  });
  const pageText = pageTextResult.result.value || '';
  
  console.log('\n=== PAGE TITLE ===');
  const titleResult = await Runtime.evaluate({ expression: 'document.title', returnByValue: true });
  console.log(titleResult.result.value);

  // Extract upvotes - look for the number near vote button
  const upvoteResult = await Runtime.evaluate({
    expression: `(() => {
      // Try various upvote selectors
      const selectors = [
        '[data-test="vote-count"]',
        '[class*="voteCount"]', 
        '[class*="vote_count"]',
        '[class*="VoteCount"]',
        'button[class*="vote"] span',
        '[aria-label*="upvote"] span',
        '[data-test*="upvote"]',
      ];
      for (const s of selectors) {
        const el = document.querySelector(s);
        if (el) return { selector: s, text: el.textContent.trim() };
      }
      // Fallback: look for number next to "upvote" text
      const allText = document.body.innerText;
      const match = allText.match(/(\\d+)\\s*\\n?\\s*upvote/i);
      if (match) return { method: 'text-regex', count: match[1] };
      return { error: 'not found' };
    })()`,
    returnByValue: true,
    awaitPromise: true,
  });
  console.log('\n=== UPVOTE DATA ===');
  console.log(JSON.stringify(upvoteResult.result.value));

  // Check login state
  const loginResult = await Runtime.evaluate({
    expression: `(() => {
      // Look for meetrickai in page
      const links = [...document.querySelectorAll('a[href*="meetrickai"]')];
      if (links.length) return 'LOGGED_IN_AS_meetrickai - links: ' + links.map(l => l.href).slice(0, 3).join(', ');
      // Look for any user avatar (logged in indicator)
      const nav = document.querySelector('nav, header');
      if (nav) {
        const imgs = nav.querySelectorAll('img[alt*="avatar" i], img[class*="avatar" i]');
        if (imgs.length) return 'LIKELY_LOGGED_IN (avatar in nav)';
      }
      const loginBtn = document.querySelector('[href*="login"], button[class*="sign-in"]');
      if (loginBtn) return 'NOT_LOGGED_IN';
      return 'UNKNOWN';
    })()`,
    returnByValue: true,
  });
  console.log('\n=== LOGIN STATE ===');
  console.log(loginResult.result.value);

  // Scrape all comments
  const commentsResult = await Runtime.evaluate({
    expression: `(() => {
      const comments = [];
      
      // Strategy 1: data-test attributes
      let els = [...document.querySelectorAll('[data-test="comment"]')];
      
      // Strategy 2: Look for comment-like structures
      if (els.length === 0) {
        // Find divs that contain a user link + paragraph text + are in a comments section
        const allDivs = [...document.querySelectorAll('div[class*="omment"]')];
        els = allDivs;
      }
      
      // Strategy 3: Find by finding the comments section then parsing children
      if (els.length === 0) {
        const sections = [...document.querySelectorAll('section, [class*="Section"], [class*="section"]')];
        const commentSection = sections.find(s => s.textContent.includes('Comments') || s.textContent.includes('comment'));
        if (commentSection) {
          // Get direct children with user links
          els = [...commentSection.querySelectorAll(':scope > div, :scope > article')].filter(el => el.querySelector('a[href*="/@"]'));
        }
      }
      
      // Strategy 4: Universal - find all elements with user profile links + paragraph text
      if (els.length === 0) {
        const candidateEls = [...document.querySelectorAll('div, article')].filter(el => {
          const hasUser = el.querySelector('a[href*="/@"]');
          const hasText = el.querySelectorAll('p').length > 0 && el.querySelectorAll('p').length < 10;
          const notHeader = !el.closest('header');
          const notNav = !el.closest('nav');
          const depth = (el.closest('[class*="comment"]') || el.closest('[class*="Comment"]'));
          return hasUser && hasText && notHeader && notNav;
        });
        // Deduplicate by picking non-nested ones
        els = candidateEls.filter(el => !candidateEls.some(other => other !== el && other.contains(el)));
        els = els.slice(0, 30);
      }

      els.forEach((el, i) => {
        const userLinks = [...el.querySelectorAll('a[href*="/@"]')];
        const paragraphs = [...el.querySelectorAll('p')].map(p => p.textContent.trim()).filter(t => t);
        const hasMakerBadge = el.textContent.includes('Maker') || el.innerHTML.includes('maker');
        const hasReplyInput = el.querySelector('textarea, [contenteditable]');
        
        // Check if meetrickai already replied (nested user link to meetrickai)
        const hasMeetrickaiReply = userLinks.some(l => l.href.includes('meetrickai'));
        
        // The top-level author (first user link if not nested)
        const topAuthor = userLinks[0] ? userLinks[0].href : null;
        const topAuthorHandle = topAuthor ? topAuthor.split('/@')[1]?.replace(/[/?].*/, '') : null;
        
        comments.push({
          index: i,
          topAuthorHandle,
          topAuthorHref: topAuthor,
          paragraphs: paragraphs.slice(0, 3),
          hasMakerBadge,
          hasMeetrickaiReply,
          allUserLinks: userLinks.map(l => l.href),
          htmlSnippet: el.outerHTML.substring(0, 400),
        });
      });
      
      return { count: comments.length, strategy: els.length > 0 ? 'found' : 'empty', comments };
    })()`,
    returnByValue: true,
    awaitPromise: true,
  });
  
  const commentData = commentsResult.result.value || { comments: [] };
  console.log(`\n=== COMMENTS (${commentData.count} found, strategy: ${commentData.strategy}) ===`);
  
  (commentData.comments || []).forEach((c, i) => {
    console.log(`\n[${i}] @${c.topAuthorHandle} | makerBadge: ${c.hasMakerBadge} | meetrickaiReply: ${c.hasMeetrickaiReply}`);
    console.log(`    Text: ${JSON.stringify(c.paragraphs)}`);
    console.log(`    All users: ${c.allUserLinks.map(u => u.split('/@')[1]?.split('?')[0]).join(', ')}`);
  });

  // Show comment section from page text
  const commentTextStart = pageText.indexOf('Comments');
  if (commentTextStart > -1) {
    console.log('\n=== COMMENTS FROM PAGE TEXT ===');
    console.log(pageText.substring(commentTextStart, commentTextStart + 4000));
  }

  // Show upvote section - find number pattern
  console.log('\n=== FULL PAGE TEXT SAMPLE ===');
  console.log(pageText.substring(0, 3000));

  // Now try to post replies for unanswered comments
  const toReply = (commentData.comments || []).filter(c => {
    // Skip if meetrickai already replied
    if (c.hasMeetrickaiReply) return false;
    // Skip if it IS meetrickai
    if (c.topAuthorHandle === MAKER_HANDLE) return false;
    // Only reply to comments with actual text
    if (!c.paragraphs || c.paragraphs.length === 0) return false;
    return true;
  });

  console.log(`\n=== REPLIES NEEDED: ${toReply.length} ===`);
  toReply.forEach(c => console.log(`  - @${c.topAuthorHandle}: "${c.paragraphs[0]?.substring(0, 80)}"`));

  if (toReply.length > 0 && loginResult.result.value?.includes('LOGGED_IN')) {
    console.log('\nAttempting to post replies...');
    // TODO: implement reply posting once comment structure is confirmed
    // For now, log what we'd post
    for (const comment of toReply) {
      const commentText = comment.paragraphs.join(' ');
      const reply = craftReply(commentText, comment.topAuthorHandle);
      console.log(`\nWould reply to @${comment.topAuthorHandle}:`);
      console.log(`  Comment: "${commentText.substring(0, 100)}"`);
      console.log(`  Reply: "${reply}"`);
    }
  }

  await client.close();
  console.log('\n=== DONE ===');
}

main().catch(e => {
  console.error('Fatal:', e.message);
  process.exit(1);
});
