"""Encode the silent product walkthrough. No audio stream is included."""
import hashlib
import json
import os
from pathlib import Path
import subprocess

source = Path(os.environ.get('DEMO_OUTPUT', '/tmp/trustchain-product-tour'))
output = Path('artifacts/demo')
output.mkdir(parents=True, exist_ok=True)
manifest = json.loads((source / 'manifest.json').read_text())
if not manifest['complete']:
    raise SystemExit('The real walkthrough did not finish; refusing to publish a partial demo.')
video = output / 'TrustChain-Silent-Product-Tour.mp4'
subprocess.run([
    'ffmpeg', '-y', '-hide_banner', '-i', str(source / 'source.webm'),
    '-ss', str(manifest['trimStartSeconds']), '-t', str(manifest['duration']),
    '-map', '0:v:0', '-an', '-vf', 'scale=1920:1080:flags=lanczos,fps=30',
    '-c:v', 'libx264', '-preset', 'medium', '-crf', '18', '-pix_fmt', 'yuv420p',
    '-movflags', '+faststart', str(video),
], check=True)
metadata = json.loads(subprocess.check_output([
    'ffprobe', '-v', 'error', '-show_entries',
    'format=duration,size:stream=codec_type,codec_name,width,height,r_frame_rate',
    '-of', 'json', str(video),
], text=True))
assert all(s['codec_type'] == 'video' for s in metadata['streams']), 'Audio must be absent.'
assert 270 <= float(metadata['format']['duration']) <= 340, 'Unexpected walkthrough duration.'
data = video.read_bytes()
metadata.update({
    'sha256': hashlib.sha256(data).hexdigest(),
    'fast_start': data.find(b'moov') < data.find(b'mdat'),
    'audio_tracks': 0,
    'run_id': manifest['runId'],
    'confirmed_pipeline_steps': manifest['confirmedSteps'],
    'presentation': manifest['presentation'],
    'theme': manifest['theme'],
    'chapters': manifest['chapters'],
    'verified_events': manifest['events'],
    'conditions': 'Real local application, real model/search calls, Anvil chain 31337, console email. No fabricated application results.',
    'editing': 'Initial loading frames trimmed; no application action speed-up.',
})
assert metadata['fast_start']
(output / 'silent-tour-metadata.json').write_text(json.dumps(metadata, indent=2) + '\n')
chapters = '# TrustChain — silent product walkthrough\n\nNo audio. Native dark UI, a visible cursor, click indicators, and short fading callouts.\n\n'
for c in manifest['chapters']:
    seconds = round(c['start'])
    chapters += '- **' + str(seconds // 60).zfill(2) + ':' + str(seconds % 60).zfill(2) + '** — ' + c['title'] + '\n'
(output / 'silent-tour-chapters.md').write_text(chapters)
print(json.dumps({'file': str(video), 'duration': metadata['format']['duration'], 'audio_tracks': 0}, indent=2))
