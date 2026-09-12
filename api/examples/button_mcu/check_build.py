"""Check that the ELF and HEX contain the same bounded, bootable ARM image."""
from pathlib import Path
import struct

root = Path(__file__).resolve().parent
elf = (root / "pa0_to_pa5.elf").read_bytes()
assert elf[:7] == b"\x7fELF\x01\x01\x01"
assert struct.unpack_from("<H", elf, 18)[0] == 40  # ARM
entry, phoff = struct.unpack_from("<II", elf, 24)
phsize, phcount = struct.unpack_from("<HH", elf, 42)
elf_image = {}
for index in range(phcount):
    kind, offset, _, physical, size, memory_size, _, _ = struct.unpack_from(
        "<8I", elf, phoff + index * phsize)
    if kind == 1:
        assert size == memory_size and 0x08000000 <= physical < physical + size <= 0x08001000
        elf_image.update((physical + i, value) for i, value in enumerate(elf[offset:offset + size]))

hex_image, base, ended = {}, 0, False
for line in (root / "pa0_to_pa5.hex").read_text(encoding="ascii").splitlines():
    assert line.startswith(":") and not ended
    record = bytes.fromhex(line[1:])
    assert len(record) == record[0] + 5 and sum(record) % 256 == 0
    kind, offset = record[3], int.from_bytes(record[1:3], "big")
    payload = record[4:-1]
    if kind == 0:
        hex_image.update((base + offset + i, value) for i, value in enumerate(payload))
    elif kind == 4:
        assert len(payload) == 2 and offset == 0
        base = int.from_bytes(payload, "big") << 16
    elif kind == 5:
        assert len(payload) == 4 and int.from_bytes(payload, "big") == entry
    elif kind == 1:
        assert not payload and offset == 0
        ended = True
    else:
        raise AssertionError(f"Unexpected HEX record {kind}")
assert ended and elf_image and elf_image == hex_image
stack, reset = struct.unpack("<II", bytes(elf_image[0x08000000 + i] for i in range(8)))
assert stack == 0x20001000 and reset == entry and reset & 1
assert (reset & ~1) in elf_image
print(f"ELF/HEX match: {len(elf_image)} bytes; SP={stack:#x}; reset={reset:#x}")
