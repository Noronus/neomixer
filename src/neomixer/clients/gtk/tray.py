'''
Tray icon through the StatusNotifierItem spec, which KDE, and most others, implement without
the old X11 tray. GTK 4 has nothing for this, but all it takes is a D-Bus object or two.
'''
import os
import gettext
import logging

from neomixer.settings import APP_ID

# pylint: disable=wrong-import-order,wrong-import-position
from gi.repository import Gio, GLib  # noqa: E402
# pylint: enable=wrong-import-order,wrong-import-position

LOG = logging.getLogger('generic')
_ = gettext.gettext

ITEM_PATH = '/StatusNotifierItem'
MENU_PATH = '/StatusNotifierItem/Menu'
WATCHER_NAME = 'org.kde.StatusNotifierWatcher'
WATCHER_PATH = '/StatusNotifierWatcher'

ITEM_XML = '''
<node>
  <interface name="org.kde.StatusNotifierItem">
    <property name="Category" type="s" access="read"/>
    <property name="Id" type="s" access="read"/>
    <property name="Title" type="s" access="read"/>
    <property name="Status" type="s" access="read"/>
    <property name="IconName" type="s" access="read"/>
    <property name="IconThemePath" type="s" access="read"/>
    <property name="ItemIsMenu" type="b" access="read"/>
    <property name="Menu" type="o" access="read"/>
    <property name="ToolTip" type="(sa(iiay)ss)" access="read"/>
    <method name="Activate"><arg name="x" type="i" direction="in"/><arg name="y" type="i" direction="in"/></method>
    <method name="SecondaryActivate"><arg name="x" type="i" direction="in"/><arg name="y" type="i" direction="in"/></method>
    <method name="ContextMenu"><arg name="x" type="i" direction="in"/><arg name="y" type="i" direction="in"/></method>
    <method name="Scroll"><arg name="delta" type="i" direction="in"/><arg name="orientation" type="s" direction="in"/></method>
    <signal name="NewStatus"><arg type="s"/></signal>
    <signal name="NewIcon"/>
  </interface>
</node>
'''

MENU_XML = '''
<node>
  <interface name="com.canonical.dbusmenu">
    <property name="Version" type="u" access="read"/>
    <property name="TextDirection" type="s" access="read"/>
    <property name="Status" type="s" access="read"/>
    <property name="IconThemePath" type="as" access="read"/>
    <method name="GetLayout">
      <arg name="parentId" type="i" direction="in"/>
      <arg name="recursionDepth" type="i" direction="in"/>
      <arg name="propertyNames" type="as" direction="in"/>
      <arg name="revision" type="u" direction="out"/>
      <arg name="layout" type="(ia{sv}av)" direction="out"/>
    </method>
    <method name="GetGroupProperties">
      <arg name="ids" type="ai" direction="in"/>
      <arg name="propertyNames" type="as" direction="in"/>
      <arg name="properties" type="a(ia{sv})" direction="out"/>
    </method>
    <method name="GetProperty">
      <arg name="id" type="i" direction="in"/>
      <arg name="name" type="s" direction="in"/>
      <arg name="value" type="v" direction="out"/>
    </method>
    <method name="Event">
      <arg name="id" type="i" direction="in"/>
      <arg name="eventId" type="s" direction="in"/>
      <arg name="data" type="v" direction="in"/>
      <arg name="timestamp" type="u" direction="in"/>
    </method>
    <method name="EventGroup">
      <arg name="events" type="a(isvu)" direction="in"/>
      <arg name="idErrors" type="ai" direction="out"/>
    </method>
    <method name="AboutToShow">
      <arg name="id" type="i" direction="in"/>
      <arg name="needUpdate" type="b" direction="out"/>
    </method>
    <method name="AboutToShowGroup">
      <arg name="ids" type="ai" direction="in"/>
      <arg name="updatesNeeded" type="ai" direction="out"/>
      <arg name="idErrors" type="ai" direction="out"/>
    </method>
    <signal name="LayoutUpdated"><arg type="u"/><arg type="i"/></signal>
    <signal name="ItemsPropertiesUpdated"><arg type="a(ia{sv})"/><arg type="a(ias)"/></signal>
    <signal name="ItemActivationRequested"><arg type="i"/><arg type="u"/></signal>
  </interface>
</node>
'''

MENU_ROOT = 0
MENU_SHOW = 1
MENU_SEPARATOR = 2
MENU_QUIT = 3


