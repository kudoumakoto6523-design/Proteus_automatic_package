"""Native wire, junction and terminal records; unknown sheet bytes are reported."""
import math
import re
import struct

from proteus_project import Project, UnsupportedFormat, _lp, _cstring, _need
from pin_geometry import _definitions, _u32, _string, transform

TERMINALS = {"label": ("$TERDEFAULT", 9), "input": ("$TERINPUT", 10),
             "output": ("$TEROUTPUT", 11), "bidir": ("$TERBIDIR", 12),
             "power": ("$TERPOWER", 13), "ground": ("$TERGROUND", 13)}


def _text(data, offset, style, role):
    _need(data[offset] == 255, "Missing label text marker")
    value, span, position = _lp(data, offset + 1)
    _need(struct.unpack_from("<I", data, position + 14)[0] == role, "Unexpected label text role")
    font, end = _cstring(data, position + 34)
    actual_style, end = _cstring(data, end)
    _need(font and actual_style == style and data[end:end + 4] == bytes(4), "Unexpected label text style")
    return {"text": value, "span": span, "xy_offset": position,
            "position": struct.unpack_from("<ii", data, position),
            "tail_hex": bytes(data[position:end + 4]).hex()}, end + 4


def read_wire(data, offset):
    _need(data[offset:offset + 6] == b"\0\x1d\0\0\0\0" and data[offset + 25:offset + 30] == b"WIRE\0", "Not a plain native wire")
    count = struct.unpack_from("<H", data, offset + 30)[0]
    _need(count < 100, "Too many wire labels")
    position, labels = offset + 32, []
    for _ in range(count):
        label, position = _text(data, position, "WIRE LABEL", 48)
        labels.append(label)
    count = struct.unpack_from("<H", data, position)[0]
    position += 2
    _need(2 <= count <= 10000 and position + count * 8 <= len(data), "Truncated wire point array")
    points = [struct.unpack_from("<ii", data, position + i * 8) for i in range(count)]
    return {"offset": offset, "end": position + count * 8, "points": points, "labels": labels}


def read_terminal(data, offset):
    _need(data[offset] == 0x10, "Not a terminal")
    x, y, rotation = struct.unpack_from("<iiI", data, offset + 1)
    symbol, _, position = _lp(data, offset + 13)
    matches = [kind for kind, (name, _) in TERMINALS.items() if name == symbol]
    _need(len(matches) == 1, "Unknown terminal symbol")
    kind = matches[0]
    terminal_type, flags = struct.unpack_from("<IH", data, position)
    _need(terminal_type == TERMINALS[kind][1] and flags == 0, "Unknown terminal fields")
    label, position = _text(data, position + 6, "TERMINAL LABEL", 52)
    reference = struct.unpack_from("<I", data, position)[0]
    return {"offset": offset, "end": position + 4, "kind": kind, "symbol": symbol,
            "name": label["text"], "position": (x, y), "rotation": rotation,
            "label": label, "wire": reference}


def terminal_markers(dsn, symbol):
    candidates = []
    for body, end in _definitions(dsn, symbol):
        count, reserved = struct.unpack_from("<II", dsn, body + 4)
        if reserved != 1 or count > 100:
            continue
        position, markers = body + 12, {}
        for _ in range(count):
            size = _u32(dsn, position)
            _need(size >= 12 and position + size <= end, "Invalid terminal glyph record")
            if size >= 40 and dsn[position + 8:position + 10] == b"\x06\x02":
                name, _ = _string(dsn, position + 28, position + size)
                if name in ("$MKRNODE", "$MKRLABEL"):
                    markers[name] = struct.unpack_from("<ii", dsn, position + 16)
            position += size
        if position == end and "$MKRNODE" in markers and "$MKRLABEL" in markers:
            candidates.append(markers)
    _need(len(candidates) == 1, "Missing or ambiguous terminal glyph")
    return candidates[0]


