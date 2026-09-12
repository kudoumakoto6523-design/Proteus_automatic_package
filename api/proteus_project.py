"""Conservative, offline Proteus project edits. Python standard library only.

Supported: one-sheet CDB v7 projects whose components use four DSN text
records. Edits preserve byte lengths and all unrecognised bytes. This is an
experimental file adapter, not a complete description of the Proteus format.
"""
import argparse
import json
import re
import struct
import zipfile
from pathlib import Path


class UnsupportedFormat(ValueError):
    pass


def _need(condition, message):
    if not condition:
        raise UnsupportedFormat(message)


def _lp(data, offset):
    _need(0 <= offset < len(data), "String outside member")
    size = data[offset]
    # ponytail: short ASCII strings only; decode extended string lengths when needed.
    _need(size < 255 and offset + 1 + size <= len(data), "Unsupported string length")
    start = offset + 1
    raw = bytes(data[start:start + size])
    _need(all(x in (9, 10, 13) or 32 <= x < 127 for x in raw), "Non-ASCII field")
    return raw.decode("ascii"), (start, start + size), start + size


def _cdb_entity_table_offset(data):
    """Locate the entity count after a v7 ROOT sheet's variable-length title."""
    _need(bytes(data[:21]) == struct.pack("<4I", 7, 1, 1, 0) + b"\x04ROOT",
          "Requires the verified v7 ROOT sheet header")
    # The installed Rescap template has an empty title; native STM32 projects
    # also use this layout with a nonempty title such as 'Master Sheet'.
    _, _, title_end = _lp(data, 50)
    offset = title_end + 29
    _need(offset + 4 <= len(data), "Truncated CDB sheet header")
    return offset


def _cstring(data, offset):
    end = data.find(b"\0", offset, min(len(data), offset + 256))
    _need(end >= offset, "Unterminated style/font")
    raw = bytes(data[offset:end])
    _need(all(32 <= x < 127 for x in raw), "Invalid style/font")
    return raw.decode("ascii"), end + 1


def _text(data, offset, style):
    _need(offset < len(data) and data[offset] == 255, "Missing text marker")
    value, span, pos = _lp(data, offset + 1)
    _need(pos + 34 <= len(data), "Truncated text attributes")
    _need(data[pos + 13] in (0, 0x10) and data[pos + 31] == 0x20
          and data[pos + 32] == 0xFF, "Unknown text attribute layout")
    role = struct.unpack_from("<I", data, pos + 14)[0]
    _need(role == {"COMPONENT ID": 50, "COMPONENT VALUE": 53, "SUBCKT NAME": 49, "PROPERTIES": 48}[style],
          "Text role/style mismatch")
    font, end = _cstring(data, pos + 34)
    actual_style, end = _cstring(data, end)
    _need(font and actual_style == style, "Unexpected text style")
    _need(bytes(data[end:end + 4]) == b"\0" * 4, "Unknown text record suffix")
    return {"text": value, "span": span, "xy_offset": pos}, end + 4


def _dsn_component(data, offset):
    fields = []
    end = offset
    for index, style in enumerate(("COMPONENT ID", "COMPONENT VALUE", "COMPONENT VALUE", "PROPERTIES")):
        try:
            field, end = _text(data, end, style)
        except UnsupportedFormat:
            _need(index == 2, "Unsupported component text layout")
            field, end = _text(data, end, "SUBCKT NAME")
        fields.append(field)
    _need(end + 6 <= len(data), "Truncated component tail")
    length = struct.unpack_from("<I", data, end)[0]
    end += 4
    _need(length <= 65535, "Oversized component properties")
    if length != len(fields[3]["text"]):
        _need(end + length + 2 <= len(data), "Truncated component properties")
        fields[3]["display_text"] = fields[3]["text"]
        try:
            fields[3]["text"] = bytes(data[end:end + length]).decode("ascii")
        except UnicodeDecodeError as exc:
            raise UnsupportedFormat("Non-ASCII component properties") from exc
        end += length
    pin_count = struct.unpack_from("<H", data, end)[0]
    _need(0 < pin_count <= 256, "Unknown component tail")
    symbol, _, pos = _lp(data, end + 2)
    _need(symbol and pos + 33 <= len(data), "Truncated component instance")
    # The trailing 32-bit values point into connected wire records in Rescap.
    # Preserve this opaque connectivity data: moving does not reconnect pins.
    instance_id = struct.unpack_from("<I", data, pos + 12)[0]
    _need(instance_id > 0, "Invalid instance id")
    return {"fields": fields, "symbol": symbol, "xy_offset": pos, "pin_count": pin_count,
            "instance_id": instance_id, "dsn_record_start": offset}


