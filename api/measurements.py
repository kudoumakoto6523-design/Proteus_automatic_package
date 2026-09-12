"""Native graph simulation and CSV measurements; no screenshot/OCR dependency.

Graphs/probes must already exist in the project. CSV column names are preserved,
including repeated names (for example gain and phase of the same AC trace).
"""
import bisect
import csv
import ctypes as c
import io
import math
from pathlib import Path
import re

from proteus_project import Project, UnsupportedFormat, _need, _text


def parse_graph_csv(text):
    rows = list(csv.reader(io.StringIO(text)))
    _need(rows and len(rows[0]) >= 2 and all(rows[0]), "Missing graph CSV column names")
    header, data = rows[0], []
    for row in rows[1:]:
        if not row:
            continue
        _need(len(row) == len(header), "Graph CSV row width mismatch")
        values = [float(value) for value in row]
        _need(all(math.isfinite(value) for value in values), "Non-finite graph sample")
        data.append(values)
    _need(data, "Graph has no samples")
    _need(all(a[0] <= b[0] for a, b in zip(data, data[1:])), "Graph axis is not ordered")
    return {"axis": {"name": header[0], "values": [row[0] for row in data]},
            "traces": [{"name": name, "column": index, "values": [row[index] for row in data]}
                       for index, name in enumerate(header[1:], 1)]}


def sample(data, trace, x, *, occurrence=0):
    """Interpolate a named trace on its exported axis; do not extrapolate."""
    traces = [item for item in data["traces"] if item["name"] == trace]
    _need(type(occurrence) is int and 0 <= occurrence < len(traces), "Unknown trace/occurrence")
    _need(isinstance(x, (int, float)) and math.isfinite(x), "Invalid sample position")
    axis, values = data["axis"]["values"], traces[occurrence]["values"]
    _need(axis[0] <= x <= axis[-1], "Sample outside exported range")
    index = bisect.bisect_right(axis, x) - 1
    if axis[index] == x or index == len(axis) - 1:
        return values[index]
    fraction = (x - axis[index]) / (axis[index + 1] - axis[index])
    return values[index] + fraction * (values[index + 1] - values[index])


def _graph_menu(session):
    import proteus_native as native
    window = session._wait(session._ready)
    menu = native.u.GetMenu(window["hwnd"])
    result = c.c_size_t()
    for index in range(native.u.GetMenuItemCount(menu)):
        label = c.create_unicode_buffer(128)
        native.u.GetMenuStringW(menu, index, label, len(label), 0x400)
        if label.value.replace("&", "") == "Graph":
            submenu = native.u.GetSubMenu(menu, index)
            # Proteus populates graph names and enabled state only on this native event.
            _need(native.u.SendMessageTimeoutW(window["hwnd"], 0x117, submenu, index,
                                              2, 3000, c.byref(result)), "Graph menu refresh failed")
            return native.menus(submenu, "Graph")
    raise UnsupportedFormat("This view has no Graph menu; open the schematic or a graph view")


def graphs(session):
    return [{"name": match.group(2), "index": int(match.group(1)), "menu": item["path"]}
            for item in _graph_menu(session)
            if (match := re.fullmatch(r"Graph/(\d+)\. (.+)", item["path"]))]


def export_graph(session, graph, destination, *, simulate=True, timeout=60):
    """Select an existing graph and export its actual numeric traces.

    simulate=True clears that graph's old results before invoking simulation.
    Save the session afterwards if the refreshed graph data should be retained.
    """
    import proteus_native as native
    destination = Path(destination).resolve()
    _need(destination.suffix.lower() == '.csv', 'Graph export requires a .csv destination')
    _need(not destination.exists(), "Measurement destination already exists")
    destination.parent.mkdir(parents=True, exist_ok=True)
    matches = [item for item in graphs(session) if item["name"] == graph or item["index"] == graph]
    _need(len(matches) == 1, "Missing or ambiguous graph name/index")
    chosen = matches[0]
    session.menu(chosen["menu"])
    session._wait(lambda: (row := session._ready()) and row["title"].endswith(" - " + chosen["name"]))
    def enabled(path):
        return any(item["path"] == path and item["enabled"] for item in _graph_menu(session))
    if simulate:
        if enabled("Graph/Clear Graph Data..."):
            session.menu("Graph/Clear Graph Data...")
            session._click(session._dialog("Graph Based Simulation"), "&Yes")
            session._wait(session._ready)
        session._wait(lambda: not enabled("Graph/Export Graph Data..."))
        hwnd = session._ready()["hwnd"]
        # The native graph's Space shortcut runs its simulator. The menu command
        # alone only arms a schematic action in this installation.
        _need(native.u.PostMessageW(hwnd, 0x100, 0x20, 0) and native.u.PostMessageW(hwnd, 0x101, 0x20, 0),
              "Could not invoke graph simulation")
        session._wait(lambda: enabled("Graph/Export Graph Data..."), timeout=timeout)
    _need(enabled("Graph/Export Graph Data..."), "Graph has no simulation results")
    session.menu("Graph/Export Graph Data...")
    session._file_dialog("Graph Data", destination)
    session._wait(lambda: destination.exists() and session._ready())
    result = parse_graph_csv(destination.read_text(encoding="cp1252"))
    result.update(graph=chosen["name"], path=str(destination), simulated=simulate)
    return result


def generators(project):
    """Read typed native generator labels and short property text records."""
    from net_objects import _text as typed_text
    project = project if isinstance(project, Project) else Project(project)
    dsn = project._dsn
    roots = [match.start() for match in re.finditer(b"ISIS CIRCUIT FILE\x1a\0", dsn)]
    _need(len(roots) == 2, "Generator inspection requires one root sheet")
    result = []
    for match in re.finditer(rb"\$[A-Z]+GEN\xff", dsn[roots[0]:roots[1]]):
        start, end = roots[0] + match.start(), roots[0] + match.end() - 1
        try:
            label, end = typed_text(dsn, end, "GENERATOR LABEL", 53)
            props, _ = _text(dsn, end, "PROPERTIES")
            values = dict(line.split("=", 1) for line in props["text"].splitlines() if line)
        except (ValueError, IndexError):
            continue
        result.append({"name": label["text"], "symbol": bytes(dsn[start:roots[0] + match.end() - 1]).decode("ascii"),
                       "properties": values, "span": props["span"], "text": props["text"]})
    return result


def set_generator_properties(source, destination, name, **properties):
    """Copy a project with same-width generator values; preserve unknown objects.

    This bounded adapter never shifts DSN offsets. Changes that need additional
    bytes are rejected, rather than risking an unrecognized generator layout.
    """
    project = Project(source)
    matches = [item for item in generators(project) if item["name"] == name]
    _need(len(matches) == 1 and properties, "Missing/ambiguous generator or no properties")
    item, text = matches[0], matches[0]["text"]
    for key, value in properties.items():
        _need(key in item["properties"], "Unknown generator property")
        value = str(value)
        _need(value and all(32 <= ord(char) < 127 and char != "=" for char in value), "Invalid generator value")
        old = item["properties"][key]
        _need(len(value) == len(old), "Generator edit currently requires the same byte length")
        pattern = re.compile(r"(?m)^" + re.escape(key) + "=" + re.escape(old) + r"$")
        text, count = pattern.subn(lambda _: key + "=" + value, text)
        _need(count == 1, "Ambiguous generator property")
    left, right = item["span"]
    _need(len(text) == right - left, "Generator property length changed")
    project._dsn[left:right] = text.encode("ascii")
    return project.save(destination)
