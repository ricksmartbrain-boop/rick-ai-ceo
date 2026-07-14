#!/usr/bin/env python3
"""
Generate a talking-head video from an avatar image and text script.

Uses ElevenLabs for TTS and VEED Fabric 1.0 (via Fal) for video synthesis.

Usage:
  python3 generate.py --image IMAGE --voice VOICE --script "TEXT" --output OUTPUT.mp4
  python3 generate.py --image IMAGE --voice-id ID --script "TEXT" --output OUTPUT.mp4
  python3 generate.py --image IMAGE --audio AUDIO.mp3 --output OUTPUT.mp4  # skip TTS

Voice shortcuts: rick/chris, iris/jessica, remy/liam
Costs: ~$0.30-0.50 per short video (30-60s)
"""

import argparse
import json
import os
import sys
import urllib.request

VOICES = {
    # Add voice shortcuts: "name": ("voice_id", "model_id", {settings})
    # Find IDs at: https://elevenlabs.io/app/voice-library
}


def get_elevenlabs_key():
    for path in [
        os.path.expanduser("~/.config/elevenlabs/api_key"),
        os.path.expanduser("~/.config/elevenlabs/api_key.env"),
    ]:
        if os.path.exists(path):
            content = open(path).read().strip()
            for line in content.splitlines():
                if line.startswith("ELEVENLABS_API_KEY="):
                    return line.split("=", 1)[1].strip()
                if not line.startswith("#") and len(line) > 10:
                    return line
    key = os.environ.get("ELEVENLABS_API_KEY", "")
    if key:
        return key
    print("Error: ElevenLabs API key not found", file=sys.stderr)
    print("  Set at ~/.config/elevenlabs/api_key or ELEVENLABS_API_KEY env var", file=sys.stderr)
    sys.exit(1)


def generate_audio(voice_id, model_id, settings, script, output_path):
    """Generate TTS audio via ElevenLabs API."""
    url = f"https://api.elevenlabs.io/v1/text-to-speech/{voice_id}"
    data = json.dumps({
        "text": script,
        "model_id": model_id,
        "voice_settings": settings
    }).encode()
    req = urllib.request.Request(url, data=data, headers={
        "xi-api-key": get_elevenlabs_key(),
        "Content-Type": "application/json",
        "Accept": "audio/mpeg"
    })
    with urllib.request.urlopen(req) as resp:
        with open(output_path, "wb") as f:
            f.write(resp.read())
    return output_path


def generate_video(image_path, audio_path, output_path, resolution="720p"):
    """Generate talking-head video via VEED Fabric 1.0 on Fal."""
    try:
        import fal_client
    except ImportError:
        print("Error: fal_client not installed. Run: pip3 install fal-client", file=sys.stderr)
        sys.exit(1)

    image_url = fal_client.upload_file(image_path)
    audio_url = fal_client.upload_file(audio_path)

    result = fal_client.run("veed/fabric-1.0", arguments={
        "image_url": image_url,
        "audio_url": audio_url,
        "resolution": resolution
    })

    video_url = result["video"]["url"]
    urllib.request.urlretrieve(video_url, output_path)
    return output_path


def add_subtitles(video_path, output_path):
    """Add auto-subtitles via Fal workflow utility."""
    import fal_client

    video_url = fal_client.upload_file(video_path)
    result = fal_client.run("fal-ai/workflow-utilities/auto-subtitle", arguments={
        "video_url": video_url,
        "language": "en",
        "font": "Montserrat",
        "font_size": 100,
        "font_weight": "bold",
        "font_color": "white",
        "highlight_color": "yellow",
        "stroke_width": 3,
        "stroke_color": "black",
        "background_color": "none",
        "background_opacity": 0,
        "position": "bottom",
        "y_offset": 75,
        "words_per_subtitle": 2,
        "animation": "off"
    })

    sub_url = result["video"]["url"]
    urllib.request.urlretrieve(sub_url, output_path)
    return output_path


def main():
    parser = argparse.ArgumentParser(description="Generate talking-head avatar video")
    parser.add_argument("--image", required=True, help="Path to avatar image (front-facing, 512x512+)")
    parser.add_argument("--voice", help="Voice shortcut name")
    parser.add_argument("--voice-id", help="ElevenLabs voice ID (overrides --voice)")
    parser.add_argument("--model", default="eleven_turbo_v2_5", help="ElevenLabs model ID")
    parser.add_argument("--script", help="Text script for TTS")
    parser.add_argument("--audio", help="Pre-generated audio file (skips TTS)")
    parser.add_argument("--output", required=True, help="Output video path (.mp4)")
    parser.add_argument("--resolution", default="720p", choices=["480p", "720p"])
    parser.add_argument("--subtitles", action="store_true", help="Add auto-subtitles")
    args = parser.parse_args()

    if not args.audio and not args.script:
        print("Error: provide --script (for TTS) or --audio (pre-generated)", file=sys.stderr)
        sys.exit(1)

    audio_path = args.audio
    if not audio_path:
        if args.voice and args.voice.lower() in VOICES:
            voice_id, model_id, settings = VOICES[args.voice.lower()]
        elif args.voice_id:
            voice_id = args.voice_id
            model_id = args.model
            settings = {"stability": 0.5, "similarity_boost": 0.75, "style": 0.3}
        else:
            print("Error: provide --voice or --voice-id for TTS", file=sys.stderr)
            sys.exit(1)

        audio_path = args.output.rsplit(".", 1)[0] + "-audio.mp3"
        print(f"Generating audio -> {audio_path}")
        generate_audio(voice_id, model_id, settings, args.script, audio_path)
        print(f"Audio OK ({os.path.getsize(audio_path) / 1024:.0f} KB)")

    print(f"Generating video -> {args.output}")
    generate_video(args.image, audio_path, args.output, args.resolution)
    size_mb = os.path.getsize(args.output) / (1024 * 1024)
    print(f"Video OK ({size_mb:.2f} MB)")

    if args.subtitles:
        sub_output = args.output.rsplit(".", 1)[0] + "-subtitled.mp4"
        print(f"Adding subtitles -> {sub_output}")
        add_subtitles(args.output, sub_output)
        size_mb = os.path.getsize(sub_output) / (1024 * 1024)
        print(f"Subtitled OK ({size_mb:.2f} MB)")


if __name__ == "__main__":
    main()
