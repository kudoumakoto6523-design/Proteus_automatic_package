"""File-level integration check; actual Proteus opening/netlists are checked separately."""
import hashlib
import copy
import json
import struct
import tempfile
from pathlib import Path
from zipfile import ZipFile

from component_codec import Circuit, SOURCE
from proteus_project import Project, UnsupportedFormat


def check():
    before = hashlib.sha256(SOURCE.read_bytes()).hexdigest()
    circuit = Circuit()
    circuit.add_component("RESISTOR", "R_INPUT10", "10k", -2540000, 2032000)
    circuit.add("CAPACITOR", "C_FILTER3", "100n", 2540000, 1016000)
    for args in (("RESISTOR", "R_INPUT10", "1k", 0, 0),
                 ("UNKNOWN", "U1", "test", 0, 0),
                 ("CAPACITOR", "C5", "1u", 2**31, 0),
                 ("CAPACITOR", "bad ref", "1u", 0, 0),
                 ("CAPACITOR", "C5", "1" * 255, 0, 0)):
        try:
            circuit.add(*args)
        except UnsupportedFormat:
            pass
        else:
            raise AssertionError(f"Invalid add accepted: {args}")
    with tempfile.TemporaryDirectory(prefix="proteus-components-") as directory:
        directory = Path(directory)
        path = circuit.save(directory / "first.pdsprj")
        parsed = Project(path)
        parts = {part["ref"]: part for part in parsed.components()}
        assert set(parts) == {"R_INPUT10", "C_FILTER3"}
        assert parts["R_INPUT10"]["value"] == "10k"
        assert parts["C_FILTER3"]["value"] == "100n"
        assert parts["C_FILTER3"]["position"] == {"x": 2540000, "y": 1016000}
        for item in parsed._components.values():
            start = item["xy_offset"] + 25
            assert parsed._dsn[start:start + 4 * item["pin_count"]] == b"\0" * (4 * item["pin_count"])
        second = circuit.save(directory / "second.pdsprj")
        with ZipFile(path) as a, ZipFile(second) as b:
            assert a.testzip() is None and b.testzip() is None
            assert "GRAPHS.DAT" not in a.namelist()
            assert {name: a.read(name) for name in a.namelist()} == {name: b.read(name) for name in b.namelist()}
        try:
            circuit.connect("R_INPUT10.1", "C_FILTER3.2", points=[(0, 0), (100, 0)])
        except UnsupportedFormat:
            pass
        else:
            raise AssertionError("Mismatched explicit wire endpoints were accepted")
        assert not circuit._connections
        circuit.connect("R_INPUT10.2", "C_FILTER3.1")
        for endpoints in (("R_INPUT10.2", "C_FILTER3.1"), ("R_INPUT10.2", "R_INPUT10.2"),
                          ("R_INPUT10.MISSING", "C_FILTER3.1"), ("R404.1", "C_FILTER3.1")):
            try:
                circuit.connect(*endpoints)
            except UnsupportedFormat:
                pass
            else:
                raise AssertionError("Invalid connection accepted")
        connected = Project(circuit.save(directory / "connected.pdsprj"))
        r = connected._components["R_INPUT10"]["xy_offset"]
        c = connected._components["C_FILTER3"]["xy_offset"]
        first = struct.unpack_from("<I", connected._dsn, r + 29)[0]
        second = struct.unpack_from("<I", connected._dsn, c + 25)[0]
        assert first == second > 0 and connected._dsn[first:first + 2] == b"\0\x1d"
        count = struct.unpack_from("<H", connected._dsn, first + 32)[0]
        assert struct.unpack_from("<ii", connected._dsn, first + 34) == (-1270000, 2032000)
        assert struct.unpack_from("<ii", connected._dsn, first + 34 + 8 * (count - 1)) == (2540000, 0)
        try:
            circuit.save(path)
        except UnsupportedFormat:
            pass
        else:
            raise AssertionError("Existing destination was overwritten")
        assert len(circuit._parts) == 2, "A rejected add partially changed state"
        assert len(circuit._connections) == 1, "A rejected connect partially changed state"
        branch = Circuit().add("CAPACITOR", "CA1", "1u", 0, 1016000)
        branch.add("CAPACITOR", "CB2", "1u", 2540000, 1016000)
        branch.add("CAPACITOR", "CC3", "1u", 5080000, 1016000)
        branch.connect("CA1.1", "CB2.1", points=[(0, 0), (0, -508000),
                                                 (2540000, -508000), (2540000, 0)])
        connections_before = copy.deepcopy(branch._connections)
        try:
            branch.connect("CB2.1", "CC3.1")
        except UnsupportedFormat:
            pass
        else:
            raise AssertionError("An explicit-path net was silently converted to a branch")
        assert branch._connections == connections_before, "Rejected graph change polluted state"
        assert len(Project(branch.save(directory / "after-rejected-branch.pdsprj")).components()) == 3
    assert hashlib.sha256(SOURCE.read_bytes()).hexdigest() == before
    result = {"checks": "passed", "source_unchanged": True,
              "variable_length_fields": True, "multiple_adds": True,
              "isolated_pin_references": True, "repeatable_member_bytes": True,
              "shared_wire_reference_and_automatic_endpoints": True,
              "candidate_graph_rejection_is_atomic": True,
              "invalid_inputs_rejected_without_mutation": True,
              "application_validation": "Separate Proteus open/SDF test"}
    print(json.dumps(result, indent=2))
    return result


if __name__ == "__main__":
    check()
