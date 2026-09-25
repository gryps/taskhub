#!/bin/sh
set -eu

root=$(CDPATH= cd -- "$(dirname -- "$0")/../.." && pwd)
version=${TASKHUB_VERSION:-0.1.0-alpha}
codex_version=${CODEX_VERSION:-0.153.4}
platform=${TASKHUB_PLATFORM:-linux/amd64}
registry=${TASKHUB_REGISTRY:-}
commit=${TASKHUB_COMMIT:-}
if [ -z "$commit" ]; then
  commit=$(git -C "$root" rev-parse HEAD 2>/dev/null || printf 'unknown')
fi
prefix=""
mirror_args=""
pull_args=""
if [ "${TASKHUB_PULL_BASE_IMAGES:-false}" = "true" ]; then
  pull_args="--pull"
fi
if [ -n "$registry" ]; then
  prefix="${registry%/}/"
fi
if [ -n "${TASKHUB_BUILD_PROXY:-}" ]; then
  mirror_args="$mirror_args --build-arg HTTP_PROXY=${TASKHUB_BUILD_PROXY}"
  mirror_args="$mirror_args --build-arg HTTPS_PROXY=${TASKHUB_BUILD_PROXY}"
fi
if [ -n "${NPM_REGISTRY:-}" ]; then
  mirror_args="$mirror_args --build-arg NPM_REGISTRY=${NPM_REGISTRY}"
fi
if [ -n "${PYPI_INDEX_URL:-}" ]; then
  mirror_args="$mirror_args --build-arg PYPI_INDEX_URL=${PYPI_INDEX_URL}"
fi
if [ -n "${DEBIAN_MIRROR:-}" ]; then
  mirror_args="$mirror_args --build-arg DEBIAN_MIRROR=${DEBIAN_MIRROR}"
fi
if [ -n "${DEBIAN_SECURITY_MIRROR:-}" ]; then
  mirror_args="$mirror_args --build-arg DEBIAN_SECURITY_MIRROR=${DEBIAN_SECURITY_MIRROR}"
fi
seed_image="${prefix}taskhub-seed:${version}"
node_image="${prefix}taskhub-node:${version}"

docker info >/dev/null
docker buildx version >/dev/null

for specification in "Dockerfile|$seed_image" "deploy/node/Dockerfile|$node_image"; do
  dockerfile=${specification%%|*}
  image=${specification#*|}
  docker buildx build --load $pull_args --provenance=false \
    --platform "$platform" \
    --build-arg "TASKHUB_VERSION=$version" \
    --build-arg "TASKHUB_COMMIT=$commit" \
    --build-arg "CODEX_VERSION=$codex_version" \
    $mirror_args \
    --tag "$image" \
    --file "$root/$dockerfile" "$root"
done

if [ "${TASKHUB_PUSH:-false}" = "true" ]; then
  [ -n "$registry" ] || {
    printf 'TASKHUB_PUSH=true 时必须设置 TASKHUB_REGISTRY。\n' >&2
    exit 1
  }
  docker push "$seed_image"
  docker push "$node_image"
fi

printf 'Seed: %s\nNode: %s\nPlatform: %s\n' "$seed_image" "$node_image" "$platform"
