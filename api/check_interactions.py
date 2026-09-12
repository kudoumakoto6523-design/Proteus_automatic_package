"""Native electrical checks for binary controls; use -I to test the wheel."""
import json
from pathlib import Path
import uuid

import proteus_api as p
from interactive_native import snapshot


def run():
    out = Path(__file__).parent / 'artifacts' / ('interactions-' + uuid.uuid4().hex[:8])
    out.mkdir()
    result = dict(version=p.__version__, imported_from=p.__file__, stages=[])
    sessions = []

    def expect_error(error, action):
        try:
            action()
        except error:
            return
        raise AssertionError(f'Expected {error.__name__}')

    def states(session, names):
        rows = {row['reference']: row for row in snapshot(session)['components']}
        return {name: rows[name]['state'] for name in names}

    try:
        for group, devices in enumerate((('BUTTON', 'SW-SPST', 'SW-SPST-MOM'),
                                        ('SWITCH', 'LOGICTOGGLE', 'LOGICSTATE'))):
            circuit = p.Circuit()
            for device in set(devices) | {'LOGICSTATE', 'LOGICPROBE'}:
                circuit.import_device(device)
            refs, probes = [], []
            for index, device in enumerate(devices, 1):
                ref, probe, y = f'SW{index}', f'P{index}', index * 7620000
                refs.append(ref)
                probes.append(probe)
                circuit.add(device, ref, device, 0, y, properties={'STATE': '0'})
                circuit.add('LOGICPROBE', probe, 'LOGICPROBE', 5080000, y)
                if device in ('LOGICSTATE', 'LOGICTOGGLE'):
                    circuit.connect(ref + '.Q0', probe + '.D0')
                else:
                    source, resistor = f'IN{index}', f'R{index}'
                    circuit.add('LOGICSTATE', source, 'LOGICSTATE', -5080000, y, properties={'STATE': '1'})
                    circuit.add('RESISTOR', resistor, '10k', 7620000, y - 2540000)
                    pins = ('1', '2') if device == 'BUTTON' else ('COM', 'NO')
                    circuit.connect(source + '.Q0', ref + '.' + pins[0])
                    circuit.connect(ref + '.' + pins[1], probe + '.D0')
                    circuit.connect(probe + '.D0', resistor + '.1')
                    circuit.add_terminal('ground', '', resistor + '.2')
            if group == 1:
                circuit.add('RESISTOR', 'R98', '10k', 10160000, 0,
                            properties={'INC': '0', 'DEC': '1', 'KEY': '2'})
                circuit.add('RESISTOR', 'R99', '10k', 12700000, 0, properties={'KEY': '3'})
            circuit.bind_controls(*refs)
            project = circuit.save(out / f'controls-{group}.pdsprj')
            session = p.Session.__new__(p.Session)
            sessions.append(session)
            p.Session.__init__(session, project)
            sim = p.Simulation(session)
            if group == 0:
                from interactive_native import _HASHES
                expected_hash = _HASHES['ISIS.DLL']
                try:
                    _HASHES['ISIS.DLL'] = '0' * 64
                    expect_error(RuntimeError, sim.controls)
                finally:
                    _HASHES['ISIS.DLL'] = expected_hash
                result['unknown_build_rejected'] = True
            expect_error(RuntimeError, lambda: sim.press('SW1'))
            stage = dict(devices=list(devices), initial_run=sim.run_for(.01), changes=[])
            result['stages'].append(stage)
            assert all(value == 0 for value in states(session, probes).values())
            controls = {row['ref']: row for row in sim.controls()}
            assert all(controls[ref]['bound'] for ref in refs)
            expect_error(KeyError, lambda: sim.press('MISSING'))
            expect_error(NotImplementedError, lambda: sim.press('P1'))
            expect_error(ValueError, lambda: sim.set_switch('SW1', 1))
            expect_error(RuntimeError, lambda: sim.press('IN1'))
            for index, ref in enumerate(refs):
                pressed = sim.press(ref)
                assert pressed['changed'] and sim.control_state(ref) == 1
                assert not sim.press(ref)['changed']
                print(json.dumps({'group': group, 'press': ref, 'command': pressed}), flush=True)
                after = sim.run_for(.01, timeout=15)
                expected = {probe: int(j <= index) for j, probe in enumerate(probes)}
                assert states(session, probes) == expected
                stage['changes'].append(dict(command=pressed, after=after, probes=expected))
            for index, ref in enumerate(refs):
                released = sim.set_switch(ref, False) if index == 1 else sim.release(ref)
                assert released['changed'] and sim.control_state(ref) == 0
                assert not sim.release(ref)['changed']
                print(json.dumps({'group': group, 'release': ref, 'command': released}), flush=True)
                after = sim.run_for(.01, timeout=15)
                expected = {probe: int(j > index) for j, probe in enumerate(probes)}
                assert states(session, probes) == expected
                stage['changes'].append(dict(command=released, after=after, probes=expected))
            sim.start()
            expect_error(KeyError, lambda: sim.press('MISSING'))
            assert sim.status()['state'] == 'running'
            pressed = sim.press('SW1')
            assert sim.status()['state'] == 'running'
            assert sim.control_state('SW1') == 1 and sim.status()['state'] == 'running'
            sim.pause()
            assert sim.control_state('SW1') == 1
            stage['running_input_propagation'] = sim.run_for(.01)
            assert states(session, ['P1']) == {'P1': 1}
            sim.release('SW1')
            sim.stop()
            session.save()
            assert session.close() == 0
            stage['running_state_preserved'] = True

            if group == 0:
                # Native keys are global: reject collisions before sending input.
                circuit.set_properties('SW2', INC='0')
                project = circuit.save(out / 'collision.pdsprj')
                session = p.Session.__new__(p.Session)
                sessions.append(session)
                p.Session.__init__(session, project)
                sim = p.Simulation(session)
                sim.run_for(.01)
                expect_error(RuntimeError, lambda: sim.press('SW1'))
                assert sim.control_state('SW1') == sim.control_state('SW2') == 0
                sim.stop()
                assert session.close() == 0
                result['collision_rejected_without_input'] = True
        result['all_processes_closed'] = True
        result['timer_roundoff_checked'] = any(
            change['after']['completion'] == 'timer_roundoff_pause'
            for stage in result['stages'] for change in stage['changes'])
        assert result['timer_roundoff_checked'], 'The native double timer boundary was not exercised'
        (out / 'result.json').write_text(json.dumps(result, indent=2), encoding='utf-8')
        print(json.dumps({'directory': str(out), 'version': p.__version__, 'passed': True}), flush=True)
    except Exception as error:
        result['error'] = repr(error)
        if 'sim' in locals():
            result['last_status'] = sim.status()
        (out / 'failure.json').write_text(json.dumps(result, indent=2), encoding='utf-8')
        print(json.dumps({'directory': str(out), 'error': repr(error)}), flush=True)
        raise
    finally:
        for session in sessions:
            if hasattr(session, 'process') and session.process.poll() is None:
                session.process.terminate()
                session.process.wait(timeout=5)


if __name__ == '__main__':
    run()
