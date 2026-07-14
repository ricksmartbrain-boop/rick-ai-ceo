#!/usr/bin/env python3
"""Hourly Roast Sprint v3 — curl for both Anthropic and Resend"""
import json, re, os, sys, time, datetime, urllib.request, urllib.error, subprocess, urllib.parse
from email_safety import block_reason_for_recipient

# Load env by reading the file directly
ENV_FILE = os.path.expanduser('~/clawd/config/rick.env')
env_vars = {}
if os.path.exists(ENV_FILE):
    with open(ENV_FILE) as f:
        for line in f:
            line = line.strip()
            if line.startswith('export '):
                line = line[7:]
            if '=' in line and not line.startswith('#'):
                k, _, v = line.partition('=')
                v = v.strip().strip('"').strip("'")
                env_vars[k] = v
                os.environ[k] = v

OPENAI_KEY = env_vars.get('OPENAI_API_KEY', os.environ.get('OPENAI_API_KEY', ''))
RESEND_KEY = env_vars.get('RESEND_API_KEY', os.environ.get('RESEND_API_KEY', ''))
SOCIAVAULT_KEY = env_vars.get('SOCIAVAULT_API_KEY', os.environ.get('SOCIAVAULT_API_KEY', ''))
FROM_EMAIL = 'rick@meetrick.ai'
LOG_FILE = os.path.expanduser('~/rick-vault/logs/pipeline.jsonl')
OPENAI_MODEL = 'gpt-5.4-mini'
SOCIAVAULT_CALLS = 0
SOCIAVAULT_MAX_CALLS = 5

NOW = datetime.datetime.utcnow()
HOUR = NOW.hour

CITIES = ['Austin', 'Miami', 'Denver', 'Los Angeles', 'Chicago', 'Phoenix', 'Seattle', 'Boston', 'Atlanta', 'New York']
CATS   = ['dentist', 'salon', 'chiropractor', 'realtor', 'law firm', 'restaurant', 'veterinarian', 'barbershop', 'gym', 'spa']
city = CITIES[HOUR % len(CITIES)]

print(f'🎯 Sprint: {city} | {NOW.strftime("%H:%M UTC")}')
print(f'🔑 OpenAI: {OPENAI_KEY[:14]}... | Resend: {RESEND_KEY[:14]}...')

