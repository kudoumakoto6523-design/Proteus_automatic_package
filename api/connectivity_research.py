"""Read official Comb01 connectivity records; make a bounded wire-only experiment."""
import argparse
import copy
import hashlib
import json
import re
import struct
from pathlib import Path
from zipfile import ZipFile

SAMPLE = Path(r"C:\ProgramData\program\SAMPLES\Interactive Simulation\Animated Circuits\Comb01.pdsprj")


def wires(dsn):
    """Recognize observed inline wire definitions; this is not a complete DSN graph parser."""
    start = dsn.index(b"ISIS CIRCUIT FILE")
    found = []
    for match in re.finditer(rb"WIRE\x00", dsn[start:]):
        label = start + match.start()
        style = label - 25
        if struct.unpack_from("<I", dsn, style - 4)[0] != style:
            continue
        offset = next((style - n for n in (5, 9)
                       if style >= n and dsn[style - n] == 0x10
                       and (n == 5 or dsn[style - n + 1:style - 4] == bytes(4))), None)
        if offset is None:
            continue
        if dsn[label + 5:label + 7] != b"\x00\x00":
            continue
        count = struct.unpack_from("<H", dsn, label + 7)[0]
        end = label + 9 + count * 8
        if not 2 <= count <= 4096 or end > len(dsn):
            continue
        points = [struct.unpack_from("<ii", dsn, label + 9 + 8 * i) for i in range(count)]
        found.append({"offset": offset, "style_offset": style, "end": end, "points_offset": label + 9,
                      "point_count": count, "points": points})
    return found


def comb01_cdb_instances(cdb):
    # ponytail: single-root CDB v2 sample family only; parse the header before expanding scope.
    assert cdb[:21] == bytes.fromhex("0200000001000000010000000000000004524f4f54")
    assert cdb[21:80] == bytes.fromhex(
        "000000000001000000010000000200000002000000010000000100000000"
        "0a00000000000000030000000200000000000000000a00000000000000")
    pos = 80

    def u32():
        nonlocal pos
        value = struct.unpack_from("<I", cdb, pos)[0]
        pos += 4
        return value

    def lp8():
        nonlocal pos
        length = cdb[pos]
        pos += 1
        value = cdb[pos:pos + length].decode("ascii")
        pos += length
        return value

    result = []
    for _ in range(u32()):
        index, kind, dsn_object_id = u32(), u32(), u32()
        ref = lp8()
        pins = [{"name": lp8(), "second_string": lp8()} for _ in range(u32())]
        trailing = [u32(), u32(), u32()]
        assert kind == 2 and trailing == [0, index, 0xFFFFFFFF]
        result.append({"index": index, "dsn_object_id": dsn_object_id, "ref": ref, "pins": pins})
    return result


def reroute_demo(source, destination):
    """Connect A-INPUT's existing wire to U1.Q instead of U1.D0; retain length/CDB."""
    destination = Path(destination)
    assert destination.resolve() != source.resolve()
    if destination.exists():
        raise FileExistsError(destination)
    with ZipFile(source) as z:
        dsn = z.read("ROOT.DSN")
        records = wires(dsn)
        assert len(records) == 3
        a_wire = records[1]
        assert a_wire["points"] == [(-1143000, 2286000), (-2032000, 2286000),
                                    (-2032000, 2540000), (-2286000, 2540000)]
        points = [(1397000, 2032000), (1397000, 2540000),
                  (-2032000, 2540000), (-2286000, 2540000)]
        updated = bytearray(dsn)
        for i, point in enumerate(points):
            struct.pack_into("<ii", updated, a_wire["points_offset"] + i * 8, *point)
        destination.parent.mkdir(parents=True, exist_ok=True)
        with ZipFile(destination, "x") as out:
            for info in z.infolist():
                out.writestr(copy.copy(info), updated if info.filename == "ROOT.DSN" else z.read(info.filename))
        assert len(updated) == len(dsn)
        with ZipFile(destination) as check:
            assert check.testzip() is None
            assert check.read("ROOT.CDB") == z.read("ROOT.CDB")
            assert wires(check.read("ROOT.DSN"))[1]["points"] == points
    return {"destination": str(destination), "changed_dsn_bytes": sum(a != b for a, b in zip(dsn, updated)),
            "dsn_size_preserved": True, "cdb_unchanged": True,
            "expected_topology": "A-INPUT.Q0 -> U1.Q and Q-OUTPUT.Q0; U1.D0 disconnected",
            "application_verification": "pending"}


