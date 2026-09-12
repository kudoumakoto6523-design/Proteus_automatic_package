"""Fresh native RC simulations: doubling source amplitude doubles exported traces."""
import hashlib
import json
import math
from pathlib import Path
import uuid

from component_codec import SOURCE
from measurements import export_graph, generators, graphs, parse_graph_csv, sample, set_generator_properties
from proteus_session import Session


def check_parser():
    data = parse_graph_csv('Time,OUT,OUT\n0,1,5\n1,3,9\n')
    assert sample(data, 'OUT', 0.5) == 2 and sample(data, 'OUT', 0.5, occurrence=1) == 7
    operations = [lambda: sample(data, 'OUT', -1), lambda: sample(data, 'OUT', 2),
                  lambda: sample(data, 'OUT', 0, occurrence=2), lambda: sample(data, 'missing', 0)]
    for text in ('', 'Time,OUT\n', 'Time,OUT\n0\n', 'Time,OUT\n0,nan\n',
                 'Time,OUT\n1,2\n0,3\n', 'Time,OUT\n0,no-number\n'):
        operations.append(lambda text=text: parse_graph_csv(text))
    for operation in operations:
        try:
            operation()
        except ValueError:
            pass
        else:
            raise AssertionError('Invalid graph measurement input accepted')
    return {'duplicate_columns_interpolation': True, 'malformed_csv_and_invalid_samples_rejected': True}


def run():
    parser_checks = check_parser()
    digest = hashlib.sha256(SOURCE.read_bytes()).hexdigest()
    output = Path(__file__).parent / 'artifacts' / ('measurement-check-' + uuid.uuid4().hex[:8])
    output.mkdir()
    print(json.dumps({'generators': generators(SOURCE)}), flush=True)
    results = []
    for amplitude in ('1', '2'):
        project = set_generator_properties(SOURCE, output / f'amp{amplitude}.pdsprj', 'INPUT', AMP=amplitude)
        session = Session.__new__(Session)
        try:
            Session.__init__(session, project)
            print(json.dumps({'amplitude': amplitude, 'graphs': graphs(session)}), flush=True)
            data = export_graph(session, 'ANALOGUE ANALYSIS', output / f'amp{amplitude}.csv', simulate=True)
            values = {name: sample(data, name, 0.00025) for name in ('INPUT', 'R1(2)', 'ICAP')}
            assert all(math.isfinite(value) and value != 0 for value in values.values())
            session.save()
            assert session.close() == 0
            results.append({'amplitude': amplitude, 'graph': data['graph'], 'samples': len(data['axis']['values']),
                            'time': 0.00025, 'values': values, 'simulated': data['simulated']})
            print(json.dumps(results[-1]), flush=True)
        finally:
            if hasattr(session, 'process') and session.process.poll() is None:
                session.process.terminate()
                session.process.wait(timeout=5)
    ratios = {name: results[1]['values'][name] / results[0]['values'][name] for name in results[0]['values']}
    assert all(math.isclose(ratio, 2.0, rel_tol=0.002) for ratio in ratios.values()), ratios
    assert hashlib.sha256(SOURCE.read_bytes()).hexdigest() == digest
    result = {'output': str(output.resolve()), 'results': results, 'amplitude_ratios': ratios,
              'fresh_simulation_verified': True, 'source_unchanged': True, 'parser_checks': parser_checks}
    (output / 'result.json').write_text(json.dumps(result, indent=2), encoding='utf-8')
    print(json.dumps(result), flush=True)
    return result


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--parser-only', action='store_true')
    args = parser.parse_args()
    print(check_parser()) if args.parser_only else run()
