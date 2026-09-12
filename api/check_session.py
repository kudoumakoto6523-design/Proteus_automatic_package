"""Live check: py -3.12 api/check_session.py (opens only new copies of official samples)."""
import hashlib
import atexit
import json
from pathlib import Path
import shutil
import uuid

from proteus_session import Session, parse_sdf


def run():
    sessions = []
    def cleanup():
        for session in sessions:
            if session.process.poll() is None:
                session.process.terminate()
                session.process.wait(timeout=5)
    atexit.register(cleanup)
    source = Path(r"C:\ProgramData\program\SAMPLES\Graph Based Simulation\Rescap.pdsprj")
    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    output = Path(__file__).parent / "artifacts" / ("session-check-" + uuid.uuid4().hex[:8])
    output.mkdir()
    target = output / "rescap_api.pdsprj"
    shutil.copy2(source, target)
    original = parse_sdf((Path(__file__).parent / "artifacts/comb01_baseline.SDF").read_text())
    assert len(original["parts"]) == 4 and len(original["nets"]) == 3
    for tail in ["-1", "1\n#n,-1", "0\n#extra,0"]:
        try:
            parse_sdf("ISIS SCHEMATIC DESCRIPTION FORMAT 8.0\n*PARTLIST,0\n*NETLIST," + tail)
        except ValueError:
            pass
        else:
            raise AssertionError("Malformed SDF accepted")
    session = Session(target)
    sessions.append(session)
    before = session.netlist()
    assert before["parts"]["C1"]["value"] == "1u"
    changed = session.set_properties("C1", VALUE="4.7u", API_NOTE="comma, quoted value")
    assert changed["value"] == "4.7u" and changed["properties"]["API_NOTE"] == "comma, quoted value"
    try:
        session.set_properties("MISSING999", VALUE="1")
    except KeyError:
        pass
    else:
        raise AssertionError("Missing component accepted")
    session.save()
    assert session.close() == 0
    reopened = Session(target)
    sessions.append(reopened)
    after = reopened.export_netlist(output / "readback.sdf")
    assert after["parts"]["C1"]["value"] == "4.7u"
    assert after["parts"]["C1"]["properties"]["API_NOTE"] == "comma, quoted value"
    assert before["nets"] == after["nets"], "Property change modified connectivity"
    assert reopened.close() == 0
    assert hashlib.sha256(source.read_bytes()).hexdigest() == digest
    result = dict(project=str(target), netlist=str(output / "readback.sdf"),
                  checks=["native_sdf_read", "malformed_sdf_rejected", "variable_length_value_write", "quoted_comma_property",
                          "missing_ref_rejected", "save_and_reopen_readback", "connectivity_preserved",
                          "source_unchanged", "owned_processes_closed"],
                  simulation_measurements_validated=False)
    (output / "result.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(json.dumps(result), flush=True)


if __name__ == "__main__":
    run()
