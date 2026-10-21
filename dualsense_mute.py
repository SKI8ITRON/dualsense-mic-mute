"""DualSense mic mute button on Windows, like on PS5.

Button press toggles mute:
  * USB: the controller mic is muted in hardware and its Windows endpoint
    "Headset Microphone (DualSense)" is muted too,
  * Bluetooth: Windows has no DualSense mic then, so the Windows default
    recording device (e.g. your headset) is muted instead,
  * the button LED: off = mic is live, orange = muted.

Only Python standard library (ctypes + Windows hid.dll / setupapi / Core Audio),
nothing to pip install.

  pythonw dualsense_mute.py           run (autostart uses this)
  python  dualsense_mute.py --probe   print device info and button presses
  python  dualsense_mute.py --release unmute everything and turn the LED off
                                      (uninstall runs this after stopping it)
"""
import ctypes
import ctypes.wintypes as wt
import json
import os
import sys
import time
import winreg
import zlib

HERE = os.path.dirname(os.path.abspath(__file__))
LOG = os.path.join(HERE, "dualsense_mute.log")
STATE = os.path.join(HERE, "state.json")

SONY_VID = 0x054C
DUALSENSE_PIDS = (0x0CE6, 0x0DF2)  # DualSense, DualSense Edge
USB_INPUT_LEN = 64
BT_OUTPUT_LEN = 78
MUTE_BIT = 0x04  # input report: USB 0x01 byte 10, Bluetooth 0x31 byte 11
REASSERT_SEC = 2.0  # Steam/games may overwrite the LED, so we resend it

kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
setupapi = ctypes.WinDLL("setupapi", use_last_error=True)
hid = ctypes.WinDLL("hid")
ole32 = ctypes.WinDLL("ole32")


