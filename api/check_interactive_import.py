"""Native roundtrip check for interactive devices and short property records."""
import json
from pathlib import Path
import uuid

from component_codec import Circuit
from proteus_project import Project
from proteus_session import Session


def run():
    output = Path(__file__).parent / 'artifacts' / ('interactive-format-' + uuid.uuid4().hex[:8])
    output.mkdir()
    circuit = Circuit()
    for device in ('LOGICSTATE', 'LOGICTOGGLE', 'SW-SPST', 'SW-SPST-MOM',
                   'SWITCH', 'BUTTON', 'LOGICPROBE'):
        circuit.import_device(device)
    circuit.add('LOGICSTATE', 'IN1', 'LOGICSTATE', -5080000, 0, properties={'STATE': '1'})
    circuit.add('SW-SPST', 'SW1', 'SW-SPST', 0, 0)
    circuit.add('LOGICPROBE', 'P1', 'LOGICPROBE', 5080000, 0)
    circuit.add('RESISTOR', 'R1', '10k', 5080000, -2540000)
    circuit.add('BUTTON', 'SW2', 'BUTTON', 0, 5080000)
    circuit.add('LOGICPROBE', 'P2', 'LOGICPROBE', 5080000, 5080000)
    circuit.add('RESISTOR', 'R2', '10k', 7620000, 2540000)
    circuit.connect('IN1.Q0', 'SW1.COM').connect('SW1.NO', 'P1.D0')
    circuit.connect('P1.D0', 'R1.1').add_terminal('ground', '', 'R1.2')
    circuit.connect('IN1.Q0', 'SW2.1').connect('SW2.2', 'P2.D0')
    circuit.connect('P2.D0', 'R2.1').add_terminal('ground', '', 'R2.2')
    for index, device in enumerate(('LOGICTOGGLE', 'SW-SPST-MOM', 'SWITCH')):
        circuit.add(device, f'T{index + 1}', device, index * 5080000, 10160000)
    circuit.add('LOGICPROBE', 'P50', 'LOGICPROBE', 0, 15240000, properties={'X': 'aaa'})
    circuit.add('LOGICPROBE', 'P51', 'LOGICPROBE', 5080000, 15240000, properties={'X': 'aaaa'})
    project = circuit.save(output / 'generated.pdsprj')
    parsed = Project(project)
    assert len(parsed._components['P50']['fields'][3]['text']) == 50
    assert len(parsed._components['P51']['fields'][3]['text']) == 51
    expected = {part['ref']: (part['device'], part['value']) for part in circuit.components()}
    stages, baseline = [], None

    def nets(data):
        return {frozenset((pin['ref'], pin['pin']) for pin in net['pins']) for net in data['nets']}

    for stage in ('generated', 'rebuilt'):
        session = Session.__new__(Session)
        try:
            Session.__init__(session, project)
            data = session.export_netlist(output / (stage + '.sdf'))
            assert {ref: (part['device'], part['value']) for ref, part in data['parts'].items()} == expected
            if baseline is None:
                baseline = nets(data)
            else:
                assert nets(data) == baseline
            assert frozenset({('SW1', 'NO'), ('P1', 'D0'), ('R1', '1')}) in baseline
            assert frozenset({('SW2', '2'), ('P2', 'D0'), ('R2', '1')}) in baseline
            session.save()
            assert session.close() == 0
            stages.append({'stage': stage, 'parts': len(expected), 'nets': len(baseline), 'normal_exit': True})
        finally:
            if hasattr(session, 'process') and session.process.poll() is None:
                session.process.terminate()
                session.process.wait(timeout=5)
        assert Project(project).components()
        if stage == 'generated':
            circuit = Circuit.open(project)
            project = circuit.save(output / 'rebuilt.pdsprj')
    result = {'stages': stages, 'short_property_lengths': [44, 50, 51],
              'native_netlists_preserved': True, 'runtime_interaction_tested': False}
    (output / 'result.json').write_text(json.dumps(result, indent=2), encoding='utf-8')
    print(json.dumps({'directory': str(output), 'result': result}), flush=True)
    return result


if __name__ == '__main__':
    run()