LEAD_POOLS = {
    'Austin': [
        {'business': 'Austin Smile Dentistry', 'url': 'https://www.austinosmiledentistry.com', 'email': 'info@austinosmiledentistry.com', 'category': 'dentist'},
        {'business': 'Salon Navajo Austin', 'url': 'https://www.salonnavajo.com', 'email': 'hello@salonnavajo.com', 'category': 'salon'},
        {'business': 'ATX Spine & Sport', 'url': 'https://www.atxspineandsport.com', 'email': 'info@atxspineandsport.com', 'category': 'chiropractor'},
        {'business': 'Ivy Law Group', 'url': 'https://www.ivylawgroup.com', 'email': 'contact@ivylawgroup.com', 'category': 'law firm'},
        {'business': 'Barker & Baker ATX', 'url': 'https://www.barberandbakeratx.com', 'email': 'info@barberandbakeratx.com', 'category': 'barbershop'},
    ],
    'Miami': [
        {'business': 'Brickell Dental Arts', 'url': 'https://www.brickelldentalarts.com', 'email': 'info@brickelldentalarts.com', 'category': 'dentist'},
        {'business': 'Salon Bebe Miami', 'url': 'https://www.salonbebemiami.com', 'email': 'hello@salonbebemiami.com', 'category': 'salon'},
        {'business': 'South Beach Chiro', 'url': 'https://www.southbeachchiro.com', 'email': 'info@southbeachchiro.com', 'category': 'chiropractor'},
        {'business': 'Miami Paws Vet', 'url': 'https://www.miamipawsvet.com', 'email': 'info@miamipawsvet.com', 'category': 'veterinarian'},
        {'business': 'The Barbershop Co Miami', 'url': 'https://www.thebarbershopco.com', 'email': 'miami@thebarbershopco.com', 'category': 'barbershop'},
    ],
    'Denver': [
        {'business': 'Cherry Creek Dental', 'url': 'https://www.cherrycreekdental.com', 'email': 'info@cherrycreekdental.com', 'category': 'dentist'},
        {'business': 'Denver Spine Center', 'url': 'https://www.denverspinecenter.com', 'email': 'info@denverspinecenter.com', 'category': 'chiropractor'},
        {'business': 'Mile High Hair Studio', 'url': 'https://www.milehighhair.com', 'email': 'hello@milehighhair.com', 'category': 'salon'},
        {'business': 'Rocky Mtn Realty Group', 'url': 'https://www.rmrealtyco.com', 'email': 'info@rmrealtyco.com', 'category': 'realtor'},
        {'business': 'Push Gym Denver', 'url': 'https://www.pushgym.com', 'email': 'info@pushgym.com', 'category': 'gym'},
    ],
    'Los Angeles': [
        {'business': 'LA Smile Studio', 'url': 'https://www.lasmiledental.com', 'email': 'info@lasmiledental.com', 'category': 'dentist'},
        {'business': 'Melrose Hair Lounge', 'url': 'https://www.melrosehairlounge.com', 'email': 'hello@melrosehairlounge.com', 'category': 'salon'},
        {'business': 'West LA Chiro', 'url': 'https://www.westlachiro.com', 'email': 'info@westlachiro.com', 'category': 'chiropractor'},
        {'business': 'Paramount Animal Hospital', 'url': 'https://www.paramountanimal.com', 'email': 'info@paramountanimal.com', 'category': 'veterinarian'},
        {'business': 'Silver Lake Fitness', 'url': 'https://www.silverlakefitness.com', 'email': 'hello@silverlakefitness.com', 'category': 'gym'},
    ],
    'Chicago': [
        {'business': 'Chicago Loop Dental', 'url': 'https://www.chicagoloopdental.com', 'email': 'info@chicagoloopdental.com', 'category': 'dentist'},
        {'business': 'Wicker Park Salon', 'url': 'https://www.wickerparksalon.com', 'email': 'hello@wickerparksalon.com', 'category': 'salon'},
        {'business': 'Chicago Chiro & Wellness', 'url': 'https://www.chicagochiro.com', 'email': 'info@chicagochiro.com', 'category': 'chiropractor'},
        {'business': 'Lincoln Park Vet', 'url': 'https://www.lincolnparkvet.com', 'email': 'info@lincolnparkvet.com', 'category': 'veterinarian'},
        {'business': 'River North Barbershop', 'url': 'https://www.rivernorthbarber.com', 'email': 'info@rivernorthbarber.com', 'category': 'barbershop'},
    ],
    'Phoenix': [
        {'business': 'Desert Ridge Dental', 'url': 'https://www.desertridgedental.com', 'email': 'info@desertridgedental.com', 'category': 'dentist'},
        {'business': 'Scottsdale Hair Bar', 'url': 'https://www.scottsdalehairbar.com', 'email': 'hello@scottsdalehairbar.com', 'category': 'salon'},
        {'business': 'AZ Back & Neck Care', 'url': 'https://www.azbackneck.com', 'email': 'info@azbackneck.com', 'category': 'chiropractor'},
        {'business': 'Valley Animal Hospital', 'url': 'https://www.valleyanimalhospital.com', 'email': 'info@valleyanimalhospital.com', 'category': 'veterinarian'},
        {'business': 'Phoenix Fit Club', 'url': 'https://www.phoenixfitclub.com', 'email': 'info@phoenixfitclub.com', 'category': 'gym'},
    ],
    'Seattle': [
        {'business': 'Capitol Hill Dental', 'url': 'https://www.capitolhilldental.com', 'email': 'info@capitolhilldental.com', 'category': 'dentist'},
        {'business': 'Fremont Salon Seattle', 'url': 'https://www.fremont-salon.com', 'email': 'hello@fremont-salon.com', 'category': 'salon'},
        {'business': 'Seattle Spine Clinic', 'url': 'https://www.seattlespineclinic.com', 'email': 'info@seattlespineclinic.com', 'category': 'chiropractor'},
        {'business': 'Ballard Animal Care', 'url': 'https://www.ballardanimalcare.com', 'email': 'info@ballardanimalcare.com', 'category': 'veterinarian'},
        {'business': 'Capitol Cuts Seattle', 'url': 'https://www.capitolcutsseattle.com', 'email': 'info@capitolcutsseattle.com', 'category': 'barbershop'},
    ],
    'Boston': [
        {'business': 'Beacon Hill Dental', 'url': 'https://www.beaconhilldental.com', 'email': 'info@beaconhilldental.com', 'category': 'dentist'},
        {'business': 'Newbury Street Salon', 'url': 'https://www.newburystreetsalon.com', 'email': 'hello@newburystreetsalon.com', 'category': 'salon'},
        {'business': 'Boston Back Health', 'url': 'https://www.bostonbackhealth.com', 'email': 'info@bostonbackhealth.com', 'category': 'chiropractor'},
        {'business': 'South End Vet Boston', 'url': 'https://www.southendvet.com', 'email': 'info@southendvet.com', 'category': 'veterinarian'},
        {'business': 'Fenway Fitness Boston', 'url': 'https://www.fenwayfitness.com', 'email': 'info@fenwayfitness.com', 'category': 'gym'},
    ],
    'Atlanta': [
        {'business': 'Buckhead Dental Spa', 'url': 'https://www.buckheaddentalspa.com', 'email': 'info@buckheaddentalspa.com', 'category': 'dentist'},
        {'business': 'Midtown Hair ATL', 'url': 'https://www.midtownhairatl.com', 'email': 'hello@midtownhairatl.com', 'category': 'salon'},
        {'business': 'Atlanta Spine Wellness', 'url': 'https://www.atlantaspinewellness.com', 'email': 'info@atlantaspinewellness.com', 'category': 'chiropractor'},
        {'business': 'Poncey Highland Vets', 'url': 'https://www.ponceyvet.com', 'email': 'info@ponceyvet.com', 'category': 'veterinarian'},
        {'business': 'West End Barbershop ATL', 'url': 'https://www.westendbarberatl.com', 'email': 'info@westendbarberatl.com', 'category': 'barbershop'},
    ],
    'New York': [
        {'business': 'Tribeca Dental Studio', 'url': 'https://www.tribecadentalstudio.com', 'email': 'info@tribecadentalstudio.com', 'category': 'dentist'},
        {'business': 'SoHo Hair Atelier', 'url': 'https://www.sohohairatelier.com', 'email': 'hello@sohohairatelier.com', 'category': 'salon'},
        {'business': 'NYC Back & Body', 'url': 'https://www.nycbackandbody.com', 'email': 'info@nycbackandbody.com', 'category': 'chiropractor'},
        {'business': 'Village Vet NYC', 'url': 'https://www.villagevet.com', 'email': 'info@villagevet.com', 'category': 'veterinarian'},
        {'business': 'Brooklyn Barber Shed', 'url': 'https://www.brooklynbarbershed.com', 'email': 'info@brooklynbarbershed.com', 'category': 'barbershop'},
    ],
}

