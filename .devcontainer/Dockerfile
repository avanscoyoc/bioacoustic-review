FROM ghcr.io/prefix-dev/pixi:0.44.0

RUN apt-get update && apt-get install -y git && rm -rf /var/lib/apt/lists/*

# Code is NOT baked into the image: the repo is bind-mounted at /workspace by
# devcontainer.json, and the pixi env is installed there by postCreateCommand.
# Activate the pixi env for any interactive shell opened in the container.
RUN echo 'eval "$(pixi shell-hook -s bash --manifest-path /workspace/pixi.toml)"' >> /root/.bashrc

WORKDIR /workspace
EXPOSE 8888
