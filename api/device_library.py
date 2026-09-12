"""Read installed Proteus DEVICE LIBRARY v400 catalogues and embedded definitions."""
import argparse
import json
import re
import struct
from pathlib import Path

from pin_geometry import _device_pins
from proteus_project import UnsupportedFormat, _need


LIBRARY_ROOT = Path(r"C:\ProgramData\program\LIBRARY")


def property_sections(raw):
    """Preserve model/default/footprint sections; do not conflate package pin maps."""
    sections, current = {}, "PREAMBLE"
    for line in raw.splitlines():
        line = line.strip()
        marker = re.fullmatch(r"\{\*([^}]+)\}", line)
        if marker:
            current = marker.group(1)
        elif line.startswith("*PINOUT "):
            current = line
        elif line:
            sections.setdefault(current, []).append(line)
    return sections


def properties_dict(lines):
    result = {}
    for line in lines:
        line = line.strip().strip("{}")
        if "=" in line:
            key, value = line.split("=", 1)
            result[key.strip()] = value.strip()
    return result


def describe_definition(entry, body):
    _need(isinstance(body, (bytes, bytearray)) and len(body) >= 24, "Truncated device definition")
    geometry_end = struct.unpack_from("<I", body, 4)[0]
    _need(20 <= geometry_end <= len(body) - 4, "Invalid geometry/property boundary")
    size = struct.unpack_from("<I", body, geometry_end)[0]
    _need(geometry_end + 4 + size == len(body) and body[-1] == 0, "Invalid device property boundary")
    try:
        raw = body[geometry_end + 4:-1].decode("cp1252")
    except UnicodeDecodeError as exc:
        raise UnsupportedFormat("Unsupported device property encoding") from exc
    sections = property_sections(raw)
    defaults = properties_dict(sections.get("COMPONENT", []))
    index = properties_dict(sections.get("INDEX", []))
    result = dict(entry, description=index.get("DESC", ""), category=index.get("CAT", ""),
                  properties_raw=raw, sections=sections, defaults=defaults,
                  models={k: defaults[k] for k in ("PRIMITIVE", "MODEL", "MODFILE", "MODDLL", "ITFMOD") if k in defaults},
                  package=defaults.get("PACKAGE"), pinouts={k[8:]: v for k, v in sections.items() if k.startswith("*PINOUT ")})
    try:
        result["pins"] = _device_pins(body, 0, len(body))
        result["pin_parse_status"] = "parsed"
    except UnsupportedFormat as exc:
        result["pins"] = []
        result["pin_parse_status"] = str(exc)
    return result


class Library:
    def __init__(self, directory=LIBRARY_ROOT):
        self.directory = Path(directory).resolve()
        _need(self.directory.is_dir(), f'Missing Proteus library directory: {self.directory}')
        _need(self.directory.is_dir(), "Device library directory does not exist")
        self._entries = []
        self.skipped = []
        for path in sorted(self.directory.glob("*.LIB")):
            file_size = path.stat().st_size
            with path.open("rb") as stream:
                header = stream.read(32)
                if len(header) < 32 or not header.startswith(b"DEVICE LIBRARY\x1a\0"):
                    continue
                version, capacity = struct.unpack_from("<II", header, 16)
                count = struct.unpack_from("<H", header, 24)[0]
                if version != 400 or not 0 <= count <= capacity <= 65535:
                    self.skipped.append({"path": str(path), "version": version})
                    continue
                directory_bytes = stream.read(capacity * 80)
                _need(len(directory_bytes) == capacity * 80, "Truncated LIB directory")
                for index in range(count):
                    entry = directory_bytes[index * 80:(index + 1) * 80]
                    name = entry[:64].split(b"\0", 1)[0].decode("cp1252")
                    offset = struct.unpack_from("<I", entry, 64)[0]
                    _need(name and 32 + capacity * 80 <= offset <= file_size - 4,
                          "Device pointer outside LIB body area")
                    self._entries.append({"name": name, "library": path.stem,
                        "path": str(path), "offset": offset, "timestamp": struct.unpack_from("<I", entry, 68)[0]})

    def search(self, query="", limit=30):
        _need(isinstance(query, str) and type(limit) is int and 1 <= limit <= 1000, "Invalid search")
        tokens = query.casefold().split()
        return [dict(entry) for entry in self._entries
                if all(token in (entry["name"] + " " + entry["library"]).casefold() for token in tokens)][:limit]

    def _entry(self, name, library=None):
        _need(isinstance(name, str) and 0 < len(name) <= 63, "Invalid device name")
        _need(library is None or isinstance(library, (str, Path)), "Invalid library selector")
        matches = [entry for entry in self._entries if entry["name"].casefold() == name.casefold()
                   and (library is None or entry["library"].casefold() == Path(library).stem.casefold())]
        _need(len(matches) == 1, f"Expected one device {name}; matches: {[e['library'] for e in matches]}")
        return matches[0]

    def definition(self, name, library=None):
        """Return exact device bytes for an explicit importer, with catalogue provenance."""
        entry = self._entry(name, library)
        with Path(entry["path"]).open("rb") as stream:
            stream.seek(entry["offset"])
            header = stream.read(4)
            _need(len(header) == 4, "Truncated device length")
            size = struct.unpack("<I", header)[0]
            _need(20 <= size <= 32 * 1024 * 1024, "Invalid device size")
            body = struct.pack("<I", size) + stream.read(size - 4)
        _need(len(body) == size, "Truncated device body")
        return dict(entry), body

    def get(self, name, library=None):
        entry, body = self.definition(name, library)
        return describe_definition(entry, body)


def definition_groups(dsn):
    """Decode the two name/timestamp/length-delimited graphics and device groups."""
    root = dsn.find(b"ISIS CIRCUIT FILE\x1a\0")
    _need(root > 0, "Missing circuit section")
    for marker in (match.start() for match in re.finditer(b"\xfe\xff", dsn[:root])):
        try:
            pos, groups = marker, []
            for _ in range(2):
                _need(dsn[pos:pos + 2] == b"\xfe\xff", "Missing definition-group marker")
                pos += 2
                entries = {}
                while dsn[pos] != 0:
                    start, length = pos, dsn[pos]
                    _need(0 < length < 255, "Unsupported definition name")
                    name = dsn[pos + 1:pos + 1 + length].decode("ascii")
                    body = pos + 1 + length + 4
                    size = struct.unpack_from("<I", dsn, body)[0]
                    end = body + size
                    _need(4 <= size and end <= root - 4 and name not in entries, "Invalid definition boundary")
                    entries[name] = {"entry": bytes(dsn[start:end]), "body": bytes(dsn[body:end])}
                    pos = end
                pos += 1
                groups.append(entries)
            _need(pos == root - 4, "Definition groups do not end at the circuit pointer")
            return {"start": marker, "end": pos, "graphics": groups[0], "devices": groups[1]}
        except (UnsupportedFormat, UnicodeDecodeError, struct.error, IndexError):
            continue
    raise UnsupportedFormat("Unsupported embedded-definition directory")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("query")
    parser.add_argument("--get", action="store_true")
    parser.add_argument("--library")
    args = parser.parse_args()
    catalogue = Library()
    print(json.dumps(catalogue.get(args.query, args.library) if args.get else catalogue.search(args.query),
                     ensure_ascii=False, indent=2))