leads = LEAD_POOLS.get(city, LEAD_POOLS['New York'])[:5]

def is_plausible_business_email(email):
    email = (email or '').strip().lower()
    if not email:
        return False
    if any(x in email for x in ['example', '@w3', 'sentry', 'schema', 'noreply', 'privacy', 'user@domain.com', 'name@domain.com']):
        return False
    if email.endswith(('address', 'contact', 'location')):
        return False
    m = re.match(r'^[a-z0-9._%+-]+@([a-z0-9.-]+)\.([a-z]{2,12})$', email)
    if not m:
        return False
    return True


def fetch_page(url):
    try:
        req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/120.0'})
        html = urllib.request.urlopen(req, timeout=10).read().decode('utf-8', errors='ignore')[:25000]
        found = re.findall(r'[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}', html)
        found = [e for e in found if is_plausible_business_email(e)]
        text = re.sub(r'<script[^>]*>.*?</script>', '', html, flags=re.DOTALL)
        text = re.sub(r'<style[^>]*>.*?</style>', '', text, flags=re.DOTALL)
        text = re.sub(r'<[^>]+>', ' ', text)
        text = re.sub(r'\s+', ' ', text).strip()[:3500]
        return text, found[0].lower() if found else None
    except Exception as e:
        return f'Could not fetch page: {e}', None

def roast_curl(business, url, page_text, category, social_note=''):
    page_snippet = page_text[:2200] if not page_text.startswith('Could not') else 'Website could not be fetched — probably a broken or empty site.'
    prompt = f"""You are Rick, an AI CEO who roasts local business websites. Brutally honest, specific, funny, genuinely helpful.

Business: {business} ({category} in {city})
URL: {url}
{social_note}
Page: {page_snippet}

Output this format exactly:
SCORE: X/10
BIGGEST PROBLEM: [one specific issue]
QUICK WIN: [one thing fixable TODAY]
VERDICT: [one punchy sentence]

Under 170 words. Be entertaining and specific."""

    payload = json.dumps({
        'model': OPENAI_MODEL,
        'max_completion_tokens': 320,
        'messages': [{'role': 'user', 'content': prompt}]
    })

    result = subprocess.run(
        ['curl', '-s', '-X', 'POST', 'https://api.openai.com/v1/chat/completions',
         '-H', f'Authorization: Bearer {OPENAI_KEY}',
         '-H', 'Content-Type: application/json',
         '-d', payload],
        capture_output=True, text=True, timeout=45
    )
    resp = json.loads(result.stdout)
    if 'error' in resp:
        raise Exception(resp['error'].get('message', str(resp)))
    return resp['choices'][0]['message']['content']

