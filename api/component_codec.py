"""Build and edit recognized native single-sheet Proteus projects.

Imports real installed device definitions and preserves recognized existing
geometry. Unsupported objects fail before writing; no GUI is used by this codec.
"""
import copy
import hashlib
import os
import re
import struct
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path
from zipfile import ZipFile

from proteus_project import Project, UnsupportedFormat, _cdb_entity_table_offset, _lp, _need, _text
from pin_geometry import symbol_pins, transform
from net_edit import invalidate_routes


SOURCE = Path(r"C:\ProgramData\program\SAMPLES\Graph Based Simulation\Rescap.pdsprj")
CONTROL_DEVICES = frozenset({'BUTTON', 'SW-SPST', 'SW-SPST-MOM', 'SWITCH',
                             'LOGICSTATE', 'LOGICTOGGLE'})


def _short(value):
    raw = value.encode("ascii")
    _need(len(raw) < 255 and all(32 <= x < 127 or x in (9, 10, 13) for x in raw),
          "Requires short ASCII strings")
    return bytes([len(raw)]) + raw


def _entities(data):
    """Read the observed v7 root entity table, retaining pin order and numbers."""
    table = _cdb_entity_table_offset(data)
    count = struct.unpack_from("<I", data, table)[0]
    _need(count < 10000, "Too many entity records")
    pos = table + 4
    records = {}
    for _ in range(count):
        index, kind, zero, object_id = struct.unpack_from("<4I", data, pos)
        pos += 16
        ref, _, pos = _lp(data, pos)
        pin_count = struct.unpack_from("<I", data, pos)[0]
        pos += 4
        _need(pin_count < 256, "Unknown pin layout")
        pins = []
        for _ in range(pin_count):
            name, _, pos = _lp(data, pos)
            number, _, pos = _lp(data, pos)
            pins.append((name, number))
        a, property_index, b = struct.unpack_from("<3I", data, pos)
        pos += 12
        # Native ADI property edits change this auxiliary tail word from -1 to
        # 0 on the edited entity. Both exact forms have passed native roundtrips.
        _need(kind == 2 and zero == a == 0 and b in (0xFFFFFFFF, 0), "Unknown entity layout")
        records[ref] = {"index": index, "object_id": object_id,
                        "property_index": property_index, "pins": pins, "auxiliary": b}
    _need(data[pos:pos + 14] == bytes.fromhex("0100000001000000000001000000"),
          "Unknown entity/property table bridge")
    return records, data[pos:pos + 14]


def _value_properties(properties, value):
    """Keep a model's explicit VALUE property consistent with the instance value."""
    return re.sub(r"(?m)^([ \t]*\{?VALUE=)[^{}\r\n]*(\}?[ \t]*)(?=\r?$)",
                  lambda match: match.group(1) + value + match.group(2), properties)


def _render_component(template, ref, value, x, y, index):
    dx, dy = x - template["x"], y - template["y"]
    actual_properties = _value_properties(template["properties"], value)
    # Proteus 8.16 does not consume a separate property body of 50 bytes or
    # fewer. Store those strings in the text record, retaining its draw flags.
    visible_properties = actual_properties if ((len(actual_properties) <= 50
                                                or template.get("show_properties", True))
                                               and len(actual_properties.encode("ascii")) < 255) else ""
    values = [ref, value, template["device"], visible_properties]
    result = bytearray([template.get("record_tag", 0)])
    for text, tail in zip(values, template["text_tails"]):
        tail = bytearray(tail)
        old_x, old_y = struct.unpack_from("<ii", tail)
        _need(-2**31 <= old_x + dx < 2**31 and -2**31 <= old_y + dy < 2**31,
              "Text coordinate overflow")
        struct.pack_into("<ii", tail, 0, old_x + dx, old_y + dy)
        result += b"\xff" + _short(text) + tail
    result += struct.pack("<I", len(actual_properties))
    if visible_properties != actual_properties:
        result += actual_properties.encode("ascii")
    result += struct.pack("<H", len(template["pins"])) + _short(template["symbol"])
    instance = bytearray(template["instance"])
    struct.pack_into("<ii", instance, 0, x, y)
    struct.pack_into("<I", instance, 12, index)
    instance[25:] = b"\0" * (4 * len(template["pins"]))
    result += instance
    entity = struct.pack("<4I", index, 2, 0, index) + _short(ref)
    entity += struct.pack("<I", len(template["pins"]))
    entity += b"".join(_short(name) + _short(number) for name, number in template["pins"])
    auxiliary = template.get("entity_auxiliary", 0xFFFFFFFF)
    _need(auxiliary in (0xFFFFFFFF, 0), "Unknown entity auxiliary word")
    entity += struct.pack("<3I", 0, index, auxiliary)
    props = actual_properties.encode("ascii") + b"\0"
    component = struct.pack("<5I", index, 1, 0, 0, 0)
    component += b"".join(_short(text) for text in (ref, value, template["device"], template["package"]))
    component += struct.pack("<I", len(props) + 4) + props
    return bytes(result), entity, component


