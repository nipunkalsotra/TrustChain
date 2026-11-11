# README media

The hero and diagrams are original code-generated motion graphics and SVGs.
Product screenshots and preview GIFs are extracted from the actual recorded
TrustChain session. They do not replace application content with mock data.
The top 1600×900 crop removes the recording’s chapter-caption overlay.

See [media-provenance.json](media-provenance.json) for the source file hash,
screenshot timestamps, clip ranges, and processing details. Product previews
run at 8 fps and 720 pixels wide to keep the repository README lightweight.

Regenerate from the repository root:

```sh
.venv/bin/python scripts/render_readme.py
```

The generator uses Pillow, FFmpeg, and the locally installed Adwaita / Readex
fonts named in the script. It reads the existing local demo MP4. The root
README deliberately has no full-video link; a Drive URL can be added later.