def label_record(name, position, style="TERMINAL LABEL", role=52, tail_hex=None):
    raw = name.encode("ascii")
    _need(len(raw) < 255 and all(32 <= value < 127 for value in raw), "Use a short printable ASCII net name")
    if tail_hex is not None:
        tail = bytearray.fromhex(tail_hex)
        _need(len(tail) >= 38, "Truncated stored label style")
        struct.pack_into("<ii", tail, 0, *position)
        encoded = b"\xff" + bytes([len(raw)]) + raw + tail
        _, end = _text(encoded, 0, style, role)
        _need(end == len(encoded), "Unexpected stored label suffix")
        return encoded
    # Text layout and font fields are from the installed Rescap terminal label.
    attributes = bytearray(bytes.fromhex("00 00 00 00 08 10 34 00 00 00 30 e0 03 00 c0 19 03 00 00 00 00 00 01 20 ff 01"))
    struct.pack_into("<I", attributes, 6, role)
    return (b"\xff" + bytes([len(raw)]) + raw + struct.pack("<ii", *position) + attributes
            + b"Default Font\0" + style.encode("ascii") + bytes(5))


def terminal_record(dsn, kind, name, contact, rotation=0, label=None):
    _need(kind in TERMINALS, "Unknown terminal kind")
    symbol, terminal_type = TERMINALS[kind]
    markers = terminal_markers(dsn, symbol)
    node = transform(markers["$MKRNODE"], rotation)
    anchor = tuple(contact[i] - node[i] for i in (0, 1))
    label_tip = transform(markers["$MKRLABEL"], rotation)
    label_position = tuple(anchor[i] + label_tip[i] for i in (0, 1))
    if label is not None:
        label_position = label["position"]
    raw = symbol.encode("ascii")
    record = b"\x10" + struct.pack("<iiI", *anchor, rotation) + bytes([len(raw)]) + raw
    record += struct.pack("<IH", terminal_type, 0) + label_record(name, label_position, tail_hex=label.get("tail_hex") if label else None) + bytes(4)
    direction = ((node[0] > 0) - (node[0] < 0), (node[1] > 0) - (node[1] < 0))
    _need(direction in ((1, 0), (-1, 0), (0, 1), (0, -1)), "Unsupported terminal contact direction")
    return record, direction


def encode_net_body(objects, parts, templates, connections, body_offset, dsn, terminals=()):
    """Add actual native terminal objects; pseudo pins exist only inside the encoder."""
    from wire_codec import encode_connected_body
    objects, parts, connections = list(objects), list(parts), list(connections)
    by_ref = {part["ref"]: part for part in parts}
    for index, terminal in enumerate(terminals):
        kind, name = terminal["kind"], terminal.get("name", "")
        endpoint = terminal.get("pin")
        if endpoint is not None:
            ref, pin = endpoint
            _need(ref in by_ref, "Terminal component does not exist")
            part = by_ref[ref]
            template = part.get("template") or templates[part["device"]]
            _need(pin in template["pin_offsets"], "Terminal pin does not exist")
            tip = tuple(part[axis] + template["pin_offsets"][pin][i] for i, axis in enumerate(("x", "y")))
            direction = template["pin_directions"][pin]
            contact = terminal.get("position") or tuple(tip[i] + direction[i] * 508000 for i in (0, 1))
        else:
            contact = terminal.get("position")
            _need(contact is not None, "An unconnected terminal needs an explicit contact position")
        rotation = terminal.get("rotation")
        if rotation is None and endpoint is not None:
            node = terminal_markers(dsn, TERMINALS[kind][0])["$MKRNODE"]
            rotations = [angle for angle in (0, 900, 1800, 2700)
                         if tuple((v > 0) - (v < 0) for v in transform(node, angle)) == tuple(-v for v in direction)]
            _need(rotations, "Cannot orient terminal toward pin")
            rotation = rotations[0]
        elif rotation is None:
            rotation = 0
        record, terminal_direction = terminal_record(dsn, kind, name, contact, rotation, terminal.get("label"))
        pseudo = "@TERMINAL_" + terminal.get("id", "T" + str(index + 1))
        _need(pseudo not in by_ref, "Reserved terminal reference collision")
        term_template = {"pins": [("PIN", "")], "pin_offsets": {"PIN": (0, 0)},
                         "pin_directions": {"PIN": terminal_direction}}
        objects.append(record)
        parts.append({"ref": pseudo, "device": "@TERMINAL", "x": contact[0], "y": contact[1], "template": term_template})
        if endpoint is not None:
            connections.append({"first": (ref, pin), "second": (pseudo, "PIN"), "points": terminal.get("points"),
                                "layout": terminal.get("layout"), "labels": terminal.get("labels", []),
                                "preserve_geometry": terminal.get("preserve_geometry", False)})
    return encode_connected_body(objects, parts, templates, connections, body_offset)