class TrayIcon:
    '''
    Left click shows or hides the window, the menu has the same and a way to quit.
    Without a tray on the desktop nothing shows, and launching the app again opens the window.
    '''

    def __init__(self, on_toggle_window, on_quit):
        self.on_toggle_window = on_toggle_window
        self.on_quit = on_quit
        self.bus_name = f'org.kde.StatusNotifierItem-{os.getpid()}-1'
        self.registered = False
        self.bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)

        item = Gio.DBusNodeInfo.new_for_xml(ITEM_XML).interfaces[0]
        menu = Gio.DBusNodeInfo.new_for_xml(MENU_XML).interfaces[0]
        self.object_ids = [
            self.bus.register_object(ITEM_PATH, item, self._on_item_call, self._on_item_property, None),
            self.bus.register_object(MENU_PATH, menu, self._on_menu_call, self._on_menu_property, None),
        ]
        self.name_id = Gio.bus_own_name_on_connection(
            self.bus, self.bus_name, Gio.BusNameOwnerFlags.NONE, None, None
        )
        # Registers now if there is a tray, and again whenever one starts (plasmashell restarting)
        self.watch_id = Gio.bus_watch_name_on_connection(
            self.bus, WATCHER_NAME, Gio.BusNameWatcherFlags.NONE, self._on_watcher_appeared, self._on_watcher_vanished
        )

    def close(self):
        Gio.bus_unwatch_name(self.watch_id)
        Gio.bus_unown_name(self.name_id)
        for object_id in self.object_ids:
            self.bus.unregister_object(object_id)

    def _on_watcher_appeared(self, bus, *_):
        bus.call(
            WATCHER_NAME, WATCHER_PATH, WATCHER_NAME, 'RegisterStatusNotifierItem',
            GLib.Variant('(s)', (self.bus_name,)), None, Gio.DBusCallFlags.NONE, -1, None, self._on_registered
        )

    def _on_registered(self, bus, result):
        try:
            bus.call_finish(result)
            self.registered = True
        except GLib.Error as e:
            LOG.warning('The tray refused the icon: %s', e.message)

    def _on_watcher_vanished(self, *_):
        self.registered = False

    # org.kde.StatusNotifierItem
    def _on_item_property(self, _bus, _sender, _path, _interface, name):
        values = {
            'Category': GLib.Variant('s', 'Hardware'),
            'Id': GLib.Variant('s', 'neomixer'),
            'Title': GLib.Variant('s', 'NeoMixer'),
            'Status': GLib.Variant('s', 'Active'),
            'IconName': GLib.Variant('s', APP_ID),
            'IconThemePath': GLib.Variant('s', ''),
            'ItemIsMenu': GLib.Variant('b', False),
            'Menu': GLib.Variant('o', MENU_PATH),
            'ToolTip': GLib.Variant('(sa(iiay)ss)', (APP_ID, [], 'NeoMixer', '')),
        }
        return values.get(name)

    def _on_item_call(self, _bus, _sender, _path, _interface, method, _params, invocation):
        if method == 'Activate':
            GLib.idle_add(self._toggle_window)
        invocation.return_value(None)

    def _toggle_window(self):
        self.on_toggle_window()
        return False

    def _quit(self):
        self.on_quit()
        return False

    # com.canonical.dbusmenu
    def _on_menu_property(self, _bus, _sender, _path, _interface, name):
        values = {
            'Version': GLib.Variant('u', 3),
            'TextDirection': GLib.Variant('s', 'ltr'),
            'Status': GLib.Variant('s', 'normal'),
            'IconThemePath': GLib.Variant('as', []),
        }
        return values.get(name)

    @staticmethod
    def _item_properties(item_id):
        if item_id == MENU_SHOW:
            return {'label': GLib.Variant('s', _('Show or hide NeoMixer')), 'enabled': GLib.Variant('b', True)}
        if item_id == MENU_SEPARATOR:
            return {'type': GLib.Variant('s', 'separator')}
        if item_id == MENU_QUIT:
            return {'label': GLib.Variant('s', _('Quit NeoMixer')), 'enabled': GLib.Variant('b', True)}
        return {'children-display': GLib.Variant('s', 'submenu')}

    def _menu_layout(self):
        children = [
            GLib.Variant('(ia{sv}av)', (item_id, self._item_properties(item_id), []))
            for item_id in (MENU_SHOW, MENU_SEPARATOR, MENU_QUIT)
        ]
        return (MENU_ROOT, self._item_properties(MENU_ROOT), children)

    def _on_menu_call(self, _bus, _sender, _path, _interface, method, params, invocation):
        if method == 'GetLayout':
            invocation.return_value(GLib.Variant('(u(ia{sv}av))', (1, self._menu_layout())))
        elif method == 'GetGroupProperties':
            ids = params.unpack()[0] or [MENU_SHOW, MENU_SEPARATOR, MENU_QUIT]
            invocation.return_value(GLib.Variant(
                '(a(ia{sv}))', ([(item_id, self._item_properties(item_id)) for item_id in ids],)
            ))
        elif method == 'GetProperty':
            item_id, name = params.unpack()
            value = self._item_properties(item_id).get(name, GLib.Variant('s', ''))
            invocation.return_value(GLib.Variant('(v)', (value,)))
        elif method == 'Event':
            item_id, event_id, _data, _timestamp = params.unpack()
            if event_id == 'clicked':
                if item_id == MENU_SHOW:
                    GLib.idle_add(self._toggle_window)
                elif item_id == MENU_QUIT:
                    GLib.idle_add(self._quit)
            invocation.return_value(None)
        elif method == 'EventGroup':
            invocation.return_value(GLib.Variant('(ai)', ([],)))
        elif method == 'AboutToShow':
            invocation.return_value(GLib.Variant('(b)', (False,)))
        elif method == 'AboutToShowGroup':
            invocation.return_value(GLib.Variant('(aiai)', ([], [])))
        else:
            invocation.return_value(None)
