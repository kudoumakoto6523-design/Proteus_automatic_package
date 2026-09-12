"""Serialize native pin references, wires, and junctions from component templates.

Binary nets define their wire after the later endpoint's complete pin table.
Multi-pin nets use fixed-four-slot junctions, with bridged nodes for larger buses.
Routes use pin exit directions and a finite set of orthogonal candidates.
"""
import struct

from proteus_project import _need


def wire_record(points, labels=()):
    # Observed plain, unlabelled WIRE style from the installed official Rescap sample.
    prefix = bytes.fromhex("00 1d 00 00 00 00 c0 9e 00 00 00 40 00 00 01 ff ff ff 00 ff ff ff 00 02 7f") + b"WIRE\0\0\0"
    _need(2 <= len(points) <= 1000, "A wire needs 2..1000 points")
    _need(all(len(p) == 2 and all(type(v) is int and -2**31 <= v < 2**31 for v in p) for p in points),
          "Coordinates must be signed 32-bit integers")
    _need(all(a != b and (a[0] == b[0] or a[1] == b[1]) for a, b in zip(points, points[1:])),
          "Use nonzero orthogonal wire segments")
    _need(len({tuple(point) for point in points}) == len(points), "Repeated wire vertex or closed loop")
    _need(all((b[0] - a[0]) * (c[0] - b[0]) + (b[1] - a[1]) * (c[1] - b[1]) >= 0
              for a, b, c in zip(points, points[1:], points[2:])), "Wire folds back along itself")
    if labels:
        from net_objects import label_record
        a, b = max(zip(points, points[1:]), key=lambda pair: abs(pair[0][0] - pair[1][0]) + abs(pair[0][1] - pair[1][1]))
        midpoint = ((a[0] + b[0]) // 2, (a[1] + b[1]) // 2)
        encoded = b"".join(label_record(label["text"], midpoint if label.get("auto_position") else label["position"],
                                       "WIRE LABEL", 48, label.get("tail_hex")) for label in labels)
        prefix = prefix[:-2] + struct.pack("<H", len(labels)) + encoded
    return prefix + struct.pack("<H", len(points)) + b"".join(struct.pack("<ii", *p) for p in points)


def _direction(a, b):
    return ((b[0] > a[0]) - (b[0] < a[0]), (b[1] > a[1]) - (b[1] < a[1]))


def _clean(points):
    result = []
    for point in points:
        point = tuple(point)
        if not result or point != result[-1]:
            while len(result) >= 2 and _direction(result[-2], result[-1]) == _direction(result[-1], point):
                result.pop()
            result.append(point)
    return result


def _route(a, b, first_direction, second_direction=None):
    """Try a finite set of orthogonal paths, respecting pin exit directions."""
    lead = 254000
    aa = tuple(a[i] + first_direction[i] * lead for i in (0, 1))
    bb = tuple(b[i] + second_direction[i] * lead for i in (0, 1)) if second_direction else b
    candidates = [[a, b], [a, (b[0], a[1]), b], [a, (a[0], b[1]), b],
                  [a, aa, (bb[0], aa[1]), bb, b], [a, aa, (aa[0], bb[1]), bb, b]]
    for x in (min(aa[0], bb[0]) - lead, max(aa[0], bb[0]) + lead):
        candidates.append([a, aa, (x, aa[1]), (x, bb[1]), bb, b])
    for y in (min(aa[1], bb[1]) - lead, max(aa[1], bb[1]) + lead):
        candidates.append([a, aa, (aa[0], y), (bb[0], y), bb, b])
    valid = []
    for candidate in candidates:
        path = _clean(candidate)
        try:
            wire_record(path)
        except ValueError:
            continue
        if _direction(path[0], path[1]) != first_direction:
            continue
        if second_direction and _direction(path[-1], path[-2]) != second_direction:
            continue
        valid.append(path)
    _need(valid, "No simple route respects pin directions; specify points or adjust component positions")
    return min(valid, key=lambda path: (sum(abs(a[0] - b[0]) + abs(a[1] - b[1]) for a, b in zip(path, path[1:])), len(path)))


def _junction_location(endpoints):
    bounds = [[-2**31, 2**31 - 1], [-2**31, 2**31 - 1]]
    for endpoint in endpoints:
        for axis, sign in enumerate(endpoint["direction"]):
            if sign > 0:
                bounds[axis][0] = max(bounds[axis][0], endpoint["location"][axis] + 254000)
            elif sign < 0:
                bounds[axis][1] = min(bounds[axis][1], endpoint["location"][axis] - 254000)
    _need(all(low <= high for low, high in bounds), "No simple junction respects all pin directions; adjust component positions")
    return tuple(max(low, min(high, sorted(endpoint["location"][axis] for endpoint in endpoints)[len(endpoints) // 2]))
                 for axis, (low, high) in enumerate(bounds))


def _overlap(first, second):
    for a, b in zip(first, first[1:]):
        for c, d in zip(second, second[1:]):
            if a[1] == b[1] == c[1] == d[1] and max(min(a[0], b[0]), min(c[0], d[0])) < min(max(a[0], b[0]), max(c[0], d[0])):
                return True
            if a[0] == b[0] == c[0] == d[0] and max(min(a[1], b[1]), min(c[1], d[1])) < min(max(a[1], b[1]), max(c[1], d[1])):
                return True
    return False


def _junction_net(endpoints, requests, preserve_geometry):
    _need(all(request["points"] is None for request in requests),
          "Explicit point routes are not supported for a multi-pin net")
    layouts = [request["layout"] for request in requests if request["layout"] is not None]
    _need(not layouts or all(layout == layouts[0] for layout in layouts), "Conflicting layouts for one net")
    layout = layouts[0] if layouts else None
    if layout is not None:
        _need(set(layout["paths"]) == {endpoint["key"] for endpoint in endpoints}, "Stored junction pins do not match the net")
        positions = [tuple(point) for point in layout.get("junctions", [layout.get("junction")])]
        paths, links = layout["paths"], layout.get("links", [])
    else:
        if len(endpoints) <= 4:
            try:
                positions = [_junction_location(endpoints)]
                paths = {endpoint["key"]: _route(endpoint["location"], positions[0], endpoint["direction"]) for endpoint in endpoints}
                return _junction_records(endpoints, requests, positions, paths, [], {}, preserve_geometry)
            except ValueError:
                pass
        # A shared bus uses several real fixed-four-slot nodes. Try the axis
        # favored by the pin exits, then the other; do not invent a general router.
        center = _junction_location(endpoints)
        preferred = 0 if sum(bool(endpoint["direction"][1]) for endpoint in endpoints) >= len(endpoints) / 2 else 1
        failures = []
        for axis in (preferred, 1 - preferred):
            positions = sorted({tuple(endpoint["location"][i] if i == axis else center[i] for i in (0, 1))
                                for endpoint in endpoints}, key=lambda point: point[axis])
            try:
                _need(len(positions) > 1, "Pins share the same bus attachment")
                paths = {endpoint["key"]: _route(endpoint["location"],
                         tuple(endpoint["location"][i] if i == axis else center[i] for i in (0, 1)), endpoint["direction"])
                         for endpoint in endpoints}
                links = [{"first": first, "second": second, "points": [first, second]}
                         for first, second in zip(positions, positions[1:])]
                return _junction_records(endpoints, requests, positions, paths, links, {}, preserve_geometry)
            except ValueError as exc:
                failures.append(str(exc))
        _need(False, "No simple junction bus fits this layout: " + "; ".join(failures))
    return _junction_records(endpoints, requests, positions, paths, links, (layout or {}).get("labels", {}), preserve_geometry)


def _junction_records(endpoints, requests, positions, paths, links, labels_by_pin, preserve_geometry):
    _need(positions and len(set(positions)) == len(positions), "Duplicate junction positions")
    degrees, all_paths, wires = [0] * len(positions), [], []
    for endpoint in endpoints:
        path = paths[endpoint["key"]]
        _need(len(path) >= 2 and tuple(path[0]) == endpoint["location"] and tuple(path[-1]) in positions,
              "Stored branch endpoints do not match")
        _need(preserve_geometry or _direction(path[0], path[1]) == endpoint["direction"], "Stored branch violates the pin exit direction")
        labels = list(labels_by_pin.get(endpoint["key"], []))
        labels += [label for request in requests for label in request["labels"]
                   if tuple(label.get("_pin", request["keys"][0])) == endpoint["key"]]
        node = positions.index(tuple(path[-1]))
        degrees[node] += 1
        wires.append({"endpoint": endpoint, "nodes": [node], "record": wire_record(path, labels)})
        all_paths.append(path)
    adjacency = [set() for _ in positions]
    for link in links:
        first, second, path = tuple(link["first"]), tuple(link["second"]), link["points"]
        _need(first in positions and second in positions and first != second, "Invalid junction bridge")
        _need(tuple(path[0]) == first and tuple(path[-1]) == second, "Bridge ends do not match junctions")
        nodes = [positions.index(first), positions.index(second)]
        _need(nodes[1] not in adjacency[nodes[0]], "Duplicate junction bridge")
        adjacency[nodes[0]].add(nodes[1]); adjacency[nodes[1]].add(nodes[0])
        for node in nodes:
            degrees[node] += 1
        wires.append({"nodes": nodes, "record": wire_record(path, link.get("labels", []))})
        all_paths.append(path)
    _need(all(2 <= degree <= 4 for degree in degrees), "Each native junction needs two to four wires")
    _need(len(links) == len(positions) - 1, "Junction layout must form a tree")
    reached, pending = set(), [0]
    while pending:
        node = pending.pop()
        if node not in reached:
            reached.add(node); pending.extend(adjacency[node] - reached)
    _need(len(reached) == len(positions), "Disconnected junction tree")
    _need(not any(_overlap(path, other) for index, path in enumerate(all_paths) for other in all_paths[:index]),
          "Junction wires overlap before a node; adjust component positions")
    return {"positions": positions, "wires": wires}


def encode_connected_body(objects, parts, templates, connections, body_offset):
    """Render fresh records, two-endpoint wires, and explicit multi-pin junctions."""
    _need(len(objects) == len(parts), "Component record count mismatch")
    by_ref = {part["ref"]: index for index, part in enumerate(parts)}
    _need(len(by_ref) == len(parts), "Duplicate component references")
    mutable = [bytearray(obj) for obj in objects]
    owned = [[] for _ in parts]
    endpoints_by_name = {}
    parents = {}
    requests = []

    def find(key):
        while parents[key] != key:
            key = parents[key]
        return key

    edges = []
    for connection in connections:
        endpoints = []
        for endpoint in (connection["first"], connection["second"]):
            ref, pin = endpoint
            _need(ref in by_ref, "Unknown component in connection")
            index = by_ref[ref]
            part = parts[index]
            template = part.get("template") or templates[part["device"]]
            names = [name for name, number in template["pins"]]
            _need(pin in names and pin in template["pin_offsets"], "Unknown pin geometry")
            dx, dy = template["pin_offsets"][pin]
            location = (part["x"] + dx, part["y"] + dy)
            pointer = len(objects[index]) - 4 * len(names) + 4 * names.index(pin)
            _need(struct.unpack_from("<I", objects[index], pointer)[0] == 0, "Requires isolated component records")
            key = (ref, pin)
            endpoints_by_name[key] = {"index": index, "pointer": pointer,
                                      "pin_order": names.index(pin), "location": location,
                                      "direction": template["pin_directions"][pin], "key": key}
            parents.setdefault(key, key)
            endpoints.append(key)
        _need(endpoints[0] != endpoints[1], "Cannot connect a pin to itself")
        parents[find(endpoints[0])] = find(endpoints[1])
        requests.append({"keys": endpoints, "points": connection.get("points"), "layout": connection.get("layout"),
                         "labels": connection.get("labels", []), "preserve_geometry": connection.get("preserve_geometry", False)})
    groups = {}
    for key, endpoint in endpoints_by_name.items():
        groups.setdefault(find(key), []).append(endpoint)
    junctions = []
    for root, endpoints in groups.items():
        group_requests = [request for request in requests if find(request["keys"][0]) == root]
        preserve_geometry = any(request["preserve_geometry"] for request in group_requests)
        if len(endpoints) > 2 or any(request["layout"] is not None for request in group_requests):
            junctions.append(_junction_net(endpoints, group_requests, preserve_geometry))
            continue
        _need(len(group_requests) == 1, "Duplicate connection")
        request = group_requests[0]
        endpoints = [endpoints_by_name[key] for key in request["keys"]]
        first, second = endpoints
        points = request["points"]
        if points is None:
            a, b = first["location"], second["location"]
            points = _route(a, b, first["direction"], second["direction"])
        _need(tuple(points[0]) == first["location"] and tuple(points[-1]) == second["location"],
              "Wire endpoints do not match pin locations")
        _need(preserve_geometry or (_direction(points[0], points[1]) == first["direction"] and
              _direction(points[-1], points[-2]) == second["direction"]), "Wire violates a pin exit direction")
        edge = {"endpoints": endpoints, "record": wire_record(points, request["labels"])}
        owner = max(endpoints, key=lambda endpoint: endpoint["index"])
        edge["owner_pin"] = owner["pin_order"]
        owned[owner["index"]].append(edge)
        edges.append(edge)
    offset = body_offset
    for index, obj in enumerate(objects):
        offset += len(obj)
        owned[index].sort(key=lambda edge: edge["owner_pin"])
        for edge in owned[index]:
            edge["offset"] = offset
            offset += len(edge["record"])
    for edge in edges:
        for endpoint in edge["endpoints"]:
            struct.pack_into("<I", mutable[endpoint["index"]], endpoint["pointer"], edge["offset"])
    junction_records = []
    for junction in junctions:
        owned_wires = [[] for _ in junction["positions"]]
        for wire in junction["wires"]:
            owned_wires[max(wire["nodes"])].append(wire)
        for wires in owned_wires:
            offset += 25
            for wire in wires:
                wire["offset"] = offset
                offset += len(wire["record"])
        for wire in junction["wires"]:
            if "endpoint" in wire:
                endpoint = wire["endpoint"]
                struct.pack_into("<I", mutable[endpoint["index"]], endpoint["pointer"], wire["offset"])
        for index, position in enumerate(junction["positions"]):
            references = [wire["offset"] for wire in junction["wires"] if index in wire["nodes"]]
            node = b"\x01" + struct.pack("<ii", *position)
            node += b"".join(struct.pack("<I", reference) for reference in references) + bytes(4 * (4 - len(references)))
            junction_records.append(node + b"".join(wire["record"] for wire in owned_wires[index]))
    # Keep the component flags untouched. Official passive R/C records own wires with flag 0.
    return (b"".join(bytes(obj) + b"".join(edge["record"] for edge in owned[index])
                     for index, obj in enumerate(mutable)) + b"".join(junction_records))


if __name__ == "__main__":
    from proteus_project import UnsupportedFormat

    for points in [[(0, 0), (100, 0), (100, 100), (0, 100), (0, 0)],
                   [(0, 0), (100, 0), (50, 0)]]:
        try:
            wire_record(points)
        except UnsupportedFormat:
            pass
        else:
            raise AssertionError("Closed/backtracking wire accepted")
    route = _route((-1270000, 2540000), (2540000, 0), (1, 0), (0, -1))
    assert _direction(route[0], route[1]) == (1, 0)
    assert _direction(route[-1], route[-2]) == (0, -1)
    assert _overlap([(0, 0), (10, 0)], [(5, 0), (15, 0)])
    assert not _overlap([(0, 0), (10, 0)], [(10, 0), (10, 10)])
    print("Wire record and pin-direction routing checks passed")
