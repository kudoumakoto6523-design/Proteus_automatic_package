"""Component and connection CRUD, including native save/reopen verification."""
import copy
import json
from pathlib import Path
import uuid

from proteus_automatic_api import Circuit, Project, Session, UnsupportedFormat


def run():
    out = Path(__file__).parent / 'artifacts' / ('edit-check-' + uuid.uuid4().hex[:8])
    c = Circuit()
    c.add('RESISTOR', 'R1', '10k', 0, 0).add('CAPACITOR', 'C1', '100n', 2540000, 0)
    c.connect('R1.2', 'C1.2')
    c.rotate('R1', 90).mirror('R1', 'x').move('R1', -2540000, 2540000)
    c.update('R1', value='22k').set_properties('R1', API_NOTE='edited component')
    c.rename('C1', 'C_FILTER2')
    c.copy_component('R1', 'R9', 7620000, 2540000)
    assert {tuple(net) for net in c.nets()} >= {(('R9', '1'),), (('R9', '2'),)}
    c.connect('C_FILTER2.1', 'R9.2').disconnect('R9.2')
    c.delete_component('R9')
    assert c.pins('R1')[1]['position'] == (-2540000, 1270000)
    before = copy.deepcopy((c.components(), c.connections()))
    for action in (lambda: c.rotate('R1', 45), lambda: c.rename('R1', 'C_FILTER2'),
                   lambda: c.move('R1', 2**31, 0), lambda: c.set_route('R1.2', 'C_FILTER2.2', [(0, 0), (1, 0)])):
        try:
            action()
        except UnsupportedFormat:
            pass
        else:
            raise AssertionError('Invalid edit accepted')
        assert (c.components(), c.connections()) == before
    branch = Circuit()
    for i, x in enumerate((-2540000, 0, 2540000), 1):
        branch.add('CAPACITOR', f'C{i}', '1u', x, 0)
    branch.connect('C1.2', 'C2.2').connect('C1.2', 'C3.2').delete_component('C1')
    assert [('C2', '2'), ('C3', '2')] in branch.nets(), 'Deleting a branch endpoint split the surviving net'
    path = c.save(out / 'edited.pdsprj')
    expected = {frozenset(net) for net in c.nets()}
    assert Project(path).components()[0]['value'] == '22k'
    for stage in ('generated', 'reopened'):
        session = Session(path)
        try:
            actual = session.export_netlist(out / f'{stage}.sdf')
            assert set(actual['parts']) == {'R1', 'C_FILTER2'}
            assert actual['parts']['R1']['value'] == '22k'
            assert actual['parts']['R1']['properties']['API_NOTE'] == 'edited component'
            assert {frozenset((p['ref'], p['pin']) for p in n['pins']) for n in actual['nets']} == expected
            if stage == 'generated':
                session.save()
            assert session.close() == 0
        finally:
            if session.process.poll() is None:
                session.process.terminate()
                session.process.wait(timeout=5)
    result = dict(project=str(path), checks=['copy', 'delete', 'rename', 'value', 'property', 'rotate', 'mirror',
        'move_with_reroute', 'disconnect', 'preserve_remaining_net', 'invalid_edit_atomic', 'native_sdf_save_reopen'])
    (out / 'result.json').write_text(json.dumps(result, indent=2), encoding='utf-8')
    print(json.dumps(result), flush=True)


if __name__ == '__main__':
    run()
