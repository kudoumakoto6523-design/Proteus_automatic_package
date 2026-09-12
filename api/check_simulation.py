"""Real native smoke check: own copy/PID, embedded ELF, GPIO and simulator time."""
import json
import argparse
from pathlib import Path
import uuid
import zipfile

from proteus_session import Session
from simulation import Simulation, extract_firmware, firmware_info, gpio_events, _seconds


def offline():
    """Small format-boundary checks; does not launch or control Proteus."""
    out = Path(__file__).parent / "artifacts" / f"simulation-offline-{uuid.uuid4().hex[:8]}"
    out.mkdir(parents=True)
    rejected = []
    def reject(name, action, error=ValueError):
        try:
            action()
        except error:
            rejected.append(name)
        else:
            raise AssertionError(f"Accepted invalid input: {name}")

    valid = out / "valid.hex"
    valid.write_text(":0400000001020304F2\n:00000001FF\n", encoding="ascii")
    assert firmware_info(valid)["format"] == "hex"
    for name, text in {
        "checksum": ":0400000001020304F3\n:00000001FF\n",
        "missing-eof": ":0400000001020304F2\n",
        "bad-eof": ":0100000100FE\n",
        "after-eof": ":00000001FF\n:00000001FF\n",
    }.items():
        path = out / f"{name}.hex"
        path.write_text(text, encoding="ascii")
        reject(name, lambda: firmware_info(path))
    header = bytearray(52)  # synthetic ELF32 header, never executed
    header[:7], header[18:20] = b"\x7fELF\x01\x01\x01", (40).to_bytes(2, "little")
    elf = out / "header.elf"
    elf.write_bytes(header)
    assert firmware_info(elf)["machine"] == 40
    for name, data in {"bad-magic": b"NOT ELF!", "truncated": bytes(header[:20]),
                       "bad-endian": bytes(header[:5] + b"\x03" + header[6:])}.items():
        path = out / f"{name}.elf"
        path.write_bytes(data)
        reject(name, lambda: firmware_info(path))
    reject("missing-file", lambda: firmware_info(out / "missing.elf"), FileNotFoundError)

    assert _seconds("0.004620500s") == .0046205
    assert _seconds("01:02:03.500000") == 3723.5
    for bad in ("nan", "-1s", "0.5ms", "00:00", "00:60:00", "00:00:60"):
        reject(f"time-{bad}", lambda: _seconds(bad))
    log = ("[GPIO] drive PIO0.5 = 0 [U1_SYSINTERFACE] @0.004620500s\n"
           "[GPIO] drive PIO0.5 = 1 [U1_SYSINTERFACE] @00:00:01.011087\n"
           "[GPIO] drive PIO1.2 = 0 [U2_SYSINTERFACE] @01:02:03.500000\n")
    events = gpio_events(log, ref="U1", port=0, pin=5)
    assert [event["seconds"] for event in events] == [.0046205, 1.011087]
    assert [event["value"] for event in events] == [0, 1]
    assert gpio_events(log, ref="U2")[0]["seconds"] == 3723.5
    assert gpio_events(log, pin=99) == [] and gpio_events("unrelated diagnostic") == []
    reject("gpio-invalid-time", lambda: gpio_events(log.replace("00:00:01.011087", "00:60:01.011087")))
    reject("gpio-invalid-unit", lambda: gpio_events(log.replace("0.004620500s", "0.004620500ms")))
    result = dict(mode="offline", rejected=rejected, gpio=events, native_started=False)
    (out / "result.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(json.dumps(dict(directory=str(out), results=result), indent=2))


def main():
    source = Path(r"C:\ProgramData\program\SAMPLES\VSM for Cortex M3\STM32\STMCubeMX LED Blink\STMCubeMX LED Blink.pdsprj")
    out = Path(__file__).parent / "artifacts" / f"simulation-check-{uuid.uuid4().hex[:8]}"
    out.mkdir(parents=True)
    project = out / "stm32_external.pdsprj"
    # External firmware avoids VSM Studio's automatic build/PROGRAM remapping.
    with zipfile.ZipFile(source) as original, zipfile.ZipFile(project, "w", zipfile.ZIP_DEFLATED) as copy:
        for name in original.namelist():
            if not name.startswith("FIRMWARE"):
                copy.writestr(name, original.read(name))
    firmware = extract_firmware(source, out / "blink.elf",
                                "FIRMWARE/STM32F103R6/Debug/Debug.elf")
    assert firmware["machine"] == 40
    session = Session(project)
    results = dict(pid=session.pid, firmware=firmware)
    try:
        sim = Simulation(session)
        results["configured"] = sim.set_firmware("U1", out / "blink.elf")
        session.set_properties("U1", TRACE_GPIO="3")
        session.save()
        results["initial"] = sim.run_for(.01)
        results["continued"] = sim.run_for(1.24)
        log = sim.log()
        (out / "simulation.log").write_text(log, encoding="utf-8")
        results["gpio"] = gpio_events(log, ref="U1", port=0, pin=5)
        assert {event["value"] for event in results["gpio"]} == {0, 1}
        assert any(event["seconds"] >= 1 for event in results["gpio"])
        results["reset"] = sim.reset()
        results["after_reset"] = sim.run_for(.01)
        assert results["after_reset"]["start_seconds"] == 0
        results["start"] = sim.start()
        results["pause"] = sim.pause()
        results["stop"] = sim.stop()
        assert results["start"]["state"] == "running"
        assert results["pause"]["state"] == "paused"
        assert results["stop"]["state"] == "stopped"
    finally:
        try:
            results["exit_code"] = session.close()
        except Exception as error:
            results["close_error"] = str(error)
            if session.process.poll() is None:
                session.process.terminate()
                session.process.wait(timeout=5)
        (out / "check.json").write_text(json.dumps(results, indent=2), encoding="utf-8")
    assert results.get("exit_code") == 0 and "close_error" not in results, results
    (out / "result.json").write_text(json.dumps(results, indent=2), encoding="utf-8")
    print(json.dumps(dict(directory=str(out), results=results), indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--offline", action="store_true", help="Only check file/log formats; do not launch Proteus")
    args = parser.parse_args()
    offline() if args.offline else main()
