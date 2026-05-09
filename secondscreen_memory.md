# SecondScreen Project — Full Context, Memory & State

## What This Project Is

A Linux → Android secondary display app. Like Spacedesk but for Linux.
- Fedora KDE laptop acts as the host
- Samsung Galaxy Tab S7 FE (Android 14, One UI) acts as the secondary screen
- Transport: ADB USB (no WiFi, no touch input needed)
- User drags windows onto virtual display on Fedora → appears on tablet screen

---

## Developer Profile

- Name: Vinnavan
- OS: Fedora 43, KDE Plasma, x86_64
- Laptop: Lenovo (vendor ID 17AA), hybrid GPU mode
  - card0 = Intel iGPU
  - card1 = AMD GPU (PCI_ID=1002:15BF, DRIVER=amdgpu)
  - Also has Nvidia GPU (hybrid/offload mode)
- Tablet: Samsung Galaxy Tab S7 FE (Android 14, One UI, Snapdragon 778G, 2560×1600)
- Knows: Kotlin + Jetpack Compose (Android)
- Does NOT need: touch input from tablet, WiFi mode (USB only for now)

---

## Final Chosen Tech Stack

### Linux Host (Fedora)
| Component | Choice |
|---|---|
| Language | Python |
| GUI | PySide6 (Qt6) |
| Virtual Display | evdi kernel module (DKMS) + libevdi userspace |
| Frame Capture | evdi framebuffer callbacks → GStreamer appsrc |
| Encoder | GStreamer + x264enc (zerolatency tuning) |
| Transport | ADB USB via `adb forward tcp:7110 tcp:7110` |
| ADB Management | Python subprocess |
| Distribution | AppImage (linuxdeploy + GStreamer plugin) |

### Android (Samsung Tab S7 FE)
| Component | Choice |
|---|---|
| Language | Kotlin |
| UI | Jetpack Compose |
| Socket | ServerSocket on port 7110 |
| Decoder | MediaCodec async (H.264, KEY_LOW_LATENCY=1) |
| Renderer | SurfaceView |
| Distribution | Play Store |

### Transport Protocol
- ADB USB: `adb forward tcp:7110 tcp:7110`
- Framing: `[4 bytes: length][1 byte: type][N bytes: payload]`
- Packet types: `0x01` = SPS/PPS config, `0x02` = H.264 NAL unit
- Stream format: raw H.264 NAL units

---

## Project Phases & Current Status

### ✅ DONE — Phase 1: Android Receiver App
Built and tested. Working.

**What it does:**
- `ServerSocket` listens on port 7110
- Reads framing protocol: `[4B length][1B type][payload]`
- Feeds payload to `MediaCodec` async H.264 decoder
- Renders decoded frames to `SurfaceView`
- Compose UI shows connection status

**Tested with:**
```bash
adb forward tcp:7110 tcp:7110
ffmpeg -re -i video.mp4 -vcodec libx264 -preset ultrafast -tune zerolatency -f h264 tcp://127.0.0.1:7110
```
FFmpeg on laptop streams H.264 over ADB USB to tablet. App decoded and displayed correctly.

**Key implementation notes:**
- `MediaFormat.KEY_LOW_LATENCY = 1` — critical, set this
- `releaseOutputBuffer(index, true)` — renders to SurfaceView
- SurfaceView preferred over TextureView (lower latency)
- Min SDK: API 26

---

### ✅ DONE — Phase 2: evdi Virtual Display on Fedora KDE

KDE now sees a second monitor called `DVI-I-1-unknown`. Fully working as extended display.

#### What Was Installed

```bash
sudo dnf install dkms kernel-devel kernel-headers gcc make git
git clone https://github.com/DisplayLink/evdi.git
cd evdi/module
sudo make install_dkms
sudo modprobe evdi
cd ~/evdi/library
make
sudo make install
# libevdi.so installed at /usr/lib/libevdi.so
```

evdi version: **1.14.16**
Kernel: **6.19.14-300.fc44.x86_64** (fc44 kernel on fc43 system)

#### What DID NOT Work

