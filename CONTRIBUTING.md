# Contributing to Rick AI CEO

Rick is open source under MIT. Here's how to contribute.

## Adding a New Skill

Skills live in `~/.openclaw/workspace/skills/`. Each skill is a directory with a `SKILL.md` file that tells Rick what the skill does and how to use it.

### Skill Structure
```
skills/
└── your-skill-name/
    ├── SKILL.md        # Required: skill documentation
    └── scripts/        # Optional: helper scripts
        └── run.sh
```

### SKILL.md Format
```markdown
# Skill: Your Skill Name

## Commands
- `/your-command` — What it does

## Triggers
- When [event] happens, this skill [action]

## Process
1. Step one
2. Step two

## Requirements
- Any API keys or dependencies needed
```

### Submitting
1. Fork this repo
2. Create your skill directory
3. Add SKILL.md with clear documentation
4. Open a PR with a description of what the skill does

## Reporting Bugs

Use the [Bug Report template](https://github.com/ricksmartbrain-boop/rick-ai-ceo/issues/new?template=bug_report.md).

## Feature Requests

Use the [Feature Request template](https://github.com/ricksmartbrain-boop/rick-ai-ceo/issues/new?template=feature_request.md).

## Questions?

- Help Center: [meetrick.ai/help](https://meetrick.ai/help)
- Twitter: [@MeetRickAI](https://x.com/MeetRickAI)
- Telegram: [@MeetRickAI](https://t.me/MeetRickAI)
