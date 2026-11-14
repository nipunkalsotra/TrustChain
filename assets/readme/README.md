# README media — espresso, warm white, and green

The root README's hero, product screenshots, and preview GIFs come from the
new **5:26 silent cinematic tour** in
`artifacts/redesign/TrustChain-Cinematic-Product-Tour.mp4`. The primary video
link is [the user-provided Drive upload](https://drive.google.com/file/d/1sM2GhKTrmnHkojWA4kJU5ybNCBuHza0F/view?usp=sharing).

The full video frame is preserved, including the animated chapter labels,
field-note cards, cursor, and progress indicators. These are presentation
overlays over real local application actions. Application data is not
replaced. The local MP4 hash is checked against its verified metadata before
extracting the product screenshots and previews.

The hero excerpt is 1100 pixels wide; product GIFs use 720 pixels at 8 fps.
Screenshots use 1280 pixels. SVG diagrams are original code assets using the
same brown, white, and green palette. The validation strip reports the dated
November 14 results; it is not a live CI or dependency-security badge.

`landing-desktop`, `landing-pipeline`, and `landing-integrity` show the actual
public landing interactions. `mobile-overview` and `mobile-proofs` use explicit
API fixtures for repeatable layout checks; the root README identifies those
images separately from the real recorded product gallery.

See [media-provenance.json](media-provenance.json) for the source hash, frame
timestamps, excerpt ranges, and supporting-image origins. The new recording,
report, scope, and verification evidence are documented in
[artifacts/redesign](../../artifacts/redesign/README.md).

Regenerate from the repository root with FFmpeg and the project Python:

```sh
.venv/bin/python scripts/render_readme.py
```
