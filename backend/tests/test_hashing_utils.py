from blockchain.hashing_utils import AGENTS, compute_hash


def test_compute_hash_is_order_independent():
    config = AGENTS[0]
    reordered = dict(reversed(list(config.items())))
    assert compute_hash(config) == compute_hash(reordered)


def test_compute_hash_changes_when_model_changes():
    config = AGENTS[0]
    mutated = {**config, "model": "gpt-3.5-turbo"}
    assert compute_hash(config) != compute_hash(mutated)


def test_compute_hash_changes_when_prompt_changes():
    config = AGENTS[0]
    mutated = {**config, "systemPrompt": config["systemPrompt"] + " extra instruction"}
    assert compute_hash(config) != compute_hash(mutated)


def test_all_four_agents_have_distinct_hashes():
    hashes = [compute_hash(a) for a in AGENTS]
    assert len(hashes) == 4
    assert len(set(hashes)) == 4


def test_compute_hash_format():
    # web3's HexBytes.hex() has changed 0x-prefix behavior across versions —
    # client.py handles both via .removeprefix("0x"), so just check it's
    # valid 32-byte hex either way, not one specific prefixed form.
    h = compute_hash(AGENTS[0])
    stripped = h.removeprefix("0x")
    assert len(stripped) == 64
    int(stripped, 16)  # raises ValueError if not valid hex
