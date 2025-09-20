#!/usr/bin/env python3
"""Download the latest Codex CLI release and copy it into a running container."""

from __future__ import annotations

import argparse
import json
import os
import shlex
import stat
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path

REPO = "openai/codex"
ASSET_NAME = "codex-aarch64-unknown-linux-musl.tar.gz"
DEST_PATH = "/usr/local/bin/codex"


def format_process_error(prefix: str, exc: subprocess.CalledProcessError) -> str:
    details = ""
    if exc.stderr:
        if isinstance(exc.stderr, bytes):
            details = exc.stderr.decode("utf-8", "replace").strip()
        else:
            details = exc.stderr.strip()
    elif exc.stdout:
        if isinstance(exc.stdout, bytes):
            details = exc.stdout.decode("utf-8", "replace").strip()
        else:
            details = exc.stdout.strip()
    message = f"{prefix} (exit code {exc.returncode})."
    if details:
        message += f"\n{details}"
    return message


def ensure_authentication() -> None:
    if os.environ.get("GH_TOKEN"):
        return
    command = [
        "gh",
        "auth",
        "status",
        "--hostname",
        "github.com",
    ]
    try:
        subprocess.run(
            command,
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
        )
    except FileNotFoundError as exc:
        print(
            "GitHub CLI ('gh') is not installed or not on PATH. "
            "Install it from https://cli.github.com/ before running this script.",
            file=sys.stderr,
        )
        sys.exit(exc.errno or 1)
    except subprocess.CalledProcessError as exc:
        message = format_process_error(
            "GitHub CLI is not authenticated for github.com",
            exc,
        )
        message += (
            "\nAuthenticate via `gh auth login --hostname github.com` "
            "or set GH_TOKEN for this session."
        )
        print(message, file=sys.stderr)
        sys.exit(exc.returncode or 1)


def list_containers() -> None:
    print("Available containers:", file=sys.stderr)
    try:
        subprocess.run(["container", "ls"], check=False)
    except FileNotFoundError as exc:
        print(
            "The `container` CLI is not installed or not on PATH."
            " Install the Tahoe CLI before running this script.",
            file=sys.stderr,
        )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument(
        "container_id",
        nargs="?",
        help="Container ID or name where the Codex CLI should be installed.",
    )
    parser.add_argument(
        "--asset-name",
        default=ASSET_NAME,
        help="Release asset name to download from the latest codex release.",
    )
    parser.add_argument(
        "--dest-path",
        default=DEST_PATH,
        help="Destination path inside the container for the Codex binary.",
    )
    parser.add_argument(
        "--list-containers",
        action="store_true",
        help="List available containers (via `container ls`).",
    )

    args = parser.parse_args()

    if args.list_containers:
        list_containers()
        if not args.container_id:
            sys.exit(0)

    if not args.container_id:
        parser.print_usage(sys.stderr)
        list_containers()
        print("error: container_id is required", file=sys.stderr)
        sys.exit(2)

    return args


def fetch_latest_asset(asset_name: str) -> tuple[str, dict]:
    command = [
        "gh",
        "api",
        "-H",
        "Accept: application/vnd.github+json",
        f"/repos/{REPO}/releases/latest",
    ]
    result = subprocess.run(command, check=True, capture_output=True, text=True)
    release = json.loads(result.stdout)
    assets = release.get("assets", [])
    for asset in assets:
        if asset.get("name") == asset_name:
            return release.get("tag_name", ""), asset
    asset_names = ", ".join(asset.get("name", "<unknown>") for asset in assets)
    raise RuntimeError(
        f"Asset {asset_name!r} not found in latest release. Available assets: {asset_names}"
    )


def download_asset(asset_id: int, destination: Path) -> None:
    command = [
        "gh",
        "api",
        "-H",
        "Accept: application/octet-stream",
        f"/repos/{REPO}/releases/assets/{asset_id}",
    ]
    destination.parent.mkdir(parents=True, exist_ok=True)
    with destination.open("wb") as stream:
        subprocess.run(command, check=True, stdout=stream, stderr=subprocess.PIPE)


