from pathlib import Path

BASE = Path('/Users/rickthebot/rick-vault/projects/proof')

email_block = """## 2026-04-16 09:26 PT
- platform: Email
- handle: Nikola Djordjevic
- quote: \"Great to hear. Feel free to reach out with any questions after you've had a chance to look them over.\"
- context: Positive follow-up reply in the Meetrick thread.
- note: Private email; treat as testimonial candidate pending permission to quote publicly.
"""

testimonial_line = '- Nikola Djordjevic via email (2026-04-16): "Great to hear. Feel free to reach out with any questions after you\'ve had a chance to look them over."\n'

tweet_block = """## 2026-04-16 — Social Proof: Nikola said great to hear

Draft:
A founder replied in the thread: \"Great to hear.\" Short, calm validation is the good stuff.

→ https://meetrick.ai
"""


def append_if_missing(path: Path, needle: str, block: str) -> bool:
    text = path.read_text() if path.exists() else ''
    if needle in text:
        return False
    prefix = '' if not text or text.endswith('\n') else '\n'
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('a', encoding='utf-8') as f:
        f.write(prefix + block)
        if not block.endswith('\n'):
            f.write('\n')
    return True


changes = []
changes.append(('social-proof-log.md', append_if_missing(BASE / 'social-proof-log.md', 'Great to hear. Feel free to reach out', email_block)))
changes.append(('testimonials.md', append_if_missing(BASE / 'testimonials.md', 'Great to hear. Feel free to reach out', testimonial_line)))
changes.append(('tweet-queue.md', append_if_missing(BASE / 'tweet-queue.md', '2026-04-16 — Social Proof: Nikola said great to hear', tweet_block)))

for name, changed in changes:
    status = 'appended' if changed else 'skipped'
    print(f'{name}: {status}')
