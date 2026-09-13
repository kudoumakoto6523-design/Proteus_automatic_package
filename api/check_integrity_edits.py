"""Regression for shared terminals and wire labels during topology edits.

Default: three native SDF/save/close checks on private copies. --offline skips
Proteus and checks serialization/readback plus routing and logical name union.
"""
import argparse
import copy
import hashlib
import json
from pathlib import Path
import uuid

from proteus_automatic_api import Circuit, Session, UnsupportedFormat, decode_net_objects


def wire_labels(circuit):
    labels = []
    for item in circuit.connections() + circuit.terminals():
        labels += item.get('labels', [])
        layout = item.get('layout') or {}
        labels += [label for values in layout.get('labels', {}).values() for label in values]
        labels += [label for link in layout.get('links', []) for label in link.get('labels', [])]
    return sorted(label['text'] for label in labels)


def pin_sets(circuit, physical=False):
    return {frozenset(net) for net in circuit.nets(physical=physical)}


def run(offline=False):
    base = Path(__file__).resolve().parent / 'artifacts'
    out = base / ('integrity-edits-' + uuid.uuid4().hex[:8])
    out.mkdir()
    sources = {
        'hub': base / 'topology-edits-11de383d/03_add_terminal.pdsprj',
        'binary': base / 'label-roundtrip-c2346bda/binary.pdsprj',
        'star': base / 'label-roundtrip-c2346bda/star.pdsprj',
        'bus': base / 'multi-junction-8d274f26/renamed_moved.pdsprj',
    }
    digests = {name: hashlib.sha256(path.read_bytes()).hexdigest() for name, path in sources.items()}
    report = {'output': str(out), 'mode': 'offline' if offline else 'native', 'checks': [], 'native': []}

    def check_case(circuit, name, expected_pins, expected_names):
        target = circuit.save(out / (name + '.pdsprj'))
        restored = Circuit.open(target)
        assert pin_sets(restored) == pin_sets(circuit)
        assert wire_labels(restored) == wire_labels(circuit)
        graph = decode_net_objects(target)
        assert graph['complete'] and graph['rebuild_complete']
        net = next(net for net in graph['nets'] if set(net['pins']) == expected_pins)
        assert expected_names <= set(net['names']), net
        report['checks'].append(name + ': serialized labels and connectivity')
        if offline:
            return
        session = Session.__new__(Session)
        record = {'case': name, 'project': str(target)}
        report['native'].append(record)
        try:
            Session.__init__(session, target)
            record['pid'] = session.pid
            sdf = out / (name + '.sdf')
            actual = session.export_netlist(sdf)
            actual_sets = {frozenset((pin['ref'], pin['pin']) for pin in net['pins']) for net in actual['nets']}
            actual_sets.discard(frozenset())
            assert actual_sets == pin_sets(circuit), (actual_sets, pin_sets(circuit))
            net = next(net for net in actual['nets']
                       if {(pin['ref'], pin['pin']) for pin in net['pins']} == expected_pins)
            names = {net['name']} | {terminal['name'] for terminal in net['terminals']}
            assert expected_names <= names, (expected_names, net)
            session.save()
            record['exit_code'] = session.close()
            assert record['exit_code'] == 0, record
            restored = Circuit.open(target)
            assert pin_sets(restored) == pin_sets(circuit)
            assert wire_labels(restored) == wire_labels(circuit)
            record.update(sdf=str(sdf), names=sorted(names), passed=True)
            print(json.dumps(record), flush=True)
        finally:
            if hasattr(session, 'process') and session.process.poll() is None:
                session.process.terminate()
                record['cleanup_exit_code'] = session.process.wait(timeout=5)

    try:
        circuit = Circuit.open(sources['hub'])
        terminal = circuit.terminals()[0]
        circuit.delete_component('C1')
        after = circuit.terminals()[0]
        assert after['pin'] in {('C2', '2'), ('C3', '2')}
        assert (after['position'], after['rotation_raw'], after['name']) == (
            terminal['position'], terminal['rotation_raw'], terminal['name'])
        check_case(circuit, 'delete_hub', {('C2', '2'), ('C3', '2')}, {'BUS'})

        circuit = Circuit.open(sources['star'])
        circuit.disconnect('C1.1', 'C2.1')
        assert wire_labels(circuit) == ['REAL_LABEL']
        check_case(circuit, 'disconnect_branch', {('C1', '1'), ('C3', '1')}, {'REAL_LABEL'})

        circuit = Circuit.open(sources['star'])
        circuit.add_terminal('output', 'LEFT', 'C1.1', position=(-1270000, -2540000))
        circuit.add_terminal('output', 'RIGHT', 'C1.1', position=(1270000, -2540000))
        circuit.delete_component('C2').delete_component('C3').delete_terminal('T1')
        assert wire_labels(circuit) == ['REAL_LABEL']
        check_case(circuit, 'delete_terminal', {('C1', '1')}, {'REAL_LABEL', 'RIGHT'})

        for name in ('binary', 'star', 'bus'):
            circuit = Circuit.open(sources[name])
            old_labels, old_nets = wire_labels(circuit), pin_sets(circuit)
            edge = circuit.connections()[0]
            circuit.set_route(edge['first'], edge['second'], None)
            target = circuit.save(out / ('reroute_' + name + '.pdsprj'))
            restored = Circuit.open(target)
            assert wire_labels(restored) == old_labels and pin_sets(restored) == old_nets
            report['checks'].append(name + ': set_route preserves labels')
        circuit = Circuit.open(sources['binary'])
        before = copy.deepcopy((circuit.components(), circuit.connections(), circuit.terminals()))
        try:
            circuit.set_route('C1.1', 'C2.1', [(0, 0), (0, 1)])
        except UnsupportedFormat:
            pass
        else:
            raise AssertionError('Invalid route accepted')
        assert before == (circuit.components(), circuit.connections(), circuit.terminals())
        report['checks'].append('invalid set_route is atomic')

        circuit.copy_component('C1', 'C3', 2540000, 0)
        circuit.add_terminal('label', 'REAL_LABEL', 'C3.1')
        assert frozenset({('C1', '1'), ('C2', '1'), ('C3', '1')}) in pin_sets(circuit)
        assert frozenset({('C1', '1'), ('C2', '1')}) in pin_sets(circuit, physical=True)
        assert frozenset({('C3', '1')}) in pin_sets(circuit, physical=True)
        target = circuit.save(out / 'logical_label_union.pdsprj')
        restored = Circuit.open(target)
        assert pin_sets(restored) == pin_sets(circuit)
        decoded = decode_net_objects(target)
        assert {frozenset(net['pins']) for net in decoded['nets'] if net['pins']} == pin_sets(circuit)
        report['checks'].append('wire label and terminal name merge logical nets only')
        assert all(hashlib.sha256(path.read_bytes()).hexdigest() == digests[name]
                   for name, path in sources.items())
        report['checks'].append('all source fixtures unchanged')
    except Exception as exc:
        report['error'] = f'{type(exc).__name__}: {exc}'
        (out / 'check.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
        print(str(out), flush=True)
        raise
    (out / 'result.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
    print(json.dumps(report), flush=True)
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--offline', action='store_true')
    run(parser.parse_args().offline)
