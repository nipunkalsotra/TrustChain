# TrustChain demo

Upload TrustChain-End-to-End-Demo.mp4 to Google Drive. The video uses H.264
and AAC audio in an MP4 container with fast-start metadata for streaming.
The completed video is 4 minutes 23 seconds at 1600×1000.

The walkthrough records the actual local application using real Groq/Tavily
calls, PostgreSQL/Redis, SDK HTTP calls, and V2 contracts on Anvil. Email
tokens come from the console email backend. External email delivery and
public-chain deployment are outside this recording. Narration is synthetic,
with chapter captions embedded in the recording. The disposable integration
key shown during creation is revoked in the demo.

The transcript and chapter timing file accompany the video.

To reproduce with the local stack and production frontend running:

    DEMO_OUTPUT=/tmp/trustchain-demo node scripts/record_demo.mjs
    DEMO_OUTPUT=/tmp/trustchain-demo python3 scripts/render_demo.py

Set TRUSTCHAIN_DEMO_EMAIL_LOG if the console API log is elsewhere than
/tmp/trustchain-audit/api.log. Build the TypeScript SDK before recording.
Run model-heavy test suites separately to avoid competing for provider quota.
