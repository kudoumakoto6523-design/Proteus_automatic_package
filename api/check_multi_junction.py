"""Six-capacitor buses, a power terminal, bridge labels, rename and move.

The initial unlabeled native proof is artifacts/six_cap_bus_result.json.
This regression adds a real label to a junction bridge and edits the result.
"""
import json
import uuid
from pathlib import Path

from component_codec import Circuit
from net_objects import decode_net_objects
from proteus_session import Session


def inspect(target, first_ref):
    decoded = decode_net_objects(target)
    assert decoded["rebuild_complete"], decoded
    assert len(decoded["nets"]) == 2
    assert sorted(len(net["pins"]) for net in decoded["nets"]) == [6, 6]
    assert all(2 <= len(node["wires"]) <= 4 for node in decoded["junctions"])
    names = {name for net in decoded["nets"] for name in net["names"]}
    assert names == {"VCC", "BUS_SENSE"}, names
    assert sum(len(wire["labels"]) for wire in decoded["wires"]) == 1
    assert {first_ref, "C2", "C3", "C4", "C5", "C6"} == {pin[0] for net in decoded["nets"] for pin in net["pins"]}
    return decoded


def run():
    directory = Path(__file__).parent / "artifacts" / ("multi-junction-" + uuid.uuid4().hex[:8])
    directory.mkdir()
    circuit = Circuit()
    for index in range(6):
        circuit.add("CAPACITOR", "C" + str(index + 1), "1u", index * 2540000, 0)
    for index in range(2, 7):
        circuit.connect("C1.1", "C" + str(index) + ".1")
        circuit.connect("C1.2", "C" + str(index) + ".2")
    circuit.add_terminal("power", "VCC", "C3.2", position=(5080000, 1016000))
    base = circuit.save(directory / "base.pdsprj")
    circuit = Circuit.open(base)
    layout = next(connection["layout"] for connection in circuit._connections
                  if connection["first"] == ("C1", "1") and connection.get("layout"))
    bridge = layout["links"][0]
    midpoint = tuple((a + b) // 2 for a, b in zip(bridge["first"], bridge["second"]))
    bridge["labels"] = [{"text": "BUS_SENSE", "position": midpoint}]
    target = circuit.save(directory / "bridge_label.pdsprj")
    inspect(target, "C1")
    results = []
    for stage in ("bridge_label", "rename_move"):
        if stage == "rename_move":
            circuit = Circuit.open(target)
            circuit.rename("C1", "CX1")
            circuit.move("CX1", -2540000, 0)
            target = circuit.save(directory / "renamed_moved.pdsprj")
            inspect(target, "CX1")
        session = Session(target)
        try:
            netlist = session.export_netlist(directory / (stage + ".sdf"))
            assert sorted(len(net["pins"]) for net in netlist["nets"]) == [6, 6], netlist
            terms = {(terminal["name"], terminal["kind"]) for net in netlist["nets"] for terminal in net["terminals"]}
            assert terms == {("VCC", "PT"), ("BUS_SENSE", "LBL")}, terms
            session.save()
            assert session.close() == 0
        finally:
            if session.process.poll() is None:
                session.process.terminate()
                session.process.wait(timeout=5)
        decoded = inspect(target, "C1" if stage == "bridge_label" else "CX1")
        results.append({"stage": stage, "path": str(target.resolve()), "nets": netlist["nets"],
                        "junction_degrees": [len(node["wires"]) for node in decoded["junctions"]]})
    destination = directory / "result.json"
    destination.write_text(json.dumps(results, indent=2), encoding="utf-8")
    print(destination.resolve(), flush=True)
    return results


if __name__ == "__main__":
    run()
