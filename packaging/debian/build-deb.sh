#!/bin/sh
# Runs inside a clean Debian container: builds the .deb from the source archive and the
# pulsectl-asyncio wheel in /in.
#
#   build-deb.sh <version> <output dir>
set -eu
version=$1
out=$2

export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y -qq --no-install-recommends python3 python3-babel >/dev/null

tar -xzf "/in/neomixer-$version.tar.gz" -C /tmp
cd "/tmp/neomixer-$version"
root=/tmp/root

# Debian has no package of pulsectl-asyncio, so its code goes next to NeoMixer's
install -d "$root/usr/lib/neomixer"
python3 -m zipfile -e /in/pulsectl_asyncio-*.whl "$root/usr/lib/neomixer"
packaging/install-tree.sh "$root"

# Debian keeps the licenses in the package's doc directory
doc=$root/usr/share/doc/neomixer
install -d "$doc"
{
    cat LICENSE
    printf '\n\npulsectl-asyncio, which comes with this package:\n\n'
    cat "$root"/usr/lib/neomixer/pulsectl_asyncio-*.dist-info/licenses/LICENSE
} >"$doc/copyright"
chmod 644 "$doc/copyright"
rm -r "$root/usr/share/licenses"

install -d "$root/DEBIAN"
size=$(du -sk "$root" | cut -f1)
sed -e "s/@VERSION@/$version/" -e "s/@SIZE@/$size/" packaging/debian/control >"$root/DEBIAN/control"
dpkg-deb --root-owner-group -Zxz --build "$root" "$out/neomixer_$version-1_all.deb"
