"""Native save/reopen checks for real wire labels and isolated terminals."""
import copy
import json
import uuid
from pathlib import Path

from component_codec import Circuit
from net_objects import decode_net_objects
from proteus_session import Session


def run():
    directory = Path(__file__).parent / "artifacts" / ("label-roundtrip-" + uuid.uuid4().hex[:8])
    directory.mkdir()
    circuits = {}
    for name, count in (("binary", 2), ("star", 3)):
        circuit = Circuit()
        for index in range(count):
            circuit.add("CAPACITOR", "C" + str(index + 1), "1u", (index - 1) * 2540000, 0)
        for index in range(2, count + 1):
            circuit.connect("C1.1", "C" + str(index) + ".1")
        circuit._connections[0]["labels"] = [{"text": "REAL_LABEL", "position": (0, -1524000)}]
        circuits[name] = circuit
    isolated = Circuit()
    isolated.add_terminal("ground", "", position=(0, 0))
    isolated.add_terminal("power", "VCC", position=(2540000, 0))
    circuits["terminal_only"] = isolated
    results = {}
    for name, circuit in circuits.items():
        target = circuit.save(directory / (name + ".pdsprj"))
        stages = []
        for stage in ("generated", "rebuilt"):
            if stage == "rebuilt":
                circuit = Circuit.open(target)
                snapshot = copy.deepcopy(circuit._connections)
                circuit._validate(circuit._parts, circuit._connections)
                circuit._validate(circuit._parts, circuit._connections)
                assert circuit._connections == snapshot, "Validation mutated stored wire labels"
                target = circuit.save(directory / (name + "_rebuilt.pdsprj"))
            session = Session(target)
            try:
                netlist = session.export_netlist(directory / (name + "_" + stage + ".sdf"))
                session.save()
                assert session.close() == 0
            finally:
                if session.process.poll() is None:
                    session.process.terminate()
                    session.process.wait(timeout=5)
            decoded = decode_net_objects(target)
            assert decoded["rebuild_complete"], decoded
            if name == "terminal_only":
                assert not decoded["connections"] and len(decoded["semantic_terminals"]) == 2
                assert all(terminal["pin"] is None for terminal in decoded["semantic_terminals"])
            else:
                labels = [label for wire in decoded["wires"] for label in wire["labels"]]
                assert len(labels) == 1 and labels[0]["text"] == "REAL_LABEL", labels
                assert tuple(labels[0]["position"]) == (0, -1524000), labels
                count = 2 if name == "binary" else 3
                expected = {"C" + str(index) + ".1" for index in range(1, count + 1)}
                assert any({pin["ref"] + "." + pin["pin"] for pin in net["pins"]} == expected
                           and {(terminal["name"], terminal["kind"]) for terminal in net["terminals"]} == {("REAL_LABEL", "LBL")}
                           for net in netlist["nets"]), netlist
            stages.append({"stage": stage, "nets": netlist["nets"], "rebuild_complete": decoded["rebuild_complete"]})
        results[name] = stages
    destination = directory / "result.json"
    destination.write_text(json.dumps(results, indent=2), encoding="utf-8")
    print(destination.resolve(), flush=True)
    return results


if __name__ == "__main__":
    run()
