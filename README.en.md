# proteus-automatic-api

[简体中文](README.md) | **English**

proteus-automatic-api is a Python library for creating, editing, and simulating Proteus circuits. It provides schematic file operations, netlist export, microcontroller firmware loading, button and switch control, and simulation result extraction. Simulations run in an installed copy of Proteus; the code does not depend on computer-use or screen coordinates.

**Version 0.2.0 · Developer preview.** Tested on Windows with Python 3.12 and Proteus 8.16 SP3 (8.16.36097).

## Requirements

- Windows and Python **3.10 or later**.
- Proteus and the device models required by your circuit. Tests currently use **8.16 SP3 (8.16.36097)**.
- The quick start requires the official Proteus `Rescap.pdsprj` sample. Microcontroller integration tests also require the official STM32 LED blink sample.
- The Python runtime uses only the standard library. Install Proteus, its device libraries, and the official samples separately.

## Installation

Use a fresh virtual environment to avoid conflicts with earlier distributions that share internal modules. The public import name is `proteus_automatic_api`.

Install the [wheel](dist/proteus_automatic_api-0.2.0-py3-none-any.whl) from PowerShell in the project root:

```powershell
py -3.12 -m pip install --no-index --no-deps '.\dist\proteus_automatic_api-0.2.0-py3-none-any.whl'
```

Alternatively, install from source:

```powershell
py -3.12 -m pip install .
```

The package name is `proteus-automatic-api`; the import name is `proteus_automatic_api`. This command should print `0.2.0`:

```powershell
py -3.12 -c "import proteus_automatic_api; print(proteus_automatic_api.__version__)"
```

Installing from source requires `setuptools>=68`. Installing the wheel does not require build tools.

## Configuration

The table lists the built-in paths and their corresponding parameters. Check your installation paths before use; the library does not detect them automatically.

| Purpose | Default path | Parameter |
|---|---|---|
| Proteus executable | `D:\Proteus\BIN\PDS.EXE` | `Session(project, executable=...)` |
| Device catalogue queries | `C:\ProgramData\program\LIBRARY` | `Library(directory=...)` |
| New project template | `C:\ProgramData\program\SAMPLES\Graph Based Simulation\Rescap.pdsprj` | `Circuit(template_project=...)` |

`Circuit.import_device(..., library=...)` still imports from the default device directory. Its `library` argument selects a library name, not a directory. Settings passed to `Library(directory=...)` do not apply to the importer. Initialization from an empty template may also fall back to the default sample. Custom paths therefore do not yet cover every operation.

## Quick start

Set `template` to the actual path of the official sample. This code creates a `.pdsprj` project containing a resistor, a capacitor, a wire, and a ground terminal.

```python
from proteus_automatic_api import Circuit

template = r"C:\ProgramData\program\SAMPLES\Graph Based Simulation\Rescap.pdsprj"
circuit = Circuit(template_project=template)
circuit.add("RESISTOR", "R1", "10k", 0, 0)
circuit.add("CAPACITOR", "C1", "100n", 2540000, 0)
circuit.connect("R1.2", "C1.2")
circuit.add_terminal("ground", "", "C1.1")
project = circuit.save("rc.pdsprj")
print(project)
```

The resulting `rc.pdsprj` is saved in the current directory and can be opened and edited in Proteus. This example creates a project; configure its power supplies, inputs, and models before running a simulation. Existing files are preserved by default. Pass `overwrite=True` explicitly to overwrite a file.

Continue in the same script to export a Proteus-compiled SDF netlist. Set `executable` to your installation path:

```python
from proteus_automatic_api import Session

session = Session(project, executable=r"D:\Proteus\BIN\PDS.EXE")
netlist = session.export_netlist("rc.sdf")
print(netlist["parts"])
session.save()
session.close()
```

The printed component table includes `R1` and `C1`, and the netlist is saved as `rc.sdf`. Each `Session` starts a separate Proteus process. After making changes, call `save()` to save them, then `close()` to close the process.