def log(msg):
    line = time.strftime("%Y-%m-%d %H:%M:%S ") + msg
    if sys.stdout and "--probe" in sys.argv:
        print(line)
    try:
        if os.path.exists(LOG) and os.path.getsize(LOG) > 512_000:
            os.replace(LOG, LOG + ".old")
        with open(LOG, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except OSError:
        pass


# ---------------------------------------------------------------- HID access
class GUID(ctypes.Structure):
    _fields_ = [("Data1", wt.DWORD), ("Data2", wt.WORD), ("Data3", wt.WORD),
                ("Data4", ctypes.c_ubyte * 8)]

    @classmethod
    def parse(cls, s):
        s = s.strip("{}").replace("-", "")
        g = cls()
        g.Data1, g.Data2, g.Data3 = int(s[0:8], 16), int(s[8:12], 16), int(s[12:16], 16)
        for i in range(8):
            g.Data4[i] = int(s[16 + 2 * i:18 + 2 * i], 16)
        return g


class SP_DEVICE_INTERFACE_DATA(ctypes.Structure):
    _fields_ = [("cbSize", wt.DWORD), ("InterfaceClassGuid", GUID),
                ("Flags", wt.DWORD), ("Reserved", ctypes.c_size_t)]


class HIDD_ATTRIBUTES(ctypes.Structure):
    _fields_ = [("Size", wt.ULONG), ("VendorID", wt.USHORT),
                ("ProductID", wt.USHORT), ("VersionNumber", wt.USHORT)]


class HIDP_CAPS(ctypes.Structure):
    _fields_ = [("Usage", wt.USHORT), ("UsagePage", wt.USHORT),
                ("InputReportByteLength", wt.USHORT),
                ("OutputReportByteLength", wt.USHORT),
                ("FeatureReportByteLength", wt.USHORT),
                ("Reserved", wt.USHORT * 17), ("rest", wt.USHORT * 10)]


GENERIC_READ, GENERIC_WRITE = 0x80000000, 0x40000000
FILE_SHARE_RW = 0x3
OPEN_EXISTING = 3
INVALID_HANDLE = wt.HANDLE(-1).value
DIGCF_PRESENT, DIGCF_DEVICEINTERFACE = 0x2, 0x10

kernel32.CreateFileW.restype = wt.HANDLE
kernel32.CreateFileW.argtypes = [wt.LPCWSTR, wt.DWORD, wt.DWORD, wt.LPVOID,
                                 wt.DWORD, wt.DWORD, wt.HANDLE]
kernel32.ReadFile.argtypes = [wt.HANDLE, wt.LPVOID, wt.DWORD,
                              ctypes.POINTER(wt.DWORD), wt.LPVOID]
kernel32.WriteFile.argtypes = [wt.HANDLE, wt.LPVOID, wt.DWORD,
                               ctypes.POINTER(wt.DWORD), wt.LPVOID]
kernel32.CloseHandle.argtypes = [wt.HANDLE]
setupapi.SetupDiGetClassDevsW.restype = wt.HANDLE
setupapi.SetupDiGetClassDevsW.argtypes = [ctypes.POINTER(GUID), wt.LPCWSTR,
                                          wt.HWND, wt.DWORD]
setupapi.SetupDiEnumDeviceInterfaces.argtypes = [
    wt.HANDLE, wt.LPVOID, ctypes.POINTER(GUID), wt.DWORD,
    ctypes.POINTER(SP_DEVICE_INTERFACE_DATA)]
setupapi.SetupDiGetDeviceInterfaceDetailW.argtypes = [
    wt.HANDLE, ctypes.POINTER(SP_DEVICE_INTERFACE_DATA), wt.LPVOID, wt.DWORD,
    ctypes.POINTER(wt.DWORD), wt.LPVOID]
setupapi.SetupDiDestroyDeviceInfoList.argtypes = [wt.HANDLE]
hid.HidD_GetAttributes.argtypes = [wt.HANDLE, ctypes.POINTER(HIDD_ATTRIBUTES)]
hid.HidD_GetPreparsedData.argtypes = [wt.HANDLE, ctypes.POINTER(ctypes.c_void_p)]
hid.HidD_FreePreparsedData.argtypes = [ctypes.c_void_p]
hid.HidP_GetCaps.argtypes = [ctypes.c_void_p, ctypes.POINTER(HIDP_CAPS)]


def hid_paths():
    guid = GUID()
    hid.HidD_GetHidGuid(ctypes.byref(guid))
    info = setupapi.SetupDiGetClassDevsW(ctypes.byref(guid), None, None,
                                         DIGCF_PRESENT | DIGCF_DEVICEINTERFACE)
    if info == INVALID_HANDLE:
        return
    try:
        i = 0
        while True:
            data = SP_DEVICE_INTERFACE_DATA()
            data.cbSize = ctypes.sizeof(data)
            if not setupapi.SetupDiEnumDeviceInterfaces(info, None, ctypes.byref(guid),
                                                        i, ctypes.byref(data)):
                break
            i += 1
            need = wt.DWORD()
            setupapi.SetupDiGetDeviceInterfaceDetailW(info, ctypes.byref(data), None, 0,
                                                      ctypes.byref(need), None)
            buf = ctypes.create_string_buffer(need.value)
            # cbSize of SP_DEVICE_INTERFACE_DETAIL_DATA_W: 8 on x64, 6 on x86
            ctypes.cast(buf, ctypes.POINTER(wt.DWORD))[0] = 8 if ctypes.sizeof(ctypes.c_void_p) == 8 else 6
            if setupapi.SetupDiGetDeviceInterfaceDetailW(info, ctypes.byref(data), buf, need,
                                                         None, None):
                yield ctypes.wstring_at(ctypes.addressof(buf) + 4)
    finally:
        setupapi.SetupDiDestroyDeviceInfoList(info)


def open_handle(path, access):
    h = kernel32.CreateFileW(path, access, FILE_SHARE_RW, None, OPEN_EXISTING, 0, None)
    return None if h in (None, INVALID_HANDLE) else h


def find_dualsense():
    """Return (path, input_len) of a DualSense gamepad interface, USB first."""
    found = []
    for path in hid_paths():
        h = open_handle(path, 0)  # query-only access works even if opened by others
        if not h:
            continue
        try:
            attr = HIDD_ATTRIBUTES()
            attr.Size = ctypes.sizeof(attr)
            if not hid.HidD_GetAttributes(h, ctypes.byref(attr)):
                continue
            if attr.VendorID != SONY_VID or attr.ProductID not in DUALSENSE_PIDS:
                continue
            pp = ctypes.c_void_p()
            if not hid.HidD_GetPreparsedData(h, ctypes.byref(pp)):
                continue
            caps = HIDP_CAPS()
            hid.HidP_GetCaps(pp, ctypes.byref(caps))
            hid.HidD_FreePreparsedData(pp)
            if caps.UsagePage == 0x01 and caps.Usage == 0x05:
                found.append((path, caps.InputReportByteLength))
        finally:
            kernel32.CloseHandle(h)
    found.sort(key=lambda f: f[1] != USB_INPUT_LEN)
    return found[0] if found else (None, 0)


# ------------------------------------------------- Windows mic endpoint mute
CLSID_MMDeviceEnumerator = GUID.parse("BCDE0395-E52F-467C-8E3D-C4579291692E")
IID_IMMDeviceEnumerator = GUID.parse("A95664D2-9614-4F35-A746-DE8DB63617E6")
IID_IAudioEndpointVolume = GUID.parse("5CDF2C82-841E-4546-9722-0CF74078229A")
CLSCTX_ALL = 0x17
CAPTURE_KEY = r"SOFTWARE\Microsoft\Windows\CurrentVersion\MMDevices\Audio\Capture"


def dualsense_capture_ids():
    """Endpoint ids of active capture devices whose name mentions DualSense."""
    ids = []
    with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, CAPTURE_KEY) as root:
        for i in range(winreg.QueryInfoKey(root)[0]):
            guid = winreg.EnumKey(root, i)
            try:
                with winreg.OpenKey(root, guid) as k:
                    if winreg.QueryValueEx(k, "DeviceState")[0] != 1:
                        continue
                with winreg.OpenKey(root, guid + r"\Properties") as p:
                    names = []
                    for j in range(winreg.QueryInfoKey(p)[1]):
                        _, val, typ = winreg.EnumValue(p, j)
                        if typ == winreg.REG_SZ:
                            names.append(val)
                if any("DualSense" in n for n in names):
                    ids.append("{0.0.1.00000000}." + guid)
            except OSError:
                continue
    return ids


