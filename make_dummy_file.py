from pathlib import Path
Path('/Users/rickthebot/.openclaw/workspace/dummy-video-2kb.mp4').write_bytes(b'x'*2048)
print('done')