def add_wire_demo(source, destination):
    """Experimental append probe; native-open/netlist validation is required."""
    destination = Path(destination)
    assert destination.resolve() != source.resolve()
    if destination.exists():
        raise FileExistsError(destination)
    with ZipFile(source) as z:
        dsn = z.read("ROOT.DSN")
        records = wires(dsn)
        assert len(records) == 3 and len(dsn) == 14125
        circuits = [m.start() for m in re.finditer(b"ISIS CIRCUIT FILE", dsn)]
        assert circuits == [0x2118, 0x33C1] and dsn[circuits[1] - 1] == 0xFF
        insertion = circuits[1] - 1
        directory_pointer = circuits[0] - 4
        directory = struct.unpack_from("<I", dsn, directory_pointer)[0]
        assert directory == 0x33F1 and dsn[directory:directory + 2] == b"\x02\x00"
        donor = records[1]
        wire = bytearray(dsn[donor["offset"]:donor["end"]])
        struct.pack_into("<I", wire, 1, insertion + 5)
        points = [(-2286000, 2540000), (-2540000, 2540000),
                  (-2540000, 2032000), (2032000, 2032000)]
        for i, point in enumerate(points):
            struct.pack_into("<ii", wire, donor["points_offset"] - donor["offset"] + i * 8, *point)
        updated = bytearray(dsn[:insertion] + wire + dsn[insertion:])
        struct.pack_into("<I", updated, directory_pointer, directory + len(wire))
        index_marker = b"\x02\x00\x00\x00\x0b__DEFAULT__\x00\x00"
        assert updated.count(index_marker) == 1
        pointer = updated.index(index_marker) + len(index_marker)
        assert struct.unpack_from("<I", updated, pointer)[0] == circuits[1]
        struct.pack_into("<I", updated, pointer, circuits[1] + len(wire))
        destination.parent.mkdir(parents=True, exist_ok=True)
        with ZipFile(destination, "x") as out:
            for info in z.infolist():
                out.writestr(copy.copy(info), updated if info.filename == "ROOT.DSN" else z.read(info.filename))
        with ZipFile(destination) as check:
            assert check.testzip() is None
            assert check.read("ROOT.CDB") == z.read("ROOT.CDB")
            assert len(wires(check.read("ROOT.DSN"))) == 4
    return {"destination": str(destination), "inserted_bytes": len(wire), "cdb_unchanged": True,
            "expected_topology": "A-INPUT.Q0, U1.D0, U1.Q, Q-OUTPUT.Q0 share a net",
            "application_verification": "pending"}


def swap_inputs_demo(source, destination):
    """Native-validated Comb01 template operation; update pin references and geometry."""
    destination = Path(destination)
    assert destination.resolve() != source.resolve()
    if destination.exists():
        raise FileExistsError(destination)
    with ZipFile(source) as z:
        dsn = z.read("ROOT.DSN")
        if hashlib.sha256(dsn).hexdigest() != "9c31826e33182136aed42e173abcb1d520e077305d3754c07bc5b56f4c02be7e":
            raise ValueError("Unsupported DSN snapshot: this operation supports the official Comb01 template only")
        if hashlib.sha256(z.read("ROOT.CDB")).hexdigest() != "9f1806b0edbadc858f935e53ac21723cafdaf839901f546d4ce1390ad43ecaa7":
            raise ValueError("Unsupported CDB snapshot")
        records = wires(dsn)
        assert len(records) == 3
        assert struct.unpack_from("<III", dsn, 0x24BA) == (0x27D9, 0x298A, 0x2638)
        updated = bytearray(dsn)
        struct.pack_into("<II", updated, 0x24BA, 0x298A, 0x27D9)
        paths = [
            [(-1143000, 1778000), (-1778000, 1778000), (-1778000, 2540000), (-2286000, 2540000)],
            [(-1143000, 2286000), (-1524000, 2286000), (-1524000, 1524000), (-2286000, 1524000)],
        ]
        for wire, points in zip(records[1:], paths):
            for i, point in enumerate(points):
                struct.pack_into("<ii", updated, wire["points_offset"] + i * 8, *point)
        destination.parent.mkdir(parents=True, exist_ok=True)
        with ZipFile(destination, "x") as out:
            for info in z.infolist():
                out.writestr(copy.copy(info), updated if info.filename == "ROOT.DSN" else z.read(info.filename))
        with ZipFile(destination) as check:
            assert check.testzip() is None
            assert check.read("ROOT.CDB") == z.read("ROOT.CDB")
            assert len(check.read("ROOT.DSN")) == len(dsn)
    return {"destination": str(destination), "cdb_unchanged": True,
            "expected_topology": "U1.D0 -> B-INPUT.Q0; U1.D1 -> A-INPUT.Q0; Q output unchanged",
            "application_verification": "pending"}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=SAMPLE)
    parser.add_argument("--reroute-demo", type=Path)
    parser.add_argument("--add-wire-demo", type=Path)
    parser.add_argument("--swap-inputs-demo", type=Path)
    args = parser.parse_args()
    source_hash = hashlib.sha256(args.source.read_bytes()).hexdigest()
    with ZipFile(args.source) as z:
        dsn, cdb = z.read("ROOT.DSN"), z.read("ROOT.CDB")
    result = {"source": str(args.source), "sha256": source_hash,
              "wire_records": wires(dsn), "cdb_instances": comb01_cdb_instances(cdb)}
    if args.reroute_demo:
        result["experiment"] = reroute_demo(args.source, args.reroute_demo)
    if args.add_wire_demo:
        result["addition_experiment"] = add_wire_demo(args.source, args.add_wire_demo)
    if args.swap_inputs_demo:
        result["swap_experiment"] = swap_inputs_demo(args.source, args.swap_inputs_demo)
    assert hashlib.sha256(args.source.read_bytes()).hexdigest() == source_hash
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