def _vcall(obj, index, restype, argtypes=(), *args):
    """Call a COM method by vtable index."""
    vtbl = ctypes.cast(obj, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p)))[0]
    fn = ctypes.WINFUNCTYPE(restype, ctypes.c_void_p, *argtypes)(vtbl[index])
    return fn(obj, *args)


P = ctypes.c_void_p  # any pointer argument


def _endpoint_call(fn):
    """Run fn(enumerator) with an IMMDeviceEnumerator; None if COM is unavailable."""
    enum = ctypes.c_void_p()
    if ole32.CoCreateInstance(ctypes.byref(CLSID_MMDeviceEnumerator), None, CLSCTX_ALL,
                              ctypes.byref(IID_IMMDeviceEnumerator), ctypes.byref(enum)):
        return None
    try:
        return fn(enum)
    finally:
        _vcall(enum, 2, wt.ULONG)


def default_capture_id():
    """Id of the Windows default recording device (Settings -> Sound -> Input)."""
    def get(enum):
        dev, pid = ctypes.c_void_p(), ctypes.c_void_p()
        try:
            # GetDefaultAudioEndpoint(eCapture=1, eConsole=0), then IMMDevice::GetId
            _vcall(enum, 4, ctypes.HRESULT, (ctypes.c_int, ctypes.c_int, P), 1, 0,
                   ctypes.byref(dev))
            _vcall(dev, 5, ctypes.HRESULT, (P,), ctypes.byref(pid))
            return ctypes.wstring_at(pid.value)
        except OSError:
            return None
        finally:
            if pid:
                ole32.CoTaskMemFree(pid)
            if dev:
                _vcall(dev, 2, wt.ULONG)
    return _endpoint_call(get)


