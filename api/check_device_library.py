"""Verify installed LIB metadata and fresh multi-model projects in our own Proteus process."""
import hashlib
import json
import struct
import tempfile
import uuid
from pathlib import Path

from component_codec import Circuit, SOURCE
from device_library import Library, describe_definition
from proteus_project import Project
from proteus_project import UnsupportedFormat
from proteus_session import Session, parse_sdf


DEVICES = [("1N4007", "DIODE", "D1", "1N4007", 2),
           ("2N2222", "BIPOLAR", "Q1", "2N2222", 3),
           ("RELAY", "ACTIVE", "RL1", "12V", 5),
           ("LED-YELLOW", "ACTIVE", "LED1", "LED-YELLOW", 2),
           ("ULN2003A", "ANALOG", "U2", "ULN2003A", 16),
           ("ATMEGA328P", "AVR2", "U1", "ATMEGA328P", 29)]


def check_catalogue(library):
    for operation in (lambda: library.get("1N4007"),  # ambiguous across two installed libraries
                      lambda: library.get("NO_SUCH_DEVICE_894230"),
                      lambda: library.get("2N2222", "DIODE"),
                      lambda: library.get(""), lambda: library.get(None),
                      lambda: library.search("LED", limit=0), lambda: library.search(12)):
        try:
            operation()
        except UnsupportedFormat:
            pass
        else:
            raise AssertionError("Invalid catalogue request was accepted")
    with tempfile.TemporaryDirectory(prefix='proteus-lib-check-') as directory:
        directory = Path(directory)
        bad = directory / 'BAD.LIB'
        header = b'DEVICE LIBRARY\x1a\0' + struct.pack('<IIH', 400, 1, 1) + bytes(6)
        for data in (header, header + b'X\0' + bytes(62) + struct.pack('<IIII', 9999, 0, 0, 0)):
            bad.write_bytes(data)
            try:
                Library(directory)
            except UnsupportedFormat:
                pass
            else:
                raise AssertionError('Truncated/out-of-bounds LIB accepted')
        for operation in (lambda: Library(directory / 'missing'), lambda: describe_definition({}, b'\0')):
            try:
                operation()
            except UnsupportedFormat:
                pass
            else:
                raise AssertionError('Missing directory/truncated device accepted')
    return {"ambiguity_unknown_name_wrong_library_invalid_types": "rejected",
            "truncated_directory_bad_pointer_missing_directory_truncated_device": "rejected"}


