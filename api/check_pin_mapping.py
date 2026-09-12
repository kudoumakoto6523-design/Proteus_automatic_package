"""Offline package-pin regression against the installed official STM32 sample."""
import copy
import json
from pathlib import Path
import pickle
import tempfile
from unittest.mock import patch

from component_codec import Circuit, _entities
from device_library import Library
from proteus_project import Project, UnsupportedFormat


def check():
    catalogue = Library()
    source = Path(r'C:\ProgramData\program\SAMPLES\VSM for Cortex M3\STM32\STMCubeMX LED Blink\STMCubeMX LED Blink.pdsprj')
    expected = _entities(Project(source)._cdb)[0]['U1']['pins']
    circuit = Circuit().import_device('STM32F103R6')
    assert circuit.templates['STM32F103R6']['pins'] == expected
    mapped = dict(expected)
    assert (mapped['VSS'], mapped['VDD'], mapped['PC13_RTC'], mapped['VREF+']) == (
        '18/31/47/63', '19/32/48/64', '2', '*')
    donor = Circuit().import_from_project(source, 'STM32F103R6')
    assert donor.templates['STM32F103R6']['pins'] == expected
    circuit.add('STM32F103R6', 'U1', 'STM32F103R6', 0, 0)
    with tempfile.TemporaryDirectory(prefix='proteus-pin-mapping-') as directory:
        path = circuit.save(Path(directory) / 'mapped.pdsprj')
        assert _entities(Project(path)._cdb)[0]['U1']['pins'] == expected
        loaded = Circuit.open(path)
        assert [(pin['name'], pin['number']) for pin in loaded.pins('U1')] == expected

    avr = Circuit().import_device('ATMEGA328P', 'AVR2')
    pins = dict(avr.templates['ATMEGA328P']['pins'])
    assert {key: pins[key] for key in ('PB0/ICP1/CLKO/PCINT0', 'GND', 'VCC', 'ADC6')} == {
        'PB0/ICP1/CLKO/PCINT0': '12', 'GND': '3/5/21', 'VCC': '4/6', 'ADC6': '19'}
    for device, library in (('ULN2003A', 'ANALOG'), ('RELAY', 'ACTIVE')):
        metadata = catalogue.get(device, library)
        assert not metadata['pinouts']
        imported = Circuit().import_device(device, library)
        assert imported.templates[device]['pins'] == [(pin['name'], pin['number']) for pin in metadata['pins']]
    passive = Circuit()
    for device in ('RESISTOR', 'CAPACITOR'):
        metadata = catalogue.get(device, 'ASIMMDLS')
        assert passive.templates[device]['pins'] == [(pin['name'], pin['number']) for pin in metadata['pins']]

    original = catalogue.get('STM32F103R6')
    package = original['package']
    selected = original['pinouts'][package]
    malformed = {
        'missing-pin': [line for line in selected if not line.startswith('{PIN "VSS"')],
        'duplicate-pin': selected + ['{PIN "VSS" = 99}'],
        'duplicate-pad': [line.replace('{PIN "PC13_RTC" = 2}', '{PIN "PC13_RTC" = 60}') for line in selected],
        'multiple-units': [line.replace('{ELEMENTS=1}', '{ELEMENTS=2}') for line in selected],
        'unknown-record': selected + ['{UNSUPPORTED=1}'],
        'missing-package': None,
    }
    rejected = []
    for name, lines in malformed.items():
        metadata = copy.deepcopy(original)
        if lines is None:
            del metadata['pinouts'][package]
        else:
            metadata['pinouts'][package] = lines
        candidate = Circuit()
        before = pickle.dumps(candidate, protocol=5)
        with patch.object(Library, 'get', return_value=metadata):
            try:
                candidate.import_device('STM32F103R6')
            except UnsupportedFormat:
                pass
            else:
                raise AssertionError(f'Accepted {name}')
        assert pickle.dumps(candidate, protocol=5) == before
        rejected.append(name)
    result = {'official_stm32_pins_match': len(expected), 'avr_package_mapping': True,
              'unmapped_devices_preserved': True, 'atomic_rejections': rejected, 'native_started': False}
    print(json.dumps(result, indent=2))
    return result


if __name__ == '__main__':
    check()
