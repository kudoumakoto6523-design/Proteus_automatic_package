"""Proteus 8.16 simulation controls and native diagnostic readout (Windows)."""
import ctypes as c
from ctypes import wintypes as w
from pathlib import Path
import math
import re
import hashlib
import struct
import zipfile

import proteus_native as native


def _seconds(clock):
    if not re.fullmatch(r"\d+:\d{2}:\d{2}(?:\.\d+)?|\d+(?:\.\d+)?s?", clock):
        raise ValueError(f"Invalid native simulation time: {clock!r}")
    if ":" in clock:
        hours, minutes, seconds = map(float, clock.split(":"))
        if minutes >= 60 or seconds >= 60:
            raise ValueError(f"Invalid native simulation time: {clock!r}")
        value = hours * 3600 + minutes * 60 + seconds
    else:
        value = float(clock.removesuffix("s"))
    if not math.isfinite(value):
        raise ValueError(f"Invalid native simulation time: {clock!r}")
    return value


def firmware_info(path):
    """Check a real firmware file; ELF reports its machine, HEX verifies checksums."""
    path = Path(path).resolve(strict=True)
    if not path.is_file() or not path.stat().st_size:
        raise ValueError("Firmware must be a nonempty file")
    suffix = path.suffix.lower()
    result = dict(path=str(path), size=path.stat().st_size, format=suffix[1:])
    if suffix == ".elf":
        with path.open("rb") as handle:
            data = handle.read(64)
        if (len(data) < 20 or data[:4] != b"\x7fELF" or data[4] not in (1, 2)
                or data[5] not in (1, 2) or data[6] != 1
                or len(data) < (52 if data[4] == 1 else 64)):
            raise ValueError("Invalid ELF header")
        result["machine"] = int.from_bytes(data[18:20], "little" if data[5] == 1 else "big")
    elif suffix == ".hex":
        ended = False
        for line in path.read_text(encoding="ascii").splitlines():
            if not line.strip():
                continue
            if ended or not line.startswith(":"):
                raise ValueError("Invalid Intel HEX record")
            record = bytes.fromhex(line[1:])
            if len(record) < 5 or len(record) != record[0] + 5 or sum(record) % 256:
                raise ValueError("Invalid Intel HEX length/checksum")
            if record[3] == 1 and record[:3] != b"\x00\x00\x00":
                raise ValueError("Invalid Intel HEX EOF record")
            ended = record[3] == 1
        if not ended:
            raise ValueError("Intel HEX is missing EOF")
    elif suffix not in (".cof", ".coff", ".cdb", ".hex", ".bin"):
        raise ValueError(f"Unsupported firmware extension: {suffix}")
    return result


def extract_firmware(project, destination, member=None):
    """Extract one selected embedded binary; never executes compiler/project scripts."""
    destination = Path(destination).resolve()
    if destination.exists():
        raise FileExistsError(destination)
    with zipfile.ZipFile(project) as archive:
        candidates = [name for name in archive.namelist()
                      if name.startswith("FIRMWARE/") and Path(name).suffix.lower()
                      in (".elf", ".hex", ".cof", ".coff", ".cdb", ".bin")]
        if member is None:
            if len(candidates) != 1:
                raise ValueError(f"Choose an exact firmware member from {candidates}")
            member = candidates[0]
        if member not in candidates or archive.namelist().count(member) != 1:
            raise ValueError("Missing or ambiguous embedded firmware member")
        if destination.suffix.lower() != Path(member).suffix.lower():
            raise ValueError("Destination must preserve the firmware extension")
        data = archive.read(member)
    destination.parent.mkdir(parents=True, exist_ok=True)
    with destination.open("xb") as handle:
        handle.write(data)
    try:
        result = firmware_info(destination)
    except Exception:
        destination.unlink()
        raise
    return dict(result, member=member)


def gpio_events(log, ref=None, port=None, pin=None):
    """Parse CM3 model GPIO drive events. These are digital states, not voltages.

    Each timestamp comes from Proteus itself. Last event time is NOT current time.
    TRACE_GPIO=3 was exercised with STM32F103R6; other models may use other formats.
    """
    result = []
    pattern = r"\[GPIO\] drive PIO(\d+)\.(\d+) = ([01]) \[([^\]]+)_SYSINTERFACE\] @(\S+)"
    for match in re.finditer(pattern, log):
        p, n, value, component, seconds = match.groups()
        event = dict(ref=component, port=int(p), pin=int(n), value=int(value), seconds=_seconds(seconds))
        if ((ref is None or ref == component) and (port is None or port == event["port"])
                and (pin is None or pin == event["pin"])):
            result.append(event)
    return result


