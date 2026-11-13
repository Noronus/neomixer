import sys
import logging
import subprocess
from dataclasses import dataclass

from neomixer.scripts import pmctl, pw_eq, pw_phone
from neomixer.model.device_model import DeviceModel
from neomixer.model.signal_model import SignalModel
from neomixer.model.connection_model import ConnectionModel, matched_by_order, pair_match, port_position, removed_pairs
from neomixer.repository.device_repository import DeviceRepository

LOG = logging.getLogger("generic")


@dataclass
class RunningEq:
    '''The filter chain running for a device. "process" is None if an earlier run started it.'''
    process: subprocess.Popen | None
    signature: str
    positions: list[str]


class DeviceController(SignalModel):
    '''
    DeviceController manages device operations and emits signals for UI and other components.

    Signals:
        device_new(device_type: str, device_id: str, device: DeviceModel):
            Emitted when a new device is created.
        device_remove(device_type: str, device_id: str):
            Emitted when a device is removed.
        device_change(device_type: str, device_id: str, device: DeviceModel):
            Emitted when a device's configuration changes.
        connect(input_type: str, input_id: str, output_type: str, output_id: str, state: bool):
            Emitted when a connection between devices is changed.

    Attributes:
        device_repository (DeviceRepository): The repository managing device data.
    '''

    device_repository: DeviceRepository

    def __init__(self, device_repository: DeviceRepository, noinit=False):
        '''
        Initialize the device manager model, check for pmctl, create and connect devices, and cache them.
        '''
        super().__init__()

        self._loopback_procs = {}
        self._running_eqs = {}
        self.device_repository = device_repository
        if not pmctl.is_pipewire():
            LOG.error('ERROR: pipewire-pulse not detected, pipewire-pulse is required')
            sys.exit(1)

        self.setup_graph()

    def setup_graph(self):
        '''
        Build everything in the audio graph from the config: nothing NeoMixer creates survives
        a reboot or a restart of the sound server. Whatever still exists is left in place.
        '''
        # EQs are found again by their process, whose nodes are gone if the server restarted
        self._running_eqs = {}

        # we have to create the virtual devices first
        for device_type in ('vi', 'b'):
            for device_id in self.device_repository.get_devices_by_type(device_type):
                self._try_init_device(device_type, device_id, "Skipping init for %s/%s", cache=False, wait=False)

        # Wait for the ports of every device at once, after creating them all
        self._wait_for_ports([
            device
            for device_type in ('vi', 'b')
            for device in self.device_repository.get_devices_by_type(device_type).values()
        ])

        # Mark any devices PA doesn't currently know about as not present
        for device_type in ('vi', 'b', 'hi', 'a'):
            for device_id, device in self.device_repository.get_devices_by_type(device_type).items():
                if device.init_failed:
                    continue
                device.present = pmctl.device_exists(device.name)
                if device.present:
                    self._restore_device_state(device)

        for device_type in ('vi', 'hi', 'a', 'b'):
            for device_id, device in self.device_repository.get_devices_by_type(device_type).items():
                try:
                    self._setup_eq(device)
                except Exception:
                    LOG.exception("Skipping EQ for %s/%s", device_type, device_id)

        # now we connect the input devices
        for device_type in ('vi', 'hi'):
            for device_id in self.device_repository.get_devices_by_type(device_type):
                try:
                    self.reconnect(device_type, device_id)
                except Exception:
                    LOG.exception("Skipping reconnect for %s/%s", device_type, device_id)

    @staticmethod
    def _restore_device_state(device):
        '''
        Virtual devices come back at full volume, so they get their saved volume, mute and
        primary back. The session manager restores hardware by itself, and its volume may
        have changed while NeoMixer was not running: that one is read rather than set, a
        stale value could be painfully loud.
        '''
        if not DeviceController._is_ours(device):
            pa_device = pmctl.get_device_by_name(device.device_type, device.name)
            if pa_device is not None:
                device.update_from_pa(pa_device)
            return

        pmctl.set_volume(device.device_type, device.name, device.volume[0])
        pmctl.mute(device.device_type, device.name, device.mute)
        if device.primary:
            pmctl.set_primary(device.device_type, device.name)

    @staticmethod
    def _is_ours(device):
        '''
        True for a device NeoMixer creates, which starts out at full volume every time. A phone's
        devices are made anew whenever it connects, so they count although they are hardware strips.
        '''
        return (device.device_class == 'virtual' and not device.external) or device.name in pw_phone.NODE_NAMES

    def _try_init_device(self, device_type, device_id, log_msg, cache=True, wait=True):
        """
        init_device with status-flag bookkeeping. Returns True on success.
        Centralizes the init_failed / init_error update so the four call
        sites (startup, hot-plug, update, create) stay consistent.
        "wait" also waits for the device's ports.
        """
        device = self.device_repository.get_device(device_type, device_id)
        try:
            self.init_device(device_type, device_id, cache=cache)
            device.init_failed = False
            device.init_error = ''
            if wait:
                self._wait_for_ports([device])
            return True
        except Exception as e:
            LOG.exception(log_msg, device_type, device_id)
            device.init_failed = True
            device.init_error = str(e)
            return False

    def init_device(self, device_type, device_id, cache=True):
        '''
        Create Pulse device.
        Args:
            device_type (str): Type of device ('vi', 'b', etc.).
            device_id (str): Device identifier.
            cache (bool): Whether to cache the device.
        '''
        device = self.device_repository.get_device(device_type, device_id)
        if device_type in ('vi', 'b') and not device.external and not pmctl.device_exists(device.name):
            pmctl.create_device(device.device_type, device.name, device.channels, device.channel_list)

    @staticmethod
    def _wait_for_ports(devices):
        '''
        Wait until the virtual devices NeoMixer creates have a port per channel in each direction
        '''
        port_counts = {
            device.name: device.channels
            for device in devices
            if device.device_class == 'virtual' and not device.external and not device.init_failed
        }
        if not port_counts:
            return

        missing = pmctl.wait_for_ports(port_counts)
        if missing:
            LOG.warning('Not all ports of %s appeared in time, linking the ones there are', ', '.join(missing))

    def bulk_connect(self, device_type, device_id, state):
        '''
        Recreate the pmctl connections for a device.
        Args:
            device_type (str): Type of device.
            device_id (str): Device identifier.
            state (bool): Connection state.
        '''
        device = self.device_repository.get_device(device_type, device_id)
        if device_type in ('hi', 'vi'):
            for output_type, connection_list in device.connections.items():
                for output_id in connection_list:
                    self.set_connection(device_type, device_id, output_type, output_id, state, soft=True)
            return

        for input_type in ('hi', 'vi'):
            for input_id in self.device_repository.get_devices_by_type(input_type):
                self.set_connection(input_type, input_id, device_type, device_id, state, soft=True)

    def handle_hotplug(self, device_type: str, device_id: str):
        """
        Re-establish a configured device after it (re)appears in PulseAudio.

        Retries init for previously-failed virtual devices, clears the
        init-failure flag for hardware, marks the device present, then
        reconnects all routes touching it. Connection-level failure flags
        are refreshed as a side effect of set_connection.
        """
        device = self.device_repository.get_device(device_type, device_id)

        if device_type in ('vi', 'b') and device.init_failed:
            self._try_init_device(device_type, device_id, "Hot-plug init retry failed for %s/%s")
        elif device_type in ('hi', 'a'):
            device.init_failed = False
            device.init_error = ''

        device.present = True
        self._restore_after_hotplug(device)
        self.reconnect(device_type, device_id)

    def _restore_after_hotplug(self, device):
        # Pulse reports a device slightly before PipeWire has its ports
        port_type = 'output' if device.device_type == 'source' or device.get_type() == 'vi' else 'input'
        pmctl.wait_for_device_port(port_type, device.name)

        if self._is_ours(device):
            self._restore_device_state(device)

        self._setup_eq(device)

    def handle_unplug(self, device_type: str, device_id: str):
        """
        Mark a configured device as no longer present after it disappears
        from PulseAudio. This is treated as a soft "disconnected" state -
        not a hard error - so route-level connect_failed flags are left
        alone (the dimmed device name communicates the issue).
        """
        device = self.device_repository.get_device(device_type, device_id)
        device.present = False

        # The session manager already removed its nodes, the next plug gets a new one
        if self._uses_smart_filter(device) and device.name in self._running_eqs:
            self._stop_eq(device)

    def reconnect(self, device_type: str, device_id: str):
        '''
        Recreate the pmctl connections for a device.
        Args:
            device_type (str): Type of device.
            device_id (str): Device identifier.
        '''
        device = self.device_repository.get_device(device_type, device_id)

        if device_type in ('hi', 'vi'):
            for output_type, connection_list in device.connections.items():
                for output_id, connection in connection_list.items():
                    if not connection.state:
                        continue

                    self.set_connection(device_type, device_id, output_type, output_id, connection.state, soft=True)
            return

        for input_type in ('hi', 'vi'):
            input_dict = self.device_repository.get_devices_by_type(input_type)
            for input_id, input_device in input_dict.items():
                connection = input_device.connections[device_type][device_id]
                self.set_connection(input_type, input_id, device_type, device_id, connection.state, soft=True)

    def update_device(self, device_schema, device_type, device_id):
        '''
        Update a device's configuration and connections.
        Args:
            device_schema (dict): New device configuration.
            device_type (str): Type of device.
            device_id (str): Device identifier.
        '''
        device = self.device_repository.get_device(device_type, device_id)

        self.bulk_connect(device_type, device_id, False)
        self._stop_eq(device)

        if device_type in ('vi', 'b') and device.external is False:
            pmctl.remove_device(device.name)

        # update values
        self.device_repository.update_device(device_type, device_id, device_schema)

        # change connection settings
        if device_type in ('vi', 'hi'):
            self.handle_input_change(device_type, device_id)
        else:
            self.handle_output_change(device_type, device_id)

        if device_type in ('vi', 'b'):
            if self._try_init_device(device_type, device_id, "Failed to init %s/%s on update") and device.primary:
                pmctl.set_primary(device.device_type, device.name)

        self._setup_eq(device)

        self.reconnect(device_type, device_id)

        self.emit('device_change', device_type, device_id, device)

    def cleanup(self):
        '''
        Removes all pulse devices from pulse and terminates loopback processes.
        '''
        # Kill route loopbacks by name (ours or not) before their sinks go away
        for input_type in ('vi', 'hi'):
            for input_device in self.device_repository.get_devices_by_type(input_type).values():
                for output_type, connections in input_device.connections.items():
                    for output_id in connections:
                        output_device = self.device_repository.get_device(output_type, output_id)
                        if output_device is not None:
                            self._teardown_loopback(pmctl.make_loopback_name(input_device.name, output_device.name))

        # Terminate all loopback processes
        for proc in self._loopback_procs.values():
            pmctl.destroy_loopback(proc)
        self._loopback_procs.clear()

        for device_list in self.device_repository.get_all_devices().values():
            for device in device_list.values():
                self._stop_eq(device)

        device_dict = self.device_repository.get_all_devices()
        for _, device_list in device_dict.items():
            for _, device in device_list.items():
                if device.device_class == 'virtual' and not device.external:
                    pmctl.remove_device(device.name)

    def set_volume(self, device_type, device_id, volume: int):
        '''
        Set the volume of a device.
        Args:
            device_type (str): Type of device.
            device_id (str): Device identifier.
            volume (int): Volume value.
        '''
        device = self.device_repository.get_device(device_type, device_id)
        device.set_volume(volume, emit=False)
        pmctl.set_volume(device.device_type, device.name, volume)

    def set_mute(self, device_type, device_id, state: bool):
        '''
        Set the mute state of a device.
        Args:
            device_type (str): Type of device.
            device_id (str): Device identifier.
            state (bool): Mute state.
        '''
        device = self.device_repository.get_device(device_type, device_id)
        if state is None:
            state = not device.mute

        device.set_mute(state, emit=False)
        pmctl.mute(device.device_type, device.name, state)

    def set_primary(self, device_type, device_id):
        '''
        Set a device as primary.
        Args:
            device_type (str): Type of device.
            device_id (str): Device identifier.
        '''
        device = self.device_repository.get_device(device_type, device_id)
        if device.primary is True:
            return

        # Holding is about what the mixer says, and now it says this one
        held = any(other.hold_primary for other in self.device_repository.get_devices_by_type(device_type).values())
        self.unset_primary(device_type)

        device.set_primary(True, emit=False)
        device.hold_primary = held
        pmctl.set_primary(device.device_type, device.name)

    def set_hold_primary(self, device_type, device_id, state):
        '''
        Keep a device the default: when something else changes the default, it switches back.
        Args:
            device_type (str): Type of device.
            device_id (str): Device identifier.
            state (bool): Whether to hold it.
        '''
        device = self.device_repository.get_device(device_type, device_id)
        if not state:
            device.hold_primary = False
            return

        # In this order: the listener must not take the default back to the device held before
        self.unset_primary(device_type)
        device.set_primary(True, emit=False)
        device.hold_primary = True
        pmctl.set_primary(device.device_type, device.name)

    def unset_primary(self, device_type):
        '''
        Unset all devices as primary for a given type.
        Args:
            device_type (str): Type of device.
        '''
        for _, device in self.device_repository.get_devices_by_type(device_type).items():
            device.hold_primary = False
            device.set_primary(False, emit=False)

    def set_connection(self, input_type, input_id, output_type, output_id, state: bool = None, soft=False):
        '''
        Set the connection state between two devices.
        Uses pw-loopback when use_loopback is enabled, otherwise uses direct pw-link.
        Args:
            input_type (str): Input device type.
            input_id (str): Input device identifier.
            output_type (str): Output device type.
            output_id (str): Output device identifier.
            state (bool, optional): Connection state. If None, toggles state.
            soft (bool): If True, do not save to config.
        '''
        input_device = self.device_repository.get_device(input_type, input_id)
        output_device = self.device_repository.get_device(output_type, output_id)
        connection_model = input_device.connections[output_type][output_id]

        if state is None:
            state = not connection_model.state

        # by soft we mean dont save to config
        if soft is False:
            input_device.set_connection(output_type, output_id, state, emit=False)
            self.emit('connect', input_type, input_id, output_type, output_id, state)

        loopback_name = pmctl.make_loopback_name(input_device.name, output_device.name)

        # A warning only describes the last link
        connection_model.clear_warnings()

        # Disconnects always clear the failure flag
        if not state:
            connection_model.connect_failed = False
            connection_model.connect_error = ''

        # Skip PA operations if either device is disconnected. We do NOT flag
        # the route as failed here - a device-level problem is already
        # communicated by the device widget itself (warning triangle for a
        # real error, dimmed name for a soft "not present"). Route warnings
        # are reserved for failures that originate at the route level.
        if state and not self._device_available(input_device):
            LOG.warning('Input device unavailable, skipping connection: %s', input_device.name)
            return
        if state and not self._device_available(output_device):
            LOG.warning('Output device unavailable, skipping connection: %s', output_device.name)
            return

        if connection_model.use_loopback:
            self._set_loopback_connection(
                loopback_name, input_device, output_device, connection_model, state
            )
        else:
            self._set_link_connection(input_device, output_device, connection_model, state)
            self._teardown_loopback(loopback_name)

    def _device_available(self, device):
        '''Check device presence via pw-link subprocess.'''
        port_type = 'output' if device.device_type == 'source' else 'input'
        try:
            return len(pmctl.get_ports(port_type, device.name)) > 0
        except RuntimeError:
            return False

    def _set_link_connection(self, input_device, output_device, connection_model, state, previous=None, loopback_name=None):
        '''
        Direct pw-link connection.
            "previous" is a copy of the connection from before its port settings
            were edited: pairs only it maps are unlinked, and the rest keep playing
            "loopback_name" names the route's loopback, whose playback side is linked
            in place of the input device
        '''
        connection_model.clear_warnings()
        try:
            # Map and link on the live ports, not the layout saved in the config
            input_ports = pmctl.get_ports('output', input_device.name)
            output_ports = pmctl.get_ports('input', output_device.name)
            input_channels = input_device.get_selected_channels(input_ports)
            output_channels = output_device.get_selected_channels(output_ports)
            port_map = connection_model.str_port_map(input_channels, output_channels)

            # EQs and the loopback carry each channel on its port of the same index, so the map applies as it is
            source_name, source_ports = self._route_source(input_device, input_ports)
            if loopback_name is not None:
                source_name = pmctl.loopback_nodes(loopback_name)[1]
                source_ports = pmctl.get_ports('output', source_name)
            target_name, target_ports = self._route_target(output_device, output_ports)

            # An edit while a device has no ports only saves, its device widget already shows it is gone
            if previous is not None and not (source_ports and target_ports):
                return

            # Relinking a pair that is already linked is a no-op, so only the removed pairs need unlinking
            if previous is not None:
                pmctl.link_channels(
                    source_name, target_name,
                    removed_pairs(previous.str_port_map(input_channels, output_channels), port_map), False,
                    input_ports=source_ports, output_ports=target_ports
                )

            if loopback_name is not None and state:
                self._unlink_stale_loopback_links(source_name, source_ports, target_name, target_ports, port_map)

            # A manual map whose channels are all gone links nothing, which pmctl would report as success
            manual_links = not connection_model.auto_ports and any(connection_model.port_map)
            if state and manual_links and not port_map and input_channels and output_channels:
                LOG.warning('Every manual link is missing between %s and %s', input_device.name, output_device.name)
                connection_model.connect_failed = True
                connection_model.connect_error = ''
                connection_model.missing_links = connection_model.count_missing_links(input_channels, output_channels)
                return

            unlinked = pmctl.link_channels(
                source_name, target_name, port_map, state,
                input_ports=source_ports, output_ports=target_ports
            )

            # Warnings name the devices' own channels, not the ports carrying them
            input_port_of = dict(zip(source_ports, input_ports))
            output_port_of = dict(zip(target_ports, output_ports))
            unlinked = [
                input_port_of.get(source, source) + arrow + output_port_of.get(target, target)
                for source, arrow, target in (pair.partition(' -> ') for pair in unlinked)
            ]

            if state:
                connection_model.connect_failed = False
                connection_model.connect_error = ''
                self._set_route_warning(
                    connection_model, input_ports=input_ports, output_ports=output_ports,
                    input_channels=input_channels, output_channels=output_channels, unlinked=unlinked
                )
        except Exception as e:
            # Broad catch: pmctl.link_channels can raise RuntimeError (missing
            # ports) or IndexError/ValueError (malformed port_map). Crashing
            # here would abort reconnect for sibling routes, so we flag and
            # continue.
            LOG.exception("Failed to link %s -> %s", input_device.name, output_device.name)
            if state:
                connection_model.connect_failed = True
                connection_model.connect_error = f'Link failed: {e}'

    @staticmethod
    def _set_route_warning(connection_model, *, input_ports, output_ports, input_channels, output_channels, unlinked):
        '''
        Record what a linked route leaves out, and the pairs automatic mapping matched by order.
        Output channels left without a source, and channels the user deselected, are not warned about.
        '''
        if connection_model.auto_ports:
            output_positions = [port_position(port) for port in output_ports]
            unrouted = connection_model.unrouted_channels(input_channels, output_channels, output_positions)
            connection_model.unrouted_ports = [input_ports[index] for index in unrouted]

            if matched_by_order(input_channels, output_channels):
                connection_model.order_pairs = [
                    f'{input_ports[input_index]} -> {output_ports[output_index]}'
                    for input_index, output_index in pair_match(input_channels, output_channels)
                ]
        else:
            connection_model.missing_links = connection_model.count_missing_links(input_channels, output_channels)

        connection_model.failed_pairs = unlinked

    @staticmethod
    def _unlink_stale_loopback_links(playback_name, playback_ports, output_name, output_ports, port_map):
        '''
        Unlink whatever a route loopback plays into that the port map does not link. The loopback
        carries only this route, so those links are left over from an earlier map, such as one
        edited while the input was gone, or from an earlier run.
        '''
        kept = set()
        for pair in port_map.split():
            source, target = (int(index) for index in pair.split(':'))
            if source < len(playback_ports) and target < len(output_ports):
                kept.add((f'{playback_name}:{playback_ports[source]}', f'{output_name}:{output_ports[target]}'))

        for link in pmctl.get_links(playback_name):
            if link not in kept:
                pmctl.link(*link, state=False)

    def _set_loopback_connection(self, loopback_name, input_device, output_device, connection_model, state):
        '''
        pw-loopback connection for per-route volume control. The loopback takes every input
        channel on its port of the same index, and its playback side is linked to the output
        by the route's port map, as a direct route is.
        '''
        if not state:
            self._teardown_loopback(loopback_name)
            return

        try:
            input_ports = pmctl.get_ports('output', input_device.name)
            channels = len(input_ports)
            if not channels:
                raise RuntimeError(f'Ports not found for device {input_device.name}')

            if self._loopback_is_current(loopback_name, input_device, output_device, channels):
                LOG.debug('Loopback %s already exists, adopting', loopback_name)
            else:
                self._teardown_loopback(loopback_name)
                proc = pmctl.create_loopback(loopback_name, input_device.name, output_device.name, channels)
                self._loopback_procs[loopback_name] = proc
                if not pmctl.wait_for_loopback(proc, loopback_name, channels):
                    raise RuntimeError('pw-loopback did not start in time')

            # Set before the playback side is linked, so the route never plays at the wrong volume
            pmctl.set_route_volume(loopback_name, connection_model.route_volume)

            capture_map = ' '.join(f'{index}:{index}' for index in range(channels))
            source_name, source_ports = self._route_source(input_device, input_ports)
            capture_failed = pmctl.link_channels(
                source_name, pmctl.loopback_nodes(loopback_name)[0], capture_map, input_ports=source_ports
            )
        except Exception as e:
            LOG.exception('Failed to create loopback %s', loopback_name)
            connection_model.connect_failed = True
            connection_model.connect_error = f'Failed to create loopback: {e}'
            return

        self._set_link_connection(input_device, output_device, connection_model, True, loopback_name=loopback_name)
        connection_model.failed_pairs += capture_failed

    def _loopback_is_current(self, loopback_name, input_device, output_device, channels):
        '''True if exactly one loopback with this name is running, made for these devices and channel count.'''
        if pmctl.get_loopback_targets(loopback_name) != (input_device.name, output_device.name):
            return False

        return len(pmctl.get_ports('output', pmctl.loopback_nodes(loopback_name)[1])) == channels

    def _teardown_loopback(self, loopback_name):
        '''Destroy any loopback with this name, ours or a leftover, and the bridge sink older versions gave it.'''
        stopped = 0
        if loopback_name in self._loopback_procs:
            pmctl.destroy_loopback(self._loopback_procs.pop(loopback_name))
            stopped += 1
        stopped += pmctl.kill_stray_loopback(loopback_name)
        if not stopped:
            return

        # A replacement must not start while the old streams are still listed
        if not pmctl.wait_for_loopback_gone(loopback_name):
            LOG.warning('Loopback %s still present after teardown', loopback_name)
        pmctl.remove_bridge(loopback_name)

    #
    # # EQ
    #
    @staticmethod
    def _is_output(device):
        return device.get_type() in ('a', 'b')

    @staticmethod
    def _uses_smart_filter(device):
        '''
        Programs can play straight into a hardware output, only the session manager can put
        those through its EQ. Everything reaches the other devices through their routes.
        '''
        return device.get_type() == 'a'

    def _route_source(self, input_device, input_ports):
        '''
        The node a device's routes play from, with its ports: the device, or its EQ if running
        '''
        if input_device.name not in self._running_eqs:
            return input_device.name, input_ports

        playback = pw_eq.eq_nodes(input_device.name)[1]
        return playback, pmctl.get_ports('output', playback)

    def _route_target(self, output_device, output_ports):
        '''
        The node routes to a device play into, with its ports: the device, or its EQ if running
        '''
        if output_device.name not in self._running_eqs:
            return output_device.name, output_ports

        capture = pw_eq.eq_nodes(output_device.name)[0]
        return capture, pmctl.get_ports('input', capture)

    def set_eq(self, device_type, device_id):
        '''
        Bring the running audio in line with a device's EQ settings. Most changes reach the
        running filters without a sound. Starting the EQ, or changing a band's filter type,
        has to rebuild it and relink the routes, which leaves a short gap in the audio.
        '''
        device = self.device_repository.get_device(device_type, device_id)
        running = self._running_eqs.get(device.name)

        if running is None:
            if device.eq.enabled:
                self._insert_eq(device_type, device_id, device)
            return

        if pw_eq.signature(running.positions, device.eq, self._uses_smart_filter(device)) == running.signature:
            pw_eq.set_params(device.name, device.eq)
            return

        self._unlink_routes(device_type, device_id, device)
        self._stop_eq(device)
        self._start_eq(device)
        self.reconnect(device_type, device_id)

    def _insert_eq(self, device_type, device_id, device):
        # Started before the routes are touched: the gap is only as long as relinking them
        if not self._start_eq(device):
            return

        running = self._running_eqs.pop(device.name)
        self._unlink_routes(device_type, device_id, device)
        self._running_eqs[device.name] = running
        self.reconnect(device_type, device_id)

    def _unlink_routes(self, device_type, device_id, device):
        '''Unlink the active routes of an input or an output, to link them again differently.'''
        if not self._is_output(device):
            for output_type, connections in device.connections.items():
                for output_id, connection in connections.items():
                    if connection.state:
                        self.set_connection(device_type, device_id, output_type, output_id, False, soft=True)
            return

        for input_type in ('vi', 'hi'):
            for input_id, input_device in self.device_repository.get_devices_by_type(input_type).items():
                connection = input_device.connections[device_type].get(device_id)
                if connection is not None and connection.state:
                    self.set_connection(input_type, input_id, device_type, device_id, False, soft=True)

    def _eq_ports(self, device):
        '''The ports of a device its EQ attaches to.'''
        try:
            return pmctl.get_ports('input' if self._is_output(device) else 'output', device.name)
        except RuntimeError:
            return []

    def _setup_eq(self, device):
        '''Have the EQ a device's settings ask for running and connected, before its routes get linked.'''
        running = self._running_eqs.get(device.name)
        if running is not None:
            if pw_eq.wait_ready(device.name, len(running.positions), timeout=0.2):
                self._connect_eq(device)
                return
            self._stop_eq(device)

        if device.eq.enabled:
            self._start_eq(device)
        else:
            # one left from an earlier run, whose EQ was switched off since
            pw_eq.stop(device.name)

    def _start_eq(self, device):
        '''
        Start a device's EQ and connect it to the device, or take over a matching one from an
        earlier run. That one keeps playing, a restart of NeoMixer is not audible.
        '''
        ports = self._eq_ports(device)
        if not ports:
            LOG.warning('%s has no ports, not starting its EQ for now', device.name)
            return False

        positions = [port_position(port) for port in ports]
        smart = self._uses_smart_filter(device)
        signature = pw_eq.signature(positions, device.eq, smart)
        process = None
        adopted = pw_eq.running_signature(device.name) == signature and pw_eq.wait_ready(device.name, len(ports), timeout=0.2)
        if adopted:
            pw_eq.set_params(device.name, device.eq)
        else:
            pw_eq.stop(device.name)
            process = pw_eq.start(device.name, positions, device.eq, smart)
            if not pw_eq.wait_ready(device.name, len(ports)):
                LOG.error('The EQ of %s did not start', device.name)
                pw_eq.stop(device.name, process)
                return False

        self._running_eqs[device.name] = RunningEq(process, signature, positions)
        self._connect_eq(device)
        return True

    def _connect_eq(self, device):
        '''Link an EQ to its device: routes get linked to it the way they were to the device.'''
        capture, playback = pw_eq.eq_nodes(device.name)
        ports = self._eq_ports(device)
        channels = ' '.join(f'{index}:{index}' for index in range(len(ports)))
        try:
            if not self._is_output(device):
                pmctl.link_channels(device.name, capture, channels, input_ports=ports)
                return

            # It is the session manager's to link, by hand only if it does not know smart filters
            if not self._uses_smart_filter(device) or not pw_eq.wait_linked(device.name):
                pmctl.link_channels(playback, device.name, channels, output_ports=ports)
        except RuntimeError:
            LOG.warning('Could not connect the EQ of %s', device.name)

    def _stop_eq(self, device):
        running = self._running_eqs.pop(device.name, None)
        pw_eq.stop(device.name, running.process if running else None)

    def set_route_volume(self, input_type, input_id, output_type, output_id, volume):
        '''Set per-route volume for a loopback connection.'''
        input_device = self.device_repository.get_device(input_type, input_id)
        output_device = self.device_repository.get_device(output_type, output_id)
        connection_model = input_device.connections[output_type][output_id]
        connection_model.route_volume = volume

        loopback_name = pmctl.make_loopback_name(input_device.name, output_device.name)
        if connection_model.state and connection_model.use_loopback:
            pmctl.set_route_volume(loopback_name, volume)

        self.emit('route_volume', input_type, input_id, output_type, output_id, volume)

    def set_use_loopback(self, input_type, input_id, output_type, output_id, state):
        '''Toggle use_loopback for a connection. If active, reconnects with the new method.'''
        input_device = self.device_repository.get_device(input_type, input_id)
        output_device = self.device_repository.get_device(output_type, output_id)
        connection_model = input_device.connections[output_type][output_id]

        was_connected = connection_model.state
        old_use_loopback = connection_model.use_loopback

        if state == old_use_loopback:
            return

        loopback_name = pmctl.make_loopback_name(input_device.name, output_device.name)

        # Skip PA operations if either device is disconnected
        devices_available = self._device_available(input_device) and self._device_available(output_device)

        # If connection is active, disconnect with old method, flip, reconnect with new
        if was_connected and devices_available:
            if old_use_loopback:
                self._set_loopback_connection(loopback_name, input_device, output_device, connection_model, False)
            else:
                self._set_link_connection(input_device, output_device, connection_model, False)

        connection_model.use_loopback = state

        if was_connected and devices_available:
            if state:
                self._set_loopback_connection(loopback_name, input_device, output_device, connection_model, True)
            else:
                self._set_link_connection(input_device, output_device, connection_model, True)

        self.emit('use_loopback', input_type, input_id, output_type, output_id, state)

    def update_connection(self, input_type, input_id, output_type, output_id, connection_model):
        '''
        Update the port settings of a connection between two devices, relinking
        only the channel pairs that change so the rest of the route keeps playing.
        Args:
            input_type (str): Input device type.
            input_id (str): Input device identifier.
            output_type (str): Output device type.
            output_id (str): Output device identifier.
            connection_model: Connection model carrying the new auto_ports and port_map.
        '''
        input_device = self.device_repository.get_device(input_type, input_id)
        output_device = self.device_repository.get_device(output_type, output_id)
        cur_connection_model = input_device.connections[output_type][output_id]
        previous = cur_connection_model.model_copy(deep=True)

        cur_connection_model.auto_ports = connection_model.auto_ports
        cur_connection_model.port_map = connection_model.port_map

        # Nothing is linked through the map when the route is off
        if not cur_connection_model.state:
            return

        loopback_name = None
        if cur_connection_model.use_loopback:
            loopback_name = pmctl.make_loopback_name(input_device.name, output_device.name)

        self._set_link_connection(
            input_device, output_device, cur_connection_model, True, previous=previous, loopback_name=loopback_name
        )

    def create_connection(self, input_device, output_device):
        '''
        Create a connection model between two devices.
        Args:
            input_device (DeviceModel): Input device.
            output_device (DeviceModel): Output device.
        Returns:
            ConnectionModel: The connection model object.
        '''
        return ConnectionModel(
            nick=output_device.nick,
            input_name=input_device.name,
            output_name=output_device.name,
            input_sel_channels=input_device.selected_channels,
            output_sel_channels=output_device.selected_channels
        )

    def handle_output_change(self, output_type, output_id):
        '''
        Reconfigure connections to the output in all inputs.
        Args:
            output_type (str): Output device type.
            output_id (str): Output device identifier.
        '''
        output_device = self.device_repository.get_device(output_type, output_id)
        for input_type in ('vi', 'hi'):
            for _, input_device in self.device_repository.get_devices_by_type(input_type).items():
                connection_model = input_device.connections[output_type][output_id]
                connection_model.nick = output_device.nick
                connection_model.reload_settings(output_sel_channels=output_device.selected_channels)

    def handle_input_change(self, input_type, input_id):
        '''
        Reconfigure connections on a specific input device.
        Args:
            input_type (str): Input device type.
            input_id (str): Input device identifier.
        '''
        input_device = self.device_repository.get_device(input_type, input_id)
        for output_type in input_device.connections:
            for output_id in self.device_repository.get_devices_by_type(output_type):
                connection_model = input_device.connections[output_type][output_id]
                connection_model.reload_settings(input_sel_channels=input_device.selected_channels)

    def handle_new_output(self, output_type, output_id, output_device):
        '''
        Create connections to the output in all inputs.
        Args:
            output_type (str): Output device type.
            output_id (str): Output device identifier.
            output_device (DeviceModel): Output device object.
        '''
        for input_type in ('vi', 'hi'):
            for _, input_device in self.device_repository.get_devices_by_type(input_type).items():
                connection_model = self.create_connection(input_device, output_device)
                input_device.create_connection(output_type, output_id, connection_model)

    def handle_new_input(self, input_device: DeviceModel):
        '''
        Create connections to existing output devices for a new input device.
        Args:
            input_device (DeviceModel): The new input device.
        '''
        for output_type in input_device.connections:
            for output_id, output_device in self.device_repository.get_devices_by_type(output_type).items():
                connection_model = self.create_connection(input_device, output_device)
                input_device.create_connection(output_type, output_id, connection_model)

    def create_device(self, device_dict: dict):
        '''
        Insert a device into config.
        Args:
            device_dict (dict): Device configuration dictionary.
        Returns:
            Tuple of (device_type, device_id, device).
        '''
        device_type, device_id, device = self.device_repository.create_device(device_dict)
        if device_type in ('vi', 'hi'):
            self.handle_new_input(device)
        else:
            self.handle_new_output(device_type, device_id, device)

        self._try_init_device(device_type, device_id, "Failed to init new %s/%s")

        self.emit('device_new', device_type, device_id, device)
        return device_type, device_id, device

    def remove_device(self, device_type: str, device_id: str, cache=True):
        '''
        Remove a device from config.
        Args:
            device_type (str): Type of device.
            device_index (str): Device identifier.
            cache (bool): Whether to update the cache.
        Returns:
            DeviceModel: The removed device object.
        '''
        if device_type in ('a', 'hi'):
            self.bulk_connect(device_type, device_id, False)

        device = self.device_repository.remove_device(device_type, device_id)

        # remove connection dict from input devices
        if device.get_type() in ('a', 'b'):
            for input_type in ('vi', 'hi'):
                for input_device in self.device_repository.get_devices_by_type(input_type).values():
                    input_device.connections[device_type].pop(device_id)

        self._stop_eq(device)

        if device.device_class == 'virtual':
            pmctl.remove_device(device.name)

        self.emit('device_remove', device_type, device_id)
        return device

    @classmethod
    def list_devices(self, device_type):
        '''
        List devices of a given type.
        Args:
            device_type (str): Type of device ('a' for sink, 'b' for source).
        Returns:
            list: List of DeviceModel objects.
        '''
        dvtp = 'sink' if device_type == 'a' else 'source'
        pa_device_list = pmctl.list_devices(dvtp)
        device_list = []
        for device in pa_device_list:
            device_model = DeviceModel.pa_to_device_model(device, dvtp)
            device_list.append(device_model)
        return device_list
