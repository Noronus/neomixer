#!/usr/bin/env bash
# Builds the packages from what is committed, each in a clean container of its distro.
#
#   packaging/build.sh [output dir]    default: dist/packages
set -euo pipefail
cd "$(dirname "$0")/.."

ARCH_IMAGE=docker.io/cachyos/cachyos:latest
FEDORA_IMAGE=registry.fedoraproject.org/fedora:44
DEBIAN_IMAGE=docker.io/library/debian:13
# Debian has no package of it, so the .deb brings its own
PULSECTL_ASYNCIO=https://files.pythonhosted.org/packages/py3/p/pulsectl-asyncio/pulsectl_asyncio-1.3.2-py3-none-any.whl
PULSECTL_ASYNCIO_SHA256=aa3adf89fe91a80ec1a5c92de3dcfd17a8f171006a07a2ec59a4a13d031fc70d

version=$(sed -n "s/^VERSION = '\(.*\)'/\1/p" src/neomixer/settings.py)
out=$(realpath -m "${1:-dist/packages}")
mkdir -p "$out"
rm -f "$out"/neomixer-*.pkg.tar.zst "$out"/neomixer-*.rpm "$out"/neomixer_*.deb

if ! git diff --quiet HEAD -- src packaging locale; then
    echo "Note: uncommitted changes are not in the packages" >&2
fi

work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT
git archive --prefix="neomixer-$version/" -o "$work/neomixer-$version.tar.gz" HEAD
sed "s/@VERSION@/$version/" packaging/arch/PKGBUILD >"$work/PKGBUILD"
cp packaging/fedora/neomixer.spec packaging/debian/build-deb.sh "$work/"
wheel=$work/${PULSECTL_ASYNCIO##*/}
curl -fsSLo "$wheel" "$PULSECTL_ASYNCIO"
echo "$PULSECTL_ASYNCIO_SHA256  $wheel" | sha256sum -c --quiet

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

podman run --rm -v "$work:/in:ro" -v "$out:/out" "$DEBIAN_IMAGE" sh /in/build-deb.sh "$version" /out

ls -l "$out"