`Circuit(template_project=...)` creates an empty circuit using the template's device definitions. To edit an existing project, use **`Circuit.open(path)`**.

Coordinates use `100000 = 1 mm`, with the Y axis pointing upward. Component positions refer to the template origin; `pins(ref)` returns the actual pin coordinates.

## Features

| Feature | Interfaces and scope |
|---|---|
| Device queries and import | Query pins, models, and default properties with `Library.search/get`; import supported device definitions with `import_device` and `import_from_project` |
| Schematic editing | Add, copy, delete, and rename components; change properties, positions, and orientations; read supported project formats and save atomically |
| Wiring and nets | Connect and disconnect pins, route orthogonal wires, create multi-pin junctions and buses, and use six terminal types, net labels, and power rails |
| Proteus sessions | Open projects, export SDF, update and read back properties through ADI, and save and close projects |
| Microcontroller simulation | Check and load ELF / HEX firmware, start, pause, stop, reset, run for a specified duration, and read the actual simulation time |
| Buttons and switches | Prepare controls with `bind_controls`; operate binary inputs with `press`, `release`, and `set_switch`; read their positions with `controls` and `control_state` |
| Simulation results | Read STM32 CM3 GPIO log events, export voltage, current, and waveform CSV data from existing graphs, and interpolate sampled data |

See the [API guide](api/README.md) for complete examples.

## Buttons and switches

This example uses an existing simulation-ready project with a connected `BUTTON` named `SW1`. Bind the control, save the project, then open the saved file in a new `Session`:

```python
from proteus_automatic_api import Circuit, Session, Simulation

circuit = Circuit.open("button_circuit.pdsprj")
circuit.bind_controls("SW1")
project = circuit.save("button_bound.pdsprj")

session = Session(project)
sim = Simulation(session)
sim.run_for(0.01)
pressed = sim.press("SW1")
sim.run_for(0.1)
sim.release("SW1")
print(sim.control_state("SW1"))  # 0
print(pressed["command_time_seconds"])
sim.stop()
session.save()
session.close()
```

`press()` sets and holds input state 1; `release()` sets state 0. For switches, use `set_switch("SW1", True)` or `False`. Start the simulation before operating a control. During continuous simulation, an operation pauses, submits the change, then resumes. `controls()` and `control_state()` also pause briefly to read a consistent snapshot, then resume. An already paused simulation stays paused.

Supported devices are `BUTTON`, `SW-SPST`, `SW-SPST-MOM`, `SWITCH`, `LOGICSTATE`, and `LOGICTOGGLE`. Bindings use Proteus's native `INC` / `DEC` global actuator key slots. There are 10 slots, and each control uses two, allowing at most 5 controls. Existing bindings on other components reduce that capacity. After changing bindings, save and reopen the project.

`control_state()` returns a control's mechanical position or logic input state, not a downstream pin voltage. `command_time_seconds` is the paused simulation time at which the operation was submitted. Advance the simulation before checking circuit propagation or firmware response. Interaction requires the verified Proteus 8.16 fingerprints for `ISIS.DLL`, `NETLIST.DLL`, and `PRIMS.DLL`; other builds raise an error.

The [STM32 button example](api/examples/button_mcu) includes firmware source and build files for BUTTON → PA0 → firmware → PA5. Its [check script](api/check_button_mcu.py) prepares the circuit in a copy of the official sample and reads GPIO responses.

## Testing

These format parsing checks do not start Proteus:

```powershell
py -3.12 -B api/check_sdf.py
py -3.12 -B api/check_measurements.py --parser-only
py -3.12 -B api/check_control_bindings.py
```

After installing the library, run the integration test that calls Proteus:

```powershell
py -3.12 -I api/check_installed.py
```

