# AEMP EDA base image (M2.2): a minimal, pinned Linux environment with Yosys.
#
# Design (docker/README.md documents the full rationale):
#   - Debian bookworm (12) slim base, pinned by digest - never `latest`.
#   - Yosys from the Debian repository (no source build, no commercial tools,
#     no unrelated EDA software, no Python frameworks).
#   - The image verifies itself at build time (`yosys -V` must succeed).
#
# The exact base-image digest and the Yosys version delivered by this
# Dockerfile are recorded in docker/README.md.

# Digest below = manifest of debian:bookworm-slim (linux/amd64) pulled on
# 2026-10-07; see docker/README.md for verification commands.
FROM debian:bookworm-slim@sha256:7c7b2c966bc9ee8cedfeef67e0e279108992c77681fa595db4a9d65c06ccc587

LABEL org.opencontainers.image.title="aemp-eda-base" \
      org.opencontainers.image.description="Minimal Linux EDA environment: Debian bookworm + Yosys" \
      org.opencontainers.image.vendor="AEMP Contributors"

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        yosys \
    && rm -rf /var/lib/apt/lists/*

# Fail the build early if the tool cannot run.
RUN yosys -V

WORKDIR /workspace
