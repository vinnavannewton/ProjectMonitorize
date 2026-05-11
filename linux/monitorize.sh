#!/usr/bin/env bash
# =============================================================================
# Monitorize — Linux Host Launcher
# Fedora 44 / KDE Plasma 6 / Wayland
#
# Pipeline:
#   krfb-virtualmonitor  →  wf-recorder (YUV420 pipe)  →  GStreamer x264enc
#   →  tcpclientsink 127.0.0.1:7110  →  ADB forward  →  Android tablet
# =============================================================================

set -euo pipefail

# ── Config ────────────────────────────────────────────────────────────────────
PORT=7110
WIDTH=1280
HEIGHT=800
FPS=60
BITRATE=15000       # kbps — 15 Mbps for 1280x800@60fps over USB
DISPLAY_NAME="TabletDisplay"
VIRTUAL_OUTPUT_NAME=""   # filled in at runtime after krfb creates the display

# ── Colours for terminal output ───────────────────────────────────────────────
RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'
CYAN='\033[0;36m'; BOLD='\033[1m'; RESET='\033[0m'

log()  { echo -e "${CYAN}[Monitorize]${RESET} $*"; }
ok()   { echo -e "${GREEN}[OK]${RESET} $*"; }
warn() { echo -e "${YELLOW}[WARN]${RESET} $*"; }
die()  { echo -e "${RED}[ERROR]${RESET} $*" >&2; exit 1; }

# ── Cleanup handler ───────────────────────────────────────────────────────────
KRFB_PID=""
GST_PID=""
WFR_PID=""

cleanup() {
    echo ""
    log "Shutting down..."
    [[ -n "$GST_PID" ]]  && kill "$GST_PID"  2>/dev/null || true
    [[ -n "$WFR_PID" ]]  && kill "$WFR_PID"  2>/dev/null || true
    [[ -n "$KRFB_PID" ]] && kill "$KRFB_PID" 2>/dev/null || true
    # Remove ADB forward
    adb forward --remove tcp:$PORT 2>/dev/null || true
    log "Cleanup done."
}
trap cleanup EXIT INT TERM

# ── Prerequisite check ────────────────────────────────────────────────────────
check_dep() {
    command -v "$1" &>/dev/null || die "'$1' not found. Install with: $2"
}

log "Checking dependencies..."
check_dep adb          "sudo dnf install android-tools"
check_dep gst-launch-1.0 "sudo dnf install gstreamer1-plugins-bad-free gstreamer1-plugins-ugly-free"
check_dep krfb-virtualmonitor "sudo dnf install krfb"
check_dep wf-recorder  "sudo dnf install wf-recorder"
ok "All dependencies present."

# ── Check Wayland ─────────────────────────────────────────────────────────────
if [[ -z "${WAYLAND_DISPLAY:-}" ]]; then
    die "WAYLAND_DISPLAY is not set. This script must run inside a Wayland session."
fi
log "Wayland display: $WAYLAND_DISPLAY"

# ── Check ADB device ─────────────────────────────────────────────────────────
log "Checking ADB connection..."
if ! adb devices | grep -q "device$"; then
    die "No Android device detected via ADB. Connect tablet via USB and enable USB Debugging."
fi
ok "ADB device found."

# ── Set up ADB forward ────────────────────────────────────────────────────────
log "Setting up ADB port forward (tcp:$PORT)..."
adb forward tcp:$PORT tcp:$PORT
ok "ADB forward: localhost:$PORT → tablet:$PORT"

# ── Launch krfb-virtualmonitor ────────────────────────────────────────────────
log "Creating virtual display '${DISPLAY_NAME}' (${WIDTH}x${HEIGHT}) via krfb-virtualmonitor..."

# krfb-virtualmonitor runs as a daemon; it registers the virtual output with KWin
# and exits once KWin confirms the display. We run it in the background and wait.
krfb-virtualmonitor \
    --name "${DISPLAY_NAME}" \
    --resolution "${WIDTH}x${HEIGHT}" \
    &>/tmp/monitorize_krfb.log &
KRFB_PID=$!

