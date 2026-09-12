"""Native ADI property edits may write entity auxiliary word 0 instead of -1."""
import json
from pathlib import Path
import struct
import uuid

from component_codec import Circuit, _entities
from proteus_project import Project, UnsupportedFormat, _lp
from proteus_session import Session


def component_nets(data):
    return {frozenset((pin['ref'], pin['pin']) for pin in net['pins']) for net in data['nets']}


def run():
    out = Path(__file__).parent / 'artifacts' / ('native-property-roundtrip-' + uuid.uuid4().hex[:8])
    out.mkdir()
    circuit = Circuit().add('RESISTOR', 'R1', '10k', 0, 0)
    circuit.add('CAPACITOR', 'C1', '100n', 2540000, 0).connect('R1.2', 'C1.2')
    circuit.add_terminal('ground', '', 'C1.1')
    project = circuit.save(out / 'native_edit.pdsprj')
    circuit = Circuit.open(project).rename('C1', 'C_FILTER2').update('C_FILTER2', value='220n')
    circuit.move('R1', -2540000, 0).save(project, overwrite=True)
    stages, baseline = [], None
    for stage in ('native_edit', 'codec_save', 'native_reopen'):
        session = Session.__new__(Session)
        try:
            Session.__init__(session, project)
            if stage == 'native_edit':
                session.set_properties('C_FILTER2', VALUE='470n')
            data = session.export_netlist(out / (stage + '.sdf'))
            assert data['parts']['C_FILTER2']['value'] == '470n'
            if baseline is None:
                baseline = component_nets(data)
            else:
                assert component_nets(data) == baseline
            session.save()
            assert session.close() == 0
        finally:
            if hasattr(session, 'process') and session.process.poll() is None:
                session.process.terminate()
                session.process.wait(timeout=5)
        loaded = Circuit.open(project)
        assert {part['ref']: part for part in loaded.components()}['C_FILTER2']['value'] == '470n'
        entities, _ = _entities(Project(project)._cdb)
        assert entities['R1']['auxiliary'] == 0xffffffff
        assert entities['C_FILTER2']['auxiliary'] == 0
        stages.append({'stage': stage, 'value': '470n', 'native_save_close': True,
                       'circuit_readback': True, 'edited_entity_auxiliary': 0})
        if stage == 'native_edit':
            project = loaded.save(out / 'codec_roundtrip.pdsprj')
        print(json.dumps(stages[-1]), flush=True)

    # Keep the original bounded guard: an unobserved value must still fail.
    corrupt = bytearray(Project(project)._cdb)
    cursor = 84
    for _ in range(struct.unpack_from('<I', corrupt, 80)[0]):
        cursor += 16
        _, _, cursor = _lp(corrupt, cursor)
        count = struct.unpack_from('<I', corrupt, cursor)[0]
        cursor += 4
        for _ in range(count):
            _, _, cursor = _lp(corrupt, cursor)
            _, _, cursor = _lp(corrupt, cursor)
        cursor += 12
    struct.pack_into('<I', corrupt, cursor - 4, 1)
    try:
        _entities(corrupt)
    except UnsupportedFormat:
        pass
    else:
        raise AssertionError('Unknown entity auxiliary value accepted')
    result = {'directory': str(out), 'stages': stages, 'nets_preserved': True,
              'unknown_auxiliary_value_rejected': True}
    (out / 'result.json').write_text(json.dumps(result, indent=2), encoding='utf-8')
    print(out.resolve(), flush=True)
    return result


if __name__ == '__main__':
    run()
