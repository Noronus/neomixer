'''
Stands in for a desktop panel: takes the tray watcher's name, prints what registers with it,
and goes away shortly after.
'''
from gi.repository import Gio, GLib

XML = """
<node>
  <interface name="org.kde.StatusNotifierWatcher">
    <method name="RegisterStatusNotifierItem"><arg type="s" direction="in"/></method>
  </interface>
</node>
"""
loop = GLib.MainLoop()


def call(_bus, _sender, _path, _interface, _method, params, invocation):
    print(params.unpack()[0], flush=True)
    invocation.return_value(None)
    GLib.timeout_add(1500, loop.quit)


bus = Gio.bus_get_sync(Gio.BusType.SESSION)
bus.register_object('/StatusNotifierWatcher', Gio.DBusNodeInfo.new_for_xml(XML).interfaces[0], call, None, None)
Gio.bus_own_name_on_connection(bus, 'org.kde.StatusNotifierWatcher', Gio.BusNameOwnerFlags.NONE, None, None)
GLib.timeout_add_seconds(4, loop.quit)
loop.run()
