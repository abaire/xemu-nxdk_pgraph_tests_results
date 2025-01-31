#!/usr/bin/env bash

if ! which docker >/dev/null ; then
  echo Missing required 'docker' - please make sure it is installed and on PATH
  exit 1
fi

set -eu

# TODO: Switch to docker pull and take image from ghcr.io
DOCKER_BUILDKIT=1 docker build \
       --platform linux/amd64 \
       --load \
       -t xemu-nxdk-pgraph-tests .

docker run \
    --platform linux/amd64 \
    --rm -it \
    -v $PWD/results:/work/new_results:rw \
    -v $PWD/inputs:/work/inputs:ro \
    -v $PWD/cache:/work/cache:rw \
    xemu-nxdk-pgraph-tests \
    "$@"
