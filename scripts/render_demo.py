"""Mux a real Playwright recording with chapter-aligned synthetic narration."""
import json
import os
import subprocess
from pathlib import Path

source = Path(os.environ.get("DEMO_OUTPUT", "/tmp/trustchain-demo-final"))
output = Path("artifacts/demo")
output.mkdir(parents=True, exist_ok=True)
chapters = json.loads((source / "chapters.json").read_text())
inputs = []
filters = []
for index, chapter in enumerate(chapters):
    script = source / f"narration-{index}.txt"
    wav = source / f"narration-{index}.wav"
    script.write_text(chapter["narration"])
    subprocess.run(["espeak-ng", "-v", "en-us", "-s", "150", "-p", "45", "-f", str(script), "-w", str(wav)], check=True)
    inputs += ["-i", str(wav)]
    filters.append(f"[{index}:a]adelay={int(chapter['start'] * 1000)}:all=1[a{index}]")
duration = chapters[-1]["end"]
filters.append("".join(f"[a{i}]" for i in range(len(chapters))) +
               f"amix=inputs={len(chapters)}:normalize=0,loudnorm=I=-16:TP=-1.5:LRA=11,apad,atrim=duration={duration}[out]")
narration = source / "narration.wav"
subprocess.run(["ffmpeg", "-y", *inputs, "-filter_complex", ";".join(filters),
                "-map", "[out]", str(narration)], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
video = output / "TrustChain-End-to-End-Demo.mp4"
subprocess.run(["ffmpeg", "-y", "-i", str(source / "trustchain-demo.webm"), "-i", str(narration),
                "-map", "0:v:0", "-map", "1:a:0", "-c:v", "libx264", "-preset", "fast", "-crf", "22",
                "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "160k", "-ar", "48000", "-movflags", "+faststart",
                "-t", str(duration), str(video)], check=True)
transcript = "# TrustChain demo transcript\n\nRecorded locally using real model/search services and Anvil chain evidence. Narration is synthetic.\n\n"
for chapter in chapters:
    transcript += f"## {chapter['start']:.0f}s — {chapter['title']}\n\n{chapter['narration']}\n\n"
(output / "transcript.md").write_text(transcript)
(output / "chapters.json").write_text(json.dumps(chapters, indent=2))
print(video)
