"""Звук: общая громкость, громкость по приложениям, микрофон, устройство вывода.

Все функции здесь синхронные и вызываются ТОЛЬКО через comthread.run().
Исключение — Loopback (захват того, что играет, для визуализатора): у него свой поток со своим COM.
Обёртки pycaw (AudioUtilities.GetAllDevices/GetSpeakers/GetAllSessions) НЕ используются:
в связке с новым comtypes они дважды освобождают COM-объекты и роняют процесс.
Берём только описания интерфейсов из pycaw, а список устройств читаем из реестра.
"""
import logging
import os
import sys
import threading
import time

import i18n

log = logging.getLogger("audio")
IS_WIN = sys.platform == "win32"

E_RENDER, E_CAPTURE = 0, 1
ROLE_CONSOLE, ROLE_MULTIMEDIA = 0, 1

_ok = None
_enum = None
_policy = None

NICE = {"discord.exe": "Discord", "spotify.exe": "Spotify", "chrome.exe": "Chrome",
        "firefox.exe": "Firefox", "msedge.exe": "Edge", "opera.exe": "Opera", "brave.exe": "Brave",
        "steam.exe": "Steam", "steamwebhelper.exe": "Steam", "telegram.exe": "Telegram",
        "javaw.exe": "Minecraft", "factorio.exe": "Factorio", "obs64.exe": "OBS"}
SKIP = {"audiodg.exe"}
_last_err = {}


def _log_once(what):
    """Опрос идёт каждые 3 с: одну и ту же ошибку пишем в лог один раз (вызывать из except)."""
    e = sys.exc_info()[1]
    text = f"{type(e).__name__}: {e}"
    if _last_err.get(what) != text:
        _last_err[what] = text
        log.exception(what)


def available():
    global _ok
    if _ok is None:
        if not IS_WIN:
            _ok = False
        else:
            try:
                _enumerator()
                _ok = True
            except Exception:
                log.exception("Звук недоступен — микшер отключён")
                _ok = False
    return _ok


def _new_enumerator():
    import comtypes
    from pycaw.pycaw import IMMDeviceEnumerator
    from pycaw.constants import CLSID_MMDeviceEnumerator
    return comtypes.CoCreateInstance(CLSID_MMDeviceEnumerator, IMMDeviceEnumerator, comtypes.CLSCTX_INPROC_SERVER)


def _enumerator():
    global _enum
    if _enum is None:
        _enum = _new_enumerator()
    return _enum


def _default(flow):
    return _enumerator().GetDefaultAudioEndpoint(flow, ROLE_MULTIMEDIA if flow == E_RENDER else ROLE_CONSOLE)


def _activate(dev, iface):
    # Только QueryInterface (он делает AddRef). ctypes.cast создал бы второй объект на тот же
    # указатель без AddRef → двойной Release при сборке мусора → access violation.
    from comtypes import CLSCTX_ALL
    unk = dev.Activate(iface._iid_, CLSCTX_ALL, None)
    return unk.QueryInterface(iface)


def _speakers():
    from pycaw.pycaw import IAudioEndpointVolume
    return _activate(_default(E_RENDER), IAudioEndpointVolume)


def _mic():
    from pycaw.pycaw import IAudioEndpointVolume
    return _activate(_default(E_CAPTURE), IAudioEndpointVolume)


def _sessions():
    """[(exe, ISimpleAudioVolume)] для текущего устройства вывода."""
    import psutil
    from pycaw.pycaw import IAudioSessionControl2, IAudioSessionManager2, ISimpleAudioVolume
    mgr = _activate(_default(E_RENDER), IAudioSessionManager2)
    en = mgr.GetSessionEnumerator()
    out = []
    for i in range(en.GetCount()):
        ctl = en.GetSession(i)
        if not ctl:
            continue
        c2 = ctl.QueryInterface(IAudioSessionControl2)
        pid = c2.GetProcessId()
        if not pid:
            continue
        try:
            exe = psutil.Process(pid).name()
        except Exception:
            continue
        out.append((exe, c2.QueryInterface(ISimpleAudioVolume)))
    return out


