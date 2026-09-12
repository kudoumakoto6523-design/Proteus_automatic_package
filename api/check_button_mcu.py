"""Verify public button controls against executing STM32 firmware in Proteus.

Uses an installed official sample as a local template; its files are not bundled.
Each native session owns its own project copy and process, then closes it.
"""
import argparse
import copy
import hashlib
from importlib.metadata import PackageNotFoundError, version
import json
import os
from pathlib import Path
import traceback
import uuid
from zipfile import ZipFile

from proteus_api import Circuit, Project, Session, Simulation, firmware_info, gpio_events
import proteus_api


ROOT = Path(__file__).resolve().parent
DEFAULT_SAMPLE = Path(os.environ.get('PROGRAMDATA', r'C:\ProgramData')) / 'program' / 'SAMPLES' / 'VSM for Cortex M3' / 'STM32' / 'STMCubeMX LED Blink' / 'STMCubeMX LED Blink.pdsprj'


def cleanup(session, sim=None):
    """Close only this check's process; record an explicit fallback on failure."""
    result = {}
    if not hasattr(session, 'process'):
        return {'进程尚未创建': True}
    try:
        if sim is not None:
            result['停止'] = sim.stop()
        session.save()
        result['退出码'] = session.close()
    except Exception as error:
        result['关闭错误'] = str(error)
        if session.process.poll() is None:
            session.process.terminate()
            session.process.wait(timeout=5)
            result['清理自身测试进程'] = True
    result['进程已关闭'] = session.process.poll() is not None
    return result


def run(sample=DEFAULT_SAMPLE, executable=r'D:\Proteus\BIN\PDS.EXE', output=None):
    sample = Path(sample).resolve(strict=True)
    original_digest = hashlib.sha256(sample.read_bytes()).hexdigest()
    output = Path(output).resolve() if output else ROOT / 'artifacts' / ('button-mcu-' + uuid.uuid4().hex[:8])
    output.mkdir(parents=True, exist_ok=False)
    firmware = ROOT / 'examples' / 'button_mcu' / 'pa0_to_pa5.hex'
    try:
        installed_version = version('proteus-native')
    except PackageNotFoundError:
        installed_version = None
    result = {'通过': False, '官方样例': str(sample), '固件': firmware_info(firmware), '阶段': [],
              '导入库文件': proteus_api.__file__, '安装分发版本': installed_version}
    session = sim = None
    print(json.dumps({'directory': str(output)}, ensure_ascii=False), flush=True)
    try:
        normalized = output / 'normalized_template.pdsprj'
        with ZipFile(sample) as original, ZipFile(normalized, 'w') as target:
            for info in original.infolist():
                # The fixture provides its own firmware; omit the sample's IDE
                # project so it cannot build or select an unrelated image.
                if not info.filename.startswith('FIRMWARE'):
                    target.writestr(copy.copy(info), original.read(info.filename))
        session = Session.__new__(Session)
        Session.__init__(session, normalized, executable=executable)
        result['模板进程PID'] = session.pid
        template_sim = Simulation(session)
        result['模板固件'] = template_sim.set_firmware('U1', firmware)
        session.set_properties('U1', TRACE_GPIO='3')
        result['模板规范化'] = cleanup(session)
        session = None
        assert result['模板规范化'].get('退出码') == 0, result['模板规范化']

        circuit = Circuit(template_project=normalized)
        donor = next(item for item in Project(normalized).components() if item['ref'] == 'U1')
        properties = dict(donor['properties'], PROGRAM=str(firmware), TRACE_GPIO='3')
        circuit.import_device('BUTTON')
        circuit.add('STM32F103R6', 'U1', 'STM32F103R6', 7620000, 0, properties=properties)
        circuit.add('BUTTON', 'SW1', 'BUTTON', 2540000, -254000, properties={'STATE': '0'})
        circuit.bind_controls('SW1')
        # Preserve the official VCC/VDD=3.3 V and ground rail definitions.
        circuit.connect('SW1.2', 'U1.PA0-WKUP')
        circuit.add_terminal('power', 'VCC', 'SW1.1')
        for pin in ('U1.VDD', 'U1.NRST', 'U1.VBAT', 'U1.VREF+'):
            circuit.add_terminal('power', 'VCC', pin)
        for pin in ('U1.VSS', 'U1.BOOT0'):
            circuit.add_terminal('ground', '', pin)
        project = circuit.save(output / 'button_mcu.pdsprj')
        result['工程'] = str(project)
        result['电源轨'] = circuit.power_rails()

        session = Session.__new__(Session)
        Session.__init__(session, project, executable=executable)
        result['仿真进程PID'] = session.pid
        sim = Simulation(session)
        for action in ('初始松开', '按下 SW1', '松开 SW1'):
            stage = {'操作': action}
            result['阶段'].append(stage)
            if action == '按下 SW1':
                stage['返回值'] = sim.press('SW1')
            elif action == '松开 SW1':
                stage['返回值'] = sim.release('SW1')
            stage['运行'] = sim.run_for(.01, timeout=20)
            print(json.dumps(stage, ensure_ascii=False), flush=True)
        log = sim.log()
        (output / 'simulation.log').write_text(log, encoding='utf-8')
        events = gpio_events(log, ref='U1', port=0, pin=5)
        result['PA5事件'] = events
        levels = []
        for event in events:
            if not levels or event['value'] != levels[-1]:
                levels.append(event['value'])
        result['PA5去重电平'] = levels
        assert levels == [0, 1, 0], f'Expected PA5 levels [0, 1, 0], received {levels}'
        ends = [stage['运行']['end_seconds'] for stage in result['阶段']]
        assert any(ends[0] <= item['seconds'] <= ends[1] and item['value'] == 1 for item in events)
        assert any(ends[1] <= item['seconds'] <= ends[2] and item['value'] == 0 for item in events)
        result['通过'] = True
    except Exception as error:
        result['错误'] = str(error)
        result['回溯'] = traceback.format_exc()
        if sim is not None:
            try:
                result['失败时状态'] = sim.status()
                log = sim.log()
                (output / 'failure.log').write_text(log, encoding='utf-8')
                result['失败时PA5事件'] = gpio_events(log, ref='U1', port=0, pin=5)
            except Exception as log_error:
                result['日志错误'] = str(log_error)
    finally:
        if session is not None:
            result['关闭'] = cleanup(session, sim)
            if result['关闭'].get('退出码') != 0:
                result['通过'] = False
        result['原样例未修改'] = hashlib.sha256(sample.read_bytes()).hexdigest() == original_digest
        result['通过'] = result['通过'] and result['原样例未修改']
        (output / 'result.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
        print(json.dumps({'directory': str(output), 'passed': result['通过'],
                          'events': result.get('PA5事件'), 'error': result.get('错误')}, ensure_ascii=False), flush=True)
    if not result['通过']:
        raise AssertionError(f'Button/MCU integration failed; see {output / "result.json"}')
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--sample', type=Path, default=DEFAULT_SAMPLE)
    parser.add_argument('--executable', default=r'D:\Proteus\BIN\PDS.EXE')
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    run(args.sample, args.executable, args.output)