def mute_targets():
    """The DualSense mic if Windows has it (USB), else the default mic (Bluetooth)."""
    ids = dualsense_capture_ids()
    if not ids:
        d = default_capture_id()
        ids = [d] if d else []
    return ids


def set_windows_mute(muted, ids):
    # restype HRESULT raises OSError on failure; S_FALSE (already in that state) is fine
    def run(enum):
        ok = 0
        for dev_id in ids:
            dev, vol = ctypes.c_void_p(), ctypes.c_void_p()
            try:
                _vcall(enum, 5, ctypes.HRESULT, (ctypes.c_wchar_p, P), dev_id, ctypes.byref(dev))
                # IMMDevice::Activate -> IAudioEndpointVolume
                _vcall(dev, 3, ctypes.HRESULT, (P, wt.DWORD, P, P),
                       ctypes.byref(IID_IAudioEndpointVolume), CLSCTX_ALL, None,
                       ctypes.byref(vol))
                _vcall(vol, 14, ctypes.HRESULT, (wt.BOOL, P), muted, None)  # SetMute
                ok += 1
            except OSError as e:
                log(f"windows mute error ({dev_id}): {e}")
            finally:
                for obj in (vol, dev):
                    if obj:
                        _vcall(obj, 2, wt.ULONG)  # Release
        return ok
    return _endpoint_call(run) or 0


# ------------------------------------------------------------ controller I/O
class Pad:
    """One opened DualSense; report layouts as in Linux hid-playstation."""

    def __init__(self, path, in_len):
        self.bt = in_len != USB_INPUT_LEN
        self.in_len = in_len
        self.rh = open_handle(path, GENERIC_READ)
        self.wh = open_handle(path, GENERIC_WRITE)
        self.seq = 0

    def ok(self):
        return bool(self.rh and self.wh)

    def close(self):
        for h in (self.rh, self.wh):
            if h:
                kernel32.CloseHandle(h)

    def report(self, muted):
        common = bytearray(47)
        common[1] = 0x01 | 0x02              # valid_flag1: mic mute LED + power save control
        common[8] = 0x01 if muted else 0x00  # mute button LED: 0 off, 1 on, 2 pulse
        common[9] = 0x10 if muted else 0x00  # power_save_control: 0x10 = mute mic in hardware
        if not self.bt:
            return bytes([0x02]) + bytes(common)  # USB report 0x02, 48 bytes
        # Bluetooth report 0x31, 78 bytes: id, seq<<4, tag 0x10, common, ..., crc32
        r = bytearray(BT_OUTPUT_LEN)
        r[0], r[1], r[2] = 0x31, (self.seq << 4) & 0xF0, 0x10
        self.seq = (self.seq + 1) & 0x0F
        r[3:3 + len(common)] = common
        r[-4:] = zlib.crc32(b"\xa2" + bytes(r[:-4])).to_bytes(4, "little")
        return bytes(r)

    def send(self, muted):
        data = self.report(muted)
        buf = ctypes.create_string_buffer(data, len(data))
        n = wt.DWORD()
        return bool(kernel32.WriteFile(self.wh, buf, len(data), ctypes.byref(n), None))

    def read_mute_pressed(self, buf, n):
        """Block for the next input report; None on disconnect, else button state."""
        while kernel32.ReadFile(self.rh, buf, self.in_len, ctypes.byref(n), None):
            raw = buf.raw
            if not self.bt and n.value > 10 and raw[0] == 0x01:
                return bool(raw[10] & MUTE_BIT)
            if self.bt and n.value > 11 and raw[0] == 0x31:
                return bool(raw[11] & MUTE_BIT)
        return None


