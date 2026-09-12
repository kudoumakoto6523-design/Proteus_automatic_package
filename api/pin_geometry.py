"""Read embedded Proteus symbol pin tips, without guessing device-specific coordinates.

Supports the observed single-unit device definitions and orthogonal transforms.
Pin names are logical names; displayed numbers are not package pin mappings.
"""
import struct

from proteus_project import UnsupportedFormat, _need


def _u32(data, pos):
    _need(0 <= pos <= len(data) - 4, "Truncated geometry field")
    return struct.unpack_from("<I", data, pos)[0]


def _string(data, pos, end):
    stop = data.find(b"\0", pos, min(end, pos + 256))
    _need(stop >= pos, "Unterminated geometry name")
    raw = bytes(data[pos:stop])
    _need(all(32 <= n < 127 for n in raw), "Non-ASCII geometry name")
    return raw.decode("ascii"), stop + 1


def _definitions(dsn, name):
    raw = name.encode("ascii")
    _need(0 < len(raw) < 255, "Invalid symbol name")
    limit = dsn.find(b"ISIS CIRCUIT FILE")
    _need(limit > 0, "Missing DSN circuit section")
    needle, pos = bytes([len(raw)]) + raw, 0
    while True:
        pos = dsn.find(needle, pos, limit)
        if pos < 0:
            return
        body = pos + len(needle) + 4  # Skip the stored symbol timestamp.
        pos += 1
        if body + 4 <= limit:
            end = body + _u32(dsn, body)
            if body + 12 <= end <= limit:
                yield body, end