def power_rails_bytes(rails, bindings=()):
    """Serialize the public text PWRRAILS.DAT format observed in official samples."""
    def safe(name):
        _need(isinstance(name, str) and name and all(32 <= ord(c) < 127 and c not in "{}=,\r\n" for c in name),
              "Invalid power rail name")
        return name
    rows = ["{*RAILS}"]
    for name, value in dict(rails).items():
        voltage, flags = (value["voltage"], value.get("flags", "POWER")) if isinstance(value, dict) else (value, "POWER")
        _need(type(voltage) in (int, float) and math.isfinite(voltage), "Rail voltage must be finite")
        _need(flags in ("", "POWER"), "Unknown power rail flags")
        rows.append("{" + safe(name) + "=" + format(voltage, ".15g") + "," + flags + "}")
    rows.append("{*BINDINGS}")
    for name, rail in dict(bindings).items():
        _need(rail in dict(rails), "Binding target rail does not exist")
        rows.append("{" + safe(name) + "=" + safe(rail) + "}")
    return ("\n".join(rows) + "\n").encode("ascii")


def read_power_rails(data):
    text = data.decode("ascii")
    rails, bindings, unknown, section = {}, {}, [], None
    for raw in text.splitlines():
        line = raw.strip().strip("{}")
        if not line:
            continue
        if line in ("*RAILS", "*BINDINGS"):
            section = line
        elif section == "*RAILS" and "=" in line:
            name, value = line.split("=", 1)
            voltage, _, flags = value.partition(",")
            try:
                voltage = float(voltage)
                _need(math.isfinite(voltage), "Invalid stored rail voltage")
            except ValueError:
                unknown.append(raw)
                continue
            rails[name] = {"voltage": voltage, "flags": flags}
        elif section == "*BINDINGS" and "=" in line:
            name, rail = line.split("=", 1)
            bindings[name] = rail
        else:
            unknown.append(raw)
    return {"rails": rails, "bindings": bindings, "unknown_lines": unknown}