def load_state():
    try:
        with open(STATE, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def save_state(muted, ids):
    try:
        with open(STATE, "w", encoding="utf-8") as f:
            json.dump({"muted": muted, "windows_muted": ids,
                       "since": time.strftime("%Y-%m-%d %H:%M:%S")}, f)
    except OSError:
        pass


class Muter:
    def __init__(self):
        self.muted = False
        self.ids = []  # endpoints we muted in Windows, to unmute them later
        # a previous run may have left a mic muted in Windows
        left = load_state().get("windows_muted") or []
        if left:
            set_windows_mute(False, left)
        save_state(False, [])

    def apply(self, pad, muted):
        self.muted = muted
        targets = mute_targets()
        stale = [i for i in self.ids if i not in targets]
        if stale:  # switched USB <-> Bluetooth: release the old mic
            set_windows_mute(False, stale)
        win = set_windows_mute(muted, targets)
        self.ids = targets if muted else []
        led = pad.send(muted)
        save_state(muted, self.ids)
        mode = "bluetooth, default mic" if pad.bt else "usb, DualSense mic"
        log(f"{'MUTED' if muted else 'LIVE'} ({mode}; led={'ok' if led else 'fail'}, "
            f"windows endpoints={win})")


def release():
    """Undo everything a stopped run may have left: Windows mute, LED, hardware mute."""
    ole32.CoInitializeEx(None, 0x2)
    left = load_state().get("windows_muted") or []
    win = set_windows_mute(False, left) if left else 0
    save_state(False, [])
    path, in_len = find_dualsense()
    led = False
    if path:
        pad = Pad(path, in_len)
        if pad.ok():
            led = pad.send(False)
        pad.close()
    log(f"RELEASED (windows endpoints unmuted={win}, led off={'ok' if led else 'no pad'})")


def run(probe=False):
    ole32.CoInitializeEx(None, 0x2)  # apartment-threaded
    m = Muter()
    while True:
        path, in_len = find_dualsense()
        if not path:
            time.sleep(3)
            continue
        pad = Pad(path, in_len)
        if not pad.ok():
            log(f"cannot open DualSense (error {ctypes.get_last_error()}), retry")
            pad.close()
            time.sleep(5)
            continue
        log(f"DualSense connected over {'Bluetooth' if pad.bt else 'USB'}: {path}")
        m.apply(pad, m.muted)
        buf = ctypes.create_string_buffer(in_len)
        n = wt.DWORD()
        pressed = False
        last = time.monotonic()
        try:
            while True:
                now_pressed = pad.read_mute_pressed(buf, n)
                if now_pressed is None:
                    break
                if now_pressed and not pressed:
                    m.apply(pad, not m.muted)
                    last = time.monotonic()
                    if probe:
                        print("mute button pressed ->", "MUTED" if m.muted else "LIVE")
                pressed = now_pressed
                if time.monotonic() - last > REASSERT_SEC:
                    pad.send(m.muted)
                    last = time.monotonic()
        finally:
            pad.close()
        log("DualSense disconnected, waiting")
        time.sleep(2)


if __name__ == "__main__":
    # one copy only (autostart + manual launch would fight over the LED)
    kernel32.CreateMutexW.restype = wt.HANDLE
    _mutex = kernel32.CreateMutexW(None, False, "Local\\dualsense_mute")
    if ctypes.get_last_error() == 183:  # ERROR_ALREADY_EXISTS
        log("already running, exit")
        sys.exit(0)
    if "--release" in sys.argv:
        release()
        sys.exit(0)
    if "--probe" in sys.argv:
        p, ln = find_dualsense()
        print("device:", p, "input report bytes:", ln)
        ole32.CoInitializeEx(None, 0x2)
        print("mic muted by the button:", mute_targets())
        print("press the mute button (Ctrl+C to quit)")
    try:
        run(probe="--probe" in sys.argv)
    except KeyboardInterrupt:
        pass