def transform(point, rotation_raw=0):
    """Apply native X/Y reflections followed by a signed, tenths-degree rotation."""
    _need(len(point) == 2 and all(type(v) is int for v in point), "Coordinates must be integers")
    _need(type(rotation_raw) is int and 0 <= rotation_raw <= 0x3FFFF,
          "Unsupported rotation/reflection flags")
    angle = struct.unpack("<h", struct.pack("<H", rotation_raw & 0xFFFF))[0]
    _need(angle % 900 == 0, "Only orthogonal pin rotations are supported")
    x, y = point
    if rotation_raw & 0x10000:
        x = -x
    if rotation_raw & 0x20000:
        y = -y
    return ((x, y), (-y, x), (-x, -y), (y, -x))[(angle // 900) % 4]


def _glyph_tip(dsn, name):
    candidates = []
    for body, end in _definitions(dsn, name):
        try:
            count, reserved = _u32(dsn, body + 4), _u32(dsn, body + 8)
            _need(0 < count < 256 and reserved == 1, "Unknown pin graphic list")
            pos, nodes = body + 12, []
            for _ in range(count):
                size = _u32(dsn, pos)
                _need(size >= 12 and pos + size <= end, "Invalid pin graphic record")
                if size >= 40 and dsn[pos + 8:pos + 10] == b"\x06\x02":
                    child, _ = _string(dsn, pos + 28, pos + size)
                    x, y, rotation = struct.unpack_from("<iiI", dsn, pos + 16)
                    if child == "$MKRNODE":
                        _need(rotation == 0, "Rotated node marker")
                        nodes.append((x, y))
                    if child == "$MKRORIGIN":
                        _need((x, y, rotation) == (0, 0, 0), "Nonzero pin graphic origin")
                pos += size
            _need(pos == end and len(nodes) == 1, "Pin glyph needs exactly one node")
            candidates.append(nodes[0])
        except UnsupportedFormat:
            continue
    _need(len(candidates) == 1, f"Missing or ambiguous pin glyph: {name}")
    return candidates[0]


def _device_pins(data, body, end):
    geometry_end = body + _u32(data, body + 4)
    _need(body + 20 <= geometry_end <= end - 4, "Invalid device geometry length")
    count, variants, a, b = struct.unpack_from("<4H", data, body + 8)
    _need(0 < count <= 256 and variants <= 256 and (a, b) == (1, 1),
          "Requires a single-unit device with bounded animation variants")
    pos = body + 16 + _u32(data, body + 16)
    _need(body + 24 <= pos < geometry_end, "Invalid device graphics length")
    pins = []
    for index in range(count):
        _need(pos + 16 <= geometry_end, "Truncated pin header")
        flags, x, y, rotation = struct.unpack_from("<IiiI", data, pos)
        offset, pos = pos, pos + 16
        glyph, pos = _string(data, pos, geometry_end)
        name, pos = _string(data, pos, geometry_end)
        number, pos = _string(data, pos, geometry_end)
        _need(glyph.startswith("$PIN") and name, "Invalid pin definition")
        pins.append(dict(index=index, name=name, number=number, glyph=glyph,
                         anchor=(x, y), rotation_raw=rotation, flags=flags,
                         record_offset=offset))
    for _ in range(variants):
        name, pos = _string(data, pos, geometry_end)
        size = _u32(data, pos)
        _need(name and size >= 12 and pos + size <= geometry_end, "Invalid animation graphic")
        pos += size
    _need(pos == geometry_end and pos + 4 + _u32(data, pos) == end,
          "Device pin table/property boundary mismatch")
    _need(data[end - 1] == 0, "Unterminated device properties")
    return pins


def symbol_pins(dsn, symbol):
    """Return pin names, annotations, and local electrical tip coordinates."""
    candidates = []
    for body, end in _definitions(dsn, symbol):
        try:
            candidates.append(_device_pins(dsn, body, end))
        except UnsupportedFormat:
            continue
    _need(len(candidates) == 1, f"Missing or ambiguous device definition: {symbol}")
    offsets = {}
    for pin in candidates[0]:
        glyph = pin["glyph"]
        if glyph not in offsets:
            offsets[glyph] = _glyph_tip(dsn, glyph)
        dx, dy = transform(offsets[glyph], pin["rotation_raw"])
        x, y = pin["anchor"]
        pin["tip"] = (x + dx, y + dy)
    return candidates[0]


def pin_position(dsn, symbol, pin_name, position=(0, 0), rotation_raw=0):
    """Resolve a logical pin name at an instance position; reject ambiguous names."""
    pins = [p for p in symbol_pins(dsn, symbol) if p["name"] == str(pin_name)]
    _need(len(pins) == 1, f"Missing or ambiguous logical pin: {symbol}.{pin_name}")
    x, y = transform(pins[0]["tip"], rotation_raw)
    _need(len(position) == 2 and all(type(v) is int for v in position), "Invalid instance position")
    result = x + position[0], y + position[1]
    _need(all(-2**31 <= v < 2**31 for v in result), "Pin coordinate overflow")
    return result


def library_pin_anchors(library, device):
    """Read the same pin table from an observed v400 DEVICE LIBRARY directory.

    This returns anchors, not electrical tips: external glyphs are not embedded.
    """
    _need(library.startswith(b"DEVICE LIBRARY\x1a\0") and _u32(library, 16) == 400,
          "Unsupported device library header")
    capacity, count = _u32(library, 20), struct.unpack_from("<H", library, 24)[0]
    _need(0 < count <= capacity <= 10000 and 32 + capacity * 80 <= len(library),
          "Invalid device library directory")
    entries = []
    for index in range(count):
        pos = 32 + index * 80
        name, _ = _string(library, pos, pos + 64)
        if name == device:
            body = _u32(library, pos + 64)
            _need(32 + capacity * 80 <= body < len(library) - 4,
                  "Device body overlaps directory")
            end = body + _u32(library, body)
            _need(end <= len(library), "Truncated library device")
            entries.append(_device_pins(library, body, end))
    _need(len(entries) == 1, f"Missing or ambiguous library device: {device}")
    return entries[0]


if __name__ == "__main__":
    import json
    from pathlib import Path
    import zipfile

    root = Path(r"C:\ProgramData\program\SAMPLES")
    def read(relative):
        with zipfile.ZipFile(root / relative) as archive:
            return archive.read("ROOT.DSN")

    rc = read(r"Graph Based Simulation\Rescap.pdsprj")
    assert [p["tip"] for p in symbol_pins(rc, "RESISTOR")] == [(0, 0), (1270000, 0)]
    assert pin_position(rc, "CAPACITOR", "1", (2794000, 1016000), 63736) == (2794000, 0)
    assert pin_position(rc, "CAPACITOR", "2", (2794000, 1016000), 63736) == (2794000, 1016000)
    logic = read(r"Interactive Simulation\Animated Circuits\Comb01.pdsprj")
    assert pin_position(logic, "AND", "D0", (127000, 2032000)) == (-1143000, 2286000)
    assert pin_position(logic, "AND", "D1", (127000, 2032000)) == (-1143000, 1778000)
    assert pin_position(logic, "AND", "Q", (127000, 2032000)) == (1397000, 2032000)
    timer = symbol_pins(read(r"Tutorials\555.pdsprj"), "555")
    assert len(timer) == 8 and {p["number"] for p in timer} == set("12345678")
    m3 = read(r"VSM for Cortex M3\STM32\STMCubeMX LED Blink\STMCubeMX LED Blink.pdsprj")
    m4 = read(r"VSM for Cortex M4\STM32\USART_DMA\project.pdsprj")
    assert len(symbol_pins(m3, "STM32F103R6")) == 57
    assert len(symbol_pins(m4, "STM32F401VE")) == 92
    library = Path(r"C:\ProgramData\program\LIBRARY\CM3_STM32.LIB").read_bytes()
    assert [(p["name"], p["anchor"]) for p in library_pin_anchors(library, "STM32F103R6")] == [
        (p["name"], p["anchor"]) for p in symbol_pins(m3, "STM32F103R6")]
    try:
        pin_position(logic, "AND", "MISSING")
    except UnsupportedFormat:
        pass
    else:
        raise AssertionError("Unknown pin accepted")
    print(json.dumps(dict(checked=["RESISTOR", "CAPACITOR", "AND", "555",
                                  "STM32F103R6", "STM32F401VE", "LIB directory"],
                          gui_used=False)))
