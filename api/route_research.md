# Proteus native-file generation: route research

Read-only source and local-help review, 2026-09-12. No downloaded generator or EXE was executed. The offsets below are observations encoded by ProGenEDA's **legacy, donor-specific generator**, not a complete Proteus format specification and not independently proven on the installed Proteus 8.16 SP3. No implementation source was copied into this workspace.

## Useful mechanism

The useful idea is to preserve an ordinary Proteus project container and known-good device definitions, then replace a small, understood set of instance records. It is not necessary to synthesize every byte of the format. The legacy implementation uses a clean empty project as its container/base and separate known-good schematic projects as record donors. It emits both ROOT.DSN (drawing/instance data) and ROOT.CDB (component database), then repacks the ZIP container. It carries the original PROJECT.XML and scripts, modifying version fields separately.

The checked legacy resistor implementation knows a specific object ordering: one-byte `00` prefix, an input-terminal array, an output-terminal array, a `00` separator, then resistor-plus-two-short-wire groups. Individual intermediate records have a trailing `00`; the final object chunk ends `FF`. This arrangement is a donor recipe, not a general claim that all DSNs have this ordering.

Sources: [resistor_v9.py](https://github.com/ProGenEDA/ProGenEDA-WEB/blob/main/temp/progenlive-legacy/progen_runtime/src/proteusgen/resistor_v9.py), [pdsprj.py](https://github.com/ProGenEDA/ProGenEDA-WEB/blob/main/temp/progenlive-legacy/progen_runtime/src/proteusgen/pdsprj.py).

## DSN section boundaries and pointer repair

The legacy source locates a first `ISIS CIRCUIT FILE`, its following `OBJECT DATA`, and a second `ISIS CIRCUIT FILE`. Its editable object chunk starts immediately after the first `OBJECT DATA` text and ends immediately before the second circuit-file marker.

When rebuilding, it preserves the empty base prefix through the last `{PACKAGE=NULL}\n\0` before the first circuit section; substitutes the donor's device-definition section and first circuit header; inserts its new object chunk; and retains the empty base's second circuit section/tail. It updates three address-like fields:

| Location used by the legacy source | Value written |
| --- | --- |
| Last four bytes of substituted device section | Second `OBJECT DATA` position plus 13 |
| Tail: two bytes after the six-byte `CCT000` marker | Absolute first `ISIS CIRCUIT FILE` position |
| Immediately after `__DEFAULT__\0\0` in the tail | Absolute second `ISIS CIRCUIT FILE` position |

All three are little-endian u32. Changing a variable-length record requires recalculating these values. Blind ASCII replacement that shifts later bytes without pointer repair is not a safe general editor. Source function: `build_dsn` in [resistor_v9.py](https://github.com/ProGenEDA/ProGenEDA-WEB/blob/main/temp/progenlive-legacy/progen_runtime/src/proteusgen/resistor_v9.py).

## Record facts worth testing against local samples

Offsets in this section are zero-based relative to the record start.

| Record from the donor recipe | Size / fields |
| --- | --- |
| Input terminal | 103 bytes when its label is two ASCII bytes. `$TERINPUT` begins 14 bytes after record start; start byte is `10`. Position at +1/+5 (i32); angle at +9 (u32); label length +30, bytes +31; text coordinates follow the label. |
| Output terminal | 104 bytes for a two-byte label. `$TEROUTPUT` also begins at +14; its additional marker byte shifts label length to +31 and label bytes to +32. |
| Bidirectional terminal | `$TERBIDIR` at +14; start `10`; total size `101 + label_length`; label length at +30. The converter accepts 1–255 ASCII bytes and donor angles 0 or 1800. |
| Terminal association | A u16 near the end, at record `-4:-2`, is copied into a matching component pin association. The following byte is an active-link flag in the source; final byte is the record terminator. |
| Resistor instance | 346 bytes for the specific two-character reference and visible-value donor. Ref length +1, ref bytes +2; visible value length +69, bytes +70. Instance position +312/+316 (i32); angle +320 (four bytes; tenths of a degree); instance ID +324 (u32). Two association u16 values at +337 and +341 match the terminal suffixes. Text positions also require translation: +4/+8, +72/+76, +149/+153, +235/+239. |
| Short wire | 50 bytes in this donor. Four endpoint i32s are +33/+37/+41/+45. The corresponding source-driven helper also locates them as `WIRE` marker +9/+13/+17/+21. |

The resistor code generates terminal suffixes from a u16 sequence and patches each suffix at both the terminal and the component's pin association. Therefore moving visible wire endpoints is insufficient to prove topology: matching IDs/associations must also be checked. The resistor generator constructs its component origin, pin endpoints, terminal symbol positions, terminal tips and text positions together, so the short wire actually meets the intended pin and terminal tip.

Two-character restrictions belong to this early fixed-size resistor patcher. They are not a demonstrated native-format limitation: the bidirectional-terminal converter already resizes the label and shifts its following text coordinates. Later IC code has both fixed-length and resizable label routes, with family-specific offset adjustments.

Sources: [resistor_v9.py](https://github.com/ProGenEDA/ProGenEDA-WEB/blob/main/temp/progenlive-legacy/progen_runtime/src/proteusgen/resistor_v9.py), [bidirectional.py](https://github.com/ProGenEDA/ProGenEDA-WEB/blob/main/temp/progenlive-legacy/progen_runtime/src/proteusgen/bidirectional.py), [source_driven.py](https://github.com/ProGenEDA/ProGenEDA-WEB/blob/main/temp/progenlive-legacy/progen_runtime/src/proteusgen/source_driven.py), [ic_combinational.py](https://github.com/ProGenEDA/ProGenEDA-WEB/blob/main/temp/progenlive-legacy/progen_runtime/src/proteusgen/ic_combinational.py).

## CDB and instance consistency

The early resistor builder emits ROOT.CDB directly rather than blindly concatenating donor bytes. Its first u32 is 7; its root/sheet scaffold contains `ROOT` and `Master Sheet`. It then emits a count and an instance/pin block for every component, followed later by another count and the component property blocks. The same sequential component index is used in DSN and CDB. The resistor pin block names pins `1` and `2`; property blocks contain reference, actual value, `RESISTOR`, an empty string, and primitive properties. String serialization in this builder is one-byte ASCII length followed by bytes. Property text uses a little-endian u32 size including the four-byte size field.

Not all integer fields are semantically named by the source. The above is enough to guide controlled byte comparisons, but not enough to extrapolate arbitrary MCU pin blocks or packages. The IC implementation separately builds package/unit mappings and combines device sections; its older package-ref mutation only accepts U1 through U9. Components with multiple schematic units and hidden supply pins need additional evidence.

## What the current portable bundle proves

The website's current executable receives `generate input.json --output output.pdsprj --allow-unterminalized`. It accepts placement controls plus named terminal projections. It rejects `connections`, `wires`, `nets` and `netlist` as direct routing requests. Its documented connected subset is 15 component families; the 200-case qualification reports generation and static ZIP inspection and explicitly says Proteus was not opened. This is useful evidence of a generation approach, not proof that the emitted circuits simulate correctly or that the legacy generator matches the current EXE.

Sources: [current integration](https://github.com/ProGenEDA/ProGenEDA-WEB/blob/main/docs/PROTEUS_INTEGRATION.md), [API invocation](https://github.com/ProGenEDA/ProGenEDA-WEB/blob/main/apps/api/src/services/proteus-executable-service.mjs#L186), [15-family catalogue](https://github.com/ProGenEDA/ProGenEDA-WEB/blob/main/packages/component-registry/registries/PR-A-terminalized-components.json), [200-case static report](https://github.com/ProGenEDA/ProGenEDA-WEB/blob/main/vendor/proteus/circuit_corpus_200/corpus_static_validation.json).

## Local official alternatives

The extracted local help does not expose a shorter, established GUI-free route to create native schematics:

- **ADI** is a property-assignment interpreter applied to every existing component. Its two commands are IF/END and DATA/END. The documented entry is invoking ADI and selecting a file. It can be useful for bulk values/properties after a schematic exists, but has no component-placement or wiring command in its reference.
- **EDIF2** is a real schematic import. The installed help explicitly scopes it to OrCAD 9.2/17.2 exports, then documents File / Import ECAD Files, parse, Continue, connectivity comparison, correction and Close. It creates the project through that workflow. The inspected reference does not document a command-line import entry.
- EDIF validation is attractive because Proteus regenerates a netlist from the imported schematic and compares it with the EDIF's declared netlist. A custom minimal EDIF emitter may ultimately reduce binary-format work, but it still needs a proven entry point, compatible symbol definitions and simulation-model binding; those are not established merely by the help page.

Local references:

- [ADI reference](C:/Users/Administrator/AppData/Local/Temp/proteus-interface-audit-20260912/ISIS/REPORT_GENERATION/ASCII_Data_Import.htm)
- [EDIF import](C:/Users/Administrator/AppData/Local/Temp/proteus-interface-audit-20260912/IMPORTER/EDIF/Importing_EDIF_Files.htm)
- [EDIF limitations](C:/Users/Administrator/AppData/Local/Temp/proteus-interface-audit-20260912/IMPORTER/EDIF/Limitations.htm)

## Smallest useful local experiment

Start with a local Proteus 8.16 sample that already contains the required device and a short connected wire; create a new copy, translate a verified complete instance group by a fixed delta, keep all record sizes unchanged, and preserve the original version metadata. First prove the copy opens, then prove a value change and the resulting connection netlist. Only after that should component duplication introduce new instance IDs and matching CDB entries. For a new family, obtain a local donor and verify its group boundaries instead of transplanting the legacy resistor offsets. This gives a practical path to a small Python API without first decoding the entire DSN format.
