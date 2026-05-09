#!/usr/bin/env python3
import ctypes, time, signal, sys, subprocess

lib = ctypes.CDLL("/usr/lib/libevdi.so")

class EvdiRect(ctypes.Structure):
    _fields_ = [("x1",ctypes.c_int),("y1",ctypes.c_int),
                ("x2",ctypes.c_int),("y2",ctypes.c_int)]

class EvdiBuffer(ctypes.Structure):
    _fields_ = [("id",ctypes.c_int),("buffer",ctypes.c_void_p),
                ("width",ctypes.c_int),("height",ctypes.c_int),
                ("stride",ctypes.c_int),("rects",ctypes.POINTER(EvdiRect)),
                ("rect_count",ctypes.c_int)]

class EvdiMode(ctypes.Structure):
    _fields_ = [("width",ctypes.c_int),("height",ctypes.c_int),
                ("refresh_rate",ctypes.c_int),("bits_per_pixel",ctypes.c_int),
                ("pixel_format",ctypes.c_uint32)]

UPDATE_READY_CB = ctypes.CFUNCTYPE(None, ctypes.c_int, ctypes.c_void_p)
MODE_CHANGED_CB = ctypes.CFUNCTYPE(None, EvdiMode,     ctypes.c_void_p)
CRTC_STATE_CB   = ctypes.CFUNCTYPE(None, ctypes.c_int, ctypes.c_void_p)
DDCCI_CB        = ctypes.CFUNCTYPE(None, ctypes.c_void_p)

class EvdiEventContext(ctypes.Structure):
    _fields_ = [("update_ready",UPDATE_READY_CB),("mode_changed",MODE_CHANGED_CB),
                ("crtc_state",CRTC_STATE_CB),("cursor_set",ctypes.c_void_p),
                ("cursor_move",ctypes.c_void_p),("ddcci_data_ready",DDCCI_CB),
                ("user_data",ctypes.c_void_p)]

lib.evdi_open.restype             = ctypes.c_void_p
lib.evdi_open.argtypes            = [ctypes.c_int]
lib.evdi_connect.argtypes         = [ctypes.c_void_p,ctypes.c_char_p,ctypes.c_uint,ctypes.c_uint]
lib.evdi_disconnect.argtypes      = [ctypes.c_void_p]
lib.evdi_close.argtypes           = [ctypes.c_void_p]
lib.evdi_register_buffer.argtypes = [ctypes.c_void_p,EvdiBuffer]
lib.evdi_unregister_buffer.argtypes=[ctypes.c_void_p,ctypes.c_int]
lib.evdi_request_update.restype   = ctypes.c_bool
lib.evdi_request_update.argtypes  = [ctypes.c_void_p,ctypes.c_int]
lib.evdi_grab_pixels.argtypes     = [ctypes.c_void_p,ctypes.POINTER(EvdiRect),ctypes.POINTER(ctypes.c_int)]
lib.evdi_handle_events.argtypes   = [ctypes.c_void_p,ctypes.POINTER(EvdiEventContext),ctypes.c_int]

MAX_RECTS = 16
state = {"handle":None,"width":1920,"height":1080,"fb":None,"rects":None,
         "evdi_buf":None,"registered":False,"crtc_on":False,
         "frame_count":0,"fps_timer":time.time()}

def make_buffer(w, h):
    stride = w * 4
    fb    = (ctypes.c_ubyte * (stride * h))()
    rects = (EvdiRect * MAX_RECTS)()
    buf   = EvdiBuffer(id=0, buffer=ctypes.cast(fb,ctypes.c_void_p),
                       width=w, height=h, stride=stride,
                       rects=ctypes.cast(rects,ctypes.POINTER(EvdiRect)),
                       rect_count=MAX_RECTS)
    return fb, rects, buf

def on_update_ready(buf_id, user_data):
    if not state["crtc_on"]:
        return
    num_rects = ctypes.c_int(0)
    lib.evdi_grab_pixels(state["handle"], state["rects"], ctypes.byref(num_rects))
    state["frame_count"] += 1
    now = time.time()
    if now - state["fps_timer"] >= 1.0:
        print(f"FPS: {state['frame_count']}  |  dirty rects: {num_rects.value}  |  res: {state['width']}x{state['height']}")
        state["frame_count"] = 0
        state["fps_timer"]   = now
    # TODO Step 2: push state["fb"] to GStreamer appsrc here

