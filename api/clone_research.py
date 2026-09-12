"""Experimental isolated C1 -> C2 clone in the exact official Rescap fixture.

Do not infer general project support. Run this to make a new file, then verify
its component/pin nets through Proteus. No GUI or third-party dependencies.
"""
import argparse
import copy
import hashlib
import json
import struct
from pathlib import Path
from zipfile import ZipFile

from proteus_project import Project, _lp


SOURCE = Path(r"C:\ProgramData\program\SAMPLES\Graph Based Simulation\Rescap.pdsprj")
SOURCE_SHA256 = "b7efbd2fdbca1966de4ca7bb445e5207ae851056f3821f1d10e7057d5b792a59"


def clone_experiment(destination):
    assert hashlib.sha256(SOURCE.read_bytes()).hexdigest() == SOURCE_SHA256
    destination = Path(destination).resolve()
    assert destination != SOURCE.resolve() and not destination.exists()
    project = Project(SOURCE)
    item = project._components["C1"]
    with ZipFile(SOURCE) as archive:
        dsn = archive.read("ROOT.DSN")
        cdb = archive.read("ROOT.CDB")
        # ponytail: this one fixture has two short-ASCII component refs and two pins.
        # General cloning requires parsing every object reference and record version.
        root, default = 72561, 76111
        insertion = default - 1
        assert dsn[root:root + 19] == b"ISIS CIRCUIT FILE\x1a\0"
        assert dsn[insertion:default + 19] == b"\xffISIS CIRCUIT FILE\x1a\0"
        assert struct.unpack_from("<H", dsn, root + 19)[0] == 14
        directory_pointer = root - 4
        directory = struct.unpack_from("<I", dsn, directory_pointer)[0]
        assert directory == 332337 and dsn[directory:directory + 2] == b"\x02\0"

        start = item["dsn_record_start"] - 1
        pos = item["xy_offset"]
        assert start == 73044 and pos == 73363 and item["pin_count"] == 2
        assert dsn[start] == 0 and dsn[pos + 20:pos + 25] == b"\0" * 5
        clone = bytearray(dsn[start:pos + 33])
        assert len(clone) == 352
        ref_start, ref_end = item["fields"][0]["span"]
        clone[ref_start - start:ref_end - start] = b"C2"
        dx, dy = 1524000, 0
        for offset in [pos] + [field["xy_offset"] for field in item["fields"]]:
            x, y = struct.unpack_from("<ii", dsn, offset)
            struct.pack_into("<ii", clone, offset - start, x + dx, y + dy)
        struct.pack_into("<I", clone, pos - start + 12, 14)
        # Verified on the default specimen: zero references give C2 two isolated native pins.
        struct.pack_into("<II", clone, pos - start + 25, 0, 0)
        new_dsn = bytearray(dsn[:insertion] + clone + dsn[insertion:])
        struct.pack_into("<H", new_dsn, root + 19, 15)
        struct.pack_into("<I", new_dsn, directory_pointer, directory + len(clone))
        marker = b"\x02\0\0\0\x0b__DEFAULT__\0\0"
        assert new_dsn.count(marker) == 1
        default_pointer = new_dsn.index(marker) + len(marker)
        assert struct.unpack_from("<I", new_dsn, default_pointer)[0] == default
        struct.pack_into("<I", new_dsn, default_pointer, default + len(clone))

        # Decode the entity list to locate its boundary; CDB indices differ from DSN IDs.
        assert cdb[:8] == struct.pack("<II", 7, 1)
        entity_count = struct.unpack_from("<I", cdb, 80)[0]
        assert entity_count == 6
        p = 84
        donor_entity = None
        for _ in range(entity_count):
            begin = p
            index, kind, zero, dsn_id = struct.unpack_from("<4I", cdb, p)
            p += 16
            ref, span, p = _lp(cdb, p)
            pin_count = struct.unpack_from("<I", cdb, p)[0]
            p += 4
            for _ in range(pin_count):
                _, _, p = _lp(cdb, p)
                _, _, p = _lp(cdb, p)
            footer = p
            a, property_index, b = struct.unpack_from("<3I", cdb, p)
            p += 12
            assert kind == 2 and zero == 0 and a == 0 and b == 0xFFFFFFFF
            if ref == "C1":
                assert index == dsn_id == property_index == 2 and pin_count == 2
                donor_entity = bytearray(cdb[begin:p])
                struct.pack_into("<I", donor_entity, 0, 7)
                struct.pack_into("<I", donor_entity, 12, 14)
                donor_entity[span[0] - begin:span[1] - begin] = b"C2"
                struct.pack_into("<I", donor_entity, footer - begin + 4, 3)
        assert p == 334 and donor_entity is not None
        assert cdb[p:p + 14] == bytes.fromhex("0100000001000000000001000000")
        property_count_offset = p + 14
        assert struct.unpack_from("<I", cdb, property_count_offset)[0] == 2
        prop_start = item["cdb_offset"]
        assert prop_start == 427 and cdb[-4:] == b"\0" * 4
        clone_properties = bytearray(cdb[prop_start:-4])
        struct.pack_into("<I", clone_properties, 0, 3)
        assert clone_properties[20:23] == b"\x02C1"
        clone_properties[21:23] = b"C2"
        new_cdb = bytearray(cdb[:p] + donor_entity + cdb[p:-4] + clone_properties + cdb[-4:])
        struct.pack_into("<I", new_cdb, 80, 7)
        struct.pack_into("<I", new_cdb, property_count_offset + len(donor_entity), 3)

        destination.parent.mkdir(parents=True, exist_ok=True)
        with ZipFile(destination, "x") as output:
            for info in archive.infolist():
                data = new_dsn if info.filename == "ROOT.DSN" else new_cdb if info.filename == "ROOT.CDB" else archive.read(info)
                output.writestr(copy.copy(info), data)
        with ZipFile(destination) as check:
            assert check.testzip() is None
            assert check.read("PROJECT.XML") == archive.read("PROJECT.XML")
        parsed = Project(destination).components()
        by_ref = {component["ref"]: component for component in parsed}
        assert set(by_ref) == {"R1", "C1", "C2"}
        assert by_ref["C2"]["value"] == "1u"
        assert by_ref["C2"]["position"] == {"x": 4318000, "y": 1016000}
        assert by_ref["C2"]["instance_id"] == 14
        assert hashlib.sha256(SOURCE.read_bytes()).hexdigest() == SOURCE_SHA256
        return {"source": str(SOURCE), "destination": str(destination),
                "dsn_inserted_bytes": len(clone), "cdb_inserted_bytes": len(new_cdb) - len(cdb),
                "source_unchanged": True, "components": parsed,
                "application_open_and_netlist_verification": "not run by generator; default specimen verified in artifacts/clone-and-wire-v2-live.json",
                "expected_connection": "C2 pins 1 and 2 each isolated from the original nets"}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("destination", type=Path)
    args = parser.parse_args()
    print(json.dumps(clone_experiment(args.destination), ensure_ascii=False, indent=2))
