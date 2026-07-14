import os, json, subprocess, time
from datetime import datetime, timezone

api_key = os.environ['RESEND_API_KEY']
pipeline_path = os.path.expanduser('~/rick-vault/logs/pipeline.jsonl')

leads = [
    {"business":"Simply Bliss Medical Spa","email":"contact@simplyblissmedspa.com","city":"Omaha","state":"NE","category":"med spa","website":"https://simplyblissmedspa.com"},
    {"business":"Omaha Med Spa","email":"omahamedspa@gmail.com","city":"Omaha","state":"NE","category":"med spa","website":"https://www.omahamedspa.com"},
    {"business":"SB Health & Beauty Med Spa","email":"info@sbspa.com","city":"Tampa","state":"FL","category":"med spa","website":"https://www.sbspa.com"},
    {"business":"SOHO Wellness & Med Spa","email":"info@soho-wellness.com","city":"Tampa","state":"FL","category":"med spa","website":"https://soho-wellness.com"},
    {"business":"Foundation Physical Therapy","email":"info@foundationptutah.com","city":"Salt Lake City","state":"UT","category":"physical therapist","website":"https://foundationptutah.com"},
    {"business":"PhysioElite","email":"wesley@physioeliteusa.com","city":"Salt Lake City","state":"UT","category":"physical therapist","website":"https://www.physioeliteusa.com"},
    {"business":"Cassis Dermatology & Aesthetics Center","email":"frontoffice@cassisderm.com","city":"Louisville","state":"KY","category":"dermatologist","website":"https://cassisderm.com"},
    {"business":"Allied Roofing Inc","email":"officemanager@alliedroofinginc.com","city":"Columbus","state":"OH","category":"roofing company","website":"https://alliedroofinginc.com"},
    {"business":"Columbus Roofing Co","email":"website@columbusroofingco.com","city":"Columbus","state":"OH","category":"roofing company","website":"https://columbusroofingco.com"},
    {"business":"Mighty Dog Roofing Columbus West","email":"jmugler@mightydogroofing.com","city":"Columbus","state":"OH","category":"roofing company","website":"https://www.mightydogroofing.com/columbus-west-oh/"},
]

# Append the already-verified Atelier send from the test send
atelier_record = {
    'ts': '2026-04-26T15:15:18Z',
    'stage': 'contacted',
    'sprint': 'Lead Scrape + Blast',
    'business': 'Atelier Medspa Omaha',
    'email': 'ateliermedspaomaha@gmail.com',
    'city': 'Omaha',
    'state': 'NE',
    'category': 'med spa',
    'website': 'https://www.ateliermedspaomaha.com',
    'channel': 'cold_email',
    'provider': 'resend',
    'provider_id': '0528ceb3-740a-4719-9f2c-3b0c44583747',
    'subject': 'Atelier Medspa Omaha — your website is probably leaving money on the table',
    'status': 'sent',
}

with open(pipeline_path, 'a') as f:
    f.write(json.dumps(atelier_record) + '\n')

sent = []
for lead in leads:
    subject = f"{lead['business']} — your website is probably leaving money on the table"
    body = (
        f"Hey {lead['business']},\n\n"
        f"Quick roast from a friendly AI founder: the work looks real, but the website is probably not pulling its weight yet.\n"
        f"If you want, I can roast it properly and turn that into a punchier homepage.\n\n"
        f"Drop it here: https://meetrick.ai/roast\n\n"
        f"— Rick\nRick <rick@meetrick.ai>"
    )
    payload = {
        'from': 'Rick <rick@meetrick.ai>',
        'to': [lead['email']],
        'subject': subject,
        'text': body,
    }
    res = subprocess.run([
        'curl', '-sS', '-X', 'POST', 'https://api.resend.com/emails',
        '-H', f'Authorization: Bearer {api_key}',
        '-H', 'Content-Type: application/json',
        '--data', json.dumps(payload),
    ], capture_output=True, text=True)
    if res.returncode != 0:
        print('ERR', lead['business'], lead['email'], 'rc', res.returncode, res.stderr.strip(), res.stdout.strip())
        continue
    try:
        resp = json.loads(res.stdout)
    except Exception:
        print('ERR', lead['business'], lead['email'], 'bad_json', res.stdout.strip())
        continue
    rid = resp.get('id')
    record = {
        'ts': datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z'),
        'stage': 'contacted',
        'sprint': 'Lead Scrape + Blast',
        'business': lead['business'],
        'email': lead['email'],
        'city': lead['city'],
        'state': lead['state'],
        'category': lead['category'],
        'website': lead['website'],
        'channel': 'cold_email',
        'provider': 'resend',
        'provider_id': rid,
        'subject': subject,
        'status': 'sent',
    }
    with open(pipeline_path, 'a') as f:
        f.write(json.dumps(record) + '\n')
    sent.append(record)
    print('SENT', lead['business'], lead['email'], rid)
    time.sleep(0.7)

print('TOTAL_SENT', len(sent) + 1)