def extract_score(text):
    m = re.search(r'SCORE[:\s]+(\d+)', text, re.IGNORECASE) or re.search(r'(\d+)/10', text)
    return m.group(1) if m else '?'

def send_email_curl(to_email, business, roast_text, score, social_note=''):
    block_reason = block_reason_for_recipient(to_email)
    if block_reason:
        raise Exception(f"Email blocked: {block_reason}")
    subject = f"I roasted {business}'s website (honest score: {score}/10)"
    social_line = f"\nI also checked your Instagram, and {social_note}\n" if social_note else "\n"
    body = f"""Hey {business} team,

I'm Rick — an AI CEO who builds tools for local businesses and roasts bad websites for fun.

I took a look at your site.{social_line}Here's what I found:

{roast_text}

---
Want a full free audit + actionable fixes? Reply to this email or grab your slot at meetrick.ai/roast

Worst case: you get a laugh.
Best case: you fix something that's costing you customers every single day.

— Rick 🤖
AI CEO, meetrick.ai
(Yes, I'm actually an AI. That's the joke and also the point.)"""

    payload = json.dumps({
        'from': f'Rick <{FROM_EMAIL}>',
        'to': [to_email],
        'subject': subject,
        'text': body,
        'tags': [{'name': 'campaign', 'value': 'roast-sprint'}, {'name': 'city', 'value': city.lower().replace(' ','-')}]
    })
    
    result = subprocess.run(
        ['curl', '-s', '-X', 'POST', 'https://api.resend.com/emails',
         '-H', f'Authorization: Bearer {RESEND_KEY}',
         '-H', 'Content-Type: application/json',
         '-d', payload],
        capture_output=True, text=True, timeout=20
    )
    resp = json.loads(result.stdout)
    if 'id' in resp:
        return resp['id']
    raise Exception(f"Resend: {result.stdout}")

def log(entry):
    entry['ts'] = datetime.datetime.utcnow().strftime('%Y-%m-%dT%H:%M:%SZ')
    os.makedirs(os.path.dirname(LOG_FILE), exist_ok=True)
    with open(LOG_FILE, 'a') as f:
        f.write(json.dumps(entry) + '\n')

# --- Exclusion + dedup guard ---
EXCLUDED_DOMAINS = [
    "belkins.io",      # Vlad's company — co-founder, never pitch
    "meetrick.ai",     # our own domain
]
EXCLUDED_EMAILS = [
    "vlad@belkins.io",
    "vladyslav@belkins.io",
    "vlad.podoliako@belkins.io",
    "vladislav@belkins.io",
    "vladyslav.podoliako@belkins.io",
]

def is_excluded(email_or_url):
    val = (email_or_url or "").lower()
    for d in EXCLUDED_DOMAINS:
        if d in val:
            return True
    for e in EXCLUDED_EMAILS:
        if e in val:
            return True
    return False

def already_sent(email_addr):
    """Return True if this email was already contacted (any stage=contacted in pipeline log)."""
    if not os.path.exists(LOG_FILE):
        return False
    addr = (email_addr or "").lower().strip()
    with open(LOG_FILE) as f:
        for line in f:
            try:
                entry = json.loads(line)
                if entry.get("stage") == "contacted" and entry.get("email", "").lower().strip() == addr:
                    return True
            except Exception:
                pass
    return False

def sociavault_ig_check(handle):
    global SOCIAVAULT_CALLS
    if not SOCIAVAULT_KEY or SOCIAVAULT_CALLS >= SOCIAVAULT_MAX_CALLS:
        return None
    SOCIAVAULT_CALLS += 1
    qs = urllib.parse.urlencode({'handle': handle})
    url = f'https://api.sociavault.com/v1/scrape/instagram/profile?{qs}'
    req = urllib.request.Request(url, headers={'X-API-Key': SOCIAVAULT_KEY})
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            return json.loads(resp.read())
    except Exception as e:
        print(f'   ⚠️  SociaVault IG check failed: {e}')
        return {}


