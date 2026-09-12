"""Check actual exported net metadata and reject malformed/truncated SDF."""
from proteus_session import parse_sdf


def run():
    text = ('ISIS SCHEMATIC DESCRIPTION FORMAT 8.0\n*PARTLIST,1\n'
            'U1,MCU,"value, with comma",NOTE="quoted, value"\n'
            '*NETLIST,1\nVCC,3,CLASS=POWER\nU1,PP,VDD\nVCC,PT\nVCC,PR\n')
    data = parse_sdf(text)
    assert data['parts']['U1']['properties']['NOTE'] == 'quoted, value'
    assert data['nets'][0]['pins'] == [dict(ref='U1', kind='PP', pin='VDD')]
    assert data['nets'][0]['terminals'] == [dict(name='VCC', kind='PT'), dict(name='VCC', kind='PR')]
    assert data['nets'][0]['properties'] == dict(CLASS='POWER')
    for kind in ('LBL', 'IT', 'OT', 'BT'):
        assert parse_sdf(text.replace('VCC,PT', f'VCC,{kind}'))['nets'][0]['terminals'][0]['kind'] == kind
    for bad in (text.replace('VCC,3', 'VCC,-1'), text.replace('VCC,3', 'VCC,4'),
                text.replace('U1,PP,VDD', 'MISSING,PP,VDD'), text + 'VCC,PT\n',
                text.replace('CLASS=POWER', 'malformed'), text.replace('VCC,PT', 'VCC,UNKNOWN')):
        try:
            parse_sdf(bad)
        except ValueError:
            pass
        else:
            raise AssertionError('Malformed SDF accepted')
    print('SDF metadata, quoting and malformed-input checks passed')


if __name__ == '__main__':
    run()
