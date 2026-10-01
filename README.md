# non-avian-validation

## Development environment

This project uses a [dev container](https://containers.dev/) + [pixi](https://pixi.sh) for
reproducible Python/Jupyter environment management (same pattern as
[`ca-anuran-sandbox`](https://github.com/avanscoyoc/ca-anuran-sandbox)).

### Quick start

**VS Code / GitHub Codespaces**: open the repo and "Reopen in Container" (or let Codespaces
build it automatically). The container installs [pixi](https://pixi.sh) and, on creation, runs
`pixi install` to set up the `default` environment declared in `pixi.toml`.

**Without a dev container**: install [pixi](https://pixi.sh/#install) locally, then from the
repo root run:

```bash
pixi install
pixi run jupyter   # launches JupyterLab on http://localhost:8888
```

### Packages

The pixi environment (see `pixi.toml`) provides Python, JupyterLab/`ipykernel`, and:

- [`jupyter_bioacoustic`](https://github.com/SchmidtDSE/jupyter_bioacoustic) — a JupyterLab
  plugin for reviewing and annotating bioacoustic audio clips.
- [`ondio`](https://github.com/SchmidtDSE/ondio) — uniform IO of audio data and bioacoustic
  model results across S3, GCS, HTTP, and local filesystems.

`notebooks/00_environment_check.ipynb` is a minimal smoke-test notebook that imports both
packages and prints their installed versions.