def template_from_component(project, ref):
    """Extract a complete independent template from a parsed native instance."""
    item = project._components[ref]
    entities, _ = _entities(project._cdb)
    entity = entities[ref]
    _need(entity["object_id"] == item["instance_id"] and len(entity["pins"]) == item["pin_count"],
          "Entity and schematic pins disagree")
    tails = []
    for index, field in enumerate(item["fields"]):
        style = ("COMPONENT ID", "COMPONENT VALUE", "COMPONENT VALUE", "PROPERTIES")[index]
        start = field["span"][0] - 2
        try:
            _, end = _text(project._dsn, start, style)
        except UnsupportedFormat:
            _need(index == 2, "Unknown template text role")
            _, end = _text(project._dsn, start, "SUBCKT NAME")
        tails.append(bytes(project._dsn[field["span"][1]:end]))
    pos = item["xy_offset"]
    instance = bytes(project._dsn[pos:pos + 25 + 4 * item["pin_count"]])
    _need(instance[16:25] == b"\0" * 9, "Unknown instance flags")
    x, y, rotation = struct.unpack_from("<iiI", instance)
    geometry = symbol_pins(project._dsn, item["symbol"])
    offsets = {pin["name"]: transform(pin["tip"], rotation) for pin in geometry}
    directions = {}
    for pin in geometry:
        dx, dy = transform((pin["tip"][0] - pin["anchor"][0], pin["tip"][1] - pin["anchor"][1]), rotation)
        _need((dx == 0) != (dy == 0), "Requires a nonzero orthogonal pin direction")
        directions[pin["name"]] = ((dx > 0) - (dx < 0), (dy > 0) - (dy < 0))
    _need(set(offsets) == {name for name, _ in entity["pins"]}, "Pin geometry/entity names disagree")
    return {"device": item["fields"][2]["text"], "symbol": item["symbol"],
            "record_tag": project._dsn[item['dsn_record_start'] - 1],
            "text_tails": tails, "instance": instance, "pins": entity["pins"],
            "x": x, "y": y, "properties": item["fields"][3]["text"],
            "package": item["cdb"]["package"], "pin_offsets": offsets, "pin_directions": directions,
            "entity_auxiliary": entity["auxiliary"],
            "show_properties": bool(item["fields"][3].get("display_text", item["fields"][3]["text"]))}


