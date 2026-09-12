"""Native regression: extend, terminal, rename, move, detach and delete an existing net."""
import json
from pathlib import Path
import uuid

from proteus_api import Circuit, Session


def run():
    output = Path(__file__).parent / 'artifacts' / ('topology-edits-' + uuid.uuid4().hex[:8])
    output.mkdir()
    stages = []

    def verify(circuit, name, expected, terminal_count=0):
        project = circuit.save(output / (name + '.pdsprj'))
        expected_nets = {frozenset(net.split()) for net in expected}
        expected_parts = {part['ref']: (part['device'], part['value']) for part in circuit.components()}
        assert {frozenset(ref + '.' + pin for ref, pin in net) for net in circuit.nets()} == expected_nets
        for step in ('generated', 'reopened'):
            session = Session.__new__(Session)
            try:
                Session.__init__(session, project)
                actual = session.export_netlist(output / f'{name}-{step}.sdf')
                assert {ref: (part['device'], part['value']) for ref, part in actual['parts'].items()} == expected_parts
                actual_nets = {frozenset(pin['ref'] + '.' + pin['pin'] for pin in net['pins']
                                        if pin['ref'] in expected_parts and pin.get('pin') is not None)
                               for net in actual['nets']}
                actual_nets.discard(frozenset())
                assert actual_nets == expected_nets, (name, step, actual_nets, expected_nets)
                if step == 'generated':
                    session.save()
                assert session.close() == 0
            finally:
                if hasattr(session, 'process') and session.process.poll() is None:
                    session.process.terminate()
                    session.process.wait(timeout=5)
        restored = Circuit.open(project)
        assert len(restored.terminals()) == terminal_count
        stages.append({'name': name, 'project': str(project), 'parts': len(expected_parts),
                       'component_nets': len(expected_nets), 'terminals': terminal_count,
                       'native_sdf_save_reopen': True})
        print(json.dumps(stages[-1]), flush=True)
        return restored

    circuit = Circuit()
    for ref, x in [('C1', -2540000), ('C2', 0), ('C3', 2540000)]:
        circuit.add_component('CAPACITOR', ref, '100n', x, 0)
    circuit.connect('C1.2', 'C2.2')
    circuit = verify(circuit, '01_existing_two_pin', ['C1.2 C2.2', 'C3.2', 'C1.1', 'C2.1', 'C3.1'])
    assert circuit.connections()[0]['points']  # the regression requires native cached geometry

    circuit.connect('C2.2', 'C3.2')
    circuit = verify(circuit, '02_extend_third_pin', ['C1.2 C2.2 C3.2', 'C1.1', 'C2.1', 'C3.1'])
    terminal_id = circuit.add_terminal('output', 'BUS', 'C2.2', position=(0, 1016000))
    circuit = verify(circuit, '03_add_terminal', ['C1.2 C2.2 C3.2', 'C1.1', 'C2.1', 'C3.1'], 1)
    # Readback may select a different hub component for the same physical net.
    terminal_id = circuit.terminals()[0]['id']
    circuit.update_terminal(terminal_id, pin='C2.2')
    circuit.rename('C2', 'C_MAIN2')
    circuit = verify(circuit, '04_rename', ['C1.2 C_MAIN2.2 C3.2', 'C1.1', 'C_MAIN2.1', 'C3.1'], 1)
    circuit.move('C_MAIN2', 0, 254000)
    circuit = verify(circuit, '05_move', ['C1.2 C_MAIN2.2 C3.2', 'C1.1', 'C_MAIN2.1', 'C3.1'], 1)
    terminal_id = circuit.terminals()[0]['id']
    circuit.update_terminal(terminal_id, pin='C_MAIN2.2')
    contact = circuit.terminals()[0]['position']
    circuit.disconnect('C_MAIN2.2')
    assert circuit.terminals()[0]['pin'] in (('C1', '2'), ('C3', '2')) and circuit.terminals()[0]['position'] == contact
    circuit = verify(circuit, '06_detach_pin_preserve_shared_terminal',
                     ['C1.2 C3.2', 'C_MAIN2.2', 'C1.1', 'C_MAIN2.1', 'C3.1'], 1)
    circuit.delete_component('C1')
    circuit = verify(circuit, '07_delete_component', ['C3.2', 'C_MAIN2.2', 'C_MAIN2.1', 'C3.1'], 1)
    assert circuit.terminals()[0]['pin'] == ('C3', '2')
    circuit.update_terminal(circuit.terminals()[0]['id'], pin=None)
    circuit = verify(circuit, '08_explicit_terminal_disconnect', ['C3.2', 'C_MAIN2.2', 'C_MAIN2.1', 'C3.1'], 1)
    assert circuit.terminals()[0]['pin'] is None and circuit.terminals()[0]['position'] == contact
    result = {'output': str(output.resolve()), 'stages': stages, 'computer_use_required': False}
    (output / 'result.json').write_text(json.dumps(result, indent=2), encoding='utf-8')
    print(output.resolve(), flush=True)
    return result


if __name__ == '__main__':
    run()
