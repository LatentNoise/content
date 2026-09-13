#!/usr/bin/env bash
# Put the working tree into the cluster's test release, with nothing pushed.
#
# The path, and why each hop is there:
#
#   build    on an amd64 machine over SSH (DOCKER_HOST=ssh://…), because the
#            cluster node is amd64 and this laptop is not. Cross-building
#            Python images under emulation takes minutes per image; a native
#            build on a box that already has the layer cache takes seconds
#            when only application code changed.
#   move     `docker save | ssh node ctr images import`. No registry, so
#            nothing on the cluster has to be reconfigured and nothing has to
#            be restarted. The cost is the whole image on the wire each time,
#            which is why ONLY= exists: move the one that changed.
#   deploy   `helm upgrade --install` of a SECOND release, in its own
#            namespace, on its own volumes, on LAN-only hostnames. Production
#            is not touched, and nothing here is reachable from the internet.
#
# Usage:
#   make k8s-test                 build and deploy all four
#   make k8s-test ONLY=studio     just that one (engine|studio|console|hometube)
#   make k8s-test SKIP_BUILD=1    redeploy what is already on the node
set -euo pipefail

cd "$(dirname "$0")/.."

BUILDER="${BUILDER:-homelab-docker}"
NODE="${NODE:-kubernetes}"
NAMESPACE="${NAMESPACE:-content-test}"
RELEASE="${RELEASE:-content-test}"
TAG="${TAG:-test}"
VALUES="${VALUES:-$HOME/Dev/kubernetes-k3s/content/values-test.yaml}"
ONLY="${ONLY:-}"
SKIP_BUILD="${SKIP_BUILD:-}"

# name : dockerfile : image
SURFACES=(
  "engine:apps/backend/Dockerfile:latentnoise/content"
  "studio:apps/web-studio/Dockerfile:latentnoise/content-studio"
  "console:apps/web-admin/Dockerfile:latentnoise/content-console"
  "hometube:apps/web-hometube/Dockerfile:latentnoise/content-hometube"
)

[ -f "$VALUES" ] || { echo "missing values file: $VALUES" >&2; exit 1; }

selected() {
  [ -z "$ONLY" ] && return 0
  [ "$ONLY" = "$1" ]
}

if [ -z "$SKIP_BUILD" ]; then
  for entry in "${SURFACES[@]}"; do
    IFS=: read -r name dockerfile image <<<"$entry"
    selected "$name" || continue
    printf '▸ building %s on %s\n' "$image:$TAG" "$BUILDER"
    DOCKER_HOST="ssh://$BUILDER" docker build -q -f "$dockerfile" -t "$image:$TAG" . >/dev/null
    printf '▸ moving %s into the node\n' "$image:$TAG"
    # Straight through: the tarball never lands on a disk on the way.
    ssh "$BUILDER" "docker save $image:$TAG" \
      | ssh "$NODE" "sudo k3s ctr images import -" >/dev/null
  done
fi

printf '▸ deploying %s in namespace %s\n' "$RELEASE" "$NAMESPACE"
helm upgrade --install "$RELEASE" deploy/charts/content \
  -n "$NAMESPACE" --create-namespace -f "$VALUES" --wait --timeout 5m

cat <<EOF

  Studio    http://studio.content-test.k3s.lab
  Console   http://console.content-test.k3s.lab
  HomeTube  http://hometube.content-test.k3s.lab
  engine    http://api.content-test.k3s.lab/docs

  sign in   ask for a link on any surface, then: make k8s-test-link

EOF
