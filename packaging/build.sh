#!/usr/bin/env bash
# Builds the packages from what is committed, each in a clean container of its distro.
#
#   packaging/build.sh [output dir]    default: dist/packages
set -euo pipefail
cd "$(dirname "$0")/.."

ARCH_IMAGE=docker.io/cachyos/cachyos:latest
FEDORA_IMAGE=registry.fedoraproject.org/fedora:44

version=$(sed -n "s/^VERSION = '\(.*\)'/\1/p" src/neomixer/settings.py)
out=$(realpath -m "${1:-dist/packages}")
mkdir -p "$out"
rm -f "$out"/neomixer-*.pkg.tar.zst "$out"/neomixer-*.rpm

if ! git diff --quiet HEAD -- src packaging locale; then
    echo "Note: uncommitted changes are not in the packages" >&2
fi

work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT
git archive --prefix="neomixer-$version/" -o "$work/neomixer-$version.tar.gz" HEAD
sed "s/@VERSION@/$version/" packaging/arch/PKGBUILD >"$work/PKGBUILD"
cp packaging/fedora/neomixer.spec "$work/"

# makepkg refuses to run as root
podman run --rm -v "$work:/in:ro" -v "$out:/out" "$ARCH_IMAGE" bash -euc '
    # Its sandbox for hooks cannot isolate the network in a container without privileges
    sed -i "/^\[options\]/a DisableSandbox" /etc/pacman.conf
    pacman -Syu --noconfirm --needed base-devel python python-babel >/dev/null
    useradd -m builder
    install -d -o builder /home/builder/pkg
    install -o builder /in/PKGBUILD /in/*.tar.gz /home/builder/pkg/
    su builder -c "cd ~/pkg && makepkg --nodeps --noconfirm"
    cp /home/builder/pkg/*.pkg.tar.zst /out/
'

podman run --rm -v "$work:/in:ro" -v "$out:/out" "$FEDORA_IMAGE" bash -euc '
    dnf -y -q install rpm-build python3 python3-babel >/dev/null
    rpmbuild -bb --quiet \
        --define "_topdir /tmp/rpm" --define "_sourcedir /in" --define "_rpmdir /out" \
        --define "_build_name_fmt %%{NAME}-%%{VERSION}-%%{RELEASE}.%%{ARCH}.rpm" \
        --define "neomixer_version '"$version"'" /in/neomixer.spec
'

ls -l "$out"
