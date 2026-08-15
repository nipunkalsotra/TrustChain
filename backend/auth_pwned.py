"""
auth_pwned.py — breach check against Have I Been Pwned's Pwned Passwords
API, via the k-anonymity range endpoint (plan §11.3, "Credential-stuffing
defence": "optional breach check against Have I Been Pwned using the
k-anonymity range API").

k-anonymity means the password itself — and even its full hash — never
leaves this process: only the first 5 hex characters of its SHA-1 hash
are sent to the API (https://api.pwnedpasswords.com/range/{prefix}),
which returns every known-breached hash sharing that prefix (thousands of
candidates, per HIBP's own design) with its suffix and a breach count.
This process then checks the FULL hash's suffix against that list
locally — the API operator never learns which specific password (or even
which specific hash) was checked, only that "some password with this
5-char prefix" was.

Applied at signup only, not login: login must keep accepting whatever
password an existing account already has (including a legacy PBKDF2 hash
predating this check, or one chosen before this feature existed) — an
existing credential can't retroactively become invalid without warning,
that's a account-lockout footgun, not a security improvement. Blocking a
*new* signup from choosing an already-breached password is the actual
"credential-stuffing defence" this closes: a breached password is one
attackers already have in their stuffing lists, so accepting it directly
hands them a working credential for whichever account creation it's
attached to next.

Fails OPEN on any network/API error (timeout, DNS failure, non-200,
malformed response) — same posture as rate_limit.py's Redis-unavailable
handling (see its own docstring): signup staying available shouldn't
depend on a third-party API's uptime. A failure is logged so an actual
outage is visible, but never surfaces as a signup-blocking 5xx.
"""

import hashlib

import httpx
import structlog

logger = structlog.get_logger()

_RANGE_API_URL = "https://api.pwnedpasswords.com/range/{prefix}"


async def is_password_pwned(password: str, timeout_seconds: float = 3.0) -> bool:
    """True if `password`'s SHA-1 hash appears in HIBP's breach corpus.
    False both when it genuinely doesn't AND when the check couldn't be
    completed (fail-open — see module docstring)."""
    # SHA-1 isn't a choice here — it's the exact algorithm HIBP's Pwned
    # Passwords range API requires callers to hash with (see
    # https://haveibeenpwned.com/API/v3#PwnedPasswords), and this use
    # doesn't rely on SHA-1's (broken) collision resistance at all, only
    # on matching a fixed, pre-published corpus — usedforsecurity=False
    # tells bandit/OpenSSL's FIPS mode this isn't a cryptographic use of
    # the hash, which is accurate here even though the FEATURE built on
    # top of it is security-related.
    sha1_hex = hashlib.sha1(password.encode("utf-8"), usedforsecurity=False).hexdigest().upper()
    prefix, suffix = sha1_hex[:5], sha1_hex[5:]

    try:
        async with httpx.AsyncClient(timeout=timeout_seconds) as client:
            response = await client.get(
                _RANGE_API_URL.format(prefix=prefix),
                # Padding adds decoy response lines so response SIZE alone
                # can't be used to narrow down the real prefix's breach
                # count via traffic analysis — HIBP supports this at no
                # extra cost, so there's no reason not to ask for it.
                headers={"Add-Padding": "true"},
            )
            response.raise_for_status()
    except httpx.HTTPError as e:
        logger.warning("pwned_passwords_check_failed", error=str(e))
        return False

    for line in response.text.splitlines():
        candidate_suffix, _, _count = line.partition(":")
        if candidate_suffix.strip().upper() == suffix:
            return True
    return False