def decode_net_objects(project):
    project = project if isinstance(project, Project) else Project(project)
    dsn = project._dsn
    circuits = [match.start() for match in re.finditer(b"ISIS CIRCUIT FILE\x1a\0", dsn)]
    _need(len(circuits) == 2, "Requires one root sheet and one default sheet")
    start = dsn.index(b"OBJECT DATA\0", circuits[0]) + 12
    end = circuits[1] - 1
    _need(dsn[end] == 255, "Unexpected sheet terminator")
    wires, terminals, junctions, ranges = {}, [], [], []
    from component_codec import _entities
    entities, _ = _entities(project._cdb)
    pin_references = {}
    for ref, component in project._components.items():
        position = component["xy_offset"] + 25
        names = [name for name, number in entities[ref]["pins"]]
        _need(len(names) == component["pin_count"], "Entity pin count mismatch")
        for index, name in enumerate(names):
            pin_references[(ref, name)] = struct.unpack_from("<I", dsn, position + 4 * index)[0]
        ranges.append((component["dsn_record_start"] - 1, position + 4 * len(names), "component"))
    for match in re.finditer(b"WIRE\0", dsn[start:end]):
        offset = start + match.start() - 25
        try:
            wire = read_wire(dsn, offset)
            _need(start <= offset < wire["end"] <= end, "Wire outside sheet")
        except (UnsupportedFormat, struct.error, IndexError):
            continue
        wires[offset] = wire
        ranges.append((offset, wire["end"], "wire"))
    for match in re.finditer(rb"\x10.{12}[\x08-\x0f]\$TER[A-Z]+", dsn[start:end], re.DOTALL):
        offset = start + match.start()
        try:
            terminal = read_terminal(dsn, offset)
            _need(terminal["end"] <= end, "Terminal outside sheet")
        except (UnsupportedFormat, struct.error, IndexError):
            continue
        terminals.append(terminal)
        ranges.append((offset, terminal["end"], "terminal"))
    occupied = {position for left, right, _ in ranges for position in range(left, right)}
    for position in range(start, end - 24):
        if position in occupied or dsn[position] != 1:
            continue
        slots = struct.unpack_from("<4I", dsn, position + 9)
        references = [reference for reference in slots if reference]
        cursor = position + 25
        node_position = struct.unpack_from("<ii", dsn, position + 1)
        if (2 <= len(references) <= 4 and len(set(references)) == len(references)
                and all(reference in wires and node_position in (wires[reference]["points"][0], wires[reference]["points"][-1])
                        for reference in references)):
            junctions.append({"offset": position, "end": cursor,
                              "position": struct.unpack_from("<ii", dsn, position + 1), "wires": references})
            ranges.append((position, cursor, "junction"))
            occupied.update(range(position, cursor))
    unknown, cursor = [], start
    for left, right, kind in sorted(ranges):
        if left > cursor:
            unknown.append({"start": cursor, "end": left, "prefix": bytes(dsn[cursor:min(left, cursor + 16)]).hex()})
        _need(left >= cursor, "Overlapping recognized object records")
        cursor = right
    if cursor < end:
        unknown.append({"start": cursor, "end": end, "prefix": bytes(dsn[cursor:min(end, cursor + 16)]).hex()})
    missing = sorted({reference for reference in pin_references.values() if reference and reference not in wires}
                     | {terminal["wire"] for terminal in terminals if terminal["wire"] and terminal["wire"] not in wires})
    parents = {offset: offset for offset in wires}
    def find(offset):
        while parents[offset] != offset:
            offset = parents[offset]
        return offset
    def merge(references):
        for reference in references[1:]:
            parents[find(reference)] = find(references[0])
    for junction in junctions:
        merge(junction["wires"])
    # Keep geometrical connectivity separate from the later name-based electrical union.
    physical = {}
    for offset in wires:
        physical.setdefault(find(offset), {"wires": [], "pins": [], "terminals": []})["wires"].append(offset)
    for pin, reference in pin_references.items():
        if reference in parents:
            physical[find(reference)]["pins"].append(pin)
    for index, terminal in enumerate(terminals):
        if terminal["wire"] in parents:
            physical[find(terminal["wire"])]["terminals"].append(index)
    physical_junctions = {}
    for junction in junctions:
        physical_junctions.setdefault(find(junction["wires"][0]), []).append(junction)
    from pin_geometry import pin_position
    locations = {}
    def location(pin):
        if pin not in locations:
            item = project._components[pin[0]]
            x, y, rotation = struct.unpack_from("<iiI", dsn, item["xy_offset"])
            locations[pin] = pin_position(dsn, item["symbol"], pin[1], (x, y), rotation)
        return locations[pin]
    def orient(points, first, second):
        if tuple(points[0]) == first and tuple(points[-1]) == second:
            return points
        if tuple(points[-1]) == first and tuple(points[0]) == second:
            return list(reversed(points))
        raise UnsupportedFormat("Stored wire does not end at its referenced pin/node")
    connections, semantic_terminals, unsupported = [], {}, []
    for index, terminal in enumerate(terminals):
        if not terminal["wire"]:
            tip = transform(terminal_markers(dsn, terminal["symbol"])["$MKRNODE"], terminal["rotation"])
            contact = tuple(terminal["position"][i] + tip[i] for i in (0, 1))
            terminal["contact"] = contact
            semantic_terminals[index] = {"id": "T" + str(index + 1), "kind": terminal["kind"], "name": terminal["name"], "pin": None,
                                        "position": contact, "rotation": terminal["rotation"], "label": terminal["label"]}
    for group_id, group in physical.items():
        if not group["pins"]:
            unsupported.append({"wires": group["wires"], "reason": "Wire network has no component pin"})
            continue
        try:
            hub = group["pins"][0]
            endpoints = {pin: (location(pin), pin_references[pin]) for pin in group["pins"]}
            for index in group["terminals"]:
                terminal = terminals[index]
                tip = transform(terminal_markers(dsn, terminal["symbol"])["$MKRNODE"], terminal["rotation"])
                contact = tuple(terminal["position"][i] + tip[i] for i in (0, 1))
                terminal["contact"] = contact
                endpoints[("@TERMINAL_T" + str(index + 1), "PIN")] = (contact, terminal["wire"])
                semantic_terminals[index] = {"id": "T" + str(index + 1), "kind": terminal["kind"], "name": terminal["name"],
                    "pin": hub, "position": contact, "rotation": terminal["rotation"], "label": terminal["label"], "preserve_geometry": True}
            local_connections = [{"first": hub, "second": pin, "points": None, "preserve_geometry": True}
                                 for pin in group["pins"][1:]]
            nodes = physical_junctions.get(group_id, [])
            if len(endpoints) == 2 and len(group["wires"]) == 1 and not nodes:
                keys = list(endpoints)
                points = orient(wires[group["wires"][0]]["points"], endpoints[keys[0]][0], endpoints[keys[1]][0])
                if local_connections:
                    local_connections[0]["points"] = points
                    local_connections[0]["labels"] = wires[group["wires"][0]]["labels"]
                else:
                    semantic_terminals[group["terminals"][0]]["points"] = points
                    semantic_terminals[group["terminals"][0]]["labels"] = wires[group["wires"][0]]["labels"]
            elif nodes and len(group["wires"]) == len(endpoints) + len(nodes) - 1:
                _need(len({reference for _, reference in endpoints.values()}) == len(endpoints), "Several pins share one branch")
                endpoint_refs = {reference for _, reference in endpoints.values()}
                paths, links = {}, []
                for key, (point, reference) in endpoints.items():
                    attached = [node for node in nodes if reference in node["wires"]]
                    _need(len(attached) == 1, "A pin branch must meet exactly one junction")
                    paths[key] = orient(wires[reference]["points"], point, attached[0]["position"])
                for reference in group["wires"]:
                    if reference in endpoint_refs:
                        continue
                    attached = [node for node in nodes if reference in node["wires"]]
                    _need(len(attached) == 2, "A junction bridge must meet exactly two nodes")
                    first, second = [node["position"] for node in attached]
                    links.append({"first": first, "second": second,
                                  "points": orient(wires[reference]["points"], first, second),
                                  "labels": wires[reference]["labels"]})
                labels = {key: wires[reference]["labels"] for key, (_, reference) in endpoints.items()
                          if wires[reference]["labels"]}
                layout = {"paths": paths, "labels": labels}
                if len(nodes) == 1:
                    layout["junction"] = nodes[0]["position"]
                else:
                    layout.update(junctions=[node["position"] for node in nodes], links=links)
                if local_connections:
                    local_connections[0]["layout"] = layout
                else:
                    semantic_terminals[group["terminals"][0]]["layout"] = layout
            else:
                raise UnsupportedFormat("Only one wire or an explicit junction tree can be rebuilt without changing the route")
            connections.extend(local_connections)
        except (UnsupportedFormat, struct.error, IndexError) as exc:
            unsupported.append({"wires": group["wires"], "reason": str(exc)})
    names = {}
    for offset, wire in wires.items():
        for label in wire["labels"]:
            if label["text"]:
                names.setdefault(label["text"], []).append(offset)
    for terminal in terminals:
        name = terminal["name"] or ("GND" if terminal["kind"] == "ground" else "")
        if name and terminal["wire"] in wires:
            names.setdefault(name, []).append(terminal["wire"])
    for references in names.values():
        merge(references)
    nets = {}
    for offset in wires:
        nets.setdefault(find(offset), {"wires": [], "pins": [], "names": [], "terminals": []})["wires"].append(offset)
    for pin, reference in pin_references.items():
        if reference in parents:
            nets[find(reference)]["pins"].append(pin)
        elif reference == 0:
            nets[pin] = {"wires": [], "pins": [pin], "names": [], "terminals": []}
    for name, references in names.items():
        nets[find(references[0])]["names"].append(name)
    for index, terminal in enumerate(terminals):
        if terminal["wire"] in parents:
            nets[find(terminal["wire"])]["terminals"].append(index)
        elif terminal["wire"] == 0:
            name = terminal["name"] or ("GND" if terminal["kind"] == "ground" else "")
            key = find(names[name][0]) if name in names else ("terminal", name or index)
            nets.setdefault(key, {"wires": [], "pins": [], "names": [name] if name else [], "terminals": []})["terminals"].append(index)
    for offset, wire in wires.items():
        wire["pin_references"] = [pin for pin, reference in pin_references.items() if reference == offset]
        wire["terminal_indices"] = [index for index, terminal in enumerate(terminals) if terminal["wire"] == offset]
        wire["junction_indices"] = [index for index, junction in enumerate(junctions) if offset in junction["wires"]]
    return {"wires": list(wires.values()), "terminals": terminals, "junctions": junctions,
            "nets": list(nets.values()), "connections": connections,
            "semantic_terminals": [semantic_terminals[index] for index in sorted(semantic_terminals)],
            "unsupported_rebuild": unsupported,
            "unknown_ranges": unknown, "missing_wire_references": missing,
            "complete": not unknown and not missing,
            "rebuild_complete": not unknown and not missing and not unsupported}
