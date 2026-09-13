"""Run with the installed interpreter's -I flag, so source files cannot mask missing wheel modules."""
import json
from importlib.metadata import distribution
from pathlib import Path
import uuid
from zipfile import ZipFile, ZIP_DEFLATED

import proteus_automatic_api as p


def run():
    assert 'site-packages' in p.__file__, 'Run with python -I after installing the wheel'
    package = distribution('proteus-automatic-api')
    assert package.metadata['Name'] == 'proteus-automatic-api'
    assert package.version == p.__version__
    assert Path(package.locate_file('proteus_automatic_api.py')).resolve() == Path(p.__file__).resolve()
    out = Path(__file__).parent / 'artifacts' / ('installed-' + uuid.uuid4().hex[:8])
    out.mkdir()
    sessions, result = [], {'version': p.__version__, 'imported_from': p.__file__}
    try:
        assert p.Library().get('ATMEGA328P')['pins']
        c = p.Circuit().add('RESISTOR', 'R1', '10k', 0, 0)
        c.add('CAPACITOR', 'C1', '100n', 2540000, 0).connect('R1.2', 'C1.2')
        c.add_terminal('ground', '', 'C1.1')
        project = c.save(out / 'built.pdsprj')
        c = p.Circuit.open(project).rename('C1', 'C_FILTER2').update('C_FILTER2', value='220n')
        c.move('R1', -2540000, 0).save(project, overwrite=True)
        s = p.Session(project)
        sessions.append(s)
        actual = s.export_netlist(out / 'built.sdf')
        assert set(actual['parts']) == {'R1', 'C_FILTER2'}
        assert actual['parts']['C_FILTER2']['value'] == '220n'
        s.set_properties('C_FILTER2', VALUE='470n')
        s.save()
        assert s.close() == 0
        assert {item['ref']: item for item in p.Circuit.open(project).components()}['C_FILTER2']['value'] == '470n'
        result['circuit_and_native_properties'] = True

        source = Path(r'C:\ProgramData\program\SAMPLES\VSM for Cortex M3\STM32\STMCubeMX LED Blink\STMCubeMX LED Blink.pdsprj')
        project = out / 'stm32.pdsprj'
        with ZipFile(source) as original, ZipFile(project, 'w', ZIP_DEFLATED) as copied:
            for name in original.namelist():
                if not name.startswith('FIRMWARE'):
                    copied.writestr(name, original.read(name))
        firmware = out / 'blink.elf'
        p.extract_firmware(source, firmware, 'FIRMWARE/STM32F103R6/Debug/Debug.elf')
        s = p.Session(project)
        sessions.append(s)
        sim = p.Simulation(s)
        sim.set_firmware('U1', firmware)
        s.set_properties('U1', TRACE_GPIO='3')
        s.save()
        result['simulation'] = sim.run_for(.01)
        result['gpio'] = sim.gpio_events(ref='U1', port=0, pin=5)
        assert {item['value'] for item in result['gpio']} == {0, 1}
        sim.stop()
        assert s.close() == 0

        source = Path(r'C:\ProgramData\program\SAMPLES\Graph Based Simulation\Rescap.pdsprj')
        project = p.set_generator_properties(source, out / 'analogue.pdsprj', 'INPUT', AMP='2')
        s = p.Session(project)
        sessions.append(s)
        data = p.export_graph(s, 'ANALOGUE ANALYSIS', out / 'waveforms.csv')
        result['measurement'] = {name: p.sample_graph(data, name, .00025) for name in ('INPUT', 'R1(2)', 'ICAP')}
        assert 1.99 < result['measurement']['INPUT'] < 2.01
        assert 0.0049 < result['measurement']['ICAP'] < 0.0052
        s.save()
        assert s.close() == 0
        result['all_processes_closed'] = True
        (out / 'result.json').write_text(json.dumps(result, indent=2), encoding='utf-8')
        print(json.dumps({'directory': str(out), **result}), flush=True)
    finally:
        for s in sessions:
            if s.process.poll() is None:
                s.process.terminate()
                s.process.wait(timeout=5)


if __name__ == '__main__':
    run()