# Wait for KWin to register the new output (up to 10s)
log "Waiting for KWin to register the virtual output..."
FOUND_OUTPUT=""
for i in $(seq 1 20); do
    sleep 0.5
    # wl-randr lists Wayland outputs — find the one krfb just created
    FOUND_OUTPUT=$(wl-randr 2>/dev/null | grep -E "^[A-Z]" | awk '{print $1}' | \
        while read -r name; do
            # krfb virtual outputs typically appear as "Virtual-N" or with the display name
            if wl-randr 2>/dev/null | grep -A5 "^$name" | grep -qi "virtual\|$DISPLAY_NAME"; then
                echo "$name"
                break
            fi
        done) || true

    if [[ -z "$FOUND_OUTPUT" ]]; then
        # Fallback: look for any output that wasn't there before by checking the
        # most recently added output that isn't a physical GPU output
        FOUND_OUTPUT=$(wl-randr 2>/dev/null | grep -E "^Virtual|^HEADLESS|^WL-" | head -1 | awk '{print $1}') || true
    fi

    if [[ -n "$FOUND_OUTPUT" ]]; then
        ok "Virtual output detected: $FOUND_OUTPUT"
        VIRTUAL_OUTPUT_NAME="$FOUND_OUTPUT"
        break
    fi
    echo -n "."
done
echo ""

if [[ -z "$VIRTUAL_OUTPUT_NAME" ]]; then
    warn "Could not auto-detect virtual output name via wl-randr."
    log "Available outputs:"
    wl-randr 2>/dev/null || true
    echo ""
    read -rp "$(echo -e "${YELLOW}Enter the exact output name for '${DISPLAY_NAME}' shown above:${RESET} ")" VIRTUAL_OUTPUT_NAME
    [[ -z "$VIRTUAL_OUTPUT_NAME" ]] && die "No output name provided."
fi

ok "Streaming from output: ${VIRTUAL_OUTPUT_NAME}"
log ""
log "  ${BOLD}ACTION REQUIRED:${RESET}"
log "  Open ${BOLD}System Settings → Display & Monitor${RESET}"
log "  Position '${DISPLAY_NAME}' as an extended display."
log "  Then drag any window onto it."
log ""
read -rp "$(echo -e "${YELLOW}Press Enter when ready to start streaming...${RESET}")"

# ── Build & start the pipeline ────────────────────────────────────────────────
#
#  wf-recorder -o <output> -c rawvideo -m v4l2 -x yuv420p -f -
#    → raw YUV420 bytes to stdout
#
#  gst-launch-1.0 fdsrc
#    → rawvideoparse width=W height=H format=i420 framerate=FPS/1
#    → queue
#    → x264enc tune=zerolatency speed-preset=ultrafast bitrate=N key-int-max=30
#    → h264parse config-interval=1      ← injects SPS/PPS before every IDR
#    → tcpclientsink host=127.0.0.1 port=7110
#

GST_PIPELINE="gst-launch-1.0 \
    fdsrc ! \
    rawvideoparse \
        use-sink-caps=false \
        width=${WIDTH} \
        height=${HEIGHT} \
        format=i420 \
        framerate=${FPS}/1 ! \
    queue max-size-buffers=4 leaky=downstream ! \
    x264enc \
        tune=zerolatency \
        speed-preset=ultrafast \
        bitrate=${BITRATE} \
        key-int-max=15 \
        byte-stream=true \
        option-string=\"repeat-headers=1:bframes=0:ref=1:sliced-threads=0\" ! \
    h264parse config-interval=-1 ! \
    tcpclientsink host=127.0.0.1 port=${PORT}"

log "Starting GStreamer encoder → ADB tunnel..."
log "Pipeline: $GST_PIPELINE"

log "Starting wf-recorder capture of '${VIRTUAL_OUTPUT_NAME}'..."

# wf-recorder writes raw video to stdout; pipe directly into gst-launch
wf-recorder \
    --output "${VIRTUAL_OUTPUT_NAME}" \
    --codec rawvideo \
    --muxer rawvideo \
    --pixel-format yuv420p \
    --file - \
    2>/tmp/monitorize_wfrecorder.log \
    | \
eval "$GST_PIPELINE" \
    2>/tmp/monitorize_gst.log &

# Store PIDs
WFR_PID=$(pgrep -n wf-recorder 2>/dev/null || true)
GST_PID=$(pgrep -n gst-launch-1.0 2>/dev/null || true)

echo ""
ok "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
ok " Monitorize is STREAMING"
ok " Output: ${VIRTUAL_OUTPUT_NAME}  →  Tablet port ${PORT}"
ok " Resolution: ${WIDTH}×${HEIGHT} @ ${FPS}fps  Bitrate: ${BITRATE}kbps"
ok "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
log "Logs: /tmp/monitorize_*.log"
log "Press Ctrl+C to stop."

# Wait for child processes
wait
