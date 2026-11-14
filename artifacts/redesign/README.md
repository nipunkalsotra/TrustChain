# TrustChain — Evidence in focus

The redesigned local product uses an espresso brown, warm white, and green palette across the landing page, authentication, all workspace pages, recovery screens, pricing, and error states.

The cinematic tour is recorded from the real production frontend with real application actions. Cursor indicators, chapter labels, and animated field-note cards are presentation overlays. They do not fabricate application results. The disposable SDK credential is masked during capture and revoked before the tour ends.

[Watch the new cinematic tour on Google Drive](https://drive.google.com/file/d/1sM2GhKTrmnHkojWA4kJU5ybNCBuHza0F/view?usp=sharing) · [Final report PDF](TrustChain-Redesign-Final-Report.pdf) · [Detailed report](final-report.md)

**5:26 · 1080p · 30 fps · no audio.**

The existing reference video in `artifacts/demo/` is preserved.

## Reproduce the recording

Start the complete local stack and production frontend, build the TypeScript SDK, and use the console email backend for the disposable tour account:

```sh
TMPDIR=/tmp TRUSTCHAIN_DEMO_EMAIL_LOG=/path/to/console-api.log \
  DEMO_OUTPUT=/tmp/trustchain-cinematic-demo node scripts/record_cinematic_demo.mjs
DEMO_OUTPUT=/tmp/trustchain-cinematic-demo python3 scripts/render_cinematic_demo.py
```

The renderer refuses to produce a final video from an incomplete recording. It checks the duration, frame size, absence of audio, and streaming metadata. The generated chapter list and metadata accompany the MP4.

## Verification evidence

The final report and structured test summary describe the completed checks and their limits. Screenshots named `workspace-*` and `mobile-*` come from explicit API fixtures for repeatable visual inspection; the video and live workflow screenshots use actual local services.
