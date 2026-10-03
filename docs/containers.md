# Docker Compose Deployment

## Scope

Milestone 9 defines the future server deployment in `compose.yaml`. It does not
build, pull, or run an image during development. Docker Compose executes on the
host and manages three sibling services:

- `enzynotation`: project-owned Python runtime, built later on the server;
- `clean`: external `moleculemaker/clean-image-amd64` image;
- `foldseek`: external
  `ghcr.io/steineggerlab/foldseek:10-941cd33` image.

There is no Docker-in-Docker. The EnzyNotation service has no Docker client,
Docker socket, daemon access, privileged mode, or dependency on provider
services. EnzyNotation Python code does not start or control containers.

The existing `docker/clean.compose.yml` and `docker/foldseek.compose.yml` files
remain available for backward-compatible host-native provider execution. The
top-level `compose.yaml` is preferred for a new server deployment.

## Future Build Definition

The `enzynotation` service uses Compose `build.dockerfile_inline`, which keeps
the small project build recipe in `compose.yaml`. This requires a Docker Compose
implementation that supports `dockerfile_inline` (Compose 2.17 or newer).

The future image starts from `python:3.12-slim`, installs the Python package and
the lightweight Debian BLAST+/HMMER packages, and copies only source,
configuration, documentation required by packaging, and setup scripts. It does
not copy databases, models, inputs, results, logs, or caches. TM-align remains
an external configurable executable because the build does not add a source
compilation workflow.

`CLEAN_IMAGE`, `FOLDSEEK_IMAGE`, and `ENZYNOTATION_IMAGE` can contain tags or
digest-pinned references. CLEAN and Foldseek are referenced with `image:` and
are never rebuilt by this project.

## Profiles And One-Shot Services

Profiles keep batch providers opt-in:

| Service | Profile | Runtime model |
| --- | --- | --- |
| `enzynotation` | `core` | one CLI invocation |
| `clean` | `clean` | one batch invocation |
| `foldseek` | `foldseek` | one batch invocation |

All services use `restart: "no"`. CLEAN and Foldseek are not daemons and expose
no ports. There is no `depends_on`: selecting the core service does not start
heavy providers.

The server operator invokes the required services explicitly. Existing CLEAN
or Foldseek configurations that use their provider-specific Compose adapters
must be run by a native host EnzyNotation process. They must not be selected
inside the `enzynotation` container, because that would require nested Docker.
Cross-service batch sequencing is an operator responsibility in Milestone 9;
the shared filesystem makes inputs and outputs visible without copying large
artifacts between containers.

## Filesystem Contract

Every service receives the same core paths:

| Host variable | Container path | Access |
| --- | --- | --- |
| `ENZYNOTATION_INPUT_ROOT` | `/work/input` | read-only |
| `ENZYNOTATION_RESULTS_ROOT` | `/work/results` | writable |
| `ENZYNOTATION_LOGS_ROOT` | `/work/logs` | writable |
| `ENZYNOTATION_DB_ROOT` | `/databases` | read-only |
| `ENZYNOTATION_MODEL_ROOT` | `/models` | read-only |
| `ENZYNOTATION_CACHE_ROOT` | `/cache` | writable |

CLEAN additionally sees input/results at its upstream-compatible paths and the
persistent Torch/ESM checkpoint cache at
`/root/.cache/torch/hub/checkpoints`. Foldseek sees a writable temporary
directory at `/work/tmp`. These are bind mounts, not opaque database volumes.

Create host directories and permissions before service execution. Large
resources remain under administrator-controlled storage and are not copied into
Git or an image.

## Environment

Copy `.env.example` to an untracked `.env` on the target server, then edit its
host paths and optional image references. `.env.example` contains no secrets.
Do not place passwords, tokens, SSH keys, or API credentials in the committed
example.

Container paths such as `/databases` should be used in provider YAML selected
inside the core container. Native executions may continue using native host
paths. This path difference is operational only and must not change thresholds,
ranking, evidence, correlation, EC inference, or confidence.

## Future Server Commands

These commands are documentation for the target server. They were not executed
during Milestone 9:

```bash
git clone <repository-url> EnzyNotation
cd EnzyNotation
cp .env.example .env
# Edit .env and create/mount the configured directories.

docker compose config
docker compose build enzynotation
docker compose pull clean foldseek
docker compose run --rm enzynotation --help
docker compose run --rm clean ...
docker compose run --rm foldseek easy-search ...
```

Example core invocation:

```bash
docker compose run --rm enzynotation run /work/input/proteins.fasta \
  --run-id server-run \
  --results-dir /work/results \
  --logs-dir /work/logs
```

The host is the only Docker control plane. Never mount
`/var/run/docker.sock`, run a Docker daemon in a service, or add `privileged:
true`.

## Native Compatibility

Docker Compose remains optional. Native validation, providers, integration,
reporting, cache, and resumability retain their existing interfaces. The
Compose file contains no scientific filters or decision thresholds, so native
and containerized execution share the same scientific configuration and
canonical contracts.
