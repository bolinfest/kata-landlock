# kata-landlock

Use the macOS recommended kernel config settings as the basis of building a landlock-enabled kernel that is designed to work with `container` on macOS.

This makes it possible to use [Codex with Linux sandboxing](https://github.com/openai/codex/blob/main/docs/platform-sandboxing.md) in a container on a Mac, which historically was not easy to do with Docker.

## Repository Overview

- `build.py` top-level build script that compiles the kernel for `container --kernel`.
- `Dockerfile` execution environment used to compile the kernel.
- `config-arm64` derivative of [`kernel/config-arm64`](https://github.com/apple/containerization/blob/51ef9f81fef574bbd815d4f5560157297b0a4067/kernel/config-arm64) from the [`apple/containerization`](https://github.com/apple/containerization) repository.
- `config-arm64.py` script used to derive and verify `config-arm64`, printing the diff against the upstream template.
- `copy-codex.py` script that downloads the latest Codex CLI release via `gh` and copies it into a running container.

## Usage

**1. Ensure the Tahoe services are running.**

```shell
container system start
```

You can allocate more resources to the builder if needed:

```shell
container builder start --cpus 12 --memory 18G
```

**2. Build the kernel (customize as needed).**

```shell
python3 ./build.py [--kernel-branch v6.14.9] [--output-dir kernel-out]
```

The default output directory is `kernel-out`, which will contain the compiled Image.

**3. Start a container with the freshly built kernel** (replace `kernel-out/Image` if you changed the output directory).

```shell
container run -it --kernel kernel-out/Image ubuntu:latest
```

**4. Copy the Codex CLI into the container.** Locate the container identifier with `container ls`. `copy-codex.py` fetches the latest release from `github.com/openai/codex`, so you must be authenticated with `gh`.

```shell
python3 ./copy-codex.py CONTAINER_ID
```

**5. Verify Landlock is operational inside the container.**

```shell
codex debug landlock -- ls
```

If Landlock is not properly enabled, you will see an error similar to:

```
/root# codex debug landlock -- ls

thread 'main' panicked at linux-sandbox/src/linux_run_main.rs:28:9:
error running landlock: Sandbox(LandlockRestrict)
note: run with `RUST_BACKTRACE=1` environment variable to display a backtrace
```

## Maintaining the kernel config

Run `python3 ./config-arm64.py` to compare the vendored `config-arm64` against the upstream template. The script prints the diff between upstream and the derived configuration and exits non-zero if the checked-in file does not match. Use `--write` to update the repository copy after reviewing the diff.

## Caveats

More research/sanity-checking needs to be done on `config-arm64` to ensure the appropriate config values are enabled (and no critical config values are missing).
