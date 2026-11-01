import logging
from contextlib import asynccontextmanager

import pulsectl
import pulsectl_asyncio

from neomixer.utils.app_identity import is_app_stream

LOG = logging.getLogger('generic')


@asynccontextmanager
async def pulse_connection(client_name=None):
    '''
    Drop-in for `async with PulseAsync()` that also closes the connection if
    connect() is interrupted. PulseAsync.__aenter__ raising (e.g. the task is
    cancelled mid-handshake) skips __aexit__, leaving the client connected to
    the pulse server forever.
    '''
    pulse = pulsectl_asyncio.PulseAsync(client_name)
    try:
        await pulse.connect()
        yield pulse
    finally:
        pulse.close()


async def get_device_by_id(device_type: str, device_id: int, pulse):
    info = pulse.sink_info if device_type == 'sink' else pulse.source_info
    device = await info(int(device_id))
    return device


async def get_app_by_id(app_type, app_index: int, pulse):
    try:
        if app_type == 'sink_input':
            app = await pulse.sink_input_info(int(app_index))
            device = await pulse.sink_info(int(app.sink))
        else:
            app = await pulse.source_output_info(int(app_index))
            device = await pulse.source_info(int(app.source))
    except pulsectl.PulseIndexError:
        return None

    # the output a stream was put on, also with an EQ in front of it, see pmctl.device_behind_filter
    app.device_name = device.proplist.get('neomixer.eq.device', device.name)

    if not is_app_stream(app.proplist, app.name):
        return None

    # a stream may not carry the process name, its client does
    props = None
    if app.client is not None:
        props = await get_client_props(app.client, pulse)
    app.client_props = props or {}

    return app


async def get_client_props(client_index: int, pulse):
    '''Returns the proplist of a pulse client, None if it is gone already.'''
    try:
        return (await pulse.client_info(int(client_index))).proplist
    except pulsectl.PulseIndexError:
        return None


async def get_primary(device_type: str, pulse):
    try:
        if device_type == 'sink':
            return await pulse.sink_default_get()
        return await pulse.source_default_get()
    except pulsectl.PulseIndexError:
        LOG.debug('No resolvable default %s', device_type)
        return None


async def set_primary(device_type: str, device_name: str, pulse) -> bool:
    '''Make a device the default, False if it is not there.'''
    try:
        if device_type == 'sink':
            device = await pulse.get_sink_by_name(device_name)
        else:
            device = await pulse.get_source_by_name(device_name)
    except pulsectl.PulseIndexError:
        LOG.debug('No %s %s to make the default', device_type, device_name)
        return False

    await pulse.default_set(device)
    return True


async def subscribe_peak(name, device_type, callback, stream_index=None, rate=20):
    '''
    Feed peaks of a device, or of one playback stream (by index) on a sink, to `callback`.
    '''
    is_device_meter = stream_index is None
    if device_type == 'sink':
        name += '.monitor'

    async with pulse_connection(f'{name}_{device_type}_peak') as pulse:
        if is_device_meter:
            try:
                await pulse.get_source_by_name(name)
            except pulsectl.PulseError:
                LOG.debug('Peak source %s not present, idling vumeter', name)
                await callback(0.0)
                return

        async for peak in pulse.subscribe_peak_sample(name, rate, stream_idx=stream_index):
            await callback(peak)
