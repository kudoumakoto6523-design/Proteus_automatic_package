"""Offline layout check: py -3.12 api/check_layout.py (or -I for the installed wheel)."""
import copy
import hashlib
import json
import struct
import tempfile
from pathlib import Path
from zipfile import ZipFile

from proteus_automatic_api import Circuit, Project, UnsupportedFormat


def check():
    circuit = Circuit().add("RESISTOR", "R1", "10k", 0, 0)
    circuit.add("CAPACITOR", "C1", "100n", 2540000, 0).connect("R1.2", "C1.2")
    digest = hashlib.sha256(circuit.source.read_bytes()).hexdigest()
    circuit.update("C1", rotation=90, properties={"API_NOTE": "label layout"})
    pins, nets = circuit.pins("C1"), circuit.nets()
    flags = [tail[8:] for tail in circuit._template(circuit._parts[1])["text_tails"]]
    offsets = {"reference": (762000, 508000), "value": [762000, 127000],
               "device": (762000, -254000), "properties": (762000, -635000)}
    expected = copy.deepcopy(offsets)
    circuit.update("C1", label_offsets=offsets)
    offsets["value"][0] = 0
    assert circuit._parts[1]["label_offsets"] == expected, "Caller mutated stored layout"
    assert circuit.pins("C1") == pins and circuit.nets() == nets
    assert [tail[8:] for tail in circuit._template(circuit._parts[1])["text_tails"]] == flags

    for invalid in (None, [], {"unknown": (0, 0)}, {"value": None}, {"value": (0,)},
                    {"value": (True, 0)}, {"value": (1.0, 0)}, {"value": (2**31, 0)},
                    {"value": (-2**31 - 1, 0)}, {"value": (2**31 - 1, 0)}):
        before = copy.deepcopy((circuit._parts, circuit._connections, circuit._terminals))
        try:
            circuit.update("C1", value="220n", label_offsets=invalid)
        except UnsupportedFormat:
            pass
        else:
            raise AssertionError(f"Invalid label offset accepted: {invalid!r}")
        assert (circuit._parts, circuit._connections, circuit._terminals) == before

    with tempfile.TemporaryDirectory(prefix="proteus-layout-") as directory:
        path = circuit.save(Path(directory) / "labels.pdsprj")
        for _ in range(2):
            parsed = Project(path)
            item = parsed._components["C1"]
            x, y = struct.unpack_from("<ii", parsed._dsn, item["xy_offset"])
            for index, name in enumerate(expected):
                pos = item["fields"][index]["xy_offset"]
                dx, dy = expected[name]
                assert struct.unpack_from("<ii", parsed._dsn, pos) == (x + dx, y + dy)
                assert bytes(parsed._dsn[pos + 8:pos + 8 + len(flags[index])]) == flags[index]
            reopened = Circuit.open(path)
            assert reopened.pins("C1") == pins and reopened.nets() == nets
            assert next(p for p in reopened.components() if p["ref"] == "C1")["properties"]["API_NOTE"] == "label layout"
            path = reopened.save(Path(directory) / "roundtrip.pdsprj", overwrite=True)
        firmware_path = Path(directory) / "with-firmware.pdsprj"
        firmware_path.write_bytes(path.read_bytes())
        firmware = {"FIRMWARE/project.vsmproj": b"fixture project",
                    "FIRMWARE/src/main.c": b"fixture source"}
        with ZipFile(firmware_path, "a") as archive:
            for name, contents in firmware.items():
                archive.writestr(name, contents)
        blank = Circuit(firmware_path).save(Path(directory) / "fresh.pdsprj")
        with ZipFile(blank) as archive:
            assert not any(name.startswith("FIRMWARE") for name in archive.namelist())
        edited = Circuit.open(firmware_path).save(Path(directory) / "edited.pdsprj")
        with ZipFile(edited) as archive:
            assert {name: archive.read(name) for name in firmware} == firmware
    assert hashlib.sha256(circuit.source.read_bytes()).hexdigest() == digest
    result = dict(checks="passed", layout_roundtrip=True, flags_preserved=True,
                  properties_preserved=True, pins_and_nets_unchanged=True,
                  invalid_updates_atomic=True, source_unchanged=True,
                  fresh_project_drops_firmware=True, edited_project_preserves_firmware=True,
                  native_visual_validation="Performed separately")
    print(json.dumps(result, indent=2))
    return result


if __name__ == "__main__":
    check()
