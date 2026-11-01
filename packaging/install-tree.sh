#!/bin/sh
# Puts NeoMixer into a package root, the same way for every distro.
#
#   install-tree.sh <package root>
set -eu
root=$1
cd "$(dirname "$0")/.."

app=org.neomixer.NeoMixer
data=src/neomixer/data
share=$root/usr/share

install -d "$root/usr/lib/neomixer"
cp -r src/neomixer "$root/usr/lib/neomixer/"
find "$root/usr/lib/neomixer" -name __pycache__ -type d -prune -exec rm -rf {} +
# Checked by hash: packaging changes the time stamps that .pyc files are checked against by default
python3 -m compileall -q --invalidation-mode checked-hash -s "$root" -p / "$root/usr/lib/neomixer"

install -Dm755 packaging/neomixer "$root/usr/bin/neomixer"
install -d "$share/applications"
sed 's/@EXEC@/neomixer/' "$data/$app.desktop" >"$share/applications/$app.desktop"
chmod 644 "$share/applications/$app.desktop"
install -Dm644 "$data/icons/hicolor/scalable/apps/$app.svg" "$share/icons/hicolor/scalable/apps/$app.svg"
install -Dm644 LICENSE "$share/licenses/neomixer/LICENSE"

pybabel -q compile -d locale -D neomixer
for catalog in locale/*/LC_MESSAGES/neomixer.mo; do
    language=${catalog#locale/}
    install -Dm644 "$catalog" "$share/locale/$language"
done