**Attempt 1: Top-level `make` failed**
```
cc1: all warnings being treated as errors
-Werror=sign-compare errors in kernel headers
```
Root cause: evdi 1.14.16 + kernel 6.19 GCC strict warning incompatibility in kernel header files (not evdi's own code). The errors are in `linux/cleanup.h`, `linux/fs/super.h`, etc.
**Resolution:** DKMS install (`sudo make install_dkms`) already succeeded earlier — module was already built. Top-level rebuild was unnecessary. Ignored.

**Attempt 2: pyevdi had no setup.py or pyproject.toml**
```
ERROR: Directory '.' is not installable. Neither 'setup.py' nor 'pyproject.toml' found.
```
The pyevdi directory contains: `Buffer.cpp Card.cpp Makefile pytest.run.sh sample_edid Stats.h Buffer.h Card.h PyEvdi.cpp README.md Stats.cpp test`
It's a C++ extension, not a pip package. **Resolution:** Used ctypes to call libevdi.so directly.

**Attempt 3: ctypes script with wrong argtypes — segfault**
```python
# WRONG — handle return type not set, evdi_open(0) opened card0 not evdi device
handle = lib.evdi_open(0)  # opened Intel GPU card0!
lib.evdi_connect(handle, edid_buf, len(EDID), 1920*1080)  # segfault
```
Two bugs:
1. `evdi_open(0)` opened card0 (Intel iGPU), not the evdi virtual device (card2)
2. `lib.evdi_open.restype` not set → handle treated as int → segfault on connect

**Attempt 4: Fixed argtypes but segfault after connect**
Connected successfully, KDE detected display, but crashed during `time.sleep()` because evdi fires mode-change events immediately after connect with no handler registered.

#### Working Script (Current)

```python
import ctypes, time, signal, sys

lib = ctypes.CDLL("/usr/lib/libevdi.so")

lib.evdi_open.restype  = ctypes.c_void_p
lib.evdi_open.argtypes = [ctypes.c_int]

lib.evdi_connect.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_uint, ctypes.c_uint]
lib.evdi_handle_events.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_int]

lib.evdi_add_device()
handle = lib.evdi_open(2)   # card2 = evdi virtual device
print(f"handle: {handle}")
if not handle:
    print("open failed"); sys.exit(1)

EDID = bytes([
    0x00,0xFF,0xFF,0xFF,0xFF,0xFF,0xFF,0x00,
    0x31,0xD8,0x00,0x00,0x00,0x00,0x00,0x00,
    0x05,0x16,0x01,0x03,0x6D,0x32,0x1C,0x78,
    0xEA,0x5E,0xC0,0xA4,0x59,0x4A,0x98,0x25,
    0x20,0x50,0x54,0x00,0x00,0x00,0x01,0x01,
    0x01,0x01,0x01,0x01,0x01,0x01,0x01,0x01,
    0x01,0x01,0x01,0x01,0x01,0x01,0x02,0x3A,
    0x80,0x18,0x71,0x38,0x2D,0x40,0x58,0x2C,
    0x45,0x00,0xFD,0x1E,0x11,0x00,0x00,0x1E,
    0x00,0x00,0x00,0xFF,0x00,0x4C,0x69,0x6E,
    0x75,0x78,0x20,0x23,0x30,0x0A,0x20,0x20,
    0x20,0x20,0x00,0x00,0x00,0xFD,0x00,0x3B,
    0x3D,0x42,0x44,0x0F,0x00,0x0A,0x20,0x20,
    0x20,0x20,0x20,0x20,0x00,0x00,0x00,0xFC,
    0x00,0x56,0x69,0x72,0x74,0x75,0x61,0x6C,
    0x0A,0x20,0x20,0x20,0x20,0x20,0x00,0xAB
])

lib.evdi_connect(handle, EDID, len(EDID), 1920 * 1080)
print("Connected — running event loop. Ctrl+C to exit.")

def cleanup(sig, frame):
    lib.evdi_disconnect(handle)
    lib.evdi_close(handle)
    sys.exit(0)

signal.signal(signal.SIGINT, cleanup)

while True:
    lib.evdi_handle_events(handle, None, 16)
```

**Run with:**
```bash
sudo modprobe -r evdi && sudo modprobe evdi
sudo python3 ~/evdi_connect.py
```

**Result:** KDE Display Settings shows `DVI-I-1-unknown` as extended display, 1024×768 default (user changed to 1920×1080 in Display Settings). Script runs stably with event loop.

#### Important Notes on evdi Behavior
- `evdi_add_device()` must be called each time after fresh modprobe — creates the virtual DRM device
- `evdi_open(N)` opens `/dev/dri/cardN` — must pass correct card number (2 in this setup)
- Card number can change between reboots — may need dynamic detection in final app
- Without `evdi_handle_events` loop, evdi segfaults when KDE sends mode-change events
- Display disappears when script exits — must keep script/daemon running while streaming
- KDE may auto-disable display if no framebuffer content — becomes non-issue once frame pipeline is feeding data

---

## What's NOT Done Yet (Remaining Work)

### Step 3 — Capture evdi Frames (NEXT)

Need to register `evdi_event_context` with callbacks:
- `update_ready` callback: fires when KDE draws new frame to virtual display
- Call `evdi_grab_pixels()` inside callback to get raw BGRA framebuffer
- Handle dirty rects (evdi sends changed regions, not always full frame)

evdi_event_context structure (from evdi_lib.h):
```c
struct evdi_event_context {
    void (*update_ready)(int buffer_to_be_updated, void *user_data);
    void (*mode_changed)(struct evdi_mode mode, void *user_data);
    void (*crtc_state)(int state, void *user_data);
    void (*cursor_set)(struct evdi_cursor_set cursor_set, void *user_data);
    void (*cursor_move)(struct evdi_cursor_move cursor_move, void *user_data);
    void (*ddcci_data_ready)(void *user_data);
    void *user_data;
};
```

Must register buffers with `evdi_register_buffer()` before grabbing pixels.

### Step 4 — GStreamer Pipeline

Feed raw BGRA frames from evdi → GStreamer appsrc → encode → stream:
```
appsrc (raw BGRA) → videoconvert → x264enc tune=zerolatency → h264parse → tcpclientsink host=127.0.0.1 port=7110
```

Python GStreamer bindings: `pip install PyGObject` + `gst-python`

Key x264enc parameters for low latency:
- `tune=zerolatency`
- `key-int-max=30`
- `bitrate=8000` (8 Mbps)
- disable B-frames

### Step 5 — ADB Forward + End-to-End Test

```bash
adb forward tcp:7110 tcp:7110
```

Drag window onto DVI-I-1-unknown → should appear on tablet.

### Step 6 — PySide6 GUI

Wrap everything in a proper desktop app:
- Start/stop stream button
- Resolution picker
- Bitrate slider
- ADB device detection + auto-forward
- Connection status

### Step 7 — AppImage Packaging

Using linuxdeploy + GStreamer plugin:
```bash
./linuxdeploy-x86_64.AppImage --appdir MyApp.AppDir --plugin gstreamer --output appimage
```

Must bundle: GStreamer plugins, libevdi.so, adb binary
AppRun must set: `GST_PLUGIN_PATH`, `LD_LIBRARY_PATH`

### Step 8 — Play Store Publishing

- Fill Data Safety form (local video data, no third-party sharing)
- Foreground Service required for background operation (Android 14)
- 12+ testers opted in for 14 continuous days before production access
- App description must clearly state USB Debugging requirement on host device

---

## Key Technical Concepts Reference

### Why evdi and not alternatives
- **KWin virtual output**: no stable DBus API in KDE Plasma 6, KDE-only, breaks between KDE updates
- **xrandr dummy**: X11 only, not Wayland
- **evdi**: works at DRM/kernel level, DE-agnostic (KDE + GNOME + anything), DKMS handles kernel updates, same tech DisplayLink uses

### ADB Transport
```bash
adb forward tcp:7110 tcp:7110
# laptop localhost:7110 → USB → tablet port 7110
```
USB bandwidth: ~30-40 MB/s, plenty for H.264 at 5-15 Mbps

### Latency Budget (target <100ms)
| Stage | Typical |
|---|---|
| evdi frame capture | 1–5ms |
| H.264 encode (zerolatency) | 5–20ms |
| ADB USB transport | 2–10ms |
| Android MediaCodec decode | 10–30ms |
| Display vsync | 0–16ms |
| **Total** | **~20–80ms** |

### H.264 Stream Format
- Raw NAL units (no container)
- SPS/PPS sent as type `0x01` at stream start
- Android MediaCodec needs SPS/PPS before first frame
- IDR frame = keyframe, needed for decoder to start
- GOP size 30 frames (keyframe every ~1 second at 30fps)

### evdi Frame Flow
```
KDE draws to DVI-I-1-unknown
    ↓
evdi kernel module stores framebuffer
    ↓
fires update_ready callback to userspace daemon
    ↓
daemon calls evdi_grab_pixels() → gets BGRA pixels
    ↓
pushes to GStreamer appsrc
    ↓
x264enc encodes → tcpclientsink sends
    ↓
ADB tunnels to tablet
    ↓
Android MediaCodec decodes → SurfaceView renders
```

---

## File Locations on Developer Machine

| File | Path |
|---|---|
| evdi repo | `~/evdi/` |
| evdi kernel module | `/lib/modules/6.19.14-300.fc44.x86_64/extra/evdi.ko.xz` |
| libevdi.so | `/usr/lib/libevdi.so` (symlink → libevdi.so.1 → libevdi.so.1.14.16) |
| evdi connect script | `~/evdi_connect.py` |
| Android app | separate Android Studio project (Kotlin + Compose) |

---

## Commands Reference

```bash
# Load evdi fresh
sudo modprobe -r evdi && sudo modprobe evdi

# Run virtual display daemon
sudo python3 ~/evdi_connect.py

# Check DRM devices
ls /dev/dri/

# Check evdi loaded
lsmod | grep evdi

# ADB forward for streaming
adb forward tcp:7110 tcp:7110

# Test stream from laptop to tablet (requires Android app running)
ffmpeg -re -i video.mp4 -vcodec libx264 -preset ultrafast -tune zerolatency -f h264 tcp://127.0.0.1:7110

# Generate test pattern stream (no input file needed)
ffmpeg -f lavfi -i testsrc=size=1920x1080:rate=30 -vcodec libx264 -preset ultrafast -tune zerolatency -f h264 tcp://127.0.0.1:7110

# Check kernel dmesg for evdi
sudo dmesg | grep -i evdi
```

---

## Distribution Plan

### Linux: AppImage
- Covers Fedora, Arch, Ubuntu, all distros in one file
- No Flathub sandbox issues
- Must bundle: GStreamer plugins, libevdi, adb
- User prerequisite: DKMS + kernel-devel (one-time, for evdi module build)

### Android: Play Store
- $25 one-time developer fee
- 12+ testers, 14 continuous days on closed track before production
- Foreground Service + persistent notification (Android 14 requirement)
- Data Safety: declare local video data, no third-party sharing
- App description must state USB Debugging required on host laptop

---

## Known Issues / Gotchas

1. **card number not always 2**: evdi device card number depends on system state. Final app should scan `/dev/dri/` and identify evdi cards dynamically rather than hardcoding `evdi_open(2)`.

2. **evdi_add_device() accumulates**: calling it multiple times creates multiple devices (card2, card3, card4...). Should check if device already exists before adding. Or do `modprobe -r evdi && modprobe evdi` to reset clean.

3. **Display disappears on script exit**: evdi disconnect removes the DRM device. Keep daemon running. Final app must keep daemon alive while streaming.

4. **Mirror mode accident**: if user sets mirror in KDE Display Settings, virtual display loses its independent framebuffer. Always use Extended mode.

5. **KDE may disable blank virtual display**: without frame content, KDE might auto-disable the output. Becomes non-issue once GStreamer pipeline feeds frames.

6. **EDID resolution**: current EDID advertises 1920×1080. User must manually select it in KDE Display Settings. Future improvement: generate EDID dynamically matching tablet resolution (2560×1600).

7. **Hybrid GPU mode**: evdi works fine with Intel/AMD hybrid. Potential conflicts with Nvidia prime sync — test carefully when Nvidia offloading is active.

8. **DKMS rebuild on kernel update**: DKMS handles this automatically but requires `kernel-devel` to be installed. If user does `dnf upgrade` and kernel-devel is missing, evdi won't load after reboot. App should detect and warn.