def _nice(exe):
    return NICE.get(exe.lower()) or os.path.splitext(exe)[0]


def snapshot():
    """Текущее состояние для клиента."""
    if not available():
        return {"available": False}
    out = {"available": True, "master": None, "micMuted": None, "sessions": [], "devices": []}
    try:
        out["master"] = round(_speakers().GetMasterVolumeLevelScalar() * 100)
    except Exception as e:
        log.debug("master: %s", e)
    try:
        out["micMuted"] = bool(_mic().GetMute())
    except Exception as e:
        log.debug("mic: %s", e)
    groups = {}
    try:
        for exe, vol in _sessions():
            if exe.lower() in SKIP:
                continue
            name = _nice(exe)
            v = round(vol.GetMasterVolume() * 100)
            g = groups.setdefault(name, {"id": name, "name": name, "vol": v})
            g["vol"] = max(g["vol"], v)
        _last_err.pop("сессии", None)
    except Exception:
        _log_once("сессии")
    out["sessions"] = sorted(groups.values(), key=lambda g: g["name"].lower())
    out["devices"] = _devices()
    return out


def _devices():
    """Устройства вывода из реестра (без COM)."""
    try:
        import winreg
        default_id = ""
        try:
            default_id = _default(E_RENDER).GetId()
        except Exception:
            pass
        base = r"SOFTWARE\Microsoft\Windows\CurrentVersion\MMDevices\Audio\Render"
        res = []
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, base) as root:
            i = 0
            while True:
                try:
                    sub = winreg.EnumKey(root, i)
                except OSError:
                    break
                i += 1
                try:
                    with winreg.OpenKey(root, sub) as k:
                        state, _ = winreg.QueryValueEx(k, "DeviceState")
                    if state != 1:
                        continue
                    with winreg.OpenKey(root, sub + r"\Properties") as p:
                        def val(name):
                            try:
                                return winreg.QueryValueEx(p, name)[0]
                            except OSError:
                                return ""
                        desc = val("{a45c254e-df1c-4efd-8020-67d146a850e0},2")
                        iface = val("{b3f8fa53-0004-438e-9003-51a46e139bfc},6")
                    did = "{0.0.0.00000000}." + sub
                    name = f"{desc} ({iface})" if desc and iface else (desc or iface or sub)
                    res.append({"id": did, "name": name,
                                "default": did.lower() == (default_id or "").lower()})
                except OSError:
                    continue
        _last_err.pop("устройства", None)
        return sorted(res, key=lambda d: d["name"].lower())
    except Exception:
        _log_once("устройства")
        return []


def set_master(v):
    _speakers().SetMasterVolumeLevelScalar(max(0.0, min(1.0, v / 100)), None)


def set_session(name, v):
    level = max(0.0, min(1.0, v / 100))
    for exe, vol in _sessions():
        if _nice(exe) == name:
            vol.SetMasterVolume(level, None)


def toggle_mic():
    m = _mic()
    new = 0 if m.GetMute() else 1
    m.SetMute(new, None)
    log.info("Микрофон: %s", i18n.tr("off", "вимк") if new else i18n.tr("on", "увімк"))
    return bool(new)


