#!/usr/bin/env node
/**
 * Simple PH Comment Monitor + Reply
 * Connects to Chrome on port 9225 and checks for comments needing replies
 */

const { chromium } = require('playwright');

function sleep(ms) { return new Promise(r => setTimeout(r, ms)); }

// Reply crafting function (same as before)
function craftReply(commentText, authorHandle) {
  const text = (commentText || '').toLowerCase();

  if (text.includes('how') && (text.includes('work') || text.includes('does it') || text.includes('set up') || text.includes('setup'))) {
    return `It runs as your AI CEO — monitors revenue, executes tasks, posts to socials, and reviews what happened each night. You connect your tools (Stripe, GitHub, email, X) and give it a mandate. After that it operates mostly autonomously with Telegram as the control channel. Happy to walk through specifics if helpful!`;
  }
  if (text.includes('price') || text.includes('cost') || text.includes('paid') || text.includes('free') || text.includes('pricing')) {
    return `Live at meetrick.ai — early access pricing is designed to be reasonable relative to what it saves you in time and what it generates. Would love to get you set up and hear what you're working on.`;
  }
  if (text.includes('congrat') || text.includes('well done') || text.includes('amazing') || text.includes('love this') || text.includes('love it') || text.includes('impressive') || text.includes('nice') || text.includes('great job') || text.includes('great work')) {
    return `Thank you! Really means a lot on launch day. If you try it out, would love to hear what you think.`;
  }
  if (text.includes('open source') || text.includes('self.host') || text.includes('github')) {
    return `Not open source yet — still in early access. The core is pretty tightly coupled to the OpenClaw runtime, but self-hosting is something worth exploring. What's driving the interest there?`;
  }
  if (text.includes('ai ceo') || text.includes('autonomous') || text.includes('agent') || text.includes('agentic')) {
    return `Exactly the distinction we're going for. Most AI tools wait for prompts — Rick has a standing mandate and executes against it. The difference in practice is significant: you wake up and things got done, not just suggested.`;
  }
  if (text.includes('stripe') || text.includes('revenue') || text.includes('mrr') || text.includes('money') || text.includes('sales')) {
    return `Stripe monitoring is one of the most immediately useful parts. Rick flags anomalies, tracks MRR daily, and surfaces revenue context before you need to ask. One less dashboard to check manually.`;
  }
  if (text.includes('telegram')) {
    return `Telegram is the control channel — Rick sends updates, asks for approvals on high-stakes actions, and reports what it shipped. It's a surprisingly natural command interface once you're in the flow of it.`;
  }
  if (text.includes('when') || text.includes('launch') || text.includes('waitlist') || text.includes('available') || text.includes('sign up') || text.includes('get access')) {
    return `Available now at meetrick.ai! We're onboarding early users and iterating fast. Would love to get you in.`;
  }
  if (text.includes('versus') || text.includes(' vs ') || text.includes('differ') || text.includes('compar')) {
    return `The key difference from copilots or assistants: Rick operates at the CEO layer, not the task layer. It owns outcomes (revenue target, launch execution, ops reliability) rather than responding to prompts. Different abstraction entirely.`;
  }
  if (text.includes('sleep') || text.includes('night') || text.includes('while you') || text.includes('24/7') || text.includes('always on')) {
    return `That's the core of it — Rick runs the nightly review, monitors what broke, and queues up next-day work while you're asleep. The goal is to make every morning feel like you had an EA working overnight.`;
  }
  // Generic supportive
  return `Appreciate you checking it out! Happy to answer any questions. Rick is live at meetrick.ai if you want to explore — always good to hear what resonates with people seeing it fresh.`;
}

