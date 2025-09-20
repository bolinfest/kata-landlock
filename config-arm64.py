#!/usr/bin/env python3
"""Derive the vendored arm64 kernel config from the upstream containerization repo."""

from __future__ import annotations

import argparse
import difflib
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable
from urllib.request import urlopen

REPO_ROOT = Path(__file__).resolve().parent
LOCAL_CONFIG_PATH = REPO_ROOT / "config-arm64"
UPSTREAM_URL = (
    "https://raw.githubusercontent.com/apple/containerization/"
    "51ef9f81fef574bbd815d4f5560157297b0a4067/kernel/config-arm64"
)
EXPECTED_CONFIG_LSM = (
    "landlock,lockdown,yama,loadpin,safesetid,integrity,bpf,apparmor"
)


@dataclass(frozen=True)
class ConfigOverride:
    key: str
    value: str
    insert_after: str | None = None


CONFIG_OVERRIDES: tuple[ConfigOverride, ...] = (
    ConfigOverride("CONFIG_SECURITY", "y"),
    ConfigOverride("CONFIG_SECURITY_LANDLOCK", "y", insert_after="CONFIG_SECURITY"),
    ConfigOverride("CONFIG_LSM", f'"{EXPECTED_CONFIG_LSM}"'),
)


def fetch_upstream_lines() -> list[str]:
    with urlopen(UPSTREAM_URL, timeout=30) as response:  # noqa: S310
        content = response.read().decode("utf-8")
    return content.splitlines(keepends=True)


def _line_matches_key(line: str, key: str) -> bool:
    return line.startswith(f"{key}=")


def _line_disables_key(line: str, key: str) -> bool:
    return line.startswith(f"# {key} is not set")


def _ensure_insert_after(lines: list[str], marker: str) -> int:
    for index, line in enumerate(lines):
        if line.startswith(marker):
            return index
    return len(lines) - 1


def apply_overrides(lines: list[str]) -> list[str]:
    for override in CONFIG_OVERRIDES:
        desired_line = f"{override.key}={override.value}\n"
        for index, line in enumerate(lines):
            if _line_matches_key(line, override.key) or _line_disables_key(line, override.key):
                lines[index] = desired_line
                break
        else:
            insert_at = _ensure_insert_after(lines, override.insert_after or override.key)
            lines.insert(insert_at + 1, desired_line)
    return lines


def unified_diff(from_lines: Iterable[str], to_lines: Iterable[str], *, from_label: str, to_label: str) -> str:
    diff = difflib.unified_diff(
        list(from_lines),
        list(to_lines),
        fromfile=from_label,
        tofile=to_label,
    )
    return "".join(diff)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--write",
        action="store_true",
        help="Update config-arm64 with the derived configuration instead of just checking.",
    )
    return parser.parse_args()


def enforce_expected_values(lines: list[str]) -> None:
    for line in lines:
        if line.startswith("CONFIG_LSM="):
            if line.rstrip().split("=", maxsplit=1)[1].strip('"') != EXPECTED_CONFIG_LSM:
                raise SystemExit(
                    "Derived configuration has unexpected CONFIG_LSM value:"
                    f" {line.strip()}"
                )
            return
    raise SystemExit("Derived configuration is missing CONFIG_LSM")


def main() -> None:
    args = parse_args()

    upstream_lines = fetch_upstream_lines()
    derived_lines = apply_overrides(list(upstream_lines))
    enforce_expected_values(derived_lines)

    upstream_vs_derived = unified_diff(
        upstream_lines,
        derived_lines,
        from_label="upstream/config-arm64",
        to_label="derived/config-arm64",
    )
    if upstream_vs_derived:
        print("==> Diff against upstream:")
        print(upstream_vs_derived, end="")
    else:
        print("Derived configuration matches upstream with no differences.")

    if not LOCAL_CONFIG_PATH.exists():
        print(f"Vendored config missing at {LOCAL_CONFIG_PATH}", file=sys.stderr)
        if args.write:
            LOCAL_CONFIG_PATH.write_text("".join(derived_lines), encoding="utf-8")
            print(f"Wrote derived configuration to {LOCAL_CONFIG_PATH}")
            return
        raise SystemExit(1)

    current_lines = LOCAL_CONFIG_PATH.read_text(encoding="utf-8").splitlines(keepends=True)
    if current_lines == derived_lines:
        print(f"Vendored config matches derived output at {LOCAL_CONFIG_PATH}")
        return

    repo_diff = unified_diff(
        current_lines,
        derived_lines,
        from_label=f"repo/{LOCAL_CONFIG_PATH.name}",
        to_label="derived/config-arm64",
    )
    print("==> Vendored config differs from derived output:")
    print(repo_diff, end="")

    if args.write:
        LOCAL_CONFIG_PATH.write_text("".join(derived_lines), encoding="utf-8")
        print(f"Updated {LOCAL_CONFIG_PATH} to match derived configuration")
    else:
        raise SystemExit(
            "Vendored config does not match derived output."
            " Rerun with --write to update the file."
        )


if __name__ == "__main__":
    main()
