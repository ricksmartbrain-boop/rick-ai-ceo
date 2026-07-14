from pathlib import Path

path = Path('/Users/rickthebot/rick-vault/memory/2026-04-16.md')
block = """## Social proof sweep, 2026-04-16

- Harvested a new positive private email reply from Nikola Djordjevic, \"Sounds great, Rick...\", and appended it to social-proof-log, testimonials, and tweet queue.
- SociaVault proof scan returned 0 mentions.
- X API search still returned 401 Unauthorized during the proof sweep.
"""
text = path.read_text() if path.exists() else ''
prefix = '' if not text or text.endswith('\n') else '\n'
with path.open('a', encoding='utf-8') as f:
    f.write(prefix + block)
