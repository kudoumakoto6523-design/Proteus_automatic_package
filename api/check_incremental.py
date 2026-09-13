"""Open/edit/save existing native geometry; empty sheets and safe replacement."""
import hashlib
import json
from pathlib import Path
import uuid

from proteus_automatic_api import Circuit, Project, Session, UnsupportedFormat, decode_net_objects


def run():
    base = Path(__file__).parent / 'artifacts'
    out = base / ('incremental-' + uuid.uuid4().hex[:8])
    out.mkdir()
    checks = []
    sources = [base / 'edit-check-6b33bf76/edited.pdsprj',
               base / 'circuit-check-b5bf4e3e/three_parallel.pdsprj',
               base / 'net-objects-0925491a/named_networks.pdsprj']
    for source in sources:
        digest = hashlib.sha256(source.read_bytes()).hexdigest()
        circuit = Circuit.open(source)
        before = decode_net_objects(source)
        ref = circuit.components()[0]['ref']
        circuit.update(ref, value='47u' if ref.startswith('C') else '47k')
        target = circuit.save(out / source.name)
        after = decode_net_objects(target)
        assert sorted(tuple(w['points']) for w in before['wires']) == sorted(tuple(w['points']) for w in after['wires'])
        assert [(t['name'], t['position'], t['rotation']) for t in before['terminals']] == [
            (t['name'], t['position'], t['rotation']) for t in after['terminals']]
        assert circuit.power_rails() == Circuit.open(target).power_rails()
        for stage in ('generated', 'reopened'):
            session = Session(target)
            try:
                actual = session.export_netlist(out / (source.stem + '-' + stage + '.sdf'))
                assert actual['parts'][ref]['value'] == ('47u' if ref.startswith('C') else '47k')
                assert {frozenset((p['ref'], p['pin']) for p in net['pins']) for net in actual['nets']} == {
                    frozenset(net) for net in circuit.nets()}
                if stage == 'generated':
                    session.save()
                assert session.close() == 0
            finally:
                if session.process.poll() is None:
                    session.process.terminate()
                    session.process.wait(timeout=5)
        assert hashlib.sha256(source.read_bytes()).hexdigest() == digest
        checks.append(source.name + ': geometry, connectivity, power, native save/reopen')
    empty = Circuit().save(out / 'empty.pdsprj')
    for _ in range(2):
        session = Session(empty)
        try:
            session.save()
            assert session.close() == 0
        finally:
            if session.process.poll() is None:
                session.process.terminate()
                session.process.wait(timeout=5)
        assert Project(empty).components() == []
    circuit = Circuit.open(empty)
    circuit.add('CAPACITOR', 'C1', '1u', 0, 0).delete_component('C1').save(empty, overwrite=True)
    assert Project(empty).components() == []
    stale = Circuit.open(empty)
    original_empty = empty.read_bytes()
    empty.write_bytes(original_empty + b'external modification')
    snapshot = empty.read_bytes()
    try:
        stale.save(empty, overwrite=True)
    except UnsupportedFormat:
        pass
    else:
        raise AssertionError('Stale overwrite accepted')
    assert empty.read_bytes() == snapshot
    empty.write_bytes(original_empty)
    assert not list(out.glob('.proteus-*'))
    checks += ['empty native save/reopen', 'delete last component', 'atomic overwrite', 'stale overwrite rejected']
    result = dict(directory=str(out), checks=checks)
    (out / 'result.json').write_text(json.dumps(result, indent=2), encoding='utf-8')
    print(json.dumps(result), flush=True)


if __name__ == '__main__':
    run()
