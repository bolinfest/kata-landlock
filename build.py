#!/usr/bin/env python3
"""Build and export the arm64 kernel image using the Tahoe container runtime."""

import argparse
import json
import subprocess
from pathlib import Path

MIN_CPUS = 8
MIN_MEMORY_BYTES = 8 * 1024 * 1024 * 1024  # 8 GiB

DEFAULT_KERNEL_BRANCH = "v6.14.9"
DEFAULT_OUTPUT_DIR = Path("kernel-out")


def run(cmd, *, check=True, capture_output=False, stdout=None, stderr=None, text=True):
    """Run a subprocess command and return the CompletedProcess."""
    return subprocess.run(
        cmd,
        check=check,
        capture_output=capture_output,
        stdout=stdout,
        stderr=stderr,
        text=text,
    )


def start_container_system() -> None:
    """Ensure the Tahoe container services are running."""
    run(
        ["container", "system", "start"],
        check=False,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def _json_from_command(cmd):
    result = run(cmd, capture_output=True)
    return json.loads(result.stdout)


def fetch_builder_resources() -> tuple[int, int]:
    """Return (cpus, memory_bytes) from container status JSON."""
    data = _json_from_command(["container", "builder", "status", "--format", "json"])
    data = data[0]
    resources = data.get("configuration", {}).get("resources", {})
    cpus = resources.get("cpus")
    memory_bytes = resources.get("memoryInBytes")
    if cpus is not None and memory_bytes is not None:
        return int(cpus), int(memory_bytes)
    raise RuntimeError("Unable to determine builder resources")


def host_limits() -> tuple[int, int]:
    cpus = int(run(["sysctl", "-n", "hw.ncpu"], capture_output=True).stdout.strip())
    memory_bytes = int(run(["sysctl", "-n", "hw.memsize"], capture_output=True).stdout.strip())
    return cpus, memory_bytes


def format_gib(bytes_value: int) -> str:
    return f"{bytes_value / (1024**3):.1f} GiB"


def format_memory_flag(bytes_value: int) -> str:
    gib = 1024**3
    mib = 1024**2
    kib = 1024
    if bytes_value % gib == 0:
        return f"{bytes_value // gib}G"
    if bytes_value % mib == 0:
        return f"{bytes_value // mib}M"
    if bytes_value % kib == 0:
        return f"{bytes_value // kib}K"
    return str(bytes_value)


def ensure_resources(requirement_disabled: bool) -> None:
    if requirement_disabled:
        return
    try:
        cpus, memory_bytes = fetch_builder_resources()
    except RuntimeError as exc:
        raise SystemExit(f"Failed to inspect container resources: {exc}") from exc

    host_cpus, host_memory_bytes = host_limits()
    issues: list[str] = []

    if cpus < MIN_CPUS:
        issues.append(f"- Allocated CPUs: {cpus} (minimum required: {MIN_CPUS})")
    if memory_bytes < MIN_MEMORY_BYTES:
        issues.append(
            f"- Allocated memory: {format_gib(memory_bytes)} (minimum required: {format_gib(MIN_MEMORY_BYTES)})"
        )

    if not issues:
        return

    recommended_cpus = min(max(MIN_CPUS, cpus), host_cpus)
    recommended_memory_bytes = min(max(MIN_MEMORY_BYTES, memory_bytes), host_memory_bytes)
    recommended_memory = format_memory_flag(recommended_memory_bytes)

    messages = [
        "Container builder resources are below the recommended minimum:",
        *issues,
        "",
        "Recommended commands:",
        "  container builder stop",
        f"  container builder start --cpus {recommended_cpus} --memory {recommended_memory}",
        "",
        f"Host limits: CPUs={host_cpus}, Memory={format_gib(host_memory_bytes)}",
        "",
        "Rerun with --ignore-resource-check to bypass this validation.",
    ]
    raise SystemExit("\n".join(messages))


def parse_args():
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument(
        "--ignore-resource-check",
        action="store_true",
        help="Skip validation of container builder CPU and memory limits.",
    )
    parser.add_argument(
        "--kernel-branch",
        default=DEFAULT_KERNEL_BRANCH,
        help="Kernel branch or tag to build.",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=DEFAULT_OUTPUT_DIR,
        help="Directory where exported kernel artifacts should be written.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    image_tag = "aarch64-fast-kernel"
    kernel_branch = args.kernel_branch
    out_dir = args.output_dir.expanduser()
    host_mount = out_dir if out_dir.is_absolute() else Path.cwd() / out_dir

    start_container_system()
    ensure_resources(args.ignore_resource_check)

    print(f"==> Building image with Tahoe container (kernel {kernel_branch})…")
    run(
        [
            "container",
            "build",
            "--build-arg",
            f"KERNEL_BRANCH={kernel_branch}",
            "--target",
            "export",
            "-t",
            f"{image_tag}:export",
            ".",
        ]
    )

    out_dir.mkdir(parents=True, exist_ok=True)

    print(f"==> Exporting artifacts to {out_dir} via bind mount…")
    run(
        [
            "container",
            "run",
            "--rm",
            "-v",
            f"{host_mount}:/out",
            f"{image_tag}:export",
        ],
        stdout=subprocess.DEVNULL,
    )

    print("==> Done. Artifacts:")
    run(["ls", "-al", str(out_dir)])


if __name__ == "__main__":
    main()
