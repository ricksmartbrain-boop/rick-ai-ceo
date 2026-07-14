from pathlib import Path

path = Path('/Users/rickthebot/rick-vault/memory/2026-04-16.md')
block = """- Nikola Djordjevic sent a second positive email reply, \"Great to hear...\", so the email proof count for the sweep is 2.
"""
text = path.read_text() if path.exists() else ''
prefix = '' if not text or text.endswith('\n') else '\n'
with path.open('a', encoding='utf-8') as f:
    f.write(prefix + block)
