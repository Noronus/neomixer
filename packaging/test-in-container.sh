#!/usr/bin/env bash
# Runs inside a clean container: does a package install, is it complete with nothing but its
# declared dependencies, does it work, and is it gone after removing it?
#
#   test-in-container.sh arch|fedora [scenario...]
set -uo pipefail
distro=$1
failed=0

check() {
    local message=$1
    shift
    if "$@" >/dev/null 2>&1; then
        echo "ok    $message"
    else
        echo "FAIL  $message"
        failed=1
    fi
}

case $distro in
arch)
    # Its sandbox for hooks cannot isolate the network in a container without privileges
    sed -i '/^\[options\]/a DisableSandbox' /etc/pacman.conf
    pacman -Syu --noconfirm >/dev/null || exit 1
    pacman -U --noconfirm /packages/neomixer-*.pkg.tar.zst || exit 1
    pacman -Qlq neomixer >/tmp/package-files
    test_packages=(kwin ffmpeg python-pytest ttf-dejavu desktop-file-utils util-linux)
    install_test_packages() { pacman -S --noconfirm --needed "${test_packages[@]}"; }
    remove_package() { pacman -Rns --noconfirm neomixer; }
    ;;
fedora)
    dnf -y install /packages/neomixer-*.rpm || exit 1
    rpm -ql neomixer >/tmp/package-files
    test_packages=(kwin mutter ffmpeg-free python3-pytest dejavu-sans-fonts desktop-file-utils pulseaudio-utils util-linux dbus-daemon glib2 findutils procps-ng)
    install_test_packages() { dnf -y -q install "${test_packages[@]}"; }
    remove_package() { dnf -y remove neomixer; }
    ;;
esac

echo "=== with nothing but the declared dependencies"
export XDG_RUNTIME_DIR=/tmp/declared
install -d -m 700 "$XDG_RUNTIME_DIR"
pipewire >/dev/null 2>&1 &
sleep 0.5
pipewire-pulse >/dev/null 2>&1 &
sleep 1
check 'all code imports, GTK and libpulse included' neomixer --help
kill %1 %2
wait 2>/dev/null
unset XDG_RUNTIME_DIR
for command in pw-cli pw-link pw-dump pw-metadata pw-loopback pipewire pipewire-pulse wireplumber; do
    check "$command is there" command -v "$command"
done
for module in pipewire-0.3/libpipewire-module-filter-chain.so pipewire-0.3/libpipewire-module-protocol-pulse.so \
              spa-0.2/filter-graph/libspa-filter-graph-plugin-builtin.so; do
    check "$module is there" sh -c "ls /usr/lib*/$module"
done

echo "=== the test tools"
install_test_packages >/dev/null || exit 1

echo "=== what got installed"
check 'launcher is valid' desktop-file-validate /usr/share/applications/org.neomixer.NeoMixer.desktop
check 'launcher runs the installed command' grep -qx 'Exec=neomixer' /usr/share/applications/org.neomixer.NeoMixer.desktop
check 'icon is in the theme' test -f /usr/share/icons/hicolor/scalable/apps/org.neomixer.NeoMixer.svg
check 'compiled code goes with the package' sh -c "grep -q '/usr/lib/neomixer/neomixer/__pycache__/main' /tmp/package-files"
check 'translations are where it looks' test -f /usr/share/locale/de/LC_MESSAGES/neomixer.mo

# As a normal user: a package that needs to write next to its code only shows up that way
useradd -m tester
install -d -o tester /work /shots-out
cp -r /src/tests /work/
chown -R tester /work
as_tester() { runuser -u tester -- env -C /work HOME=/home/tester PYTHONDONTWRITEBYTECODE=1 "$@"; }

echo "=== the real command and launcher, as a new user"
as_tester tests/integration/sandbox.sh start /work/.sandbox-launch
launch_env() {
    as_tester bash -c "source /work/.sandbox-launch/env; $1"
}
launch_env 'gio launch /usr/share/applications/org.neomixer.NeoMixer.desktop' &
sleep 4
check 'starts from its launcher' launch_env 'gdbus call --session --dest org.freedesktop.DBus --object-path /org/freedesktop/DBus \
    --method org.freedesktop.DBus.NameHasOwner org.neomixer.NeoMixer | grep -q true'
check 'the launcher is not copied to the home directory' test ! -e /work/.sandbox-launch/data/applications/org.neomixer.NeoMixer.desktop
check 'autostart would run the installed command' launch_env \
    "python3 -c \"import sys; sys.path.insert(0, '/usr/lib/neomixer'); sys.argv = ['/usr/bin/neomixer']; from neomixer.utils.autostart import launch_command; print(launch_command())\" | grep -qx /usr/bin/neomixer"
check 'neomixer quit ends it' launch_env 'neomixer quit; sleep 1; ! pgrep -u tester -x neomixer'
check 'its log has no errors' launch_env '! grep -E "ERROR|Traceback" /work/.sandbox-launch/state/neomixer/neomixer.log'
as_tester tests/integration/sandbox.sh stop /work/.sandbox-launch

echo "=== the whole suite against the installed package"
as_tester NEOMIXER_CODE=/usr/lib/neomixer python3 tests/integration/lifecycle_test.py /work/shots
suite=$?
cp -r /work/shots/. /shots/ 2>/dev/null
[ $suite -eq 0 ] || failed=1

as_tester tests/integration/sandbox.sh start /work/.sandbox-eq
as_tester bash -c 'source /work/.sandbox-eq/env; PYTHONPATH=/usr/lib/neomixer:/work python3 -m pytest -q -p no:cacheprovider tests/integration/eq_audio_test.py' | tail -3
[ "${PIPESTATUS[0]}" -eq 0 ] || failed=1
as_tester tests/integration/sandbox.sh stop /work/.sandbox-eq

if command -v mutter >/dev/null; then
    echo "=== on GNOME's compositor"
    as_tester NEOMIXER_COMPOSITOR=mutter NEOMIXER_CODE=/usr/lib/neomixer python3 tests/integration/lifecycle_test.py /work/shots-gnome \
        first_run assign_and_restart_program default_is_held text_size_setting close_keeps_running output_eq demo_screenshot
    [ $? -eq 0 ] || failed=1
    mkdir -p /shots/gnome
    cp -r /work/shots-gnome/. /shots/gnome/ 2>/dev/null
fi

echo "=== removing it"
remove_package >/dev/null || failed=1
while read -r path; do
    case $path in
        */neomixer*|*org.neomixer*) [ -e "$path" ] && { echo "FAIL  left behind: $path"; failed=1; } ;;
    esac
done </tmp/package-files
check 'its directory is gone' test ! -e /usr/lib/neomixer
check 'its command is gone' test ! -e /usr/bin/neomixer

if [ $failed -eq 0 ]; then
    echo "=== $distro: all good"
else
    echo "=== $distro: FAILED"
fi
exit $failed
