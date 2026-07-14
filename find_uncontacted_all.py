import json
from pathlib import Path
root=Path('/Users/rickthebot/rick-vault/projects/outreach')
contacted=json.loads((root/'contacted.json').read_text())
contacted_emails=set()
for v in contacted.values():
    if isinstance(v, dict):
        e=str(v.get('email','')).strip().lower()
        if e: contacted_emails.add(e)
    elif isinstance(v, list):
        for x in v:
            if isinstance(x, dict):
                e=str(x.get('email','')).strip().lower()
                if e: contacted_emails.add(e)
rows=[]
for p in root.rglob('*'):
    if p.is_dir() or p.suffix not in {'.jsonl','.json'}:
        continue
    if p.name=='contacted.json':
        continue
    try:
        text=p.read_text()
    except Exception:
        continue
    if '"email"' not in text and 'email' not in text:
        continue
    if p.suffix=='.jsonl':
        for i,line in enumerate(text.splitlines(),1):
            if not line.strip():
                continue
            try:
                obj=json.loads(line)
            except Exception:
                continue
            e=str(obj.get('email','')).strip().lower()
            if e and e not in contacted_emails:
                rows.append((p.name,i,e,obj.get('name') or obj.get('business') or obj.get('company') or '',obj.get('context') or obj.get('title') or ''))
    else:
        try:
            obj=json.loads(text)
        except Exception:
            continue
        def walk(v,prefix=''):
            if isinstance(v, dict):
                e=str(v.get('email','')).strip().lower()
                if e and e not in contacted_emails:
                    rows.append((p.name,prefix,e,v.get('name') or v.get('business') or v.get('company') or '',v.get('context') or v.get('title') or ''))
                for k,val in v.items():
                    walk(val, f'{prefix}.{k}' if prefix else k)
            elif isinstance(v,list):
                for idx,val in enumerate(v):
                    walk(val,f'{prefix}[{idx}]')
        walk(obj)
print(json.dumps(rows[:300], indent=2))
