# AEMP OpenSTA Image (M2.5)

Thin wrapper around the official OpenROAD-project OpenSTA image — the STA
environment boundary of the platform, managed exactly like the Yosys image
(`docker/Dockerfile`, `docker/README.md`).

## Image facts (verified 2026-10-07, real outputs)

| Item | Value |
|---|---|
| Upstream image | `openroad/opensta` (OpenROAD project build of parallaxsw/OpenSTA) |
| Upstream digest | `sha256:d5aa34eed86e49a5d36afc2a81961cabb802f0439b472fe07e178289389ae1a0` (from `openroad/opensta:latest` pull on 2026-10-07; `latest` never used as a version) |
| Built image | `aemp-eda-opensta:3.1.0` (id `5732172cdfe3…`) |
| OpenSTA version | **3.1.0** (git `d1e43c6f9f`) — banner verified in-container |
| Why a wrapper | upstream ENTRYPOINT is the RELATIVE path `OpenSTA/build/sta` and breaks under a custom workdir; the wrapper clears it, puts `/OpenSTA/build` on PATH, and self-checks the tool at build time |
| CLI quirks verified | no `-f` flag: the script is a positional `cmd_file`; no `redirect` command: reports go to stdout |
| License note | OpenSTA is GPLv3 — invoked as a separate process inside its own container, never linked or redistributed by AEMP |

## Build & verify

```bash
docker build -t aemp-eda-opensta:3.1.0 -f docker/Dockerfile.opensta docker/
docker run --rm aemp-eda-opensta:3.1.0 sta -version      # 3.1.0
python scripts/check_eda_env.py
```

## Demo Liberty (Nangate45)

`examples/sta/` uses `Nangate45_typ.lib` (134 cells incl. `DFFR_X1`),
**extracted from this pinned image at runtime** — not committed to the
repository (6.7 MB, Nangate Inc. copyright, see `third_party/NOTICE.md`).
Sourcing it from the digest-pinned image keeps the demo library
version-fixed without redistributing it:

```bash
docker run --rm -v <dir>:/out --entrypoint sh aemp-eda-opensta:3.1.0 \
    -c "cp /OpenSTA/test/nangate45/Nangate45_typ.lib /out/"
```
