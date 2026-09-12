"""Small unified-library example: build, connect, copy, edit, save and read back."""
import atexit
import json
from pathlib import Path
import uuid

from proteus_api import Circuit, Session


def main():
    output = Path(__file__).parent / "artifacts" / ("demo-" + uuid.uuid4().hex[:8])
    project = output / "capacitor_copy.pdsprj"
    circuit = Circuit()
    circuit.add('RESISTOR', 'R1', '10k', 0, 0)
    circuit.add('CAPACITOR', 'C1', '1u', 2540000, 0)
    circuit.connect('R1.2', 'C1.2')
    circuit.copy_component('C1', 'C2', 7620000, 0)
    circuit.save(project)
    sessions = []
    def cleanup():
        for item in sessions:
            if item.process.poll() is None:
                item.process.terminate()
                item.process.wait(timeout=5)
    atexit.register(cleanup)
    session = Session(project)
    sessions.append(session)
    session.set_properties("C2", VALUE="4.7u")
    session.save()
    assert session.close() == 0
    session = Session(project)
    sessions.append(session)
    netlist = session.export_netlist(output / "verified.sdf")
    assert set(netlist["parts"]) == {"R1", "C1", "C2"}
    assert netlist["parts"]["C1"]["value"] == "1u" and netlist["parts"]["C2"]["value"] == "4.7u"
    c2_nets = [net for net in netlist["nets"] if any(pin["ref"] == "C2" for pin in net["pins"])]
    assert len(c2_nets) == 2 and all(len(net["pins"]) == 1 for net in c2_nets)
    assert session.close() == 0
    result = dict(project=str(project), netlist=str(output / "verified.sdf"),
                  verified=["add_connect_copy", "C2_value_4.7u", "save_reopen",
                            "two_real_isolated_pins", "normal_exit"],
                  electrical_behavior_validated=False)
    (output / "result.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(json.dumps(result), flush=True)


if __name__ == "__main__":
    main()
