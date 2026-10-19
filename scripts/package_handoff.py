"""Build an AI HUB source ZIP from an explicit allowlist, without runtime data."""
from __future__ import annotations

import argparse
import hashlib
from pathlib import Path
import zipfile

PROJECT_ROOT = Path(__file__).resolve().parents[1]
ROOT_FILES = {
    ".env.example", ".gitignore", ".python-version", "README.md", "pyproject.toml",
    "requirements.txt", "requirements-dev.txt", "render.yaml",
}
SOURCE_DIRS = {"app", "config", "docs", "skills", "tests", "scripts", ".github"}
SUFFIXES = {
    ".py", ".json", ".md", ".yaml", ".yml", ".toml", ".txt", ".ps1", ".sh",
    ".html", ".css", ".js", ".svg", ".png", ".webp", ".ico",
}
EXCLUDED_DIRS = {
    ".git", ".venv", "venv", "env", "__pycache__", ".pytest_cache", ".mypy_cache",
    ".ruff_cache", "node_modules", "data", "backups", "logs", "dist", "upload",
}


def source_files() -> list[Path]:
    result = [PROJECT_ROOT / name for name in ROOT_FILES if (PROJECT_ROOT / name).is_file()]
    result.append(PROJECT_ROOT / "agents.json")
    for dirname in sorted(SOURCE_DIRS):
        base = PROJECT_ROOT / dirname
        if not base.is_dir() or base.is_symlink():
            continue
        for path in base.rglob("*"):
            relative = path.relative_to(PROJECT_ROOT)
            if path.is_symlink() or any(part in EXCLUDED_DIRS for part in relative.parts):
                continue
            if any(part.startswith(".") and part != ".github" for part in relative.parts):
                continue
            if path.is_file() and path.suffix.lower() in SUFFIXES:
                result.append(path)
    return sorted(set(result), key=lambda p: p.relative_to(PROJECT_ROOT).as_posix())


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=PROJECT_ROOT / "dist/AI_HUB_Project.zip")
    args = parser.parse_args()
    files = source_files()
    missing = [str(path) for path in files if not path.is_file()]
    if missing:
        parser.error("A required source file is missing.")
    if any(path.is_symlink() for path in files):
        parser.error("Source symlinks are not supported in a handoff archive.")
    output = args.output.resolve()
    if output in {path.resolve() for path in files}:
        parser.error("The output must not replace a source file.")
    output.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for path in files:
            archive.write(path, "ai-hub/" + path.relative_to(PROJECT_ROOT).as_posix())
    checksum = hashlib.sha256(output.read_bytes()).hexdigest()
    print(f"Created {output.name}: {len(files)} source files, {output.stat().st_size} bytes")
    print(f"SHA-256: {checksum}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