def on_mode_changed(mode, user_data):
    print(f"Mode → {mode.width}x{mode.height} @ {mode.refresh_rate}Hz bpp={mode.bits_per_pixel}")
    state["width"] = mode.width
    state["height"] = mode.height
    if state["registered"]:
        lib.evdi_unregister_buffer(state["handle"], 0)
        state["registered"] = False
    fb, rects, buf = make_buffer(mode.width, mode.height)
    state["fb"] = fb; state["rects"] = rects; state["evdi_buf"] = buf
    lib.evdi_register_buffer(state["handle"], buf)
    state["registered"] = True
    print("Buffer registered.")

def on_crtc_state(crtc_on, user_data):
    state["crtc_on"] = bool(crtc_on)
    print(f"CRTC {'ON ✓' if crtc_on else 'OFF'}")
    if crtc_on:
        print("→ Display active. Drag a window onto DVI-I-1-unknown.")

def on_ddcci(user_data):
    pass

_cb_update = UPDATE_READY_CB(on_update_ready)
_cb_mode   = MODE_CHANGED_CB(on_mode_changed)
_cb_crtc   = CRTC_STATE_CB(on_crtc_state)
_cb_ddcci  = DDCCI_CB(on_ddcci)

ctx = EvdiEventContext(update_ready=_cb_update, mode_changed=_cb_mode,
                       crtc_state=_cb_crtc, cursor_set=None, cursor_move=None,
                       ddcci_data_ready=_cb_ddcci, user_data=None)

# ── Build correct 1920x1080@60Hz EDID (128 bytes) ──────────────────────────
e = bytearray(128)
# Header
e[0:8]   = [0x00,0xFF,0xFF,0xFF,0xFF,0xFF,0xFF,0x00]
# Vendor: "LNX", product 0x0000, no serial, week 5, year 2022
e[8:10]  = [0x31,0xD8]
e[10:12] = [0x00,0x00]
e[12:16] = [0x00,0x00,0x00,0x00]
e[16]    = 0x05
e[17]    = 0x20   # 1990+32=2022
# EDID v1.3
e[18]    = 0x01
e[19]    = 0x03
# Basic display: digital, 52x29cm, gamma 2.2
e[20]    = 0x80
e[21]    = 0x34
e[22]    = 0x1D
e[23]    = 0x78
e[24]    = 0x0A
# Chromaticity (generic)
e[25:35] = [0xCE,0x00,0xA4,0x59,0x4A,0x98,0x25,0x20,0x50,0x54]
# Established timings: none
e[35:38] = [0x00,0x00,0x00]
# Standard timings: all unused
e[38:54] = [0x01,0x01]*8

# Descriptor 1: 1920x1080@60Hz detailed timing (bytes 54–71)
# pclk = 148.5MHz = 14850 = 0x3A02 → LSB first
# Hactive=1920, Hblank=280, Vactive=1080, Vblank=45
# Hsync offset=88, Hsync pulse=44, Vsync offset=4, Vsync pulse=5
# Image: 527mm x 296mm
d = 54
e[d+0]  = 0x02          # pclk LSB  (14850 & 0xFF)
e[d+1]  = 0x3A          # pclk MSB  (14850 >> 8)
e[d+2]  = 0x80          # Hactive LSB  (1920 & 0xFF)
e[d+3]  = 0x18          # Hblank LSB   (280 & 0xFF)
e[d+4]  = 0x71          # [7:4]=Hactive hi (1920>>8=7), [3:0]=Hblank hi (280>>8=1)
e[d+5]  = 0x38          # Vactive LSB  (1080 & 0xFF)
e[d+6]  = 0x2D          # Vblank LSB   (45 & 0xFF)
e[d+7]  = 0x40          # [7:4]=Vactive hi (1080>>8=4), [3:0]=Vblank hi (45>>8=0)
e[d+8]  = 0x58          # Hsync offset LSB (88)
e[d+9]  = 0x2C          # Hsync pulse LSB  (44)
e[d+10] = 0x45          # [7:4]=Vsync offset (4), [3:0]=Vsync pulse (5)
e[d+11] = 0x00          # upper 2 bits of each sync param — all zero (fit in 8 bits)
e[d+12] = 0x0F          # Himage LSB (527 & 0xFF)
e[d+13] = 0x28          # Vimage LSB (296 & 0xFF) — 0x28=40, use 296&0xFF=0x28
e[d+14] = 0x21          # [7:4]=Himage hi (527>>8=2), [3:0]=Vimage hi (296>>8=1)
e[d+15] = 0x00          # Hborder
e[d+16] = 0x00          # Vborder
e[d+17] = 0x1E          # Flags: non-interlaced, +HSync +VSync

