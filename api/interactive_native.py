"""Read-only active-component state adapter for the verified Proteus 8.16 build.

The caller serializes access with its owned Session and pauses for a stable
snapshot. This module never writes memory, dispatches input, or exports a netlist.
"""
import ctypes as c
from ctypes import wintypes as w
import hashlib
import math
from pathlib import Path
import re
import struct

import proteus_native as native


_HASHES = {
    "ISIS.DLL": "2eaea6c06fed74f26bc0c2712ddf3205fffd1c909d0c4f9cc315771ed5aa5dfb",
    "NETLIST.DLL": "4cca0381d79bdd4651a3e15829781defd67a0369151d557dd47953b3de63e23a",
    "PRIMS.DLL": "07cac240103988a0767167063b504fdd8d5a2c74345bfd1ec7bd8c30eec45255",
}


def snapshot(session, *, timer=False):
    """Return detached active-component values from this Session's process.

    Raises RuntimeError for an unsupported DLL build, an invalid native object,
    an unavailable project window, or a process/project change during the read.
    Known non-actuator classes are skipped; unknown native classes are rejected.
    ``timer=True`` returns native run state, time, and timer remainder instead.
    """
    pid = session.pid
    if not isinstance(pid, int) or pid <= 0 or session.process.pid != pid or session.process.poll() is not None:
        raise RuntimeError("Interactive state requires a live owned Proteus Session")
    window = session._ready()
    if not window:
        raise RuntimeError("The Session's current project window is not ready")
    owner = w.DWORD()
    native.u.GetWindowThreadProcessId(window["hwnd"], c.byref(owner))
    if owner.value != pid or not window["title"].startswith(session.project.stem + " - "):
        raise RuntimeError("Interactive state project-window ownership mismatch")
    executable = Path(session.process.args[0]).resolve(strict=True)
    if executable.name.upper() != "PDS.EXE":
        raise RuntimeError("Interactive state requires PDS.EXE")
    hashes = dict(_HASHES)
    if timer:
        hashes["VSMDEBUG.DLL"] = "b75f01b89b7acb2a3c0c1a3c92244e86b67d242a851ca3a158cc87d34c0322fa"

    k, p = native.k, c.WinDLL("psapi", use_last_error=True)
    k.ReadProcessMemory.argtypes = (w.HANDLE, c.c_void_p, c.c_void_p, c.c_size_t, c.POINTER(c.c_size_t))
    k.ReadProcessMemory.restype = w.BOOL
    p.EnumProcessModulesEx.argtypes = (w.HANDLE, c.POINTER(c.c_void_p), w.DWORD, c.POINTER(w.DWORD), w.DWORD)
    p.EnumProcessModulesEx.restype = w.BOOL
    p.GetModuleFileNameExW.argtypes = (w.HANDLE, c.c_void_p, w.LPWSTR, w.DWORD)
    p.GetModuleFileNameExW.restype = w.DWORD
    handle = k.OpenProcess(0x410, False, pid)  # QUERY_INFORMATION | VM_READ only.
    if not handle:
        raise c.WinError(c.get_last_error())

    def read(address, size):
        if not 0x10000 <= address <= 0xFFFFFFFF or not 0 < size <= 4 * 100000 or address + size > 0x100000000:
            raise RuntimeError("Invalid bounded Proteus object read")
        result, got = c.create_string_buffer(size), c.c_size_t()
        if not k.ReadProcessMemory(handle, address, result, size, c.byref(got)) or got.value != size:
            raise RuntimeError("Proteus interactive object could not be read")
        return result.raw

    def u32(address):
        return struct.unpack("<I", read(address, 4))[0]

    def text(address, limit=65536):
        result = bytearray()
        while len(result) < limit:
            current = address + len(result)
            block = read(current, min(64, limit - len(result), 4096 - current % 4096))
            stop = block.find(0)
            if stop >= 0:
                return bytes(result + block[:stop]).decode("mbcs", errors="strict")
            result.extend(block)
        raise RuntimeError("Proteus interactive string exceeded its bound")

    try:
        image, length = c.create_unicode_buffer(32768), w.DWORD(32768)
        if not k.QueryFullProcessImageNameW(handle, 0, image, c.byref(length)):
            raise c.WinError(c.get_last_error())
        if Path(image.value).resolve(strict=True) != executable:
            raise RuntimeError("Session PID does not belong to its PDS.EXE executable")
        array, needed = (c.c_void_p * 2048)(), w.DWORD()
        if not p.EnumProcessModulesEx(handle, array, c.sizeof(array), c.byref(needed), 3):
            raise c.WinError(c.get_last_error())
        if needed.value > c.sizeof(array) or needed.value % c.sizeof(c.c_void_p):
            raise RuntimeError("Proteus module list exceeded its bound")
        modules = {}
        for base in array[:needed.value // c.sizeof(c.c_void_p)]:
            path = c.create_unicode_buffer(32768)
            size = p.GetModuleFileNameExW(handle, base, path, len(path))
            if not size or size >= len(path):
                raise RuntimeError("Proteus module path could not be read")
            name = Path(path.value).name.upper()
            if name not in hashes:
                continue
            file = Path(path.value).resolve(strict=True)
            if name in modules or file.parent != executable.parent:
                raise RuntimeError("Proteus interactive module path mismatch")
            if not 64 <= file.stat().st_size <= 16 * 1024 * 1024:
                raise RuntimeError("Proteus interactive module size is unsupported")
            data = file.read_bytes()
            if hashlib.sha256(data).hexdigest() != hashes[name]:
                raise RuntimeError(f"Unsupported {name} build for interactive state")
            pe = struct.unpack_from("<I", data, 60)[0]
            header = bytearray(data[pe:pe + 248])
            # This loader updates OptionalHeader.ImageBase after ASLR.
            struct.pack_into("<I", header, 24 + 28, base)
            if read(base, 64) != data[:64] or read(base + pe, 248) != header:
                raise RuntimeError(f"Loaded {name} headers differ from the verified file")
            modules[name] = (base, struct.unpack_from("<I", data, pe + 24 + 56)[0])
        if set(modules) != set(hashes):
            raise RuntimeError("Proteus interactive modules are not loaded")
        isis, net, prims = (modules[name][0] for name in ("ISIS.DLL", "NETLIST.DLL", "PRIMS.DLL"))
        if timer:
            views = [row for row in window["children"] if row["control_id"] in (100, 101)
                     and row["cls"] == "LX_APPLN_DClkNoDC" and row["title"] == "Schematic Capture"]
            if len(views) != 1:
                raise RuntimeError("Expected one native Schematic Capture view")
            hwnd = views[0]["hwnd"]
            native.u.IsChild.argtypes, native.u.IsChild.restype = (w.HWND, w.HWND), w.BOOL
            native.u.GetWindowLongW.argtypes, native.u.GetWindowLongW.restype = (w.HWND, c.c_int), w.LONG
            native.u.GetWindowThreadProcessId(hwnd, c.byref(owner))
            if owner.value != pid or not native.u.IsChild(window["hwnd"], hwnd):
                raise RuntimeError("Native simulation view ownership mismatch")
            view = native.u.GetWindowLongW(hwnd, 0) & 0xFFFFFFFF
            if u32(view) != isis + 0xA0D34 or u32(view + 4) != hwnd:
                raise RuntimeError("Native simulation view object mismatch")
            # ISIS constructor RVA 0x4bd51: APPLNVIEWWIN lives at editor+0x5c8.
            editor = view - 0x5C8
            if u32(editor) != isis + 0xA0B80:
                raise RuntimeError("Native schematic editor vtable mismatch")
            interface = u32(editor + 0x1BCC)
            debug = modules["VSMDEBUG.DLL"][0]
            # ISIMCTRL is embedded at VSMDEBUG module+0x5d8; validate both bases.
            if (u32(interface) != debug + 0x3EBA8
                    or u32(interface - 0x5D8) != debug + 0x3EAF0
                    or u32(interface - 0x5D8 + 0x180C) != view):
                raise RuntimeError("Native simulation interface ownership mismatch")
            # Getters at VSMDEBUG RVAs 0x60f0/0x6110/0x6160 use these fields.
            native_state, engine = u32(interface + 0x1254), u32(interface + 0x998)
            remaining, current = struct.unpack("<dd", read(interface + 0x1278, 16))
            if native_state not in range(5) or not math.isfinite(current) or current < 0 or not math.isfinite(remaining):
                raise RuntimeError("Native simulation timer fields are invalid")
            active = bool(engine) and native_state != 4
            state = ("stopped" if not active else "running" if native_state == 0
                     else "paused" if native_state == 3 else "transition")
            ready = session._ready()
            if (session.process.poll() is not None or not ready or ready["hwnd"] != window["hwnd"]
                    or u32(editor + 0x1BCC) != interface
                    or native.u.GetWindowLongW(hwnd, 0) & 0xFFFFFFFF != view):
                raise RuntimeError("Proteus process or view changed during timer read")
            return dict(native_state=native_state, state=state, running=state == "running",
                        active=active, current_seconds=current, remaining_seconds=remaining)
        # NETLIST.getcdb (RVA 0x4250) loads global 0x492f0. getelement indexes
        # CDBCORE+0x290 by EID-1, bounded by +0x28c; these RVAs are hash-gated.
        if read(net + 0x4250, 6) != b"\xA1" + struct.pack("<I", net + 0x492F0) + b"\xC3":
            raise RuntimeError("Loaded NETLIST.getcdb implementation does not match")
        cdb = u32(net + 0x492F0)
        if not net <= u32(cdb) < net + modules["NETLIST.DLL"][1]:
            raise RuntimeError("Proteus component database vtable mismatch")
        roots = read(cdb + 0x28C, 8)
        count, elements = struct.unpack("<II", roots)
        if count > 100000:
            raise RuntimeError("Proteus component count exceeded its bound")
        entries = struct.unpack(f"<{count}I", read(elements, count * 4)) if count else ()
        components, skipped = [], []
        for eid, element in enumerate(entries, 1):
            if not element:
                continue
            record = read(element, 0xA0)
            if struct.unpack_from("<I", record)[0] != eid:
                raise RuntimeError("Proteus element EID does not match its array slot")
            name_bytes = record[0x10:0x44]
            if b"\0" not in name_bytes:
                raise RuntimeError("Proteus element reference is not terminated")
            reference = name_bytes.split(b"\0", 1)[0].decode("mbcs", errors="strict")
            interface = struct.unpack_from("<I", record, 0x9C)[0]
            # ISIS constructor 0x3e35 installs ICDB at COMPONENT+0x38.
            interface_type = u32(interface) if interface else None
            # Ordinary COMPONENT (RTTI verified) has no INC/DEC/KEY handler.
            if interface_type is None or interface_type == isis + 0xA202C:
                skipped.append(dict(eid=eid, reference=reference, reason="no active-component interface"))
                continue
            if interface_type != isis + 0x9AC90:
                raise RuntimeError(f"Unsupported native component class: {reference}")
            component = interface - 0x38
            data = read(component, 0x37C)
            value = lambda offset: struct.unpack_from("<I", data, offset)[0]
            if value(0) != isis + 0x9AB28 or value(0x38) != isis + 0x9AC90:
                raise RuntimeError("Proteus active-component vtable mismatch")
            if text(component + 0x44, 32) != reference:
                raise RuntimeError("Proteus element and component references disagree")
            # PRIMS export ??_7TEXTBLOCK@@6BPROPTEXT@@@ has RVA 0x11b00.
            if value(0x230) != prims + 0x11B00:
                raise RuntimeError("Proteus component property-text vtable mismatch")
            properties = text(value(0x234)) if value(0x234) else ""
            states = re.findall(r"(?mi)^\s*\{?STATE\s*=\s*([+-]?\d+)\s*\}?\s*$", properties)
            if len(states) > 1:
                raise RuntimeError("Proteus component has ambiguous STATE properties")
            symbol = text(value(0x26C) + 4, 80) if value(0x26C) else None
            if symbol and not re.fullmatch(r"[ -~]{1,79}", symbol):
                raise RuntimeError("Proteus active symbol name is invalid")
            state_count = struct.unpack_from("<i", data, 0x318)[0]
            if not 0 <= state_count <= 1000000:
                raise RuntimeError("Proteus active state count exceeded its bound")
            # ISIS 0x7330: cached hidden INC/DEC/KEY properties and refresh flag.
            inc, dec, key = struct.unpack_from("<HHH", data, 0x308)
            if any(command and command & 0xFFF != 3 for command in (inc, dec, key)):
                raise RuntimeError("Proteus actuator binding cache is invalid")
            components.append(dict(
                eid=eid, reference=reference, active_symbol=symbol,
                state=int(states[0]) if states else None, state_count=state_count,
                active_kind="external" if value(0x2D0) else "builtin" if state_count else "inactive",
                inc_command=inc, dec_command=dec, key_command=key,
                bindings_need_refresh=bool(value(0x2C0)),
                markers=dict(inc=bool(value(0x2D8)), dec=bool(value(0x2DC)), toggle=bool(value(0x2E0))),
                properties=properties,
            ))
        if session.process.poll() is not None or u32(net + 0x492F0) != cdb or read(cdb + 0x28C, 8) != roots:
            raise RuntimeError("Proteus process or project changed during interactive-state read")
        current = session._ready()
        if not current or current["hwnd"] != window["hwnd"]:
            raise RuntimeError("Proteus project window changed during interactive-state read")
        return dict(components=components, skipped=skipped)
    finally:
        k.CloseHandle(handle)