def _cdb_component(data, offset):
    _need(offset + 20 <= len(data), "Truncated CDB component")
    instance_id, sheet, a, b, c = struct.unpack_from("<5I", data, offset)
    _need(instance_id > 0 and sheet == 1 and (a, b, c) == (0, 0, 0),
          "Unsupported CDB component header")
    end = offset + 20
    fields = []
    spans = []
    for _ in range(4):
        value, span, end = _lp(data, end)
        fields.append(value)
        spans.append(span)
    _need(end + 4 <= len(data), "Truncated CDB properties")
    length = struct.unpack_from("<I", data, end)[0]
    _need(5 <= length <= 65536 and end + length <= len(data), "Invalid CDB property length")
    props = bytes(data[end + 4:end + length])
    _need(props.endswith(b"\0"), "Unterminated CDB properties")
    try:
        props = props[:-1].decode("ascii")
    except UnicodeDecodeError as exc:
        raise UnsupportedFormat("Non-ASCII CDB properties") from exc
    return {"instance_id": instance_id, "ref": fields[0], "value": fields[1],
            "device": fields[2], "package": fields[3], "properties_raw": props,
            "value_span": spans[1]}, end + length


class Project:
    def __init__(self, path):
        self.path = Path(path).resolve()
        with zipfile.ZipFile(self.path) as archive:
            self._infos = archive.infolist()
            names = archive.namelist()
            _need(len(set(names)) == len(names), "Duplicate ZIP member names")
            _need(all(i.file_size <= 64 * 1024 * 1024 for i in self._infos), "Oversized ZIP member")
            _need(sum(i.file_size for i in self._infos) <= 128 * 1024 * 1024, "Oversized project")
            self._members = {i.filename: archive.read(i) for i in self._infos}
            self._comment = archive.comment
        _need({"ROOT.DSN", "ROOT.CDB", "PROJECT.XML"} <= self._members.keys(), "Not a supported project")
        self._dsn = bytearray(self._members["ROOT.DSN"])
        self._cdb = bytearray(self._members["ROOT.CDB"])
        self._parse()

    def _parse(self):
        dsn, cdb = self._dsn, self._cdb
        _need(dsn.startswith(b"ISIS SCHEMATIC FILE\x1a"), "Unknown DSN header")
        _need(cdb[:8] == struct.pack("<II", 7, 1), "Requires CDB v7, one-sheet layout")
        candidates = []
        # A candidate is accepted only after four complete typed text records,
        # an instance tail, and an exact independently decoded CDB record agree.
        for match in re.finditer(rb"\xff[\x01-\x3f][A-Z][A-Z0-9_+?\-]*", dsn):
            try:
                item = _dsn_component(dsn, match.start())
                ref, value, device, props = (f["text"] for f in item["fields"])
                _need(re.fullmatch(r"[A-Z][A-Z0-9_+?\-]*[0-9][A-Z0-9_+?\-]*", ref), "Not a reference")
                needle = b"".join(bytes([len(s)]) + s.encode("ascii") for s in (ref, value, device))
                found = cdb.find(needle)
                _need(found >= 24 and cdb.find(needle, found + 1) < 0, "Ambiguous/missing CDB component")
                found -= 20
                other, _ = _cdb_component(cdb, found)
                _need((ref, value, device, props) == (other["ref"], other["value"],
                      other["device"], other["properties_raw"]), "DSN/CDB fields disagree")
                item["cdb_offset"] = found
                item["cdb"] = other
                candidates.append(item)
            except (UnsupportedFormat, struct.error):
                continue
        if not candidates:
            empty_tail = b"\0" * 4 + bytes.fromhex("0100000001000000000001000000") + b"\0" * 8
            if bytes(cdb[_cdb_entity_table_offset(cdb):]) == empty_tail:
                self._components = {}
                return
        _need(candidates, "No supported components found")
        table = min(i["cdb_offset"] for i in candidates)
        count = struct.unpack_from("<I", cdb, table - 4)[0]
        _need(0 < count <= 10000, "Unknown CDB component table")
        records = []
        end = table
        for _ in range(count):
            record, end = _cdb_component(cdb, end)
            records.append(record)
        _need(bytes(cdb[end:]) == b"\0" * 4, "Unexpected data after CDB component table")
        _need(len(candidates) == count, "Not all CDB components have supported DSN records")
        _need({r["instance_id"] for r in records} == {r["cdb"]["instance_id"] for r in candidates},
              "DSN/CDB instance sets disagree")
        self._components = {item["cdb"]["ref"]: item for item in candidates}
        _need(len(self._components) == count, "Duplicate component references")

    def components(self):
        result = []
        for ref, item in self._components.items():
            x, y, rotation = struct.unpack_from("<iiI", self._dsn, item["xy_offset"])
            props = {}
            for line in item["cdb"]["properties_raw"].splitlines():
                line = line.strip().strip("{}")
                if "=" in line:
                    key, value = line.split("=", 1)
                    props[key] = value
            result.append({"ref": ref, "value": item["fields"][1]["text"],
                           "device": item["fields"][2]["text"], "symbol": item["symbol"],
                           "position": {"x": x, "y": y}, "rotation_raw": rotation,
                           "instance_id": item["instance_id"], "properties": props,
                           "properties_raw": item["cdb"]["properties_raw"]})
        return result

    def set_value(self, ref, value):
        item = self._components[ref]
        raw = value.encode("ascii")
        _need(raw and all(32 <= x < 127 for x in raw), "Value must be printable ASCII")
        spans = (item["fields"][1]["span"], item["cdb"]["value_span"])
        _need(all(len(raw) == end - start for start, end in spans),
              "Value must retain its encoded byte length; use native ADI for length changes")
        for data, (start, end) in zip((self._dsn, self._cdb), spans):
            data[start:end] = raw
        self._parse()
        return self

    def move(self, ref, dx, dy):
        """Translate symbol + its four text anchors in raw DSN units; wires stay put."""
        _need(type(dx) is int and type(dy) is int, "Offsets must be integers")
        item = self._components[ref]
        offsets = [item["xy_offset"]] + [f["xy_offset"] for f in item["fields"]]
        changes = []
        for offset in offsets:
            x, y = struct.unpack_from("<ii", self._dsn, offset)
            _need(-2**31 <= x + dx < 2**31 and -2**31 <= y + dy < 2**31, "Coordinate overflow")
            changes.append((offset, struct.pack("<ii", x + dx, y + dy)))
        for offset, value in changes:
            self._dsn[offset:offset + 8] = value
        self._parse()
        return self

    def save(self, path):
        """Write a new project. Never replace the source or an existing destination."""
        target = Path(path).resolve()
        _need(target != self.path, "Save must use a new project path")
        target.parent.mkdir(parents=True, exist_ok=True)
        members = dict(self._members, **{"ROOT.DSN": bytes(self._dsn), "ROOT.CDB": bytes(self._cdb)})
        with target.open("xb") as stream, zipfile.ZipFile(stream, "w") as archive:
            archive.comment = self._comment
            for info in self._infos:
                archive.writestr(info, members[info.filename])
        return target


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("project")
    args = parser.parse_args()
    print(json.dumps(Project(args.project).components(), ensure_ascii=False, indent=2))