# Descriptor 2: Monitor name "Virtual1080"
n = 72
e[n+0]  = 0x00; e[n+1]=0x00; e[n+2]=0x00; e[n+3]=0xFC; e[n+4]=0x00
name = b'Virtual1080\n   '
e[n+5:n+18] = name[:13]

# Descriptor 3: Serial number
s = 90
e[s+0]  = 0x00; e[s+1]=0x00; e[s+2]=0x00; e[s+3]=0xFF; e[s+4]=0x00
e[s+5:s+13] = b'EVDI0001'
e[s+13] = 0x0A
e[s+14:s+18] = [0x20]*4

# Descriptor 4: Range limits (56–76Hz vertical, 30–81kHz horizontal, 150MHz pclk max)
r = 108
e[r+0]  = 0x00; e[r+1]=0x00; e[r+2]=0x00; e[r+3]=0xFD; e[r+4]=0x00
e[r+5]  = 0x38   # min Vfreq 56Hz
e[r+6]  = 0x4C   # max Vfreq 76Hz
e[r+7]  = 0x1E   # min Hfreq 30kHz
e[r+8]  = 0x51   # max Hfreq 81kHz
e[r+9]  = 0x0F   # max pclk 150MHz
e[r+10] = 0x00
e[r+11:r+18] = [0x0A,0x20,0x20,0x20,0x20,0x20,0x20]

e[126] = 0x00   # no extensions
# Checksum: byte 127 makes total sum = 0 mod 256
e[127] = (256 - (sum(e[:127]) % 256)) % 256

EDID = bytes(e)
assert len(EDID) == 128, f"EDID wrong size: {len(EDID)}"
print(f"EDID built: {len(EDID)} bytes, checksum=0x{e[127]:02X}")

# ── Cleanup ────────────────────────────────────────────────────────────────
def cleanup(sig=None, frame=None):
    print("\nShutting down…")
    try:
        subprocess.run(["kscreen-doctor","output.DVI-I-1-unknown.disable"],capture_output=True, timeout=3)
    except subprocess.TimeoutExpired:
        pass
    time.sleep(0.5)
    if state["registered"]:
        lib.evdi_unregister_buffer(state["handle"], 0)
    lib.evdi_disconnect(state["handle"])
    lib.evdi_close(state["handle"])
    print("Done. You can now run: sudo modprobe -r evdi")
    sys.exit(0)

signal.signal(signal.SIGINT,  cleanup)
signal.signal(signal.SIGTERM, cleanup)

# ── Main ───────────────────────────────────────────────────────────────────
lib.evdi_add_device()
handle = lib.evdi_open(2)
if not handle:
    print("evdi_open failed — is evdi module loaded?")
    sys.exit(1)
state["handle"] = handle

fb, rects, buf = make_buffer(1920, 1080)
state["fb"] = fb; state["rects"] = rects; state["evdi_buf"] = buf

lib.evdi_connect(handle, EDID, len(EDID), 1920 * 1080)
lib.evdi_register_buffer(handle, buf)
state["registered"] = True
print("Connected + buffer pre-registered.")

# Give KDE 1s to detect the new device, then force-enable it
time.sleep(1.0)
try:
    res = subprocess.run(
        ["kscreen-doctor","output.DVI-I-1-unknown.enable"],
        capture_output=True, text=True, timeout=3
    )
    print("kscreen-doctor enable:", "✓" if res.returncode == 0 else res.stderr.strip())
except subprocess.TimeoutExpired:
    print("kscreen-doctor timed out — enable DVI-I-1-unknown manually in Display Settings")

try:
    subprocess.run(
        ["kscreen-doctor","output.DVI-I-1-unknown.mode.1920x1080@60"],
        capture_output=True, timeout=3
    )
except subprocess.TimeoutExpired:
    pass

print("\nWaiting for CRTC ON… drag a window onto DVI-I-1-unknown.\n")

while True:
    lib.evdi_request_update(handle, 0)
    lib.evdi_handle_events(handle, ctypes.byref(ctx), 16)