class Simulation:
    """Owns no process: composes an existing Session and targets its PID only.

    ``log`` uses Proteus's Copy All command and replaces the Windows clipboard.
    Run control uses menu state as acknowledgement; wall time is only a timeout.
    """

    def __init__(self, session):
        self.session = session
        core = Path(session.process.args[0]).parent / "WINCORE.DLL"
        self._known_status_layout = hashlib.sha256(core.read_bytes()).hexdigest() == (
            "944013e93f0d31addbbce797d6eb3746bf06c7497c0d04cb77955fc0acd569bb")

    def _status_text(self, window):
        """Read two bounded strings from the verified 32-bit WINCORE status widget.

        This is a read-only, version-gated native object adapter, not a process scan.
        Both text pointers and the owning HWND are checked before interpreting data.
        """
        if not self._known_status_layout:
            return None
        bars = [row for row in window["children"]
                if row["control_id"] == 3 and row["cls"] == "LX_APPLN_NoDC"]
        if len(bars) != 1:
            return None
        u, k = native.u, native.k
        u.GetWindowLongW.argtypes, u.GetWindowLongW.restype = (w.HWND, c.c_int), w.LONG
        k.ReadProcessMemory.argtypes = (w.HANDLE, c.c_void_p, c.c_void_p,
                                       c.c_size_t, c.POINTER(c.c_size_t))
        process = k.OpenProcess(0x410, False, self.session.pid)
        if not process:
            raise c.WinError(c.get_last_error())
        def read(address, size):
            buffer, got = c.create_string_buffer(size), c.c_size_t()
            if not address or not k.ReadProcessMemory(process, address, buffer, size, c.byref(got)) or got.value != size:
                raise RuntimeError("Proteus status object could not be read")
            return buffer.raw
        try:
            ptr = u.GetWindowLongW(bars[0]["hwnd"], 0) & 0xffffffff
            if struct.unpack("<I", read(ptr + 4, 4))[0] != bars[0]["hwnd"]:
                raise RuntimeError("Proteus status object HWND mismatch")
            pointers = struct.unpack("<II", read(ptr + 0x250, 8))
            strings = []
            for pointer in pointers:
                if not pointer:
                    strings.append("")
                    continue
                raw = read(pointer, 256)
                if b"\0" not in raw:
                    raise RuntimeError("Unrecognized Proteus status string")
                strings.append(raw.split(b"\0", 1)[0].decode("cp1252", errors="replace"))
            return strings
        finally:
            k.CloseHandle(process)

    @staticmethod
    def _message(hwnd, message, wp=0, lp=0):
        result = c.c_size_t()
        native.u.SendMessageTimeoutW.argtypes = (w.HWND, w.UINT, w.WPARAM, w.LPARAM,
                                               w.UINT, w.UINT, c.POINTER(c.c_size_t))
        native.u.SendMessageTimeoutW.restype = w.LPARAM
        if not native.u.SendMessageTimeoutW(hwnd, message, wp, lp, 2, 3000, c.byref(result)):
            raise RuntimeError("Proteus control message timed out")
        return result.value

    def status(self):
        session = self.session
        code = session.process.poll()
        if code is not None:
            return dict(state="exited", returncode=code, simulation_seconds=None)
        window = session._ready()
        if not window:
            return dict(state="blocked", windows=native.windows(session.pid), simulation_seconds=None)
        menus = {row["path"]: row["enabled"] for row in native.menus(native.u.GetMenu(window["hwnd"]))}
        pause, stop = menus.get("Debug/Pause VSM Debugging"), menus.get("Debug/Stop VSM Debugging")
        if pause is None or stop is None:
            state = "unknown"
        else:
            state = "running" if pause else "paused" if stop else "stopped"
        result = dict(state=state, simulation_seconds=None)
        text = self._status_text(window)
        if text is not None:
            result.update(status_text=text[0], reason=text[1])
            match = re.fullmatch(r"(PAUSED|ANIMATING): ([\d.:]+s?)(?: \(CPU load \d+%\))?", text[0])
            if match:
                result.update(state="paused" if match[1] == "PAUSED" else "running",
                              simulation_seconds=_seconds(match[2]))
            elif " - " in text[0] and menus.get("Debug/Run Simulation"):
                # The idle status is "<sheet name> - <sheet title>"; initial
                # menus may temporarily retain a stale enabled Stop command.
                result["state"] = "stopped"
        return result

    def _until(self, expected, timeout=30):
        def reached():
            state = self.status()
            return state if state["state"] in expected else None
        return self.session._wait(reached, timeout=timeout)

    def _menu(self, path):
        # Proteus can leave menu enable bits stale after a timed breakpoint.
        # On the verified build callers gate commands on native simulator state;
        # other builds must wait for the standard menu's enabled state.
        def enabled():
            window = self.session._ready()
            if not window:
                return None
            items = [row for row in native.menus(native.u.GetMenu(window["hwnd"]))
                     if row["path"] == path]
            if len(items) != 1:
                raise RuntimeError(f"Missing native simulation command: {path}")
            return ((window["hwnd"], items[0]["command_id"])
                    if self._known_status_layout or items[0]["enabled"] else None)
        hwnd, command = self.session._wait(enabled)
        self._message(hwnd, 0x111, command)

    def start(self):
        if self.status()["state"] == "running":
            return self.status()
        self._menu("Debug/Run Simulation")
        return self._until({"running"})

    def pause(self):
        state = self.status()
        if state["state"] != "running":
            return state
        self._menu("Debug/Pause VSM Debugging")
        return self._until({"paused", "stopped"})

    def stop(self):
        if self.status()["state"] == "stopped":
            return self.status()
        self._menu("Debug/Stop VSM Debugging")
        return self._until({"stopped"})

    def reset(self):
        """Stop/discard the running simulation; the next start initializes it again.

        Persistent device memory is retained, matching ordinary Stop/Run in Proteus.
        """
        return self.stop()

    def _control_rows(self):
        from interactive_native import snapshot
        running = self.status()['state'] == 'running'
        if running:
            self.pause()
        try:
            return snapshot(self.session)['components']
        finally:
            if running and self.status()['state'] == 'paused':
                self.start()

    @staticmethod
    def _binary_control(row):
        from component_codec import CONTROL_DEVICES
        return (row['active_symbol'] in CONTROL_DEVICES and row['active_kind'] == 'builtin'
                and row['state_count'] == 2 and any(row['markers'].values()))

    def controls(self):
        """List supported binary actuators and their current native positions."""
        rows = self._control_rows()
        return [dict(ref=row['reference'], device=row['active_symbol'], state=row['state'],
                     kind='momentary' if row['markers']['toggle'] else 'latched',
                     bound=bool(row['inc_command'] and row['dec_command']))
                for row in rows if self._binary_control(row)]

    def _find_control(self, rows, ref):
        if not isinstance(ref, str) or not ref:
            raise ValueError('A component reference is required')
        matches = [row for row in rows if row['reference'] == ref]
        if not matches:
            raise KeyError(ref)
        if len(matches) != 1 or not self._binary_control(matches[0]):
            raise NotImplementedError(f'Unsupported binary control: {ref}')
        row = matches[0]
        if type(row['state']) is not int or row['state'] not in (0, 1):
            raise RuntimeError(f'Invalid native control state: {ref}')
        return row

    def control_state(self, ref):
        """Read the actuator's native 0/1 position without exporting a netlist.

        This is the control position, not the downstream pin voltage. Circuit
        propagation is measured separately, for example with MCU GPIO events.
        """
        return self._find_control(self._control_rows(), ref)['state']

    def _set_control(self, ref, state):
        before = self.status()
        if before['state'] not in ('running', 'paused'):
            raise RuntimeError('Start the simulation before operating a control')
        resumed = before['state'] == 'running'
        if resumed:
            self.pause()
        try:
            return self._set_paused_control(ref, state)
        finally:
            if resumed and self.status()['state'] == 'paused':
                self.start()

    def _set_paused_control(self, ref, state):
        rows = self._control_rows()
        row = self._find_control(rows, ref)
        inc, dec = row['inc_command'], row['dec_command']
        if (not inc or not dec or inc == dec or row['bindings_need_refresh']
                or any(command & 0xFFF != 3 or command >> 12 > 9 for command in (inc, dec))):
            raise RuntimeError(f'{ref}: use Circuit.bind_controls() before saving and opening the project')
        # A global key must target this component only, including KEY bindings
        # on devices that this API does not otherwise expose.
        for other in rows:
            bindings = (other['inc_command'], other['dec_command'], other['key_command'])
            if other['reference'] != ref and any(command in bindings for command in (inc, dec)):
                raise RuntimeError(f'Actuator key collision: {ref} and {other["reference"]}')
        if row['key_command'] in (inc, dec):
            raise RuntimeError(f'Actuator KEY conflicts with INC/DEC: {ref}')
        current = self.status()
        if current['state'] != 'paused' or current['simulation_seconds'] is None:
            raise RuntimeError('A verified paused simulation time is required')
        changed = row['state'] != state
        if changed:
            window = self.session._wait(self.session._ready)
            self._message(window['hwnd'], 0x111, inc if state else dec)
            self.session._wait(lambda: self.control_state(ref) == state)
        result = dict(ref=ref, state=state, changed=changed,
                      command_time_seconds=current['simulation_seconds'])
        return result

    def press(self, ref):
        """Set a prepared binary button/input to 1, preserving run/pause state."""
        return self._set_control(ref, 1)

    def release(self, ref):
        """Set a prepared binary button/input to 0, preserving run/pause state."""
        return self._set_control(ref, 0)

    def set_switch(self, ref, closed):
        """Set a prepared binary switch to closed (True) or open (False)."""
        if type(closed) is not bool:
            raise ValueError('closed must be True or False')
        return self._set_control(ref, int(closed))

    def run_for(self, seconds, timeout=60, tolerance_seconds=0.003):
        """Use the native timed breakpoint, then wait for a pause/stop.

        Completion is checked against native simulator time and a new timer reason.
        If a positive native timer remainder cannot advance its double clock,
        pause explicitly at the reached target and report that completion path.
        Proteus can round the stop to a simulation step (2.5 ms observed here).
        The returned actual time/overshoot are authoritative. Set tolerance_seconds=0 for
        a strict stop; the default accepts up to 3 ms of native timer quantization.
        """
        seconds = float(seconds)
        tolerance = float(tolerance_seconds)
        if not math.isfinite(seconds) or seconds < 1e-6:
            raise ValueError("A finite duration of at least 1 microsecond is required")
        if abs(seconds * 1e6 - round(seconds * 1e6)) > 1e-6:
            raise ValueError("Duration must be an integer number of microseconds")
        if not math.isfinite(tolerance) or tolerance < 0:
            raise ValueError("Tolerance must be finite and nonnegative")
        before = self.status()
        if not self._known_status_layout:
            raise NotImplementedError("Timed completion requires the verified Proteus 8.16 WINCORE layout")
        if before["state"] == "running":
            self.pause()
            before = self.status()
        if before["state"] not in ("stopped", "paused"):
            raise RuntimeError("Simulation must be stopped or paused")
        start = 0.0 if before["state"] == "stopped" else before["simulation_seconds"]
        if start is None:
            raise RuntimeError("Current native simulation time is unavailable")
        target = start + seconds
        self._menu("Debug/Run Simulation (timed breakpoint)")
        dialog = self.session._dialog("Execute for specified time")
        edits = [row for row in dialog["children"] if row["cls"] == "Edit" and row["control_id"] == 257]
        if len(edits) != 1:
            raise RuntimeError("Unrecognized timed-breakpoint control")
        # This numeric subclass ignores WM_SETTEXT for its bound numeric value.
        # Native character input updates that value; no keyboard focus/global input.
        self._message(edits[0]["hwnd"], 0xB1, 0, -1)  # EM_SETSEL
        typed = format(seconds, ".6f").rstrip("0").rstrip(".")
        for char in typed:
            self._message(edits[0]["hwnd"], 0x102, ord(char))  # WM_CHAR
        buffer = c.create_unicode_buffer(128)
        self._message(edits[0]["hwnd"], 0xD, len(buffer), c.addressof(buffer))
        if buffer.value != typed:
            self.session._click(dialog, "Cancel")
            raise RuntimeError(f"Native timer input mismatch: {buffer.value!r}")
        self._message(dialog["hwnd"], 0x111, 1, 0)
        def reached():
            state = self.status()
            value = state["simulation_seconds"]
            reason = state.get("reason", "")
            if value is None or value < target - 1e-9:
                return None
            if (state['state'] == 'paused' and reason.startswith('Execution timer breakpoint: ')
                    and reason != before.get('reason')):
                return dict(state, completion='timer_breakpoint')
            if state['state'] == 'running':
                from interactive_native import snapshot
                timer = snapshot(self.session, timer=True)
                actual, remaining = timer['current_seconds'], timer['remaining_seconds']
                if (timer['running'] and remaining > 0 and actual + remaining == actual
                        and target - 1e-9 <= actual <= target + tolerance + 1e-9):
                    # Native floating-point roundoff can leave the timer positive
                    # even though adding it no longer advances the engine clock.
                    paused = self.pause()
                    confirmed = snapshot(self.session, timer=True)
                    if confirmed['native_state'] != 3 or confirmed['running']:
                        raise RuntimeError('Native timer roundoff pause was not acknowledged')
                    return dict(paused, state='paused', simulation_seconds=confirmed['current_seconds'],
                                completion='timer_roundoff_pause', timer_remaining_seconds=remaining)
            return None
        state = self.session._wait(reached, timeout=timeout)
        overshoot = max(0.0, state["simulation_seconds"] - target)
        if overshoot > tolerance + 1e-9:
            raise RuntimeError(f"Native timed breakpoint overshot {target}: {state}")
        return dict(state, requested_seconds=seconds, start_seconds=start,
                    end_seconds=state["simulation_seconds"],
                    actual_elapsed_seconds=state["simulation_seconds"] - start,
                    overshoot_seconds=overshoot, tolerance_seconds=tolerance)

    def set_firmware(self, ref, path, clock=None):
        info = firmware_info(path)
        if self.status()["state"] != "stopped":
            raise RuntimeError("Stop simulation before configuring firmware")
        values = dict(PROGRAM=info["path"])
        if clock is not None:
            values["CLOCK"] = str(clock)
        actual = self.session.set_properties(ref, **values)
        return dict(firmware=info, part=actual)

    def log(self):
        """Read the native Simulation Log as text; replaces system clipboard.

        The supported window is the floating Simulation Log. A VSM Studio dock
        without a matching native window is rejected instead of reading stale data.
        """
        session, u, k = self.session, native.u, native.k
        def log_window():
            rows = [row for row in native.windows(session.pid) if row["title"] == "Simulation Log"]
            return rows[0] if len(rows) == 1 else None
        # Qt's keyboard context event uses its active debug widget. Always activate
        # this popup, even when it is already visible, before requesting its menu.
        main = session._wait(session._ready)
        entries = [row for row in native.menus(u.GetMenu(main["hwnd"]))
                   if row["path"] == "Debug/1. Simulation Log" and row["enabled"]]
        if len(entries) != 1:
            raise RuntimeError("Native Simulation Log command is unavailable")
        self._message(main["hwnd"], 0x111, entries[0]["command_id"])
        window = session._wait(log_window)
        u.GetClipboardSequenceNumber.restype = w.DWORD
        u.GetClipboardOwner.restype = w.HWND
        u.GetClipboardData.argtypes, u.GetClipboardData.restype = (w.UINT,), w.HANDLE
        u.OpenClipboard.argtypes = (w.HWND,)
        k.GlobalLock.argtypes, k.GlobalLock.restype = (w.HGLOBAL,), c.c_void_p
        k.GlobalUnlock.argtypes = (w.HGLOBAL,)
        before = u.GetClipboardSequenceNumber()
        u.PostMessageW(window["hwnd"], 0x7B, window["hwnd"], -1)
        def popup():
            rows = [row for row in native.windows(session.pid) if row["cls"] == "QPopup"]
            return rows[0] if len(rows) == 1 else None
        menu = session._wait(popup)
        # Proteus 8.16 / documented Simulation Advisor menu: Copy Selection, Copy All.
        for key in (0x24, 0x28, 0x0D):  # Home, Down, Enter, targeted to this popup only
            u.PostMessageW(menu["hwnd"], 0x100, key, 0)
            u.PostMessageW(menu["hwnd"], 0x101, key, 0)
        session._wait(lambda: u.GetClipboardSequenceNumber() != before)
        session._wait(lambda: u.OpenClipboard(None))
        try:
            owner = w.DWORD()
            u.GetWindowThreadProcessId(u.GetClipboardOwner(), c.byref(owner))
            if owner.value != session.pid or u.GetClipboardSequenceNumber() == before:
                raise RuntimeError("Clipboard changed outside this Proteus session; refusing to read it")
            handle = u.GetClipboardData(13)  # CF_UNICODETEXT
            pointer = k.GlobalLock(handle) if handle else None
            if not pointer:
                raise RuntimeError("Proteus did not provide a Unicode simulation log")
            try:
                return c.wstring_at(pointer)
            finally:
                k.GlobalUnlock(handle)
        finally:
            u.CloseClipboard()

    def gpio_events(self, **filters):
        return gpio_events(self.log(), **filters)

    def errors(self):
        """Return matching native diagnostics with raw text, not a fabricated verdict."""
        text = self.log()
        lines = [line for line in text.splitlines()
                 if re.search(r"\b(error|fatal|failed|failure|exception)\b", line, re.I)]
        return dict(messages=lines, log=text)
