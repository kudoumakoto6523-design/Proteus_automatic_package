"""Invalidate cached wire geometry without changing a circuit's connectivity."""
import copy
import json


def invalidate_routes(connections, terminals, pins):
    """Return independent (connections, terminals) with touched physical nets rerouted.

    Call with the candidate topology before committing an edit. ``pins`` are
    ``(ref, pin)`` pairs (or ``REF.PIN`` strings) involved in the edit. Cached
    layouts also join the invalidation set, so a split net cannot retain a stale
    junction. Named terminals do not join unrelated physical wires here.

    Wire label text/style survives and is reattached to a new wire segment.
    A terminal's own label is preserved; a caller moving its glyph should remove
    that terminal's ``label`` separately so the encoder recalculates its anchor.
    """
    connections, terminals = copy.deepcopy(connections), copy.deepcopy(terminals)
    requests = []
    for connection in connections:
        requests.append((connection, tuple(connection['first']), tuple(connection['second'])))
    for index, terminal in enumerate(terminals):
        if terminal.get('pin') is not None:
            requests.append((terminal, tuple(terminal['pin']),
                             ('@TERMINAL_' + terminal.get('id', 'T' + str(index + 1)), 'PIN')))
    affected = {tuple(pin.rsplit('.', 1) if isinstance(pin, str) else pin) for pin in pins}
    scopes = []
    for request, first, second in requests:
        scope = {first, second}
        scope.update((request.get('layout') or {}).get('paths', {}))
        scopes.append(scope)
    while True:
        expanded = affected | set().union(*(scope for scope in scopes if scope & affected))
        if expanded == affected:
            break
        affected = expanded
    touched = [(item, scope) for item, scope in zip(requests, scopes) if scope & affected]

    # A duplicated cached layout represents the same native label only once.
    # Native span/offset fields distinguish separate labels with identical text.
    recovered, seen = [], set()
    for (request, first, second), _ in touched:
        layout = request.get('layout') or {}
        for owner, labels in layout.get('labels', {}).items():
            for label in labels:
                key = (tuple(owner), json.dumps(label, sort_keys=True, separators=(',', ':')))
                if key not in seen:
                    seen.add(key)
                    recovered.append((tuple(owner), copy.deepcopy(label), request))
        for link in layout.get('links', []):
            for label in link.get('labels', []):
                key = ('link', tuple(link['first']), tuple(link['second']),
                       json.dumps(label, sort_keys=True, separators=(',', ':')))
                if key not in seen:
                    seen.add(key)
                    recovered.append((first, copy.deepcopy(label), request))
        request.pop('points', None)
        request.pop('layout', None)
        request.pop('preserve_geometry', None)

    # Labels belonging to a deleted branch move to a surviving request of the
    # same old net. The helper never invents connectivity or terminal labels.
    for owner, label, original in recovered:
        old_scope = next(scope for (request, _, _), scope in touched if request is original)
        candidates = [item for item, scope in touched if scope & old_scope]
        target = next((item for item in candidates if item[1] == owner), None)
        if target is None:
            target = next((item for item in candidates if owner in item[1:]), None)
        if target is None:
            target = candidates[0]
        label['_pin'] = owner
        target[0].setdefault('labels', []).append(label)

    # Reconstruct the candidate's actual physical groups, excluding stale
    # layout membership, to check whether a label's original branch survives.
    groups = []
    for request, first, second in requests:
        matching = [group for group in groups if first in group or second in group]
        groups = [group for group in groups if group not in matching]
        groups.append({first, second}.union(*matching))
    for (request, first, _), _scope in touched:
        actual = next(group for group in groups if first in group)
        for label in request.get('labels', []):
            label['auto_position'] = True
            if label.get('_pin') is not None and tuple(label['_pin']) not in actual:
                label.pop('_pin', None)
    return connections, terminals


def _check():
    a, b, c, d = ('R1', '1'), ('R2', '1'), ('R3', '1'), ('R4', '1')
    label = {'text': 'SENSE', 'position': (0, 0), 'tail_hex': '00'}
    untouched = {'first': ('R5', '1'), 'second': ('R6', '1'), 'points': [(4, 5), (6, 5)]}
    connections = [{'first': a, 'second': b, 'points': [(0, 0), (1, 0)],
                    'layout': {'junction': (1, 0), 'paths': {a: [], b: [], c: []},
                               'labels': {c: [label]}}, 'preserve_geometry': True},
                   {'first': b, 'second': c}, untouched]
    terminals = [{'id': 'T1', 'kind': 'output', 'name': 'OUT', 'pin': c,
                  'position': (9, 0), 'points': [(8, 0), (9, 0)], 'label': label}]
    before = copy.deepcopy((connections, terminals))
    result, terms = invalidate_routes(connections, terminals, [a])
    assert (connections, terminals) == before
    assert result[2] == untouched and terms[0]['label'] == label
    assert all(not set(request) & {'points', 'layout', 'preserve_geometry'}
               for request in result[:2] + terms)
    labels = [item for request in result + terms for item in request.get('labels', [])]
    assert len(labels) == 1 and labels[0]['text'] == 'SENSE' and labels[0]['_pin'] == c
    assert labels[0]['auto_position'] is True and labels[0]['tail_hex'] == '00'
    split = [connections[0], {'first': c, 'second': d}, untouched]
    rerouted, _ = invalidate_routes(split, [], [a, c])
    assert all('_pin' not in item or item['_pin'] in (c, d)
               for request in rerouted for item in request.get('labels', []))
    assert sum(len(request.get('labels', [])) for request in rerouted) == 1
    bridge = copy.deepcopy(connections[:2])
    bridge[0]['layout']['links'] = [{'first': (0, 0), 'second': (1, 0),
                                   'points': [(0, 0), (1, 0)], 'labels': [dict(label, text='BRIDGE')]}]
    rerouted, _ = invalidate_routes(bridge, [], [a])
    assert {label['text'] for request in rerouted for label in request.get('labels', [])} == {'SENSE', 'BRIDGE'}
    print('net_edit: affected-net invalidation, label preservation, split-net relocation, and no mutation passed')


if __name__ == '__main__':
    _check()
