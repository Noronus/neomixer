#!/usr/bin/env bash
# Installs the built packages in a clean container of each distro, and tests what got installed.
# A container reaches nothing of the desktop it runs on.
#
#   packaging/test.sh [packages dir] [screenshot dir]
set -uo pipefail
cd "$(dirname "$0")/.."
packages=$(realpath "${1:-dist/packages}")
shots=$(realpath -m "${2:-dist/package-tests}")

run() {
    local distro=$1 image=$2
    mkdir -p "$shots/$distro"
    # Their KWin has the capability to run at a higher priority, and does not start without it.
    # Without a render node it starts, but stops drawing after the first frame.
    podman run --rm --cap-add=SYS_NICE "${render_nodes[@]}" -v "$packages:/packages:ro" -v "$PWD/tests:/src/tests:ro" -v "$shots/$distro:/shots" \
        -v "$PWD/packaging/test-in-container.sh:/test.sh:ro" "$image" bash /test.sh "$distro" 2>&1 \
        | tee "$shots/$distro.log" | grep -E '^(ok |FAIL|===|-->|[0-9]+ of [0-9]+|  FAILED|[0-9]+ passed|.* failed)'
    return "${PIPESTATUS[0]}"
}

render_nodes=()
for node in /dev/dri/renderD*; do
    render_nodes+=(--device "$node")
done

status=0
run arch docker.io/cachyos/cachyos:latest || status=1
run fedora registry.fedoraproject.org/fedora:44 || status=1
run debian docker.io/library/debian:13 || status=1
exit $status