def _policy_config():
    global _policy
    if _policy is None:
        import comtypes
        from ctypes import c_int, c_void_p, c_wchar_p
        from comtypes import COMMETHOD, GUID, HRESULT, IUnknown

        class IPolicyConfig(IUnknown):
            _iid_ = GUID("{f8679f50-850a-41cf-9c72-430f290290c8}")
            _methods_ = [
                COMMETHOD([], HRESULT, "GetMixFormat", (["in"], c_wchar_p), (["in"], c_void_p)),
                COMMETHOD([], HRESULT, "GetDeviceFormat", (["in"], c_wchar_p), (["in"], c_int), (["in"], c_void_p)),
                COMMETHOD([], HRESULT, "ResetDeviceFormat", (["in"], c_wchar_p)),
                COMMETHOD([], HRESULT, "SetDeviceFormat", (["in"], c_wchar_p), (["in"], c_void_p), (["in"], c_void_p)),
                COMMETHOD([], HRESULT, "GetProcessingPeriod", (["in"], c_wchar_p), (["in"], c_int), (["in"], c_void_p), (["in"], c_void_p)),
                COMMETHOD([], HRESULT, "SetProcessingPeriod", (["in"], c_wchar_p), (["in"], c_void_p)),
                COMMETHOD([], HRESULT, "GetShareMode", (["in"], c_wchar_p), (["in"], c_void_p)),
                COMMETHOD([], HRESULT, "SetShareMode", (["in"], c_wchar_p), (["in"], c_void_p)),
                COMMETHOD([], HRESULT, "GetPropertyValue", (["in"], c_wchar_p), (["in"], c_void_p), (["in"], c_void_p)),
                COMMETHOD([], HRESULT, "SetPropertyValue", (["in"], c_wchar_p), (["in"], c_void_p), (["in"], c_void_p)),
                COMMETHOD([], HRESULT, "SetDefaultEndpoint", (["in"], c_wchar_p), (["in"], c_int)),
                COMMETHOD([], HRESULT, "SetEndpointVisibility", (["in"], c_wchar_p), (["in"], c_int)),
            ]

        clsid = GUID("{870af99c-171d-4f9e-af0d-e63df40c2bc9}")
        _policy = comtypes.CoCreateInstance(clsid, IPolicyConfig, comtypes.CLSCTX_ALL)
    return _policy


def set_default_output(device_id):
    pc = _policy_config()
    for role in (0, 1, 2):  # console, multimedia, communications
        pc.SetDefaultEndpoint(device_id, role)
    log.info("Устройство вывода по умолчанию: %s", device_id)


# ---------- что играет на ПК: захват выхода (WASAPI loopback) для визуализатора ----------

AUDCLNT_STREAMFLAGS_LOOPBACK = 0x00020000
AUDCLNT_BUFFERFLAGS_SILENT = 0x2
WAVE_FORMAT_PCM, WAVE_FORMAT_IEEE_FLOAT, WAVE_FORMAT_EXTENSIBLE = 1, 3, 0xFFFE
_ICAP = None


def _capture_iface():
    """IAudioCaptureClient — в pycaw его нет."""
    global _ICAP
    if _ICAP is None:
        from ctypes import HRESULT, POINTER, c_ubyte, c_uint32, c_uint64
        from ctypes.wintypes import DWORD

        from comtypes import COMMETHOD, GUID, IUnknown

        class IAudioCaptureClient(IUnknown):
            _iid_ = GUID("{C8ADBD64-E71E-48A0-A4DE-185C395CD317}")
            _methods_ = (
                COMMETHOD([], HRESULT, "GetBuffer", (["out"], POINTER(POINTER(c_ubyte)), "ppData"),
                          (["out"], POINTER(c_uint32), "pNumFramesToRead"), (["out"], POINTER(DWORD), "pdwFlags"),
                          (["out"], POINTER(c_uint64), "pu64DevicePosition"),
                          (["out"], POINTER(c_uint64), "pu64QPCPosition")),
                COMMETHOD([], HRESULT, "ReleaseBuffer", (["in"], c_uint32, "NumFramesRead")),
                COMMETHOD([], HRESULT, "GetNextPacketSize", (["out"], POINTER(c_uint32), "pNumFramesInNextPacket")),
            )
        _ICAP = IAudioCaptureClient
    return _ICAP


