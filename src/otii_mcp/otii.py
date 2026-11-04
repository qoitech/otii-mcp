"""Thin layer over otii_tcp_client used by the MCP server.

All times are in seconds from the start of a recording, which is the unit the
Otii TCP API uses for log timestamps and statistics ranges.

Configuration comes from environment variables (see README.md):
OTII_HOST, OTII_PORT, OTII_CREDENTIALS, OTII_USERNAME, OTII_PASSWORD, OTII_DEVICE.
"""
import contextlib
import os
import sys
import time
from collections.abc import Iterator
from typing import Optional

from mcp.server.mcpserver.exceptions import ToolError
from otii_tcp_client import arc, otii_client, otii_exception, recording

DEFAULT_HOST = '127.0.0.1'
DEFAULT_PORT = 1905


@contextlib.contextmanager
def connect() -> Iterator[otii_client.Connect]:
    '''Connect to the Otii desktop application's TCP server.

    Logs in with the file in OTII_CREDENTIALS if it exists, otherwise with the
    OTII_USERNAME/OTII_PASSWORD environment variables (handled by otii_tcp_client),
    and reserves an Automation license for the duration of the connection.
    '''
    host = os.environ.get('OTII_HOST', DEFAULT_HOST)
    port = int(os.environ.get('OTII_PORT', DEFAULT_PORT))
    # An empty or missing path makes otii_tcp_client fall back to the environment variables.
    credentials = os.path.expanduser(os.environ.get('OTII_CREDENTIALS', ''))
    try:
        # otii_tcp_client prints to stdout, which would corrupt the MCP stdio protocol.
        with contextlib.redirect_stdout(sys.stderr):
            client = otii_client.OtiiClient()
            connection = client.connect(host=host, port=port, credentials=credentials)
    except OSError as e:
        raise ToolError(f'Could not connect to Otii at {host}:{port} ({e}). '
                        'Is the Otii 3 application running with its TCP server enabled?') from e
    except otii_client.OtiiClientException as e:
        raise ToolError(f'{e}. Is an Otii Automation license available, and are the Otii '
                        'credentials (OTII_CREDENTIALS or OTII_USERNAME/OTII_PASSWORD) set?') from e
    except otii_exception.Otii_Exception as e:
        raise _otii_error(e) from e
    # Errors reported by Otii, e.g. a voltage out of range, are useful to the model.
    try:
        with connection as otii:
            yield otii
    except TimeoutError as e:
        raise ToolError(NOT_RESPONDING) from e
    except otii_exception.Otii_Exception as e:
        raise _otii_error(e) from e


NOT_RESPONDING = ('The Otii application did not respond in time. It may be busy or stuck; '
                  'check the Otii 3 window and restart the application if it does not recover.')


def _otii_error(e: otii_exception.Otii_Exception) -> ToolError:
    if e.type == 'Not ready':
        return ToolError(f'Otii is not ready. {NOT_RESPONDING}')
    return ToolError(f'Otii error: {e.message}')


def list_devices(otii: otii_client.Connect) -> list[arc.Arc]:
    return otii.get_devices()


def get_device(otii: otii_client.Connect, name: Optional[str] = None) -> arc.Arc:
    '''Return the device with the given name, else OTII_DEVICE, else the only connected device.'''
    name = name or os.environ.get('OTII_DEVICE') or None
    devices = otii.get_devices()
    if name is not None:
        devices = [device for device in devices if device.name == name]
        if len(devices) != 1:
            raise ToolError(f'Found {len(devices)} devices named "{name}", expected exactly one')
    elif len(devices) != 1:
        names = ', '.join(f'"{device.name}"' for device in devices) or 'none'
        raise ToolError(f'Found {len(devices)} devices ({names}); pass `device` or set OTII_DEVICE')
    device = devices[0]
    device.add_to_project()
    return device


def measure(otii: otii_client.Connect, device: arc.Arc, duration: float, main_voltage: float,
            max_current: float, channels: list[str], uart_baudrate: Optional[int],
            exp_voltage: Optional[float]) -> recording.Recording:
    '''Power the device under test from the main output for `duration` seconds while
    recording the given channels, then turn power off.'''
    device.set_main_voltage(main_voltage)
    device.set_max_current(max_current)
    if exp_voltage is not None:
        device.set_exp_voltage(exp_voltage)
    if uart_baudrate is not None:
        device.set_uart_baudrate(uart_baudrate)
        if 'rx' not in channels:
            channels = [*channels, 'rx']
    for channel in channels:
        device.enable_channel(channel, True)

    project = otii.get_active_project()
    project.start_recording()
    device.set_main(True)
    try:
        time.sleep(duration)
    finally:
        device.set_main(False)
        project.stop_recording()

    rec = project.get_last_recording()
    assert rec is not None
    return rec


def get_recording(otii: otii_client.Connect, recording_id: Optional[int] = None) -> recording.Recording:
    '''Return the recording with the given id, or the latest recording if id is None.'''
    project = otii.get_active_project()
    if recording_id is None:
        rec = project.get_last_recording()
        if rec is None:
            raise ToolError('There are no recordings in the active project')
        return rec
    for rec in project.get_recordings():
        if rec.id == recording_id:
            return rec
    raise ToolError(f'No recording with id {recording_id} in the active project')


def get_log(device: arc.Arc, rec: recording.Recording) -> list[dict]:
    '''UART log lines as [{"timestamp": seconds, "value": text}, ...].'''
    try:
        count = rec.get_channel_data_count(device.id, 'rx')
    except Exception:
        return []  # The rx channel was not recorded.
    if count == 0:
        return []
    return rec.get_channel_data(device.id, 'rx', 0, count)['values']


def get_statistics(device: arc.Arc, rec: recording.Recording,
                   from_time: float, to_time: float, channel: str = 'mc') -> dict:
    '''min/max/average, plus energy (J) and charge (C) for current channels.'''
    try:
        return rec.get_channel_statistics(device.id, channel, from_time, to_time)
    except otii_exception.Otii_Exception as e:
        if e.type == 'Not ready':
            raise _otii_error(e) from e
        raise ToolError(f'Could not get statistics for channel "{channel}" of recording {rec.id}. '
                        f'Was that channel recorded? (Otii: {e.message})') from e


def get_duration(device: arc.Arc, rec: recording.Recording, channel: str = 'mc') -> tuple[float, float]:
    info = rec.get_channel_info(device.id, channel)
    return info['from'], info['to']


def energy_between_messages(device: arc.Arc, rec: recording.Recording, start_message: str,
                            end_message: Optional[str] = None, occurrence: int = 0) -> dict:
    '''Statistics from the n:th log line equal to start_message to the next line equal to
    end_message (defaults to start_message, i.e. one full cycle of a periodic log).'''
    end_message = end_message or start_message
    log = get_log(device, rec)
    starts = [line['timestamp'] for line in log if line['value'] == start_message]
    if len(starts) <= occurrence:
        raise ToolError(f'Found {len(starts)} "{start_message}" log lines, need at least {occurrence + 1}')
    from_time = starts[occurrence]
    ends = [line['timestamp'] for line in log if line['value'] == end_message and line['timestamp'] > from_time]
    if not ends:
        raise ToolError(f'No "{end_message}" log line after {from_time} s')
    to_time = ends[0]
    return {'from': from_time, 'to': to_time, **get_statistics(device, rec, from_time, to_time)}
