# EDA Base Image (M2.2)

A minimal, pinned Linux container with Yosys — the EDA environment boundary
of the AEMP platform. EDA tools run inside this container, never on the host.

## Image decision record (why Debian + apt)

| Option | Verdict |
|---|---|
| A. Prebuilt all-in-one EDA image (e.g. IIC-OSIC-TOOLS) | Rejected: multi-GB, dozens of unrelated tools, third-party supply chain |
| **B. Pinned Debian + apt Yosys** | **Chosen**: small, stable, digest-pinnable, easy to audit and to explain |
| C. Build Yosys from source | Rejected for M2.2: long builds, many build deps, no benefit at this stage |

## Image facts (verified 2026-10-07, real outputs)

| Item | Value |
|---|---|
| Base image | `debian:bookworm-slim` pinned by digest `sha256:7c7b2c966bc9ee8cedfeef67e0e279108992c77681fa595db4a9d65c06ccc587` |
| Yosys | **0.23 (git sha1 7ce5011c24b)** from the Debian bookworm repository |
| Built image | `aemp-eda-base:0.1.0` (id `ea27a88104d5…`) |
| Not included | `latest` tags, commercial EDA tools, unrelated EDA software, Python frameworks |

## Build & verify

```bash
docker build -t aemp-eda-base:0.1.0 docker/
docker run --rm aemp-eda-base:0.1.0 yosys -V      # Yosys 0.23 (git sha1 7ce5011c24b)
python scripts/check_eda_env.py                   # CLI/daemon/image/Yosys, exit 0 = READY
python scripts/demo_yosys_docker.py               # real synthesis end-to-end
```

## Volume mount (Windows host ↔ container)

```
Windows host (repo root)  --bind mount-->  /workspace (container, read-write)
```

- The container reads RTL/scripts from and writes netlists/logs back to the
  host through this mount — that is how outputs appear in `runs/` without
  copying files manually.
- Only the **project directory** is shared (never `C:\`, a user profile, or
  any host-wide path). Demo outputs land in `runs/demo-yosys/` (gitignored).
- Paths inside the container are always POSIX (`/workspace/...`); the host
  path is passed in Windows form by the demo scripts, so no shell-specific
  path conversion is involved.

## Reproducibility

- The base image is **pinned by digest** (`FROM …@sha256:…`), never
  `latest`: rebuilds start from bit-identical input layers. Verified: the
  digest-pinned rebuild produced the identical image id.
- Yosys comes from the Debian bookworm repository (0.23 at the time of
  writing). Debian stable keeps the version until point updates; for a
  long-term bit-exact toolchain, snapshotting the apt repository is
  documented future work (M2.4+).
- Verified end-to-end: two independent synthesis runs produced
  **byte-identical netlists** and identical `stat` output.
