"""Dumps the live FastAPI app's OpenAPI schema to a file.

Pure in-process introspection of route/model definitions — `main.py`'s
`lifespan` never runs under a plain import, so this needs no live
Postgres/Redis/chain connection (verified: works with a bare env, no
.env file, no running services).

Used two places: `release.yml` snapshots this as a GitHub Release asset
on every tagged release, and `test.yml`'s `api-compat-check` job diffs
the current commit's output against the previous release's snapshot
via `oasdiff breaking`. See docs/api-deprecation-policy.md.
"""

import json
import os
import sys

# main.py's own internal imports (`import auth`, `import db`, ...) assume
# backend/ itself is on sys.path — true when run as `python3 main.py` from
# backend/, but NOT when this script is invoked as
# `python3 backend/scripts/generate_openapi_schema.py` from the repo root
# (Python puts backend/scripts/ on sys.path[0] in that case, not backend/).
# Inserting backend/ explicitly makes this work regardless of invocation cwd.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def main() -> None:
    from main import app

    output_path = sys.argv[1] if len(sys.argv) > 1 else "openapi.json"
    with open(output_path, "w") as f:
        json.dump(app.openapi(), f, indent=2, sort_keys=True)
    print(f"wrote {output_path}")


if __name__ == "__main__":
    main()
