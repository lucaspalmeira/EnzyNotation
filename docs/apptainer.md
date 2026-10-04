# Apptainer on HPC

`apptainer/enzynotation.def` defines the general EnzyNotation runtime. It
contains the Python package plus lightweight BLAST+ and HMMER executables. It
does not contain databases, structures, CLEAN/ESM models, Foldseek databases,
user data, or results.

Builds are administrator/deployment operations, not pipeline operations:

```bash
apptainer build enzynotation.sif apptainer/enzynotation.def
```

Example worker invocation:

```bash
apptainer exec \
  --bind /shared/input:/work/input:ro \
  --bind /shared/results:/work/results \
  --bind /shared/logs:/work/logs \
  --bind /shared/databases:/databases:ro \
  --bind /shared/models:/models:ro \
  --bind /shared/cache:/cache \
  enzynotation.sif enzynotation run /work/input/proteins.fasta
```

The paths mirror Docker Compose. Slurm runs Apptainer or native/wrapped
commands directly; Docker Compose is never launched inside a Slurm job.

## Providers

- BLAST/HMMER may use executables inside the general image or site wrappers.
- CLEAN keeps its existing `command` strategy. Its prefix can point to a Conda
  wrapper or a separate administrator-managed CLEAN image.
- Foldseek supports its existing Docker Compose strategy and an HPC `command`
  strategy. For example, set the prefix to `apptainer exec
  /containers/foldseek.sif` and the executable to `foldseek`.
- TM-align remains an external executable. A site wrapper path may encapsulate
  any required module or container setup without changing its parser.

Environment Modules are intentionally not managed by EnzyNotation. Site
administrators should supply stable wrapper commands when modules are needed.

