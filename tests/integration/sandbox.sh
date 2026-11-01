#!/usr/bin/env bash
# Private PipeWire + WirePlumber + pipewire-pulse + headless KWin, isolated from the
# running desktop through its own XDG dirs and D-Bus. Hardware monitors are disabled, so
# it can never grab a real sound card.
#
#   sandbox.sh start [dir]   start it, default dir is $NEOMIXER_SANDBOX or ./.sandbox
#   sandbox.sh stop  [dir]   stop it and delete its runtime dir
#   sandbox.sh audio-restart [dir]   restart the audio daemons, as a package update can do
#   sandbox.sh reboot [dir]  what a reboot does to the audio side
#   source <dir>/env         run commands inside it
set -u
cmd=${1:-start}
SB=$(realpath -m "${2:-${NEOMIXER_SANDBOX:-./.sandbox}}")

start_audio() {
    ( source "$SB/env"
      nohup pipewire          >"$SB/log/pipewire.log"      2>&1 & echo $! >"$SB/pid/pipewire"
      sleep 0.3
      nohup pipewire-pulse    >"$SB/log/pipewire-pulse.log" 2>&1 & echo $! >"$SB/pid/pipewire-pulse"
      nohup wireplumber       >"$SB/log/wireplumber.log"   2>&1 & echo $! >"$SB/pid/wireplumber"
      for _ in $(seq 100); do
          pactl info >/dev/null 2>&1 && exit 0
          sleep 0.1
      done
      echo "pipewire-pulse did not come up" >&2; exit 1 )
}

stop_audio() {
    for name in wireplumber pipewire-pulse pipewire; do
        [ -f "$SB/pid/$name" ] && kill "$(cat "$SB/pid/$name")" 2>/dev/null
        rm -f "$SB/pid/$name"
    done
    sleep 0.3
}

case "$cmd" in
start)
    mkdir -p "$SB"/{run,config/wireplumber/wireplumber.conf.d,state,data,log,pid}
    chmod 700 "$SB/run"
    cat >"$SB/config/wireplumber/wireplumber.conf.d/90-sandbox.conf" <<CONF
wireplumber.profiles = {
  main = {
    monitor.alsa = disabled
    monitor.alsa-midi = disabled
    monitor.bluez = disabled
    monitor.bluez-midi = disabled
    monitor.libcamera = disabled
    monitor.v4l2 = disabled
  }
}
CONF
    # A tone source stands in for a microphone in the tests: it only runs when something pulls
    mkdir -p "$SB/config/pipewire/pipewire.conf.d"
    echo 'context.spa-libs = { audiotestsrc = audiotestsrc/libspa-audiotestsrc }' \
        >"$SB/config/pipewire/pipewire.conf.d/90-sandbox.conf"
    dbus_addr=$(dbus-daemon --session --fork --print-address=1 --print-pid=3 3>"$SB/pid/dbus" | head -1)
    cat >"$SB/env" <<ENV
export NEOMIXER_SANDBOX="$SB"
export XDG_RUNTIME_DIR="$SB/run"
export XDG_CONFIG_HOME="$SB/config"
export XDG_STATE_HOME="$SB/state"
export XDG_DATA_HOME="$SB/data"
export DBUS_SESSION_BUS_ADDRESS="$dbus_addr"
export WAYLAND_DISPLAY=wl-sandbox
export GDK_BACKEND=wayland
export GSK_RENDERER=cairo
export GTK_A11Y=none
export QT_QPA_PLATFORM=offscreen
unset DISPLAY PULSE_SERVER PIPEWIRE_REMOTE PIPEWIRE_RUNTIME_DIR PULSE_RUNTIME_PATH XAUTHORITY SESSION_MANAGER
ENV
    start_audio || exit 1
    ( source "$SB/env"
      nohup kwin_wayland --virtual --no-lockscreen --no-global-shortcuts --no-kactivities \
          --socket wl-sandbox --width 1920 --height 1080 >"$SB/log/kwin.log" 2>&1 &
      echo $! >"$SB/pid/kwin" )
    for _ in $(seq 100); do
        [ -S "$SB/run/wl-sandbox" ] && break
        sleep 0.1
    done
    [ -S "$SB/run/wl-sandbox" ] || echo "kwin did not start, see $SB/log/kwin.log" >&2
    ;;
audio-restart)
    stop_audio
    start_audio
    ;;
reboot)
    # What a reboot does to NeoMixer: its EQ processes and everything in the graph are gone
    for proc in /proc/[0-9]*; do
        if tr '\0' ' ' <"$proc/cmdline" 2>/dev/null | grep -qF "$SB/run/neomixer/eq-"; then
            kill "${proc#/proc/}" 2>/dev/null
        fi
    done
    stop_audio
    rm -rf "$SB/run/neomixer"
    start_audio
    ;;
stop)
    stop_audio
    for name in kwin dbus; do
        [ -f "$SB/pid/$name" ] && kill "$(cat "$SB/pid/$name")" 2>/dev/null
        rm -f "$SB/pid/$name"
    done
    rm -rf "$SB/run"
    ;;
*)
    echo "usage: $0 start|stop|audio-restart|reboot [dir]" >&2
    exit 2
    ;;
esac