def run():
    before = hashlib.sha256(SOURCE.read_bytes()).hexdigest()
    output = Path(__file__).parent / "artifacts" / ("device-import-" + uuid.uuid4().hex[:8])
    output.mkdir()
    library = Library()
    catalogue_checks = check_catalogue(library)
    assert library.search("ATMEGA328P")
    circuit = Circuit()
    metadata = []
    for index, (name, lib, ref, value, count) in enumerate(DEVICES):
        info = library.get(name, lib)
        assert info["pin_parse_status"] == "parsed" and len(info["pins"]) == count
        assert info["models"]
        circuit.import_device(name, lib)
        circuit.add_component(name, ref, value, (index % 3) * 7620000, (index // 3) * 10160000)
        metadata.append({"name": name, "library": info["path"], "pins": count,
                         "models": info["models"], "package": info["package"]})
    project = circuit.save(output / "device_models.pdsprj")
    assert len(Project(project).components()) == len(DEVICES)
    expected = {ref: (name, value) for name, _, ref, value, _ in DEVICES}
    expected_endpoints = {f"{ref}.{name}" for _, _, ref, _, _ in DEVICES
                          for name, _ in circuit.templates[expected[ref][0]]["pins"]}
    stages = []
    for stage in ("generated", "reopened"):
        session = Session.__new__(Session)
        try:
            Session.__init__(session, project)
            netlist = session.export_netlist(output / f"{stage}.sdf")
            assert {ref: (part["device"], part["value"]) for ref, part in netlist["parts"].items()} == expected
            actual_endpoints = {f"{pin['ref']}.{pin['pin']}" for net in netlist["nets"]
                                for pin in net["pins"] if pin.get("pin") is not None}
            assert actual_endpoints == expected_endpoints, (actual_endpoints ^ expected_endpoints)
            if stage == "generated":
                session.save()
            assert session.close() == 0
            stages.append({"stage": stage, "parts": len(expected), "pins": len(actual_endpoints),
                           "nets": len(netlist["nets"]), "normal_exit": True})
            print(json.dumps(stages[-1]), flush=True)
        finally:
            if hasattr(session, "process") and session.process.poll() is None:
                session.process.terminate()
                session.process.wait(timeout=5)
    assert hashlib.sha256(SOURCE.read_bytes()).hexdigest() == before
    result = {"project": str(project), "catalogue_entries": len(library._entries),
              "metadata": metadata, "native_checks": stages, "source_unchanged": True,
              "catalogue_checks": catalogue_checks,
              "simulation_behavior_tested": False}
    (output / "result.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(output.resolve(), flush=True)
    return result


def edit_existing(source, baseline_sdf):
    source = Path(source).resolve()
    before = hashlib.sha256(source.read_bytes()).hexdigest()
    baseline = parse_sdf(Path(baseline_sdf).read_text(encoding="utf-8-sig"))
    def nets(data):
        return {frozenset((pin["ref"], pin["pin"]) for pin in net["pins"]
                          if pin.get("pin") is not None) for net in data["nets"]}
    expected_nets = nets(baseline)
    circuit = Circuit.open(source)
    original_display = {ref: item['fields'][3].get('display_text', item['fields'][3]['text'])
                        for ref, item in circuit._project._components.items()}
    original = {part["ref"]: part for part in circuit.components()}
    assert len(original) == 6 and original["U1"]["device"] == "ATMEGA328P"
    circuit.update("RL1", value="5V")
    position = original["U1"]["position"]
    circuit.move("U1", position["x"] + 508000, position["y"] + 254000)
    output = Path(__file__).parent / "artifacts" / ("device-edit-" + uuid.uuid4().hex[:8])
    output.mkdir()
    project = circuit.save(output / "edited_models.pdsprj")
    edited_project = Project(project)
    assert {ref: item['fields'][3].get('display_text', item['fields'][3]['text'])
            for ref, item in edited_project._components.items()} == original_display
    expected = {part["ref"]: (part["device"], part["value"]) for part in circuit.components()}
    stages = []
    for stage in ("edited", "reopened"):
        session = Session.__new__(Session)
        try:
            Session.__init__(session, project)
            actual = session.export_netlist(output / f"{stage}.sdf")
            assert {ref: (part["device"], part["value"]) for ref, part in actual["parts"].items()} == expected
            assert nets(actual) == expected_nets
            if stage == "edited":
                session.save()
            assert session.close() == 0
            stages.append({"stage": stage, "parts": 6, "nets_preserved": True, "normal_exit": True})
            print(json.dumps(stages[-1]), flush=True)
        finally:
            if hasattr(session, "process") and session.process.poll() is None:
                session.process.terminate()
                session.process.wait(timeout=5)
    reopened = Circuit.open(project)
    loaded = {part["ref"]: part for part in reopened.components()}
    assert loaded["U1"]["position"] == {"x": position["x"] + 508000, "y": position["y"] + 254000}
    assert loaded["RL1"]["value"] == "5V"
    assert {ref: item['fields'][3].get('display_text', item['fields'][3]['text'])
            for ref, item in reopened._project._components.items()} == original_display
    assert hashlib.sha256(source.read_bytes()).hexdigest() == before
    result = {"source": str(source), "project": str(project), "stages": stages,
              "value_and_position_after_native_reopen": True, "source_unchanged": True,
              "visible_properties_unchanged": True}
    (output / "result.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(output.resolve(), flush=True)
    return result


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--edit-existing", type=Path)
    parser.add_argument("--baseline-sdf", type=Path)
    args = parser.parse_args()
    if args.edit_existing:
        parser.error("--baseline-sdf is required with --edit-existing") if args.baseline_sdf is None else None
        edit_existing(args.edit_existing, args.baseline_sdf)
    else:
        run()
