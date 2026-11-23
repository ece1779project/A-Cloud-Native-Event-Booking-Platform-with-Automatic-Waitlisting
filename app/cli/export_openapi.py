"""Writes the generated OpenAPI document for the contract check in CI:
``python -m app.cli.export_openapi > openapi.json``.

Keys are sorted so that two runs produce identical output and a diff shows
only real contract changes.

Design: TECHNICAL_DESIGN.md Section 8.1.
"""

import json
import sys

from app.main import create_app


def main() -> None:
    spec = create_app().openapi()
    json.dump(spec, sys.stdout, indent=2, sort_keys=True)
    sys.stdout.write("\n")


if __name__ == "__main__":
    main()