class Circuit:
    def __init__(self, template_project=SOURCE):
        self.source = Path(template_project).resolve()
        self._source_digest = hashlib.sha256(self.source.read_bytes()).digest()
        self._project = Project(self.source)
        self._members = self._project._members
        self._dsn = self._members["ROOT.DSN"]
        self._cdb = self._members["ROOT.CDB"]
        metadata = ET.fromstring(self._members["PROJECT.XML"])
        _need(metadata.find("TIMESTAMP").get("FILEVER") in ("840", "847"), "Requires the verified FILEVER 840/847 template layout")
        circuits = [match.start() for match in re.finditer(b"ISIS CIRCUIT FILE\x1a\0", self._dsn)]
        _need(len(circuits) == 2, "Requires one user sheet and one default sheet")
        self._root, self._default = circuits
        self._body = self._dsn.index(b"OBJECT DATA\0", self._root) + 12
        self._directory = struct.unpack_from("<I", self._dsn, self._root - 4)[0]
        _need(self._dsn[self._directory:self._directory + 2] == b"\x02\0", "Unknown sheet directory")
        _need(self._dsn[self._default - 1] == 255, "Unknown sheet terminator")
        _, self._bridge = _entities(self._cdb)
        self.templates = {}
        for ref, item in self._project._components.items():
            device = item["fields"][2]["text"]
            if device in self.templates:
                continue
            self.templates[device] = template_from_component(self._project, ref)
        if not self.templates:
            from device_library import definition_groups
            donor = Project(SOURCE)
            source_defs = definition_groups(donor._dsn)["devices"]
            actual_defs = definition_groups(self._dsn)["devices"]
            for ref, item in donor._components.items():
                name = item["symbol"]
                if name in actual_defs and actual_defs[name]["body"] == source_defs[name]["body"]:
                    self.templates[item["fields"][2]["text"]] = template_from_component(donor, ref)
        self._parts = []
        self._connections = []
        self._terminals = []
        self._title = 'Generated circuit'

    @classmethod
    def open(cls, path):
        """Open a fully recognized existing sheet, preserving its component and wire geometry."""
        from net_objects import decode_net_objects
        project = Project(path)
        graph = decode_net_objects(project)
        _need(graph['complete'], f"Unsupported sheet objects: {graph['unknown_ranges']}; missing references: {graph['missing_wire_references']}")
        _need(graph.get('rebuild_complete', False), f"Cannot preserve this layout: {graph.get('unsupported_rebuild', [])}")
        circuit = cls(path)
        for ref, item in project._components.items():
            template = template_from_component(project, ref)
            x, y = struct.unpack_from('<ii', project._dsn, item['xy_offset'])
            circuit._parts.append(dict(ref=ref, device=template['device'], value=item['fields'][1]['text'],
                                       x=x, y=y, template=template, properties={}))
        circuit._connections = copy.deepcopy(graph['connections'])
        circuit._terminals = copy.deepcopy(graph.get('semantic_terminals', []))
        for index, terminal in enumerate(circuit._terminals, 1):
            terminal.setdefault('id', f'T{index}')
        circuit._title = ET.fromstring(project._members['PROJECT.XML']).get('TITLE', Path(path).stem)
        circuit._loaded = True
        circuit._validate(circuit._parts, circuit._connections)
        return circuit

    def import_device(self, name, library=None, donor_project=None):
        """Import an installed single-unit device definition and its glyph dependencies.

        Optional donor_project supplies the complete device and graphics definitions;
        its instances/wiring are not copied. Imported names are never replaced.
        """
        from device_library import Library, definition_groups, describe_definition

        donor_definitions = None
        if donor_project is not None:
            with ZipFile(donor_project) as donor:
                donor_definitions = definition_groups(donor.read("ROOT.DSN"))
            _need(name in donor_definitions["devices"], "Device is not embedded in the donor project")
            definition = donor_definitions["devices"][name]
            body = definition["body"]
            entry = {"name": name, "path": str(Path(donor_project).resolve()), "library": "embedded",
                     "timestamp": struct.unpack_from("<I", definition["entry"], 1 + len(name))[0]}
            metadata = describe_definition(entry, body)
            device_entry = definition["entry"]
        else:
            catalogue = Library()
            metadata = catalogue.get(name, library)
            entry, body = catalogue.definition(name, library)
            device_entry = _short(metadata["name"]) + struct.pack("<I", entry["timestamp"]) + body
        _need(metadata["pin_parse_status"] == "parsed", metadata["pin_parse_status"])
        name = metadata["name"]
        _need(name != "VPULSE", "VPULSE native instance serialization is not supported; metadata remains readable")
        _need(name not in self.templates, "Device template is already imported")
        base = definition_groups(self._dsn)
        graphics, devices = dict(base["graphics"]), dict(base["devices"])
        _need(name not in devices, "An embedded device with this name already exists")
        if donor_definitions is not None:
            for key, definition in donor_definitions["graphics"].items():
                graphics.setdefault(key, definition)
        required = {match.group().decode("ascii").rstrip("\0")
                    for match in re.finditer(rb"\$[A-Z][A-Z0-9_]*\x00", body)}
        _need(required <= graphics.keys(), f"Missing graphic dependencies: {sorted(required - graphics.keys())}")
        devices[name] = {"entry": device_entry, "body": body}
        group_bytes = b"\xfe\xff" + b"".join(graphics[key]["entry"] for key in sorted(graphics)) + b"\0"
        group_bytes += b"\xfe\xff" + b"".join(devices[key]["entry"] for key in sorted(devices)) + b"\0"
        delta = len(group_bytes) - (base["end"] - base["start"])
        dsn = bytearray(self._dsn[:base["start"]] + group_bytes + self._dsn[base["end"]:])
        root, default, directory = self._root + delta, self._default + delta, self._directory + delta
        struct.pack_into("<I", dsn, root - 4, directory)
        pos = directory + 2
        for _ in range(2):
            pos += 4
            _, _, pos = _lp(dsn, pos)
            _need(dsn[pos:pos + 2] == b"\0\0", "Unknown sheet-directory entry")
            old_pointer = struct.unpack_from("<I", dsn, pos + 2)[0]
            _need(old_pointer in (self._root, self._default), "Unknown sheet-directory target")
            struct.pack_into("<I", dsn, pos + 2, old_pointer + delta)
            pos += 6
        geometry = symbol_pins(dsn, name)
        pins = [(pin['name'], pin['number']) for pin in geometry]
        if metadata['pinouts']:
            package = metadata['package']
            _need(package in metadata['pinouts'], 'Missing pin mapping for the selected package')
            mapping, power_pins, physical, common, elements = {}, set(), set(), set(), 0
            for line in metadata['pinouts'][package]:
                if re.fullmatch(r'\{ELEMENTS\s*=\s*1\}', line):
                    elements += 1
                    continue
                group = re.fullmatch(r'\{COMMON\s*=\s*([^{}]+)\}', line)
                if group:
                    names = [value.strip() for value in group[1].split(',')]
                    _need(not common and all(names) and len(set(names)) == len(names),
                          'Duplicate or invalid common-pin declaration')
                    common = set(names)
                    continue
                pin_entry = re.fullmatch(r'\{(?:PIN\s+"([^"{}]+)"|PP\s+\(([^(){}]+)\))\s*=\s*'
                                         r'([A-Za-z0-9]+(?:/[A-Za-z0-9]+)*|\*)\}', line)
                _need(pin_entry is not None, f'Unsupported single-unit package mapping: {line}')
                logical, hidden, numbers = pin_entry.groups()
                pin_name = logical or hidden
                _need(pin_name not in mapping and pin_name not in power_pins,
                      f'Duplicate package pin mapping: {pin_name}')
                pads = numbers.split('/') if numbers != '*' else []
                _need(len(set(pads)) == len(pads) and not physical.intersection(pads),
                      'A package pad is mapped more than once')
                physical.update(pads)
                if logical is not None:
                    mapping[logical] = numbers
                else:
                    power_pins.add(hidden)
            _need(elements == 1 and common <= mapping.keys() | power_pins,
                  'Unsupported package units or common pins')
            _need(len(mapping) == len(pins) and set(mapping) == {pin[0] for pin in pins},
                  'Package mapping does not match the symbol pins')
            # Graphic numbers may be reused annotations. CDB pin fields must use
            # the selected package's mapping, including grouped pads and '*'.
            pins = [(logical, mapping[logical]) for logical, _ in pins]
        pin_offsets = {pin["name"]: pin["tip"] for pin in geometry}
        directions = {}
        for pin in geometry:
            dx, dy = pin["tip"][0] - pin["anchor"][0], pin["tip"][1] - pin["anchor"][1]
            _need((dx == 0) != (dy == 0), "Device has unsupported zero/nonorthogonal pin glyph")
            directions[pin["name"]] = ((dx > 0) - (dx < 0), (dy > 0) - (dy < 0))
        defaults = metadata["sections"].get("COMPONENT", [])
        properties = "\n".join(defaults) + "\n"
        _need(len(properties) <= 65535 and properties.isascii(), "Unsupported device-default encoding")
        prototype = next(iter(self.templates.values()))
        tails = [bytearray(tail) for tail in prototype["text_tails"]]
        for tail in tails:
            x, y = struct.unpack_from("<ii", tail)
            struct.pack_into("<ii", tail, 0, x - prototype["x"], y - prototype["y"])
        # Complex devices use the native subcircuit-name text role.
        tails[2] = bytearray(bytes(tails[2]).replace(b"COMPONENT VALUE\0", b"SUBCKT NAME\0"))
        struct.pack_into("<I", tails[2], 14, 49)
        for index in (1, 2):
            struct.pack_into("<I", tails[index], 8, 0x20000)
        simple_analogue = metadata["models"] in ({"PRIMITIVE": "ANALOG"}, {"PRIMITIVE": "ANALOGUE"})
        if simple_analogue:
            # Ordinary voltage sources use the native visible primitive text
            # layout observed in the installed DC-Generator VSOURCE instance.
            # Preserve the observed primitive flags instead of the complex form.
            for tail in tails:
                tail[13] = 0
            tails[0][12] = 4
            for index in (1, 2):
                struct.pack_into("<I", tails[index], 8, 0)
        instance = bytearray(25 + 4 * len(geometry))
        struct.pack_into("<I", instance, 12, 1)
        template = {"device": name, "symbol": name, "text_tails": [bytes(tail) for tail in tails],
            "instance": bytes(instance), "pins": pins,
            "x": 0, "y": 0, "properties": properties, "package": metadata["package"] or "",
            "pin_offsets": pin_offsets, "pin_directions": directions,
            # Native tag 8 creates ACTIVECOMPONENT; tag 0 has no actuator.
            "record_tag": 8 if any(re.match(r'\{?ACTIVE=', line)
                                  for line in metadata['sections'].get('DEVICE', [])) else 0,
            "show_properties": simple_analogue,
            "library": entry["path"], "model_evidence": metadata["models"]}
        # Commit only after parsing and validating the entire imported dependency set.
        self._dsn = bytes(dsn)
        self._members["ROOT.DSN"] = self._dsn
        self._root, self._default, self._directory = root, default, directory
        self._body += delta
        self.templates[name] = template
        return self

    def import_from_project(self, project, device):
        return self.import_device(device, donor_project=project)

    def add(self, device, ref, value, x, y, *, rotation=0, mirror_x=False, mirror_y=False, properties=None):
        _need(device in self.templates, "Device is not in the imported template library")
        _need(isinstance(ref, str) and re.fullmatch(r"[A-Z][A-Z0-9_]*[0-9][A-Z0-9_]*", ref), "Invalid reference")
        _need(len(ref) <= 63 and not any(part["ref"] == ref for part in self._parts), "Duplicate/oversized reference")
        _need(isinstance(value, str) and value and all(32 <= ord(c) < 127 for c in value), "Value must be printable ASCII")
        _short(value)
        _need(type(x) is int and type(y) is int and -2**31 <= x < 2**31 and -2**31 <= y < 2**31, "Invalid coordinate")
        _need(len(self._parts) < 65533, "Too many components")
        # Render now so invalid fields cannot enter the circuit.
        part = {"device": device, "ref": ref, "value": value, "x": x, "y": y,
                "rotation": rotation, "mirror_x": mirror_x, "mirror_y": mirror_y,
                "properties": dict(properties or {})}
        self._validate(self._parts + [part], self._connections)
        self._parts.append(part)
        return self

    add_component = add

    def _endpoint(self, endpoint):
        """Resolve a logical pin name or an unambiguous physical pin number."""
        if isinstance(endpoint, str):
            endpoint = endpoint.rsplit('.', 1)
        _need(isinstance(endpoint, (tuple, list)) and len(endpoint) == 2
              and all(isinstance(value, str) and value for value in endpoint), 'Use REF.PIN or (ref, pin)')
        ref, pin = endpoint
        _need(any(part['ref'] == ref for part in self._parts), 'Unknown component in connection')
        pins = self._template(self._parts[self._index(ref)])['pins']
        if pin not in {name for name, _ in pins}:
            matches = [name for name, number in pins if number == pin]
            _need(len(matches) == 1, 'Unknown or ambiguous pin name/number')
            pin = matches[0]
        return ref, pin

    def connect(self, first, second, points=None):
        """Connect REF.PIN endpoints; optional points include both endpoints."""
        from wire_codec import encode_connected_body, wire_record

        endpoints, locations = [], []
        by_ref = {part["ref"]: part for part in self._parts}
        for endpoint in (first, second):
            ref, pin = self._endpoint(endpoint)
            template = self._template(by_ref[ref])
            _need(pin in [name for name, _ in template["pins"]], "Unknown pin in connection")
            endpoints.append((ref, pin))
            dx, dy = template["pin_offsets"][pin]
            locations.append((by_ref[ref]["x"] + dx, by_ref[ref]["y"] + dy))
        _need(endpoints[0] != endpoints[1], "Cannot connect a pin to itself")
        _need(not any(set(endpoints) == {item["first"], item["second"]} for item in self._connections),
              "Connection already exists")
        if points is not None:
            points = [tuple(point) for point in points]
            wire_record(points)  # validate before mutating the circuit
            _need(points[0] == locations[0] and points[-1] == locations[1],
                  "Wire endpoints do not match pin locations")
        candidate = {"first": endpoints[0], "second": endpoints[1], "points": points}
        for net in self.nets(physical=True):
            if set(net) & set(endpoints) and len(set(net) | set(endpoints)) > 2:
                _need(not any(item.get('points') is not None and not item.get('preserve_geometry')
                              and item['first'] in net for item in self._connections),
                      'Clear the manual route with set_route(first, second, None) before adding a branch')
        # ponytail: validate the complete small circuit; cache layout only if larger designs need it.
        connections, terminals = invalidate_routes(self._connections + [candidate], self._terminals, endpoints)
        connections[-1]['points'] = points
        self._validate(self._parts, connections, terminals)
        self._connections, self._terminals = connections, terminals
        return self

    def _template(self, part):
        """Derive one instance's properties and orientation without changing its library template."""
        template = copy.deepcopy(part.get("template") or self.templates[part["device"]])
        angle = part.get("rotation", 0)
        mx, my = part.get("mirror_x", False), part.get("mirror_y", False)
        _need(type(angle) is int and angle % 90 == 0 and type(mx) is type(my) is bool,
              "Rotation must be a multiple of 90 degrees; mirrors must be booleans")
        delta = (angle % 360) * 10 | (0x10000 if mx else 0) | (0x20000 if my else 0)
        if delta:
            original = struct.unpack_from("<I", template["instance"], 8)[0]
            basis = [transform(transform(point, original), delta) for point in ((1, 0), (0, 1))]
            combined = next(raw for raw in [a + m for a in (0, 900, 1800, 2700) for m in (0, 0x10000)]
                            if [transform(point, raw) for point in ((1, 0), (0, 1))] == basis)
            instance = bytearray(template["instance"])
            struct.pack_into("<I", instance, 8, combined)
            template["instance"] = bytes(instance)
            for name in ("pin_offsets", "pin_directions"):
                template[name] = {pin: transform(point, delta) for pin, point in template[name].items()}
            tails = []
            for original_tail in template["text_tails"]:
                tail = bytearray(original_tail)
                x, y = struct.unpack_from("<ii", tail)
                x, y = transform((x - template["x"], y - template["y"]), delta)
                _need(all(-2**31 <= v < 2**31 for v in (x + template["x"], y + template["y"])),
                      "Rotated text coordinate overflow")
                struct.pack_into("<ii", tail, 0, x + template["x"], y + template["y"])
                tails.append(bytes(tail))
            template["text_tails"] = tails
        label_offsets = part.get("label_offsets", {})
        label_names = ("reference", "value", "device", "properties")
        _need(isinstance(label_offsets, dict) and set(label_offsets) <= set(label_names),
              "Unknown component label")
        for name, offset in label_offsets.items():
            _need(isinstance(offset, (tuple, list)) and len(offset) == 2
                  and all(type(v) is int and -2**31 <= v < 2**31 for v in offset),
                  "Label offsets must be two int32 coordinates")
            xy = (template["x"] + offset[0], template["y"] + offset[1])
            _need(all(-2**31 <= v < 2**31 for v in xy), "Label coordinate overflow")
            index = label_names.index(name)
            tail = bytearray(template["text_tails"][index])
            struct.pack_into("<ii", tail, 0, *xy)
            template["text_tails"][index] = bytes(tail)
        properties = part.get("properties", {})
        lines = template["properties"].splitlines()
        for key, value in properties.items():
            _need(isinstance(key, str) and re.fullmatch(r"[A-Z_][A-Z0-9_.]*", key)
                  and key not in {"REF", "DEVICE", "VALUE"}, "Invalid property key")
            if value is not None:
                value = str(value)
                _need(all(32 <= ord(char) < 127 and char not in "{}" for char in value), "Invalid property value")
            pattern = re.compile(r"^\s*\{?" + re.escape(key) + "=")
            hidden = any(line.lstrip().startswith("{") for line in lines if pattern.match(line))
            lines = [line for line in lines if not pattern.match(line)]
            if value is not None:
                text = key + "=" + value
                lines.append("{" + text + "}" if hidden else text)
        if properties:
            template["properties"] = "\n".join(lines) + "\n"
        template['properties'] = _value_properties(template['properties'], part['value'])
        return template

    def _validate(self, parts, connections, terminals=None):
        from net_objects import encode_net_body
        resolved, objects = [], []
        for index, part in enumerate(parts, 1):
            template = self._template(part)
            obj = _render_component(template, part["ref"], part["value"], part["x"], part["y"], index)[0]
            resolved.append(dict(part, template=template))
            objects.append(obj)
        encode_net_body(objects, resolved, self.templates, connections, self._body, self._dsn,
                        self._terminals if terminals is None else terminals)

    def _index(self, ref):
        for index, part in enumerate(self._parts):
            if part["ref"] == ref:
                return index
        raise KeyError(ref)

    def components(self):
        result = []
        for part in self._parts:
            template = self._template(part)
            properties = {}
            for line in template["properties"].splitlines():
                text = line.strip().strip("{}")
                if "=" in text:
                    key, value = text.split("=", 1)
                    properties[key] = value
            result.append(dict(ref=part["ref"], device=part["device"], value=part["value"],
                               position={"x": part["x"], "y": part["y"]}, properties=properties,
                               rotation_raw=struct.unpack_from("<I", template["instance"], 8)[0]))
        return result

    def pins(self, ref):
        part = self._parts[self._index(ref)]
        template = self._template(part)
        return [dict(name=name, number=number, position=(part["x"] + template["pin_offsets"][name][0],
                    part["y"] + template["pin_offsets"][name][1]), direction=template["pin_directions"][name])
                for name, number in template["pins"]]

    def connections(self):
        return copy.deepcopy(self._connections)

    def nets(self, *, physical=False):
        groups = [{(part["ref"], pin["name"])} for part in self._parts for pin in self.pins(part["ref"])]
        for connection in self._connections:
            matches = [group for group in groups if connection["first"] in group or connection["second"] in group]
            groups = [group for group in groups if group not in matches] + [set().union(*matches)]
        if not physical:
            by_name = {}
            for terminal in self._terminals:
                name = terminal.get('name') or ('GND' if terminal['kind'] == 'ground' else '')
                if name and terminal.get('pin') is not None:
                    by_name.setdefault(name, []).append(terminal['pin'])
            for item in self._connections + self._terminals:
                pin = item.get('first', item.get('pin'))
                if pin is None:
                    continue
                layout = item.get('layout') or {}
                labels = list(item.get('labels', []))
                labels += [label for values in layout.get('labels', {}).values() for label in values]
                labels += [label for link in layout.get('links', []) for label in link.get('labels', [])]
                for label in labels:
                    if label.get('text'):
                        by_name.setdefault(label['text'], []).append(pin)
            for pins in by_name.values():
                matches = [group for group in groups if any(pin in group for pin in pins)]
                if matches:
                    groups = [group for group in groups if group not in matches] + [set().union(*matches)]
        return [sorted(group) for group in groups]

    def terminals(self):
        result = copy.deepcopy(self._terminals)
        for item in result:
            raw = item.pop('rotation', None)
            item['rotation_raw'] = raw
            item['rotation'] = None if raw is None else struct.unpack('<h', struct.pack('<H', raw & 65535))[0] / 10
        return result

    def _terminal_contact(self, terminal):
        if terminal.get('position') is not None:
            return terminal['position']
        ref, name = terminal['pin']
        pin = next(pin for pin in self.pins(ref) if pin['name'] == name)
        return tuple(pin['position'][i] + pin['direction'][i] * 508000 for i in (0, 1))

    def _isolate_terminal(self, terminal):
        """Detach a terminal without moving or turning its glyph."""
        from net_objects import terminal_markers, TERMINALS
        terminal['position'] = self._terminal_contact(terminal)
        if terminal.get('rotation') is None and terminal.get('pin'):
            ref, name = terminal['pin']
            pin = next(pin for pin in self.pins(ref) if pin['name'] == name)
            node = terminal_markers(self._dsn, TERMINALS[terminal['kind']][0])['$MKRNODE']
            terminal['rotation'] = next(angle for angle in (0, 900, 1800, 2700)
                if tuple((value > 0) - (value < 0) for value in transform(node, angle)) == tuple(-v for v in pin['direction']))
        terminal['pin'] = None
        for field in ('points', 'layout', 'preserve_geometry', 'labels'):
            terminal.pop(field, None)

    @staticmethod
    def _retain_branch_labels(removed, connections, terminals):
        """Keep labels on surviving branches when their former container is removed."""
        for item in removed:
            for label in item.get('labels', []):
                owner = label.get('_pin')
                if owner is None:
                    continue  # A label on a deleted two-endpoint wire goes with that wire.
                owner = tuple(owner)
                target = next((c for c in connections if owner in (c['first'], c['second'])), None)
                if target is None:
                    target = next((t for t in terminals if t.get('pin') is not None and
                                   owner in (t['pin'], ('@TERMINAL_' + t['id'], 'PIN'))), None)
                if target is not None:
                    target.setdefault('labels', []).append(copy.deepcopy(label))

    def add_terminal(self, kind, name, pin=None, *, position=None, rotation=None):
        from net_objects import TERMINALS
        _need(kind in TERMINALS and isinstance(name, str), 'Unknown terminal kind or invalid name')
        if pin is not None:
            pin = self._endpoint(pin)
        number = 1
        while any(item['id'] == f'T{number}' for item in self._terminals):
            number += 1
        if rotation is not None:
            _need(type(rotation) is int and rotation % 90 == 0, 'Terminal rotation must be a multiple of 90 degrees')
            rotation = rotation % 360 * 10
        item = dict(id=f'T{number}', kind=kind, name=name, pin=pin, position=position, rotation=rotation)
        connections, terminals = invalidate_routes(self._connections, self._terminals + [item], [pin] if pin else [])
        self._validate(self._parts, connections, terminals)
        self._connections, self._terminals = connections, terminals
        return item['id']

    def update_terminal(self, terminal_id, **changes):
        _need(changes and set(changes) <= {'kind', 'name', 'pin', 'position', 'rotation'}, 'Unsupported terminal update')
        terminals = copy.deepcopy(self._terminals)
        matches = [item for item in terminals if item['id'] == terminal_id]
        _need(len(matches) == 1, 'Terminal does not exist')
        old_pin = matches[0].get('pin')
        connections = self._connections
        removed = []
        if 'pin' in changes and changes['pin'] is None and old_pin is not None:
            connections, terminals = invalidate_routes(connections, terminals, [old_pin])
            matches = [item for item in terminals if item['id'] == terminal_id]
            removed = [copy.deepcopy(matches[0])]
            self._isolate_terminal(matches[0])
        if 'pin' in changes and changes['pin'] is not None:
            changes['pin'] = self._endpoint(changes['pin'])
        if changes.get('rotation') is not None:
            rotation = changes['rotation']
            _need(type(rotation) is int and rotation % 90 == 0, 'Terminal rotation must be a multiple of 90 degrees')
            changes['rotation'] = rotation % 360 * 10
        if set(changes) & {'position', 'rotation', 'kind'}:
            matches[0].pop('label', None)
        matches[0].update(changes)
        if set(changes) & {'pin', 'position', 'rotation', 'kind'}:
            pins = [pin for pin in (old_pin, matches[0].get('pin')) if pin is not None]
            connections, terminals = invalidate_routes(connections, terminals, pins)
        self._retain_branch_labels(removed, connections, terminals)
        self._validate(self._parts, connections, terminals)
        self._connections, self._terminals = connections, terminals
        return self

    def delete_terminal(self, terminal_id):
        old = next((item for item in self._terminals if item['id'] == terminal_id), None)
        _need(old is not None, 'Terminal does not exist')
        connections, terminals = invalidate_routes(self._connections, self._terminals, [old['pin']] if old.get('pin') else [])
        removed = [item for item in terminals if item['id'] == terminal_id]
        terminals = [item for item in terminals if item['id'] != terminal_id]
        self._retain_branch_labels(removed, connections, terminals)
        self._validate(self._parts, connections, terminals)
        self._connections, self._terminals = connections, terminals
        return self

    def configure_power(self, rails, bindings=None):
        from net_objects import power_rails_bytes
        encoded = power_rails_bytes(rails, bindings or {})
        self._members = dict(self._members, **{'SCRIPTS/PWRRAILS.DAT': encoded})
        return self

    def power_rails(self):
        from net_objects import read_power_rails
        return read_power_rails(self._members.get('SCRIPTS/PWRRAILS.DAT', b''))

    def update(self, ref, **changes):
        """Change an instance atomically; connected automatic routes are regenerated."""
        _need(changes and set(changes) <= {"value", "x", "y", "rotation", "mirror_x", "mirror_y", "properties", "label_offsets"},
              "Unsupported component update")
        parts = copy.deepcopy(self._parts)
        part = parts[self._index(ref)]
        if "properties" in changes:
            changes = dict(changes, properties=dict(part.get("properties", {}), **changes["properties"]))
        if "label_offsets" in changes:
            changes = dict(changes, label_offsets=copy.deepcopy(changes["label_offsets"]))
        part.update(changes)
        _need(isinstance(part["value"], str) and part["value"] and all(32 <= ord(c) < 127 for c in part["value"]),
              "Value must be printable ASCII")
        _short(part["value"])
        _need(all(type(part[k]) is int and -2**31 <= part[k] < 2**31 for k in ("x", "y")), "Invalid position")
        connections, terminals = self._connections, self._terminals
        if set(changes) & {"x", "y", "rotation", "mirror_x", "mirror_y"}:
            connections, terminals = invalidate_routes(connections, terminals, [(ref, pin['name']) for pin in self.pins(ref)])
        self._validate(parts, connections, terminals)
        self._parts, self._connections = parts, connections
        self._terminals = terminals
        return self

    def move(self, ref, x, y):
        """Move to absolute native coordinates and reroute attached wires."""
        return self.update(ref, x=x, y=y)

    def rotate(self, ref, degrees=90):
        return self.update(ref, rotation=self._parts[self._index(ref)].get("rotation", 0) + degrees)

    def mirror(self, ref, axis="x"):
        _need(axis in ("x", "y"), "Mirror axis must be x or y")
        key = "mirror_" + axis
        return self.update(ref, **{key: not self._parts[self._index(ref)].get(key, False)})

    def set_properties(self, ref, **properties):
        return self.update(ref, properties=properties)

    def bind_controls(self, *refs):
        """Assign native INC/DEC keys before saving and opening the project.

        Each binary control uses two of Proteus's ten global actuator keys.
        Other components' bindings are reserved; the update is atomic. Reopen
        an existing Session after loading a project with changed bindings.
        """
        if not refs or any(not isinstance(ref, str) for ref in refs) or len(set(refs)) != len(refs):
            raise ValueError('Provide distinct component references')
        components = {item['ref']: item for item in self.components()}
        for ref in refs:
            if ref not in components:
                raise KeyError(ref)
            if components[ref]['device'] not in CONTROL_DEVICES:
                raise UnsupportedFormat(f'Unsupported binary control: {ref}')
        occupied = set()
        for ref, item in components.items():
            for key in ('INC', 'DEC', 'KEY'):
                value = item['properties'].get(key)
                if value is None:
                    continue
                if not re.fullmatch(r'[0-9]', value):
                    raise ValueError(f'{ref}.{key} must be one digit from 0 to 9')
                if ref not in refs:
                    occupied.add(int(value))
        free = [str(key) for key in range(10) if key not in occupied]
        # ponytail: two native key slots per binary control; a different native
        # dispatch mechanism is needed when more than five controls are required.
        if len(free) < 2 * len(refs):
            raise ValueError('Not enough free actuator keys (0-9; two per control)')
        parts = copy.deepcopy(self._parts)
        for index, ref in enumerate(refs):
            part = parts[self._index(ref)]
            part['properties'] = dict(part.get('properties', {}),
                                      INC=free[2 * index], DEC=free[2 * index + 1], KEY=None)
        self._validate(parts, self._connections)
        self._parts = parts
        return self

    def rename(self, ref, new_ref):
        _need(isinstance(new_ref, str) and re.fullmatch(r"[A-Z][A-Z0-9_]*[0-9][A-Z0-9_]*", new_ref)
              and len(new_ref) <= 63 and not any(p["ref"] == new_ref for p in self._parts), "Invalid or duplicate reference")
        parts, connections = copy.deepcopy(self._parts), copy.deepcopy(self._connections)
        parts[self._index(ref)]["ref"] = new_ref
        for connection in connections:
            for side in ("first", "second"):
                if connection[side][0] == ref:
                    connection[side] = (new_ref, connection[side][1])
            if connection.get('layout'):
                connection['layout']['paths'] = {(new_ref if key[0] == ref else key[0], key[1]): points
                    for key, points in connection['layout']['paths'].items()}
        terminals = copy.deepcopy(self._terminals)
        for terminal in terminals:
            if terminal.get('pin') is not None and terminal['pin'][0] == ref:
                terminal['pin'] = (new_ref, terminal['pin'][1])
            if terminal.get('layout'):
                terminal['layout']['paths'] = {(new_ref if key[0] == ref else key[0], key[1]): points
                    for key, points in terminal['layout']['paths'].items()}
                if 'labels' in terminal['layout']:
                    terminal['layout']['labels'] = {(new_ref if key[0] == ref else key[0], key[1]): labels
                        for key, labels in terminal['layout']['labels'].items()}
        for item in connections + terminals:
            if item.get('layout') and 'labels' in item['layout']:
                item['layout']['labels'] = {(new_ref if key[0] == ref else key[0], key[1]): labels
                    for key, labels in item['layout']['labels'].items()}
            for label in item.get('labels', []):
                if label.get('_pin') and label['_pin'][0] == ref:
                    label['_pin'] = (new_ref, label['_pin'][1])
        self._validate(parts, connections, terminals)
        self._parts, self._connections = parts, connections
        self._terminals = terminals
        return self

    def copy_component(self, ref, new_ref, x, y):
        part = copy.deepcopy(self._parts[self._index(ref)])
        _need(not any(p["ref"] == new_ref for p in self._parts) and isinstance(new_ref, str)
              and re.fullmatch(r"[A-Z][A-Z0-9_]*[0-9][A-Z0-9_]*", new_ref) and len(new_ref) <= 63,
              "Invalid or duplicate reference")
        _need(all(type(v) is int and -2**31 <= v < 2**31 for v in (x, y)), "Invalid position")
        part.update(ref=new_ref, x=x, y=y)
        self._validate(self._parts + [part], self._connections)
        self._parts.append(part)
        return self

    def delete_component(self, ref):
        index = self._index(ref)
        parts = self._parts[:index] + self._parts[index + 1:]
        connections, terminals = self._detach({(ref, pin["name"]) for pin in self.pins(ref)})
        self._validate(parts, connections, terminals)
        self._parts, self._connections = parts, connections
        self._terminals = terminals
        return self

    def _detach(self, pins):
        """Remove pins while preserving connectivity between the surviving members of each net."""
        affected = set(pins)
        connections, terminals = invalidate_routes(self._connections, self._terminals, pins)
        for net in self.nets(physical=True):
            if not set(net) & pins:
                continue
            affected.update(net)
            owners = [item for item in connections if item['first'] in net]
            owners += [item for item in terminals if item.get('pin') in net]
            labels = [label for item in owners for label in item.get('labels', [])]
            for item in owners:
                item.pop('labels', None)
            connections = [c for c in connections if c["first"] not in net]
            remaining = [pin for pin in net if pin not in pins]
            if remaining:
                for terminal in terminals:
                    if terminal.get('pin') in pins and terminal.get('pin') in net:
                        self._isolate_terminal(terminal)
                        terminal['pin'] = remaining[0]
            rebuilt = [dict(first=remaining[0], second=pin, points=None) for pin in remaining[1:]]
            target = rebuilt[0] if rebuilt else next((item for item in terminals if item.get('pin') in remaining), None)
            if target is not None and labels:
                target.setdefault('labels', []).extend(labels)
            connections.extend(rebuilt)
        for terminal in terminals:
            if terminal.get('pin') in pins:
                self._isolate_terminal(terminal)
        return invalidate_routes(connections, terminals, affected)

    def disconnect(self, first, second=None):
        """Remove a requested edge, or detach a pin from all connections."""
        first = self._endpoint(first)
        second = self._endpoint(second) if second is not None else None
        matches = [c for c in self._connections if (first in (c["first"], c["second"]) if second is None
                   else {first, second} == {c["first"], c["second"]})]
        _need(matches or (second is None and any(t.get('pin') == first for t in self._terminals)), 'Connection does not exist')
        if second is None:
            connections, terminals = self._detach({first})
        else:
            connections, terminals = invalidate_routes(self._connections, self._terminals, [first, second])
            removed = [item for item in connections if {item['first'], item['second']} == {first, second}]
            connections = [item for item in connections if item not in removed]
            self._retain_branch_labels(removed, connections, terminals)
            connections, terminals = invalidate_routes(connections, terminals, [first, second])
        self._validate(self._parts, connections, terminals)
        self._connections, self._terminals = connections, terminals
        return self

    def set_route(self, first, second, points):
        first, second = self._endpoint(first), self._endpoint(second)
        connections, terminals = invalidate_routes(self._connections, self._terminals, [first, second])
        matches = [item for item in connections if {item['first'], item['second']} == {first, second}]
        _need(len(matches) == 1, 'Connection does not exist')
        matches[0].update(first=first, second=second, points=None if points is None else [tuple(point) for point in points])
        self._validate(self._parts, connections, terminals)
        self._connections, self._terminals = connections, terminals
        return self

    def save(self, destination, title=None, *, overwrite=False):
        """Validate a complete temporary archive, then atomically publish it.

        Overwriting requires an explicit flag. Saving over the loaded source also
        refuses concurrent external changes, so a stale editor cannot erase them.
        """
        destination = Path(destination).resolve()
        _need(type(overwrite) is bool, 'overwrite must be a boolean')
        _need(destination.suffix.lower() == '.pdsprj', 'Destination must be a .pdsprj file')
        _need(overwrite or not destination.exists(), "Destination exists; pass overwrite=True to replace it")
        if destination == self.source:
            _need(hashlib.sha256(self.source.read_bytes()).digest() == self._source_digest,
                  "Source changed outside this Circuit; reopen it before saving")
        objects, entities, properties = [], [], []
        resolved = []
        for index, part in enumerate(self._parts, 1):
            template = self._template(part)
            resolved.append(dict(part, template=template))
            obj, entity, props = _render_component(template, **{k: part[k] for k in ("ref", "value", "x", "y")}, index=index)
            objects.append(obj); entities.append(entity); properties.append(props)
        count = len(self._parts)
        from net_objects import encode_net_body
        body = encode_net_body(objects, resolved, self.templates, self._connections, self._body, self._dsn, self._terminals)
        delta = len(body) - (self._default - 1 - self._body)
        dsn = bytearray(self._dsn[:self._body] + body + self._dsn[self._default - 1:])
        struct.pack_into("<H", dsn, self._root + 19, count + 1)
        struct.pack_into("<I", dsn, self._root - 4, self._directory + delta)
        marker = b"\x02\0\0\0\x0b__DEFAULT__\0\0"
        _need(dsn.count(marker) == 1, "Ambiguous default-sheet entry")
        pointer = dsn.index(marker) + len(marker)
        _need(struct.unpack_from("<I", dsn, pointer)[0] == self._default, "Default-sheet pointer mismatch")
        struct.pack_into("<I", dsn, pointer, self._default + delta)
        cdb = self._cdb[:_cdb_entity_table_offset(self._cdb)] + struct.pack("<I", count) + b"".join(entities) + self._bridge
        cdb += struct.pack("<I", count) + b"".join(properties) + b"\0" * 4
        metadata = ET.fromstring(self._members["PROJECT.XML"])
        metadata.set("TITLE", self._title if title is None else title)
        members = dict(self._members, **{"ROOT.DSN": bytes(dsn), "ROOT.CDB": cdb,
            "PROJECT.XML": ET.tostring(metadata, encoding="utf-8", xml_declaration=True)})
        # The new sheet has no graph objects, so old graph caches have no owner.
        members.pop("GRAPHS.DAT", None)
        if not getattr(self, '_loaded', False):
            # A new sheet must not build/load the template's VSM Studio firmware
            # in place of the image assigned to its newly created MCU.
            members = {name: data for name, data in members.items()
                       if not name.startswith('FIRMWARE')}
        destination.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temporary = tempfile.mkstemp(prefix=".proteus-", suffix=".pdsprj", dir=destination.parent)
        os.close(descriptor)
        temporary = Path(temporary)
        try:
            with ZipFile(temporary, "w") as archive:
                written = set()
                for info in self._project._infos:
                    if info.filename in members:
                        archive.writestr(copy.copy(info), members[info.filename])
                        written.add(info.filename)
                for name in members.keys() - written:
                    archive.writestr(name, members[name])
            _need(len(Project(temporary).components()) == count, "Generated component count mismatch")
            if destination == self.source:
                _need(hashlib.sha256(self.source.read_bytes()).digest() == self._source_digest,
                      "Source changed while saving; reopen it before saving")
            if overwrite:
                os.replace(temporary, destination)
            else:
                os.rename(temporary, destination)  # Windows refuses a destination created concurrently.
            if destination == self.source:
                self._source_digest = hashlib.sha256(destination.read_bytes()).digest()
        finally:
            temporary.unlink(missing_ok=True)
        return destination
