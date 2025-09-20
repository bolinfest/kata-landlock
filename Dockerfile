# syntax=docker/dockerfile:1.7
# ===== Build stage: compiles an arm64 kernel with fast-boot settings =====
FROM ubuntu:24.04 AS build

ENV DEBIAN_FRONTEND=noninteractive \
    TZ=Etc/UTC \
    LC_ALL=C.UTF-8 \
    LANG=C.UTF-8

# 1) Toolchain & helpers (no tzdata prompt)
RUN --mount=type=cache,target=/var/cache/apt --mount=type=cache,target=/var/lib/apt \
    apt-get update && apt-get install -y --no-install-recommends \
    build-essential bc bison flex \
    clang llvm lld \
    libelf-dev libssl-dev \
    libncurses5-dev libncursesw5-dev \
    dwarves ccache git fakeroot curl wget rsync \
    python3 zstd ca-certificates \
 && rm -rf /var/lib/apt/lists/*

# 2) Pick kernel version (override with: --build-arg KERNEL_BRANCH=v6.6.52)
ARG KERNEL_BRANCH=v6.6
RUN git clone --depth=1 --branch ${KERNEL_BRANCH} \
  https://git.kernel.org/pub/scm/linux/kernel/git/stable/linux.git /src/linux

WORKDIR /src/linux

ENV CCACHE_DIR=/root/.ccache \
    PATH="/usr/lib/ccache:${PATH}"

# 3) Use repository-provided arm64 config as-is
COPY config-arm64 /src/linux/.config

# 4) Build (optimized by default)
RUN --mount=type=cache,target=/root/.ccache \
    make -j"$(nproc)" ARCH=arm64 LLVM=1 LLVM_IAS=1 \
      CC="ccache clang" HOSTCC="ccache clang" \
      LD=ld.lld AR=llvm-ar NM=llvm-nm OBJCOPY=llvm-objcopy OBJDUMP=llvm-objdump STRIP=llvm-strip \
      Image

# 5) Stage artifacts for export
RUN mkdir -p /artifacts && \
    cp arch/arm64/boot/Image /artifacts/ && \
    cp .config /artifacts/config && \
    cp System.map /artifacts/System.map

# ===== Tiny export stage: runnable, copies /artifacts -> /out (a bind mount) =====
FROM busybox:stable-musl AS export
COPY --from=build /artifacts /artifacts
# When run with -v $PWD/kernel-out:/out, this will populate ./kernel-out on the host
ENTRYPOINT ["/bin/sh","-c","mkdir -p /out && cp -vr /artifacts/. /out/ && ls -al /out"]
