"""Generate real electrical terminals; verify native SDF and save/reopen behavior."""
import copy
import json
import re
import struct
import uuid
from pathlib import Path
from zipfile import ZipFile

from component_codec import Circuit, _render_component
from net_objects import decode_net_objects, encode_net_body, power_rails_bytes, read_power_rails
from proteus_project import Project
from proteus_session import Session


def build(directory):
    circuit = Circuit()
    for ref, x in [("C1", -2540000), ("C2", 2540000), ("C3", 7620000), ("C4", 12700000)]:
        circuit.add("CAPACITOR", ref, "100n", x, 0)
    base = circuit.save(directory / "isolated.pdsprj")
    project = Project(base)
    dsn = project._dsn
    objects = [_render_component(part.get("template") or circuit.templates[part["device"]],
                                 part["ref"], part["value"], part["x"], part["y"], index)[0]
               for index, part in enumerate(circuit._parts, 1)]
    terminals = [
        {"kind": "ground", "pin": ("C1", "1")}, {"kind": "ground", "pin": ("C2", "1")},
        {"kind": "power", "name": "VCC", "pin": ("C1", "2")},
        {"kind": "power", "name": "VCC", "pin": ("C2", "2")},
        {"kind": "label", "name": "BUS", "pin": ("C3", "1")},
        {"kind": "input", "name": "BUS", "pin": ("C4", "1")},
        {"kind": "output", "name": "OUT", "pin": ("C3", "2")},
        {"kind": "bidir", "name": "OUT", "pin": ("C4", "2")},
    ]
    circuits = [match.start() for match in re.finditer(b"ISIS CIRCUIT FILE\x1a\0", dsn)]
    root, default = circuits
    start = dsn.index(b"OBJECT DATA\0", root) + 12
    body = encode_net_body(objects, circuit._parts, circuit.templates, [], start, dsn, terminals)
    delta = len(body) - (default - 1 - start)
    updated = bytearray(dsn[:start] + body + dsn[default - 1:])
    directory_pointer = root - 4
    struct.pack_into("<I", updated, directory_pointer, struct.unpack_from("<I", dsn, directory_pointer)[0] + delta)
    marker = b"\x02\0\0\0\x0b__DEFAULT__\0\0"
    pointer = updated.index(marker) + len(marker)
    struct.pack_into("<I", updated, pointer, default + delta)
    target = directory / "named_networks.pdsprj"
    rails = power_rails_bytes({"GND": 0, "VCC": 3.3, "VEE": -5}, {"VDD": "VCC", "VSS": "GND"})
    with ZipFile(base) as archive, ZipFile(target, "x") as output:
        for info in archive.infolist():
            output.writestr(copy.copy(info), updated if info.filename == "ROOT.DSN" else archive.read(info.filename))
        output.writestr("SCRIPTS/PWRRAILS.DAT", rails)
    decoded = decode_net_objects(target)
    assert decoded["complete"] and len(decoded["terminals"]) == 8
    assert {tuple(sorted(net["pins"])) for net in decoded["nets"]} == {
        (("C1", "1"), ("C2", "1")), (("C1", "2"), ("C2", "2")),
        (("C3", "1"), ("C4", "1")), (("C3", "2"), ("C4", "2"))}
    return target


def run():
    directory = Path(__file__).parent / "artifacts" / ("net-objects-" + uuid.uuid4().hex[:8])
    directory.mkdir()
    target = build(directory)
    results = []
    expected = {frozenset(("C1.1", "C2.1")), frozenset(("C1.2", "C2.2")),
                frozenset(("C3.1", "C4.1")), frozenset(("C3.2", "C4.2"))}
    for stage in ("generated", "reopened"):
        session = Session(target)
        try:
            netlist = session.export_netlist(directory / (stage + ".sdf"))
            actual = {frozenset(pin["ref"] + "." + pin["pin"] for pin in net["pins"]) for net in netlist["nets"]}
            assert actual == expected, (stage, netlist)
            if stage == "generated":
                session.save()
            assert session.close() == 0
        finally:
            if session.process.poll() is None:
                session.process.terminate()
                session.process.wait(timeout=5)
        results.append({"stage": stage, "nets": netlist["nets"]})
    decoded = decode_net_objects(target)
    assert decoded["complete"] and len(decoded["terminals"]) == 8
    with ZipFile(target) as archive:
        rails = read_power_rails(archive.read("SCRIPTS/PWRRAILS.DAT"))
    assert rails["bindings"] == {"VDD": "VCC", "VSS": "GND"}
    assert rails["rails"]["VCC"]["voltage"] == 3.3
    result = {"project": str(target.resolve()), "tests": results, "rails": rails,
              "terminal_kinds": sorted({terminal["kind"] for terminal in decoded["terminals"]}),
              "hidden_power_model_behavior_verified": False}
    (directory / "result.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(json.dumps(result, indent=2), flush=True)
    return result


if __name__ == "__main__":
    run()
