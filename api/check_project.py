"""Run: py -3.12 api/check_project.py. No GUI and no third-party packages."""
import hashlib
import json
import tempfile
import zipfile
from pathlib import Path

from proteus_project import Project, UnsupportedFormat


SOURCE = Path(r"C:\ProgramData\program\SAMPLES\Graph Based Simulation\Rescap.pdsprj")


def members(path):
    with zipfile.ZipFile(path) as archive:
        assert archive.testzip() is None
        return {name: archive.read(name) for name in archive.namelist()}


def run():
    source_hash = hashlib.sha256(SOURCE.read_bytes()).hexdigest()
    before = members(SOURCE)
    p = Project(SOURCE)
    original = {c["ref"]: c for c in p.components()}
    assert set(original) == {"R1", "C1"} and original["C1"]["value"] == "1u"
    with tempfile.TemporaryDirectory(prefix="proteus-project-check-") as directory:
        directory = Path(directory)
        value_path = p.set_value("C1", "2u").save(directory / "value.pdsprj")
        modified = members(value_path)
        assert [c for c in Project(value_path).components() if c["ref"] == "C1"][0]["value"] == "2u"
        for name in before:
            assert len(before[name]) == len(modified[name])
            changed = sum(a != b for a, b in zip(before[name], modified[name]))
            assert changed == (1 if name in ("ROOT.DSN", "ROOT.CDB") else 0)
        try:
            p.set_value("C1", "10u")
        except UnsupportedFormat:
            pass
        else:
            raise AssertionError("Length-changing edit was accepted")
        p.set_value("C1", "1u").move("C1", 254000, -254000)
        moved = {c["ref"]: c for c in p.components()}
        assert moved["C1"]["position"] == {"x": 3048000, "y": 762000}
        assert moved["R1"] == original["R1"]
        try:
            p.move("C1", 2**31, 0)
        except UnsupportedFormat:
            pass
        else:
            raise AssertionError("Coordinate overflow was accepted")
        p.move("C1", -254000, 254000)
        restored = p.save(directory / "restored.pdsprj")
        assert members(restored) == before, "Round trip changed opaque bytes"
        try:
            p.save(SOURCE)
        except UnsupportedFormat:
            pass
        else:
            raise AssertionError("Source overwrite was accepted")
    assert hashlib.sha256(SOURCE.read_bytes()).hexdigest() == source_hash
    report = {"source": str(SOURCE), "source_sha256": source_hash,
              "checks": {"bounded_component_read": True, "dsn_cdb_value_sync": True,
                         "opaque_bytes_preserved": True, "length_change_rejected": True,
                         "move_readback": True, "overflow_rejected": True,
                         "exact_member_round_trip": True, "source_overwrite_rejected": True},
              "application_open_test": "Performed separately by the main task",
              "original_components": list(original.values())}
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return report


if __name__ == "__main__":
    run()