async function main() {
  console.log('=== Simple PH Comment Monitor + Reply ===');
  console.log(`Time: ${new Date().toISOString()}`);

  let browser = null;
  try {
    console.log('Connecting to Chrome via CDP on port 9225...');
    browser = await chromium.connectOverCDP('http://localhost:9225');
    console.log('✅ Connected successfully!');
    
    const contexts = browser.contexts();
    if (contexts.length === 0) {
      console.log('❌ No browser contexts found');
      return;
    }
    
    const context = contexts[0];
    let pages = context.pages();
    console.log(`📄 Found ${pages.length} pages`);
    
    // Find the Product Hunt Rick post
    let phPage = pages.find(p => p.url().includes('producthunt.com/posts/rick'));
    if (!phPage) {
      phPage = pages.find(p => p.url().includes('producthunt.com'));
    }
    if (!phPage && pages.length > 0) {
      phPage = pages[0];
    }
    
    if (!phPage) {
      console.log('❌ No pages available, creating new page...');
      phPage = await context.newPage();
    }
    
    console.log(`📍 Using page: ${phPage.url()}`);
    
    // Navigate to Rick's post if not already there
    if (!phPage.url().includes('producthunt.com/posts/rick')) {
      console.log('🔄 Navigating to Rick\'s Product Hunt post...');
      try {
        await phPage.goto('https://www.producthunt.com/posts/rick', { 
          waitUntil: 'domcontentloaded',
          timeout: 20000
        });
        await phPage.waitForTimeout(8000); // Wait for React to hydrate
        console.log('✅ Navigation complete');
      } catch (navError) {
        console.log(`⚠️  Navigation had issues: ${navError.message}`);
        // Continue anyway - maybe we're already on the right page
      }
    } else {
      // Refresh to get latest content
      console.log('🔄 Refreshing to get latest comments...');
      await phPage.reload({ waitUntil: 'domcontentloaded' });
      await phPage.waitForTimeout(5000);
    }
    
    // Get page information
    const title = await phPage.title();
    const url = phPage.url();
    console.log(`📰 Page title: ${title}`);
    console.log(`🔗 URL: ${url}`);
    
    // Extract text content for analysis
    const pageText = await phPage.evaluate(() => document.body.innerText);
    console.log(`\n📄 Page text length: ${pageText.length} characters`);
    
    // Look for upvote count
    console.log('\n=== UPVOTE DETECTION ===');
    const upvotePatterns = [
      /(\d{1,4})\s*\n\s*(?:upvote|vote)/i,
      /(?:upvote|vote)\s*:?\s*(\d{1,4})/i,
      /^(\d{1,4})$/gm
    ];
    
    let upvotesFound = 'unknown';
    for (const pattern of upvotePatterns) {
      const match = pageText.match(pattern);
      if (match) {
        // For the multiline pattern, we need to check context
        if (pattern === /^(\d{1,4})$/gm) {
          const lines = pageText.split('\n');
          for (let i = 0; i < lines.length; i++) {
            if (/^\d{1,4}$/.test(lines[i].trim()) && 
                i + 1 < lines.length && 
                /upvote|vote/i.test(lines[i + 1])) {
              upvotesFound = lines[i].trim();
              break;
            }
          }
        } else {
          upvotesFound = match[1];
        }
        break;
      }
    }
    console.log(`👍 Upvotes: ${upvotesFound}`);
    
    // Check login status
    console.log('\n=== LOGIN STATUS ===');
    const isLoggedIn = await phPage.evaluate(() => {
      const links = [...document.querySelectorAll('a[href]')];
      return links.some(l => l.href && (l.href.includes('/@meetrickai') || l.href.includes('meetrickai')));
    });
    console.log(`🔐 Logged in as @meetrickai: ${isLoggedIn}`);
    
    if (!isLoggedIn) {
      console.log('⚠️  Not logged in - cannot reply to comments');
      console.log('💡 Please ensure you\'re logged into Product Hunt as @meetrickai in the Chrome session on port 9225');
      return;
    }
    
    // Look for comment section
    console.log('\n=== COMMENT ANALYSIS ===');
    
    // Try to find comment elements using various selectors
    const commentSelectors = [
      '[data-test="comment"]',
      '[class*="comment_"]',
      '[class*="commentBody"]',
      'article',
      '[role="article"]',
      '[class*="Comment"]'
    ];
    
    let commentElements = [];
    let usedSelector = '';
    
    for (const selector of commentSelectors) {
      try {
        const elements = await phPage.$$(selector);
        if (elements.length > 0) {
          commentElements = elements;
          usedSelector = selector;
          console.log(`✅ Found ${elements.length} comment elements using selector: ${selector}`);
          break;
        }
      } catch (e) {
        // Selector might not work, continue
      }
    }
    
    if (commentElements.length === 0) {
      console.log('⚠️  No comment elements found with standard selectors');
      console.log('🔍 Trying text-based analysis...');
      
      // Split text into lines and look for potential comments
      const lines = pageText.split('\n')
        .map(line => line.trim())
        .filter(line => line.length > 15 && line.length < 1000); // Reasonable comment length
      
      console.log(`📝 Analyzing ${lines.length} text lines...`);
      
      // Look for lines that contain comment-like content
      const commentIndicators = [
        'how', 'what', 'when', 'why', 'who', 'where',
        'congrat', 'love', 'great', 'awesome', 'amazing',
        'ai ceo', 'autonomous', 'agent', 'rick', 'stripe',
        'telegram', 'price', 'cost', 'launch', 'when',
        'thanks', 'thank', 'impressive', 'nice', 'cool'
      ];
      
      const potentialComments = lines.filter(line => {
        const lower = line.toLowerCase();
        return commentIndicators.some(indicator => lower.includes(indicator));
      });
      
      console.log(`💬 Found ${potentialComments.length} potential comment lines`);
      
      // Show first few potential comments
      potentialComments.slice(0, 5).forEach((line, index) => {
        console.log(`  ${index + 1}. ${line.substring(0, 100)}${line.length > 100 ? '...' : ''}`);
      });
      
      // For each potential comment, try to find and reply to it
      console.log('\n=== ATTEMPTING TO REPLY TO COMMENTS ===');
      let repliesPosted = 0;
      
      for (let i = 0; i < Math.min(potentialComments.length, 5); i++) {
        const commentText = potentialComments[i];
        
        // Skip if it looks like it's from Rick already
        const lowerText = commentText.toLowerCase();
        if (lowerText.includes('meetrickai') || lowerText.includes('rick') && lowerText.includes('maker')) {
          console.log(`⏭️  Skipping comment ${i+1} - appears to be from Rick already`);
          continue;
        }
        
        console.log(`\n--- Processing potential comment ${i+1} ---`);
        console.log(`Comment: "${commentText.substring(0, 150)}${commentText.length > 150 ? '...' : ''}"`);
        
        // Craft reply
        const replyText = craftReply(commentText, '');
        console.log(`Reply: "${replyText.substring(0, 150)}${replyText.length > 150 ? '...' : ''}"`);
        
        // Try to find and click a reply button, then post
        try {
          console.log('🔍 Looking for reply button...');
          
          // Look for any reply button on the page
          const replyButtons = await phPage.$$('#button:has-text("reply"), button:has-text("Reply"), [role="button"]:has-text("reply"), [role="button"]:has-text("Reply")');
          
          if (replyButtons.length > 0) {
            console.log(`🔘 Found ${replyButtons.length} reply buttons, trying first one...`);
            
            // Click the first reply button
            await replyButtons[0].click();
            await phPage.waitForTimeout(2000);
            
            // Look for textarea to type in
            const textarea = await phPage.$('textarea', { timeout: 5000 });
            if (textarea) {
              await textarea.click();
              await phPage.waitForTimeout(500);
              await textarea.fill(''); // Clear first
              await textarea.type(replyText, { delay: 15 }); // Type with delay
              await phPage.waitForTimeout(1000);
              
              // Look for submit/post button
              const submitButton = await phPage.$$('#button:has-text("Post"), button:has-text("Submit"), button:has-text("Comment"), [role="button"]:has-text("Post"), [role="button"]:has-text("Submit"), [role="button"]:has-text("Comment")', { timeout: 5000 });
              
              if (submitButton.length > 0) {
                await submitButton[0].click();
                await phPage.waitForTimeout(3000);
                console.log('✅ Reply posted successfully!');
                repliesPosted++;
              } else {
                console.log('⚠️  Could not find submit button');
              }
            } else {
              console.log('⚠️  Could not find textarea for reply');
            }
          } else {
            console.log('⚠️  No reply buttons found on page');
          }
        } catch (replyError) {
          console.log(`❌ Error trying to reply: ${replyError.message}`);
        }
        
        // Wait between attempts
        if (i < Math.min(potentialComments.length, 5) - 1) {
          await phPage.waitForTimeout(3000);
        }
      }
      
      console.log(`\n📊 Summary: Posted ${repliesPosted} replies`);
      
    } else {
      // We found comment elements, process them
      console.log(`🔍 Analyzing ${commentElements.length} comment elements...`);
      
      let repliesPosted = 0;
      let processed = 0;
      
      for (let i = 0; i < Math.min(commentElements.length, 10); i++) {
        try {
          const element = commentElements[i];
          const commentText = await element.evaluate(el => el.innerText || el.textContent || '');
          
          if (!commentText || commentText.trim().length < 10) {
            continue;
          }
          
          // Check if this is already a Rick comment
          const lowerText = commentText.toLowerCase();
          const isRickComment = lowerText.includes('meetrickai') || 
                               (lowerText.includes('rick') && lowerText.includes('maker')) ||
                               await element.evaluate(el => {
                                 const html = el.innerHTML || '';
                                 return html.includes('meetrickai') || html.includes('Maker') || html.includes('badge');
                               });
          
          if (isRickComment) {
            console.log(`⏭️  Skipping comment ${i+1} - already from Rick`);
            continue;
          }
          
          processed++;
          console.log(`\n--- Processing comment ${processed} ---`);
          console.log(`Author/Element text: "${commentText.substring(0, 150)}${commentText.length > 150 ? '...' : ''}"`);
          
          // Craft reply
          const replyText = craftReply(commentText, '');
          console.log(`Reply: "${replyText.substring(0, 150)}${replyText.length > 150 ? '...' : ''}"`);
          
          // Try to find reply button within or near this comment
          try {
            // Look for reply button in this comment element
            let replyButton = await element.$('button:has-text("reply"), button:has-text("Reply"), [role="button"]:has-text("reply"), [role="button"]:has-text("Reply")', { timeout: 2000 });
            
            // If not found in element, look nearby
            if (!replyButton) {
              replyButton = await element.$('xpath=./following-sibling::*[contains(concat(" ", @class, " "), " reply ") or contains(concat(" ", @class, " "), " Reply ") or self::button[contains(text(), "reply") or contains(text(), "Reply")]]', { timeout: 2000 });
            }
            
            // If still not found, look for any button that might be a reply
            if (!replyButton) {
              const allButtons = await element.$$('button, [role="button"]');
              for (const btn of allButtons) {
                const btnText = await btn.evaluate(el => el.textContent || '');
                if (btnText.toLowerCase().includes('reply')) {
                  replyButton = btn;
                  break;
                }
              }
            }
            
            if (replyButton) {
              console.log('🔘 Found reply button, clicking...');
              await replyButton.click();
              await phPage.waitForTimeout(2000);
              
              // Look for textarea (could be in a modal or near the button)
              let textarea = await phPage.$('textarea', { timeout: 5000 });
              
              // If not found, look for contenteditable div
              if (!textarea) {
                textarea = await phPage.$('[contenteditable="true"]', { timeout: 3000 });
              }
              
              if (textarea) {
                await textarea.click();
                await phPage.waitForTimeout(500);
                await textarea.fill(''); // Clear first
                await textarea.type(replyText, { delay: 15 });
                await phPage.waitForTimeout(1000);
                
                // Look for submit button
                let submitButton = await phPage.$$('#button:has-text("Post"), button:has-text("Submit"), button:has-text("Comment"), [role="button"]:has-text("Post"), [role="button"]:has-text("Submit"), [role="button"]:has-text("Comment")', { timeout: 5000 });
                
                // If not found, look for button with specific classes
                if (submitButton.length === 0) {
                  submitButton = await phPage.$$('button[class*="submit"], button[class*="post"], [role="button"][class*="submit"], [role="button"][class*="post"]', { timeout: 3000 });
                }
                
                if (submitButton.length > 0) {
                  await submitButton[0].click();
                  await phPage.waitForTimeout(3000);
                  console.log('✅ Reply posted successfully!');
                  repliesPosted++;
                } else {
                  console.log('⚠️  Could not find submit button');
                }
              } else {
                console.log('⚠️  Could not find textarea or contenteditable for reply');
              }
            } else {
              console.log('⚠️  No reply button found for this comment');
            }
          } catch (replyError) {
            console.log(`❌ Error processing comment reply: ${replyError.message}`);
          }
          
          // Wait between replies
          if (processed < Math.min(commentElements.length, 10) && i < commentElements.length - 1) {
            await phPage.waitForTimeout(3000);
          }
        } catch (elemError) {
          console.log(`Error processing comment element ${i}:`, elemError.message);
        }
      }
      
      console.log(`\n📊 Summary: Processed ${processed} comments, posted ${repliesPosted} replies`);
    }
    
    // Final summary
    console.log('\n=== FINAL SUMMARY ===');
    console.log(`🕐 Time: ${new Date().toISOString()}`);
    console.log(`👍 Upvotes: ${upvotesFound}`);
    console.log(`🔐 Logged in: ${isLoggedIn}`);
    console.log(`📄 Page: ${url}`);
    
  } catch (error) {
    console.error('❌ Fatal error:', error.message);
    console.error(error.stack);
  } finally {
    if (browser) {
      // Don't close the browser - leave user's session alone
      console.log('\n🚪 Leaving browser session open (port 9225)...');
    }
  }
}

main().catch(e => {
  console.error('Fatal:', e.message);
  console.error(e.stack);
  process.exit(1);
});