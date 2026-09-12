"""Add a native DC source with a variable-length value; reject unsupported pulse instances."""
import copy
import json
from pathlib import Path
import uuid

from component_codec import Circuit
from proteus_session import Session
from proteus_project import UnsupportedFormat


def run():
    output = Path(__file__).parent / 'artifacts' / ('source-devices-' + uuid.uuid4().hex[:8])
    output.mkdir()
    circuit = Circuit()
    circuit.import_device('VSOURCE', 'ASIMMDLS')
    before = copy.deepcopy((circuit.templates, circuit._dsn, circuit._members, circuit._parts))
    try:
        circuit.import_device('VPULSE', 'ASIMMDLS')
    except UnsupportedFormat:
        pass
    else:
        raise AssertionError('Unsupported VPULSE native instance accepted')
    assert (circuit.templates, circuit._dsn, circuit._members, circuit._parts) == before
    circuit.add_component('VSOURCE', 'V_DC1', '1V', 0, 0)
    circuit.update('V_DC1', value='12.345V')
    project = circuit.save(output / 'voltage_sources.pdsprj')
    results = []
    for stage in ('generated', 'reopened'):
        session = Session.__new__(Session)
        try:
            Session.__init__(session, project)
            data = session.export_netlist(output / (stage + '.sdf'))
            assert data['parts']['V_DC1']['value'] == '12.345V'
            assert {frozenset(pin['pin'] for pin in net['pins']) for net in data['nets']} == {
                frozenset({'+'}), frozenset({'-'})}
            if stage == 'generated':
                session.save()
            assert session.close() == 0
            results.append({'stage': stage, 'parts': data['parts'], 'normal_exit': True})
        finally:
            if hasattr(session, 'process') and session.process.poll() is None:
                session.process.terminate()
                session.process.wait(timeout=5)
    assert Circuit.open(project).components()[0]['value'] == '12.345V'
    result = {'project': str(project), 'results': results, 'variable_length_source_parameters': True,
              'waveform_behavior_tested': False, 'unsupported_vpulse_rejected_atomically': True}
    (output / 'result.json').write_text(json.dumps(result, indent=2), encoding='utf-8')
    print(json.dumps(result), flush=True)
    return result


if __name__ == '__main__':
    run()