The integration test requires the device library, the Rescap sample, and `SAMPLES\VSM for Cortex M3\STM32\STMCubeMX LED Blink\STMCubeMX LED Blink.pdsprj` under the default directories. It works on copies of the samples and saves results in `api/artifacts/installed-*/`. The `-I` flag ensures that the test imports the installed library.

The interaction checks cover the six supported controls and STM32 button input. Both start separate Proteus processes:

```powershell
py -3.12 -I api/check_interactions.py
py -3.12 -I api/check_button_mcu.py
```

All recorded results below come from the Windows / Proteus 8.16 environment described above:

| Test | Result record |
|---|---|
| Installation and integration (0.2.0) | Imported from `site-packages`, then completed project editing, property updates, firmware loading, GPIO reading, and analog measurement: [results](api/artifacts/installed-447ced83/result.json) |
| Microcontroller simulation | Continued simulation from 0 → 0.01 → 1.25 seconds, read PA5 high/low events, and tested reset, start, stop, and pause: [results](api/artifacts/simulation-check-a7ea5ae3/result.json) |
| Interactive controls (installed 0.2.0) | Six devices, key slots 0–9, collision and unknown-build rejection, run-state restoration, and timer roundoff handling: [results](api/artifacts/interactions-5e95f58e/result.json) |
| STM32 button input (installed 0.2.0) | BUTTON → PA0 → firmware → PA5, with output changing 0 → 1 → 0 on release, press, and release: [results](api/artifacts/button-mcu-aa66411e/result.json), [native log](api/artifacts/button-mcu-aa66411e/simulation.log) |
| Analog simulation | Changed the input amplitude from 1 to 2; after rerunning the simulation, voltage and current were approximately twice their original values: [results](api/artifacts/measurement-check-c048d7fb/result.json) |

## Limitations

- **Project formats:** Structural editing supports recognized single user sheets, CDB v7, and FILEVER 840/847. A full rewrite is rejected for unknown objects, multi-unit devices, and multi-sheet structures. A device appearing in catalogue queries does not mean its import and simulation are supported.
- **Layout and routing:** The library uses a limited set of orthogonal paths and bus layouts. Full obstacle-aware automatic layout is not available. Routing failures raise errors.
- **Analog inputs and measurements:** Graphs and probes must already exist in the project. Dedicated signal generator parameters can only be replaced with values of the same byte length. Creating arbitrary graphs or probes, or forcing arbitrary pin inputs at runtime, is not supported.
- **GPIO:** The current parser has been tested with STM32 CM3 logs. Other MCUs require corresponding parsers. Reading logs replaces the system clipboard contents.
- **Timed execution:** `run_for()` uses native timed breakpoints and normally returns `completion='timer_breakpoint'`. If the engine is still running with a positive remainder too small to advance its clock (`time + remaining == time`), it pauses only after reaching the target within tolerance. This returns `completion='timer_roundoff_pause'` and `timer_remaining_seconds`, and requires an additional verified VSMDEBUG.DLL fingerprint. Time reading requires the verified WINCORE.DLL. Some continuation runs have a 2.5 ms scheduling granularity; the default permits 3 ms overshoot. See the [simulation interface notes](api/simulation_research.md).
- **Compatibility:** Testing across different machines, Proteus builds, and long-running batch workloads is not yet complete.

## Documentation

The detailed documents below are currently in Simplified Chinese.

- [API guide](api/README.md): Examples for schematic editing, power supplies, firmware, buttons and switches, simulation, and waveforms.
- [Feature coverage and test results](api/IMPLEMENTATION.md): Test scope and result files for each feature.
- [Device library format](api/device_library_notes.md), [connectivity format](api/connectivity_notes.md), and [project file format](api/file_format_notes.md): File structures and implementation notes.

## License

The project's original code and documentation are available under the [MIT License](LICENSE).

Third-party projects, Proteus software, device models, and samples are excluded from this project's MIT license and remain subject to their respective licenses.
