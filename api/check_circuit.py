"""Real Proteus check: generate components/wires, export SDF, save and reopen."""
import hashlib
import json
from pathlib import Path
import uuid

from component_codec import SOURCE
from proteus_automatic_api import Circuit, Session


def nets(data):
    return {frozenset(f"{pin['ref']}.{pin['pin']}" for pin in net['pins']) for net in data['nets']}


def run():
    digest = hashlib.sha256(SOURCE.read_bytes()).hexdigest()
    output = Path(__file__).parent / "artifacts" / ("circuit-check-" + uuid.uuid4().hex[:8])
    output.mkdir()
    cases = [
        ("rc_chain", [
            ("RESISTOR", "R_INPUT10", "10k", -2540000, 2540000),
            ("CAPACITOR", "C_FILTER3", "100n", 2540000, 1016000),
            ("RESISTOR", "R_OUTPUT2", "4.7k", 7620000, 0),
            ("CAPACITOR", "C_SPARE4", "22u", 10160000, 2540000)],
         [("R_INPUT10.2", "C_FILTER3.1"), ("C_FILTER3.2", "R_OUTPUT2.1")],
         ["R_INPUT10.1", "R_INPUT10.2 C_FILTER3.1", "C_FILTER3.2 R_OUTPUT2.1",
          "R_OUTPUT2.2", "C_SPARE4.1", "C_SPARE4.2"]),
        ("parallel", [
            ("CAPACITOR", "C1", "47n", 0, 0),
            ("CAPACITOR", "C2", "2.2u", 2540000, 0)],
         [("C2.2", "C1.2"), ("C1.1", "C2.1")],
         ["C1.1 C2.1", "C1.2 C2.2"]),
        ("branch", [
            ("CAPACITOR", "C1", "100n", -2540000, 0),
            ("CAPACITOR", "C2", "100n", 0, 2540000),
            ("CAPACITOR", "C3", "100n", 2540000, 0)],
         [("C1.2", "C3.2"), ("C1.2", "C2.1")],
         ["C1.2 C3.2 C2.1", "C1.1", "C2.2", "C3.1"]),
        ("three_parallel", [
            ("CAPACITOR", "C1", "10n", -2540000, 0),
            ("CAPACITOR", "C2", "100n", 0, 0),
            ("CAPACITOR", "C3", "1u", 2540000, 0)],
         [("C1.2", "C2.2"), ("C2.2", "C3.2"),
          ("C1.1", "C3.1"), ("C2.1", "C1.1")],
         ["C1.2 C2.2 C3.2", "C1.1 C2.1 C3.1"]),
    ]
    results = []
    for name, parts, connections, expected in cases:
        circuit = Circuit()
        for part in parts:
            circuit.add_component(*part)
        for first, second in connections:
            circuit.connect(first, second)
        target = circuit.save(output / f"{name}.pdsprj", title=name)
        expected_nets = {frozenset(net.split()) for net in expected}
        expected_parts = {ref: (device, value) for device, ref, value, x, y in parts}
        for stage in ("generated", "reopened"):
            session = Session(target)
            try:
                actual = session.export_netlist(output / f"{name}_{stage}.sdf")
                assert {ref: (item['device'], item['value']) for ref, item in actual['parts'].items()} == expected_parts
                assert nets(actual) == expected_nets, (name, stage, nets(actual), expected_nets)
                if stage == "generated":
                    session.save()
                assert session.close() == 0
            finally:
                # Dispose only a process this check created, including failed-format probes.
                if session.process.poll() is None:
                    session.process.terminate()
                    session.process.wait(timeout=5)
        results.append(dict(case=name, project=str(target.resolve()), parts=len(parts),
                            nets=len(expected_nets), save_reopen=True, normal_exit=True))
        print(json.dumps(results[-1]), flush=True)
    assert hashlib.sha256(SOURCE.read_bytes()).hexdigest() == digest
    result = dict(cases=results, source_unchanged=True, computer_use_required=False,
                  electrical_behavior_validated=False)
    (output / "result.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(str((output / "result.json").resolve()), flush=True)
    return result


if __name__ == "__main__":
    run()
