import json
from pathlib import Path
root = Path('/Users/rickthebot/rick-vault/projects/outreach')
lead_files = [
    'sociavault-leads.jsonl',
    'warm-pipeline.jsonl',
    'roast-leads.jsonl',
    'leads-founders-2026-04-21.jsonl',
    'leads-south-midwest-2026-04-21.jsonl',
    'founder-leads-2026-04-21.jsonl',
]
contacted = json.loads((root/'contacted.json').read_text())
contacted_emails = set()
for v in contacted.values():
    if isinstance(v, dict):
        e = str(v.get('email','')).strip().lower()
        if e:
            contacted_emails.add(e)
    elif isinstance(v, list):
        for x in v:
            if isinstance(x, dict):
                e = str(x.get('email','')).strip().lower()
                if e:
                    contacted_emails.add(e)

rows=[]
for fn in lead_files:
    p=root/fn
    if not p.exists():
        continue
    for i,line in enumerate(p.read_text().splitlines(),1):
        if not line.strip():
            continue
        try:
            obj=json.loads(line)
        except Exception:
            continue
        e=str(obj.get('email','')).strip().lower()
        if e and e not in contacted_emails:
            rows.append((fn,i,e,obj.get('name') or obj.get('business') or obj.get('company') or '',obj.get('context') or obj.get('title') or ''))
print(json.dumps(rows[:200], indent=2))