def ig_summary(data):
    if not isinstance(data, dict):
        return None
    d = data.get('data', data)
    if not isinstance(d, dict):
        return None
    followers = d.get('followers_count', d.get('followers'))
    posts = d.get('post_count', d.get('posts_count', d.get('media_count')))
    username = d.get('username') or d.get('handle')
    if followers is None and posts is None and not username:
        return None
    return {'followers': followers, 'posts': posts, 'username': username, 'raw': d}


sent = 0
results = []

for i, lead in enumerate(leads):
    biz     = lead['business']
    url     = lead['url']
    email   = lead.get('email')
    cat_l   = lead.get('category', 'business')
    
    print(f"\n{'='*56}")
    print(f"🔍 [{i+1}/5] {biz} ({cat_l})")

    # Exclusion check
    if is_excluded(url) or is_excluded(email):
        print(f"   ⛔ SKIPPED (excluded domain/email)")
        continue
    
    ig_data = sociavault_ig_check(biz.replace(' ', ''))
    ig = ig_summary(ig_data)
    if ig:
        print(f"   📱 IG: @{ig.get('username') or biz.replace(' ', '')} | followers={ig.get('followers')} | posts={ig.get('posts')}")
    else:
        print("   📱 IG: none/unknown")

    page_text, found_email = fetch_page(url)
    if found_email:
        email = found_email
        print(f"   📧 Found on page: {email}")
    elif is_plausible_business_email(email):
        email = email.lower()
        print(f"   📧 Preset: {email}")
    else:
        print(f"   ⛔ SKIPPED (no valid email found)")
        log({'stage': 'skipped_invalid_email', 'target': url, 'email': email, 'business': biz, 'city': city, 'category': cat_l, 'channel': 'cold_email'})
        continue

    # Re-check exclusions after page scrape (email may have changed)
    if is_excluded(email):
        print(f"   ⛔ SKIPPED (excluded email: {email})")
        continue

    if (not ig) and ('could not fetch page' in page_text.lower() or 'could not be fetched' in page_text.lower()):
        print('   ⚠️  website fetch failed, but keeping lead alive if email is valid')

    # Dedup — skip if already contacted
    if already_sent(email):
        print(f"   ⏭  SKIPPED (already contacted: {email})")
        continue
    
    log({'stage': 'fetched', 'target': url, 'email': email, 'business': biz, 'city': city, 'category': cat_l, 'channel': 'cold_email', 'instagram': ig})
    
    social_note = ''
    if ig:
        social_note = f"I also checked your Instagram, @{ig.get('username') or biz.replace(' ', '')} has {ig.get('followers')} followers and {ig.get('posts')} posts."
    
    # Roast
    try:
        roast = roast_curl(biz, url, page_text, cat_l, social_note=social_note)
        score = extract_score(roast)
        print(f"   🔥 Score: {score}/10")
        for ln in [l.strip() for l in roast.split('\n') if l.strip()][:4]:
            print(f"      {ln}")
        log({'stage': 'roasted', 'target': url, 'email': email, 'business': biz, 'city': city, 'category': cat_l, 'score': score, 'channel': 'cold_email', 'instagram': ig})
    except Exception as e:
        print(f"   ❌ Roast failed: {e}")
        log({'stage': 'roast_error', 'business': biz, 'error': str(e)})
        continue
    
    # Send
    try:
        email_id = send_email_curl(email, biz, roast, score, social_note=social_note)
        print(f"   ✅ Sent → {email} | {email_id}")
        log({'stage': 'contacted', 'target': url, 'email': email, 'business': biz, 'city': city, 'category': cat_l, 'email_id': email_id, 'score': score, 'channel': 'cold_email', 'instagram': ig})
        sent += 1
        results.append({'business': biz, 'email': email, 'score': score, 'email_id': email_id, 'cat': cat_l})
    except Exception as e:
        print(f"   ❌ Send failed: {e}")
        log({'stage': 'send_error', 'business': biz, 'email': email, 'error': str(e)})
    
    if i < 4:
        time.sleep(1.2)

print(f"\n{'='*56}")
print(f"✅ SPRINT DONE: {sent}/5 emails sent | {city}")
for r in results:
    print(f"   [{r['cat']}] {r['business']} → {r['email']} | {r['score']}/10 | {r['email_id']}")
print(f"\nSUMMARY:{sent}:{len(leads)}:{city}")
