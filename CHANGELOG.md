## [1.0.1](https://github.com/nipunkalsotra/TrustChain/compare/v1.0.0...v1.0.1) (2026-11-15)


### Bug Fixes

* **deps:** preserve SQLAlchemy async greenlet dependency in grouped update ([1cbc5a0](https://github.com/nipunkalsotra/TrustChain/commit/1cbc5a0c61fb8aa1f9aabac3fba7af82c484019e))
* **deps:** retain SQLAlchemy 2.0 type contract while upgrading other packages ([4a46c59](https://github.com/nipunkalsotra/TrustChain/commit/4a46c599fa0bf5b9ff1e404ba8b14a1cda470f31))

# 1.0.0 (2026-11-15)


### Bug Fixes

* add working-directory to forge CI steps ([5fb35b8](https://github.com/nipunkalsotra/TrustChain/commit/5fb35b89f3e818f1ef654abb02d08add3329e6f3))
* **ci:** build SDK before frontend checks ([3fbf499](https://github.com/nipunkalsotra/TrustChain/commit/3fbf49985e34e4bbc646cc946939db3d387c2b75))
* **ci:** format backend/agents/llm_provider.py ([d3d65c1](https://github.com/nipunkalsotra/TrustChain/commit/d3d65c17707f9f0b5c4e462e50f2d47d454fe15a))
* **ci:** format backend/agents/mcp_result.py ([6df5cf0](https://github.com/nipunkalsotra/TrustChain/commit/6df5cf059a14303374ae40fd704d2e8e80ab4dc4))
* **ci:** format backend/blockchain/transaction_lock.py ([556133e](https://github.com/nipunkalsotra/TrustChain/commit/556133eb3f077878b3e3441d7e68f745e726eb23))
* **ci:** format backend/tests/test_reporter_output.py ([48cacd6](https://github.com/nipunkalsotra/TrustChain/commit/48cacd6161ecc74739d2814734a0000a325663cf))
* **ci:** restore TrustChain backend, SDK and real-chain workflows ([#109](https://github.com/nipunkalsotra/TrustChain/issues/109)) ([65d50c6](https://github.com/nipunkalsotra/TrustChain/commit/65d50c6e987e6b4c038d1a3ff1ea026d27deca05))
* **ci:** use Node 22-compatible test runner options ([e38f02a](https://github.com/nipunkalsotra/TrustChain/commit/e38f02acf4a199ba5699a2b34635dd3c54c76a96))
* **deps:** retain SQLAlchemy asyncio greenlet extra through upgrades ([1a47716](https://github.com/nipunkalsotra/TrustChain/commit/1a47716f5f434842ff36c0e62632460669562061))
* **release:** install lockfile-pinned release tooling verified by CI ([f0f5510](https://github.com/nipunkalsotra/TrustChain/commit/f0f551069d0837a782ace5b0de410f472b65b5bd))
* **release:** use writer-8-compatible release notes preset ([91a730a](https://github.com/nipunkalsotra/TrustChain/commit/91a730a5ad8c6138c7d591edbb85316ba9d517a8))
* **security:** upgrade vulnerable Python dependency pins in backend/requirements.txt ([405631f](https://github.com/nipunkalsotra/TrustChain/commit/405631f92a3eca97e4ca4a9cfe5f427d3308afad))
* **security:** upgrade vulnerable Python dependency pins in mcp_servers/blockchain/requirements.txt ([e5c1579](https://github.com/nipunkalsotra/TrustChain/commit/e5c1579693d12279c09f3e656ee66431ebfa7030))
* **security:** upgrade vulnerable Python dependency pins in mcp_servers/web_search/requirements.txt ([c31a227](https://github.com/nipunkalsotra/TrustChain/commit/c31a22711ce05b9ecb85d433ace2a27f38474ed3))


### Features

* add 4 LangGraph agents + blockchain bridge ([f094b51](https://github.com/nipunkalsotra/TrustChain/commit/f094b512e02b60e8d56f4bf5b12ba94079cd204d))
* add MCP servers and wire agents to MCP tools ([8d1b185](https://github.com/nipunkalsotra/TrustChain/commit/8d1b185b3d291e7ae48998560f4f7527af95114a))
* FastAPI backend with SSE streaming endpoints ([5453224](https://github.com/nipunkalsotra/TrustChain/commit/545322485c00d45d5d0fb38842bc77c364c3bc32))
* web3 bridge verified, structure clean, contracts connected ([1e5f3d6](https://github.com/nipunkalsotra/TrustChain/commit/1e5f3d62d78c9ec60486b7eb208cf88c08682947))