def ensure_executable(target: Path) -> None:
    current_mode = target.stat().st_mode
    target.chmod(current_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


def _cleanup_parent_dirs(path: Path, stop_dir: Path) -> None:
    parent = path
    while parent != stop_dir and parent.parent != parent:
        try:
            parent.rmdir()
        except OSError:
            break
        parent = parent.parent


def prepare_binary(asset_path: Path, asset_name: str) -> Path:
    workdir = asset_path.parent
    lowered = asset_name.lower()

    if lowered.endswith((".tar.gz", ".tgz")):
        try:
            with tarfile.open(asset_path, mode="r:gz") as archive:
                members = [entry for entry in archive.getmembers() if entry.isfile()]
                member = next(
                    (
                        entry
                        for entry in members
                        if Path(entry.name).name in {"codex", "codex.exe"}
                        or Path(entry.name).name.startswith("codex-")
                    ),
                    None,
                )
                if member is None:
                    if not members:
                        raise RuntimeError(
                            f"Archive {asset_name!r} does not contain any files"
                        )
                    member = members[0]
                archive.extract(member, path=workdir, filter="data")
        except (tarfile.TarError, OSError) as exc:
            raise RuntimeError(f"Failed to extract {asset_name}: {exc}") from exc

        extracted_path = workdir / member.name
        target_path = workdir / "codex"
        extracted_parent = extracted_path.parent
        if target_path.exists():
            target_path.unlink()
        try:
            extracted_path.replace(target_path)
        except OSError:
            target_path.write_bytes(extracted_path.read_bytes())
            extracted_path.unlink()

        _cleanup_parent_dirs(extracted_parent, workdir)
        asset_path.unlink(missing_ok=True)
        ensure_executable(target_path)
        return target_path

    if lowered.endswith(".zst"):
        raise RuntimeError(
            f"Unsupported asset compression for {asset_name!r}; use the .tar.gz asset"
        )

    ensure_executable(asset_path)
    return asset_path


def copy_binary(container_id: str, source_path: Path, destination: str) -> None:
    quoted_dest = shlex.quote(destination)
    command = [
        "container",
        "exec",
        "-i",
        container_id,
        "sh",
        "-c",
        f"cat > {quoted_dest} && chmod +x {quoted_dest}",
    ]
    with source_path.open("rb") as src:
        subprocess.run(command, stdin=src, check=True, stderr=subprocess.PIPE)


def main() -> None:
    args = parse_args()
    container_id = args.container_id
    asset_name = args.asset_name
    dest_path = args.dest_path

    ensure_authentication()

    try:
        tag_name, asset = fetch_latest_asset(asset_name)
    except (subprocess.CalledProcessError, json.JSONDecodeError, RuntimeError) as exc:
        if isinstance(exc, subprocess.CalledProcessError):
            reason = format_process_error(
                f"Failed to inspect latest release for {REPO}",
                exc,
            )
            lower_reason = reason.lower()
            if "saml enforcement" in lower_reason:
                reason += (
                    "\nRun `gh auth refresh -h github.com -s org:read` to enable SSO for "
                    "the OpenAI organization."
                )
            elif "gh auth login" in lower_reason or "get started with github cli" in lower_reason:
                reason += "\nAuthenticate via `gh auth login --hostname github.com`."
        else:
            reason = f"Failed to inspect latest release for {REPO}: {exc}"
        print(reason, file=sys.stderr)
        sys.exit(1)

    asset_id = asset.get("id")
    if asset_id is None:
        print(f"Latest release asset missing id: {asset}", file=sys.stderr)
        sys.exit(1)

    with tempfile.TemporaryDirectory(prefix="codex-download-") as tmpdir:
        tmpdir_path = Path(tmpdir)
        asset_path = tmpdir_path / asset_name
        try:
            download_asset(int(asset_id), asset_path)
        except subprocess.CalledProcessError as exc:
            message = format_process_error(
                f"Failed to download {asset_name} from release {tag_name}",
                exc,
            )
            print(message, file=sys.stderr)
            sys.exit(exc.returncode)

        try:
            binary_path = prepare_binary(asset_path, asset_name)
        except RuntimeError as exc:
            print(str(exc), file=sys.stderr)
            sys.exit(1)

        if not os.access(binary_path, os.R_OK):
            print(f"Prepared binary is not readable: {binary_path}", file=sys.stderr)
            sys.exit(1)
        if binary_path.stat().st_size == 0:
            print(f"Prepared binary is empty: {binary_path}", file=sys.stderr)
            sys.exit(1)

        print(
            f"Copying {asset_name} from release {tag_name or '<unknown>'} into {container_id}…",
            file=sys.stderr,
        )
        try:
            copy_binary(container_id, binary_path, dest_path)
        except subprocess.CalledProcessError as exc:
            message = format_process_error(
                f"Failed to copy codex binary to container {container_id}",
                exc,
            )
            print(message, file=sys.stderr)
            sys.exit(exc.returncode)

    print(f"Installed Codex CLI at {dest_path} in container {container_id}")


if __name__ == "__main__":
    main()
