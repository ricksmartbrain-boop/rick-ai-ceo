from pathlib import Path

BASE = Path('/Users/rickthebot/rick-vault/projects/proof')

email_block = """## 2026-04-16 09:24 PT
- platform: Email
- handle: Nikola Djordjevic
- quote: \"Sounds great, Rick. Please feel free to reach out if any questions come up. I'm here to help.\"
- context: Positive direct reply to the Meetrick idea.
- note: Private email; treat as testimonial candidate pending permission to quote publicly.
"""

testimonial_line = '- Nikola Djordjevic via email (2026-04-16): "Sounds great, Rick. Please feel free to reach out if any questions come up. I\'m here to help."\n'

tweet_block = """## 2026-04-16 — Social Proof: Nikola said sounds great

Draft:
A founder just replied to the Meetrick idea with a clean \"Sounds great, Rick.\" That kind of signal beats vibes every time.

→ https://meetrick.ai
"""


def append_if_missing(path: Path, needle: str, block: str) -> bool:
    text = path.read_text() if path.exists() else ""
    if needle in text:
        return False
    prefix = "" if not text or text.endswith("\n") else "\n"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('a', encoding='utf-8') as f:
        f.write(prefix + block)
        if not block.endswith("\n"):
            f.write("\n")
    return True


changes = []
changes.append(('social-proof-log.md', append_if_missing(BASE / 'social-proof-log.md', 'Nikola Djordjevic', email_block)))
changes.append(('testimonials.md', append_if_missing(BASE / 'testimonials.md', 'Nikola Djordjevic via email', testimonial_line)))
changes.append(('tweet-queue.md', append_if_missing(BASE / 'tweet-queue.md', '2026-04-16 — Social Proof: Nikola said sounds great', tweet_block)))

for name, changed in changes:
    status = 'appended' if changed else 'skipped'
    print(f'{name}: {status}')
