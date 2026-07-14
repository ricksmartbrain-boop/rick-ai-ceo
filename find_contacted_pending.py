import json
from pathlib import Path
p=Path('/Users/rickthebot/rick-vault/projects/outreach/contacted.json')
obj=json.loads(p.read_text())
rows=[]
for k,v in obj.items():
    if isinstance(v, dict):
        e=v.get('email')
        if e and not v.get('email_sent'):
            rows.append((k,e,v))
    elif isinstance(v, list):
        for x in v:
            if isinstance(x, dict):
                e=x.get('email')
                if e and not x.get('email_sent'):
                    rows.append((k,e,x))
print(json.dumps(rows[:200], indent=2))
