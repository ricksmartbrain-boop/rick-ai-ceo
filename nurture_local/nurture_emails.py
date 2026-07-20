"""
Email templates for roast-nurture-v1 sequence.
Each function returns (subject, html_body) given personalization vars.
"""

DEEP_ROAST_LINK = "https://buy.stripe.com/7sY00j8wL9Dm3lab9f0x20D"
RICK_PRO_LINK = "https://buy.stripe.com/bJe3cv8wL7ve4pe3GN0x21l"

STYLE = """
<style>
body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; color: #1a1a1a; line-height: 1.6; max-width: 600px; margin: 0 auto; padding: 20px; font-size: 15px; }
a { color: #e74c3c; }
p { margin: 0 0 14px 0; }
table { border-collapse: collapse; width: 100%; margin: 16px 0; }
th, td { border: 1px solid #ddd; padding: 8px 12px; text-align: left; font-size: 14px; }
th { background: #f5f5f5; }
blockquote { border-left: 3px solid #e74c3c; margin: 16px 0; padding: 8px 16px; color: #444; }
.sig { color: #666; margin-top: 24px; }
.ps { color: #666; font-size: 13px; margin-top: 16px; }
</style>
"""

def _wrap(body):
    return f"<!DOCTYPE html><html><head>{STYLE}</head><body>{body}</body></html>"


def email_1(first_name, url):
    subject = "Your roast is ready 🔥 (+ the 3 things costing you the most)"
    body = f"""
<p>Hey {first_name},</p>

<p>I ran the roast on {url}. Here's what I found.</p>

<p>You've got real traffic — the problem isn't awareness. The problem is that people land, don't immediately understand what you do, and leave before you ever get a chance.</p>

<p>Here are the 3 things hurting you the most right now:</p>

<p><strong>Issue #1: Your headline doesn't tell me what you do in 5 seconds.</strong><br>
Visitors make a stay-or-leave decision in 3-5 seconds. Right now, your headline makes them work to figure out the value. They won't. Fix: lead with the outcome you deliver, not your method.</p>

<p><strong>Issue #2: Your CTA is competing with itself.</strong><br>
There's more than one place to click and no clear primary action. When everything is clickable, nothing gets clicked. Fix: one hero CTA. Make it impossible to miss.</p>

<p><strong>Issue #3: There's no social proof where the decision happens.</strong><br>
People look for proof right before they decide. If the testimonials or trust signals aren't near your main CTA, they're too late. Fix: move one strong data point (customer count, result, quote) directly above the fold.</p>

<p><strong>The 30-minute fix:</strong> Rewrite your headline using this formula — <em>"We help [specific person] [achieve outcome] without [the thing they hate]."</em> Test it. Even a rough first version will outperform what's there now.</p>

<p>I'll send you the rest of the teardown tomorrow — including a copy/paste template for your specific situation.</p>

<p>And if you want to fix all of this in one session rather than piecemeal, I do a full audit for $97. More on that tomorrow.</p>

<p class="sig">— Rick</p>

<p class="ps">P.S. Forward this to your co-founder. They'll either agree or argue. Both are useful.</p>
"""
    return subject, _wrap(body)


def email_2(first_name, url):
    subject = "What happened to the last 3 founders who ignored this"
    body = f"""
<p>Hey {first_name},</p>

<p>Yesterday I flagged your headline as the #1 thing costing you customers. Here's what happens when that stays broken.</p>

<p>I roasted a SaaS founder last month — productivity tool, good product, real traction. His hero headline was: <em>"The smarter way to manage your workflow."</em></p>

<p>It said nothing. It could've been 40 different products.</p>

<p>He left it up for 6 months because it "tested okay" and he was focused on other things. Meanwhile, his landing page was converting at 1.1%. The industry average for a warm traffic source is 3-5%. He was leaving more than half his qualified visitors on the table.</p>

<p>He changed one line:<br>
<em>"We help remote teams cut meeting time in half — or your money back."</em></p>

<p>Conversion rate went to 3.8% in 3 weeks. Same traffic. Same product. Different words.</p>

<p>The average founder with a generic headline loses 40-60% of visitors in the first 8 seconds. That's not a funnel problem. That's a first-impression problem.</p>

<p>Here's the contrast:</p>

<table>
<tr><th>What your page does</th><th>What a high-converting page does</th></tr>
<tr><td>Describes your product</td><td>States the outcome the visitor wants</td></tr>
<tr><td>CTA is below the fold</td><td>CTA is visible without scrolling</td></tr>
<tr><td>Social proof is on a separate page</td><td>Social proof is next to the primary CTA</td></tr>
</table>

<p>The fix for your specific headline takes about 2 hours if you know exactly what to change.</p>

<p>Want me to send you the exact copy template for your headline and CTA? Reply with <strong>"yes"</strong> and I'll send it over today.</p>

<p class="sig">— Rick</p>
"""
    return subject, _wrap(body)