class Loopback:
    """Что играет на ПК — для визуализатора: захват устройства вывода по умолчанию (WASAPI loopback), моно float32
    → callback(block, rate) примерно каждые 10 мс. Не через comthread: опрос каждые 10 мс занял бы общий COM-поток
    микшера. У захвата свой поток со своим COM (MTA); все его COM-объекты — локальные и освобождаются в нём же.
    Сменили устройство вывода — переподключается к новому; устройства нет — пробует снова раз в 3 с.
    flow=E_CAPTURE — то же с микрофона по умолчанию (для проверки под Wine: там loopback не сделан)."""

    def __init__(self, callback, flow=E_RENDER):
        self.callback, self.flow = callback, flow
        self.error = None
        self._stop = threading.Event()
        self.thread = threading.Thread(target=self._run, name="loopback", daemon=True)
        self.thread.start()

    def close(self):
        self._stop.set()
        self.thread.join(2.0)

    def _run(self):
        import comtypes          # если это первый импорт comtypes — он сам включил COM в этом потоке (STA)
        try:
            comtypes.CoInitializeEx(comtypes.COINIT_MULTITHREADED)
            own = True
        except OSError:          # RPC_E_CHANGED_MODE: COM в потоке уже включён — им и пользуемся
            own = False
        try:
            while not self._stop.is_set():
                try:
                    self._capture()
                    self.error = None
                except Exception as e:
                    self.error = f"{type(e).__name__}: {e}"       # строкой: исключение держало бы COM-объекты кадра
                    _log_once("Визуализатор: нет захвата звука")
                    self._stop.wait(3)
        finally:
            if own:
                comtypes.CoUninitialize()

    def _capture(self):
        import ctypes

        import numpy as np
        from comtypes import CLSCTX_ALL
        from pycaw.api.audioclient import IAudioClient
        icap = _capture_iface()
        enum = _new_enumerator()
        role = ROLE_MULTIMEDIA if self.flow == E_RENDER else ROLE_CONSOLE
        dev = enum.GetDefaultAudioEndpoint(self.flow, role)
        dev_id = dev.GetId()
        client = dev.Activate(IAudioClient._iid_, CLSCTX_ALL, None).QueryInterface(IAudioClient)
        fmt = client.GetMixFormat()
        try:
            wf = fmt.contents
            ch, rate, bits, align, tag = wf.nChannels, wf.nSamplesPerSec, wf.wBitsPerSample, wf.nBlockAlign, wf.wFormatTag
            if tag == WAVE_FORMAT_EXTENSIBLE:        # WAVEFORMATEXTENSIBLE: код формата — в начале SubFormat (смещение 24)
                tag = int.from_bytes(ctypes.string_at(ctypes.addressof(wf) + 24, 2), "little")
            flags = AUDCLNT_STREAMFLAGS_LOOPBACK if self.flow == E_RENDER else 0
            client.Initialize(0, flags, 2_000_000, 0, fmt, None)    # общий режим, буфер 200 мс
        finally:
            ctypes.windll.ole32.CoTaskMemFree(fmt)
        kinds = {(WAVE_FORMAT_IEEE_FLOAT, 32): (np.float32, 1.0), (WAVE_FORMAT_PCM, 16): (np.int16, 1 / 32768),
                 (WAVE_FORMAT_PCM, 32): (np.int32, 1 / 2147483648)}
        if (tag, bits) not in kinds:
            raise OSError(f"format {tag}/{bits} bit")
        dtype, scale = kinds[(tag, bits)]
        cap = client.GetService(icap._iid_).QueryInterface(icap)
        client.Start()
        try:
            check = time.monotonic() + 2
            while not self._stop.is_set():
                n = cap.GetNextPacketSize()
                if not n:
                    if time.monotonic() > check:          # сменили устройство вывода — заново на новом
                        check = time.monotonic() + 2
                        if enum.GetDefaultAudioEndpoint(self.flow, role).GetId() != dev_id:
                            return
                    self._stop.wait(0.01)
                    continue
                data, frames, flags, _, _ = cap.GetBuffer()
                try:
                    if flags & AUDCLNT_BUFFERFLAGS_SILENT or not data or not frames:
                        block = np.zeros(frames, np.float32)
                    else:
                        raw = np.frombuffer(ctypes.string_at(data, frames * align), dtype).reshape(frames, ch)
                        block = (raw.mean(axis=1) * scale).astype(np.float32)
                finally:
                    cap.ReleaseBuffer(frames)
                self.callback(block, rate)
        finally:
            client.Stop()
