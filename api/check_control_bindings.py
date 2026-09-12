"""Offline actuator-key allocation, atomic rejection and project roundtrip check."""
import copy
import json
from pathlib import Path
import pickle
import tempfile

from component_codec import Circuit
from proteus_project import Project, UnsupportedFormat


def check():
    circuit = Circuit()
    devices = ('BUTTON', 'SW-SPST', 'SW-SPST-MOM', 'SWITCH', 'LOGICSTATE', 'LOGICTOGGLE')
    for index, device in enumerate(devices, 1):
        circuit.import_device(device)
        assert circuit.templates[device]['record_tag'] == 8
        circuit.add(device, f'SW{index}', device, index * 2540000, 0)
    circuit.add('RESISTOR', 'R1', '10k', 0, 2540000,
                properties={'INC': '9', 'DEC': '7', 'KEY': '8', 'NOTE': 'reserved'})
    circuit.set_properties('SW1', KEY='6')

    def properties(candidate):
        return {part['ref']: part['properties'] for part in candidate.components()}

    def bindings(candidate):
        return {ref: {key: value for key, value in values.items() if key in ('INC', 'DEC', 'KEY')}
                for ref, values in properties(candidate).items()}

    reserved = properties(circuit)['R1']
    assert circuit.bind_controls('SW1', 'SW2') is circuit
    assert bindings(circuit)['SW1'] == {'INC': '0', 'DEC': '1'}
    assert bindings(circuit)['SW2'] == {'INC': '2', 'DEC': '3'}
    assert properties(circuit)['R1'] == reserved
    circuit.bind_controls('SW3')
    assert bindings(circuit)['SW3'] == {'INC': '4', 'DEC': '5'}
    previous = bindings(circuit)
    circuit.bind_controls('SW1', 'SW2')
    assert bindings(circuit) == previous
    assert properties(circuit)['R1'] == reserved

    rejected = []

    def reject(name, candidate, refs, error):
        before = pickle.dumps(candidate, protocol=5)
        try:
            candidate.bind_controls(*refs)
        except error:
            pass
        else:
            raise AssertionError(f'Accepted {name}')
        assert pickle.dumps(candidate, protocol=5) == before, f'{name} changed the Circuit'
        rejected.append(name)

    for name, refs, error in (
        ('empty', (), ValueError),
        ('duplicate', ('SW1', 'SW1'), ValueError),
        ('non-string', ('SW1', None), ValueError),
        ('missing', ('SW1', 'SW404'), KeyError),
        ('unsupported', ('SW1', 'R1'), UnsupportedFormat),
        ('reserved-key-capacity', ('SW1', 'SW2', 'SW3', 'SW4'), ValueError),
    ):
        reject(name, circuit, refs, error)

    capacity = copy.deepcopy(circuit)
    capacity.set_properties('R1', INC=None, DEC=None, KEY=None)
    capacity.bind_controls(*(f'SW{i}' for i in range(1, 6)))
    allocated = [value for ref, values in bindings(capacity).items() if ref != 'R1'
                 for value in values.values()]
    assert len(allocated) == 10 and set(allocated) == set('0123456789')
    reject('ten-key-capacity', capacity, tuple(f'SW{i}' for i in range(1, 7)), ValueError)

    malformed = copy.deepcopy(circuit)
    malformed.set_properties('R1', INC='10')
    reject('malformed-reserved-key', malformed, ('SW1',), ValueError)
    malformed.set_properties('R1', INC='9')
    malformed.set_properties('SW1', INC='-1')
    reject('malformed-target-key', malformed, ('SW1',), ValueError)

    with tempfile.TemporaryDirectory(prefix='proteus-control-bindings-') as directory:
        first = circuit.save(Path(directory) / 'bound.pdsprj')
        def assert_record_tags(path):
            project = Project(path)
            assert {ref: project._dsn[item['dsn_record_start'] - 1]
                    for ref, item in project._components.items()} == {
                        **{f'SW{i}': 8 for i in range(1, 7)}, 'R1': 0}
        assert_record_tags(first)
        loaded = Circuit.open(first)
        assert all(loaded.templates[device]['record_tag'] == 8 for device in devices)
        assert bindings(loaded) == bindings(circuit)
        assert properties(loaded) == properties(circuit)
        second = loaded.save(Path(directory) / 'roundtrip.pdsprj')
        assert_record_tags(second)
        assert bindings(Circuit.open(second)) == bindings(circuit)
    result = {'bindings': bindings(circuit), 'atomic_rejections': rejected,
              'five_controls_use_ten_keys': True, 'active_record_tags_preserved': True,
              'file_roundtrip': True, 'native_started': False}
    print(json.dumps(result, indent=2))
    return result


if __name__ == '__main__':
    check()