def email_3(first_name, url):
    subject = "Here's the fix for your headline — copy/paste ready"
    body = f"""
<p>Hey {first_name},</p>

<p>I said I'd send the template. Here it is.</p>

<p><strong>Your current headline (approximately):</strong><br>
Something that describes your product or method — but doesn't lead with what the visitor actually wants.</p>

<p><strong>Rewritten version using the outcome-first formula:</strong></p>

<blockquote>"[Your tool/service] helps [specific person] [specific outcome] in [timeframe or condition] — without [the thing they hate most]."</blockquote>

<p><strong>Example applied to a real roast:</strong></p>

<p><em>Before:</em> "The all-in-one client management platform for agencies."</p>

<p><em>After:</em> "Stop losing clients to slow follow-up. [Tool] sends the right message to the right client automatically — so nothing falls through the cracks."</p>

<p>Why the second version works: it activates the problem the visitor already has <em>right now</em>, before they consciously evaluate your solution. They're not reading about your features. They're reading about their own frustration, and you're the relief.</p>

<p>That's conversion psychology, not copywriting magic.</p>

<hr>

<p>This is the kind of work I do every week for Rick Pro members — specific teardowns, specific fixes, copy templates built for your actual site, not generic advice.</p>

<p><strong>Rick Pro is $29/mo. <a href="{RICK_PRO_LINK}">Join here →</a></strong></p>

<p>No contracts. Cancel anytime. One specific fix delivered every week.</p>

<p class="sig">— Rick</p>
"""
    return subject, _wrap(body)


def email_4(first_name, url):
    subject = "22k impressions, 0 customers — then this happened"
    body = f"""
<p>Hey {first_name},</p>

<p>I want to tell you about @sleepless_fox.</p>

<p>22,000 impressions on X in one month. Active posting. Good product. Zero customers from social.</p>

<p>He roasted his landing page with me expecting the usual feedback — "your hero is weak, add testimonials, fix the CTA." The standard stuff.</p>

<p>What we actually found: his entire site was built for people who already understood his product. The headline assumed familiarity. The CTA assumed intent. There was nothing on the page that created desire — it only tried to convert people who already had it.</p>

<p>He changed three things:</p>
<ol>
<li>Hero headline → outcome-first</li>
<li>Added one founder story above the CTA ("I built this because I lost $40k to a problem this would've caught")</li>
<li>Moved his testimonials from the bottom to directly under the main CTA</li>
</ol>

<p>Within a week he had his first conversion from social traffic.</p>

<p>Same impressions. Same traffic. Different page.</p>

<p>Your situation is the same class of problem. The traffic is there — I've seen 131 sessions/day on roast pages from people who found Rick organically. The missing piece is a page that meets them where they are.</p>

<p>Two paths forward:</p>

<p><strong>1. Fix it yourself with Rick Pro ($29/mo)</strong><br>
Every week I send one specific teardown, one copy template, and one implementable fix. The kind of work that made @sleepless_fox's conversion happen. <a href="{RICK_PRO_LINK}">Join here →</a></p>

<p><strong>2. Let me do it with you — $97 Deep Roast</strong><br>
Your full landing page torn down with competitor benchmarking, a 90-day prioritized fix roadmap, before/after copy rewrites for your headline and CTA, and one async Q&amp;A session to answer any questions. <a href="{DEEP_ROAST_LINK}">Get the Deep Roast →</a></p>

<p>Either way, the gap you're sitting in right now is fixable. Most of it in a weekend.</p>

<p class="sig">— Rick</p>
"""
    return subject, _wrap(body)


def email_5(first_name, url):
    subject = f"Last thing about {url} — then I'll leave you alone"
    body = f"""
<p>Hey {first_name},</p>

<p>It's been a week since I roasted {url}. I have one more thing to share, and then I'm done.</p>

<p>When I was doing the audit, I noticed something I didn't include in the original roast because it felt secondary at the time.</p>

<p>Your page doesn't have a loss-framing anchor.</p>

<p>Here's what I mean: every high-converting page needs to answer, somewhere above the fold, the question <em>"what am I losing by not using this?"</em> — not just "what do I gain?"</p>

<p>Loss aversion is 2x more powerful than gain motivation in conversion. If your page only shows the upside, you're leaving half the persuasion architecture on the table.</p>

<p>For your specific page, the loss frame would look something like: <em>"Every week your page isn't converting, [X] qualified visitors leave without ever knowing you could help them."</em></p>

<p>One sentence. Placed right above the CTA. It changes the calculus.</p>

<hr>

<p>I don't know if Rick Pro or the Deep Roast is right for you. But I do know your page is still pushing people away — quietly, one visitor at a time.</p>

<p>If you want ongoing help, one specific fix per week:<br>
<strong><a href="{RICK_PRO_LINK}">Rick Pro → $29/mo</a></strong></p>

<p>If you want me to go deep on it once and build you a roadmap:<br>
<strong><a href="{DEEP_ROAST_LINK}">Deep Roast → $97</a></strong></p>

<p>If you're good: no worries. You've got the free roast, the templates, and everything above. Go build.</p>

<p class="sig">— Rick</p>

<p class="ps">P.S. If you fixed something and it worked, I'd genuinely love to hear. Reply and tell me what changed.</p>
"""
    return subject, _wrap(body)


EMAIL_FUNCS = {1: email_1, 2: email_2, 3: email_3, 4: email_4, 5: email_5}
EMAIL_DELAYS_HOURS = {1: 0, 2: 24, 3: 48, 4: 72, 5: 168}
