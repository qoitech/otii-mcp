"""MCP server exposing a Qoitech Otii Arc/Ace power analyzer.

Requires the Otii 3 desktop application running with its TCP server enabled.
Each tool call opens its own connection to Otii.
"""
import importlib.metadata
from typing import Optional

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError

from . import otii as otii_lib

INSTRUCTIONS = '''Measures the energy consumption of a device under test powered by a Qoitech Otii Arc/Ace.
Before calling measure, make sure you know the device's supply voltage and IO (UART) voltage;
ask the user if they have not said. Never guess a supply voltage. To compare firmware versions,
measure each and compare energy_between_messages over the same log markers, skipping the first
cycle (occurrence >= 1) since it includes power-on.'''

mcp = MCPServer('otii', version=importlib.metadata.version('otii-mcp'), instructions=INSTRUCTIONS)

MAX_DURATION_S = 300


def _statistics(statistics: dict, channel: str) -> dict:
    if channel != 'mc':
        return {'min': statistics['min'], 'max': statistics['max'], 'average': statistics['average']}
    return {
        'energy_J': statistics['energy'],
        'energy_Wh': statistics['energy'] / 3600,
        'charge_C': statistics['charge'],
        'current_min_A': statistics['min'],
        'current_max_A': statistics['max'],
        'current_average_A': statistics['average'],
    }


@mcp.tool()
def list_devices() -> list[dict]:
    '''List the Otii devices (Arc/Ace) available to the Otii application.'''
    with otii_lib.connect() as otii:
        return [{'name': d.name, 'type': d.type, 'id': d.id} for d in otii_lib.list_devices(otii)]


@mcp.tool()
def measure(main_voltage: float, duration_s: float = 10, max_current_A: float = 0.5,
            channels: Optional[list[str]] = None, uart_baudrate: Optional[int] = None,
            exp_voltage: Optional[float] = None, device: Optional[str] = None) -> dict:
    '''Power the device under test from the Otii main output at main_voltage for duration_s
    seconds and record it, then turn power off. Returns the recording id and statistics for
    the whole recording.

    main_voltage: supply voltage in V. Check it is safe for the device under test.
    max_current_A: the main output cuts off above this current.
    channels: channels to record in addition to main current 'mc', e.g. 'mv' (main voltage),
        'mp' (main power), 'i1'/'i2' (digital inputs).
    uart_baudrate: set to record the device's UART output (rx channel) as a timestamped log,
        which enables get_log and energy_between_messages.
    exp_voltage: expansion port voltage (1.2-5 V), i.e. the UART/GPIO logic level. Must match
        the device's IO voltage when recording UART. Left unchanged if not given.
    device: Otii device name; defaults to OTII_DEVICE or the only connected device.
    '''
    if not 0 < duration_s <= MAX_DURATION_S:
        raise ToolError(f'duration_s must be in (0, {MAX_DURATION_S}]')
    channels = list(dict.fromkeys(['mc', *(channels or [])]))
    with otii_lib.connect() as otii:
        dev = otii_lib.get_device(otii, device)
        rec = otii_lib.measure(otii, dev, duration_s, main_voltage, max_current_A,
                               channels, uart_baudrate, exp_voltage)
        from_s, to_s = otii_lib.get_duration(dev, rec)
        statistics = otii_lib.get_statistics(dev, rec, from_s, to_s)
        return {
            'recording_id': rec.id,
            'name': rec.name,
            'length_s': to_s - from_s,
            'log_lines': len(otii_lib.get_log(dev, rec)),
            'whole_recording': _statistics(statistics, 'mc'),
        }


@mcp.tool()
def list_recordings(device: Optional[str] = None) -> list[dict]:
    '''List recordings in the active Otii project with id, name and length in seconds.'''
    with otii_lib.connect() as otii:
        dev = otii_lib.get_device(otii, device)
        result = []
        for rec in otii.get_active_project().get_recordings():
            try:
                from_s, to_s = otii_lib.get_duration(dev, rec)
                length: Optional[float] = to_s - from_s
            except Exception:
                length = None  # Recorded with another device.
            result.append({'recording_id': rec.id, 'name': rec.name, 'length_s': length})
        return result


@mcp.tool()
def get_log(recording_id: Optional[int] = None, contains: Optional[str] = None,
            limit: int = 500, device: Optional[str] = None) -> list[dict]:
    '''UART log lines recorded from the device under test, with timestamps in seconds from
    recording start. Defaults to the latest recording. Optionally keep only lines containing
    `contains`.'''
    with otii_lib.connect() as otii:
        dev = otii_lib.get_device(otii, device)
        log = otii_lib.get_log(dev, otii_lib.get_recording(otii, recording_id))
    if contains is not None:
        log = [line for line in log if contains in line['value']]
    return [{'t_s': line['timestamp'], 'line': line['value']} for line in log[:limit]]


@mcp.tool()
def get_statistics(from_s: float, to_s: float, channel: str = 'mc', recording_id: Optional[int] = None,
                   device: Optional[str] = None) -> dict:
    '''Statistics between two times (seconds from recording start). For the main current
    channel 'mc' this includes energy and charge. Defaults to the latest recording.'''
    with otii_lib.connect() as otii:
        dev = otii_lib.get_device(otii, device)
        rec = otii_lib.get_recording(otii, recording_id)
        statistics = otii_lib.get_statistics(dev, rec, from_s, to_s, channel)
    return {'from_s': from_s, 'to_s': to_s, 'channel': channel, **_statistics(statistics, channel)}


@mcp.tool()
def energy_between_messages(start_message: str, end_message: Optional[str] = None, occurrence: int = 0,
                            recording_id: Optional[int] = None, device: Optional[str] = None) -> dict:
    '''Energy from the n:th (0-based `occurrence`) UART log line exactly equal to start_message
    to the next line exactly equal to end_message. end_message defaults to start_message, which
    gives one full cycle for firmware that prints a marker once per cycle. Use occurrence >= 1
    to skip a first cycle that includes start-up. Defaults to the latest recording.'''
    with otii_lib.connect() as otii:
        dev = otii_lib.get_device(otii, device)
        rec = otii_lib.get_recording(otii, recording_id)
        statistics = otii_lib.energy_between_messages(dev, rec, start_message, end_message, occurrence)
    return {'from_s': statistics['from'], 'to_s': statistics['to'], **_statistics(statistics, 'mc')}


@mcp.tool()
def set_gpo(pin: int, enabled: bool, device: Optional[str] = None) -> str:
    '''Set Otii digital output GPO pin 1 or 2. Useful for switching hardware around the
    device under test, e.g. a USB/debugger connection that should be off while measuring.'''
    with otii_lib.connect() as otii:
        otii_lib.get_device(otii, device).set_gpo(pin, enabled)
    return f'GPO {pin} {"enabled" if enabled else "disabled"}'


@mcp.tool()
def set_ace_0_15_voltage(voltage: float, device: Optional[str] = None) -> str:
    '''Set the voltage of the Ace 0-15 V pin (0 turns it off). Ace only.'''
    with otii_lib.connect() as otii:
        otii_lib.get_device(otii, device).set_0_to_15_voltage(voltage)
    return f'0-15 V pin set to {voltage} V'


def main() -> None:
    mcp.run()


if __name__ == '__main__':
    main()
