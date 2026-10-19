"""Validate render.yaml with Render's current public schema; never deploy anything."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from urllib.request import urlopen

import jsonschema
import yaml

SCHEMA_URL = "https://render.com/schema/render.yaml.json"
PROJECT_ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--schema", type=Path, help="Use a previously downloaded official schema.")
    parser.add_argument("--blueprint", type=Path, default=PROJECT_ROOT / "render.yaml")
    args = parser.parse_args()
    try:
        blueprint = yaml.safe_load(args.blueprint.read_text(encoding="utf-8"))
        if args.schema:
            schema = json.loads(args.schema.read_text(encoding="utf-8"))
        else:
            with urlopen(SCHEMA_URL, timeout=30) as response:
                schema = json.load(response)
        validator_type = jsonschema.validators.validator_for(schema)
        validator_type.check_schema(schema)
        errors = sorted(validator_type(schema).iter_errors(blueprint), key=lambda e: str(e.path))
        if errors:
            for error in errors:
                path = ".".join(str(part) for part in error.absolute_path) or "<root>"
                print(f"INVALID {path}: {error.message}", file=sys.stderr)
            return 1
    except (OSError, ValueError, yaml.YAMLError, jsonschema.SchemaError) as exc:
        print(f"Blueprint validation could not finish: {type(exc).__name__}", file=sys.stderr)
        return 2
    print("Render Blueprint: valid against the official JSON schema.")
    print("This checks structure only. No cloud resources were created or updated.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
