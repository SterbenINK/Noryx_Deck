"""Чтение ярлыков Windows (.lnk) на чистом Python, без COM (формат MS-SHLLINK)."""
import os
import struct

HAS_IDLIST, HAS_LINKINFO, HAS_NAME, HAS_RELPATH = 0x1, 0x2, 0x4, 0x8
HAS_WORKDIR, HAS_ARGS, HAS_ICON, IS_UNICODE = 0x10, 0x20, 0x40, 0x80
ENV_BLOCK = 0xA0000001


def _cstr(buf, off, wide=False):
    if wide:
        end = off
        while end + 1 < len(buf) and buf[end:end + 2] != b"\0\0":
            end += 2
        return buf[off:end].decode("utf-16-le", "replace")
    end = buf.find(b"\0", off)
    return buf[off:end if end >= 0 else None].decode("mbcs" if os.name == "nt" else "latin-1", "replace")


def _idlist_path(b, pos, end):
    """Грубый разбор LinkTargetIDList: диск + имена папок/файла."""
    parts = []
    while pos + 2 <= end:
        n = struct.unpack_from("<H", b, pos)[0]
        if n == 0:
            break
        item = b[pos:pos + n]
        pos += n
        if len(item) < 4:
            continue
        t = item[2]
        if t == 0x2F:  # том, например "C:\\"
            parts = [_cstr(item, 3).rstrip("\\") + "\\"]
        elif t & 0x70 == 0x30 and len(item) > 14:  # файл/папка
            name = _cstr(item, 14)
            sig = item.find(b"\x04\x00\xef\xbe")
            if sig >= 4:
                ext = sig - 4
                ver = struct.unpack_from("<H", item, ext + 2)[0]
                off = {3: 18, 7: 26, 8: 30}.get(ver, 46)
                if ext + off < len(item):
                    long = _cstr(item, ext + off, True)
                    if long:
                        name = long
            parts.append(name)
    if not parts:
        return ""
    return parts[0] + "\\".join(parts[1:]) if parts[0].endswith("\\") else "\\".join(parts)


def read_lnk(path):
    """→ (target, arguments, icon_location 'file,index')"""
    with open(path, "rb") as f:
        b = f.read()
    if len(b) < 0x4C or struct.unpack_from("<I", b, 0)[0] != 0x4C:
        raise ValueError("не ярлык")
    flags = struct.unpack_from("<I", b, 0x14)[0]
    icon_index = struct.unpack_from("<i", b, 0x38)[0]
    pos = 0x4C
    id_target = ""
    if flags & HAS_IDLIST:
        n = struct.unpack_from("<H", b, pos)[0]
        try:
            id_target = _idlist_path(b, pos + 2, pos + 2 + n)
        except Exception:
            id_target = ""
        pos += 2 + n
    target = ""
    if flags & HAS_LINKINFO:
        li = pos
        size, hdr, liflags, _vol, lbp, _cnr, cps = struct.unpack_from("<7I", b, li)
        if liflags & 1:
            if hdr >= 0x24:
                lbpu, cpsu = struct.unpack_from("<2I", b, li + 28)
                target = _cstr(b, li + lbpu, True) + _cstr(b, li + cpsu, True)
            else:
                target = _cstr(b, li + lbp) + _cstr(b, li + cps)
        pos += size
    wide = bool(flags & IS_UNICODE)
    strings = {}
    for flag, key in ((HAS_NAME, "name"), (HAS_RELPATH, "rel"), (HAS_WORKDIR, "wd"),
                      (HAS_ARGS, "args"), (HAS_ICON, "icon")):
        if flags & flag:
            n = struct.unpack_from("<H", b, pos)[0]
            pos += 2
            ln = n * (2 if wide else 1)
            raw = b[pos:pos + ln]
            strings[key] = raw.decode("utf-16-le" if wide else "latin-1", "replace")
            pos += ln
    # ExtraData: блок переменных окружения (у части ярлыков цель лежит только там)
    while pos + 8 <= len(b):
        bsize = struct.unpack_from("<I", b, pos)[0]
        if bsize < 8:
            break
        sig = struct.unpack_from("<I", b, pos + 4)[0]
        if sig == ENV_BLOCK and not target:
            target = _cstr(b, pos + 8 + 260, True) or _cstr(b, pos + 8)
        pos += bsize
    target = os.path.expandvars(target or id_target)
    icon = os.path.expandvars(strings.get("icon", ""))
    icon_loc = f"{icon},{icon_index}" if icon else ""
    return target, strings.get("args", ""), icon_loc
