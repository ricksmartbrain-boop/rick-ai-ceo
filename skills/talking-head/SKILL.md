# Talking Head — Avatar Video Generation

Generate lip-synced avatar videos from text scripts. ElevenLabs for TTS, VEED Fabric 1.0 (via Fal) for video synthesis.

## Quick Start

```bash
python3 skills/talking-head/scripts/generate.py \
  --image ~/assets/rick-avatar.png \
  --voice-id YOUR_VOICE_ID \
  --script "Hey, this is Rick. MeetRick.ai just crossed $547 MRR." \
  --output ~/Desktop/rick-video.mp4
```

## Options

| Flag | Purpose |
|------|---------|
| `--image` | Front-facing avatar image (512x512+ JPG/PNG) |
| `--voice` | Voice shortcut (configure in script) |
| `--voice-id` | Direct ElevenLabs voice ID |
| `--script` | Text to speak |
| `--audio` | Pre-recorded audio (skips TTS) |
| `--output` | Output .mp4 path |
| `--resolution` | 480p or 720p (default) |
| `--subtitles` | Auto-add subtitles |

## API Keys

- ElevenLabs: `~/.config/elevenlabs/api_key` or `ELEVENLABS_API_KEY` env
- Fal: `~/.config/fal/api_key` or `FAL_KEY` env

## Costs

- ElevenLabs TTS: ~$0.15-0.30/min audio
- Fal Fabric 1.0: ~$0.10-0.20/video
- Total: ~$0.30-0.50 per short video (30-60s)

## Tips

- Keep scripts under 60 seconds
- Use consistent avatar for brand recognition
- Test with short phrase first
- Add `--subtitles` for social media
