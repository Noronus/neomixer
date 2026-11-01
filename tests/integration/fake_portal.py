'''
Stands in for a desktop with global shortcuts on the sandbox's private bus, where the real portal
must not run. It behaves as KDE's does in the kde_ scenarios, what it remembers lasts across its
runs like the desktop's settings do. Scenarios press keys and answer its dialogs through the extra
interface at the end.

    fake_portal.py <state file> [--version N] [--no-kglobalaccel]
'''
import argparse
import json
import os

from gi.repository import Gio, GLib

PORTAL = 'org.freedesktop.portal.Desktop'
PATH = '/org/freedesktop/portal/desktop'
SHORTCUTS = 'org.freedesktop.portal.GlobalShortcuts'
REQUEST = 'org.freedesktop.portal.Request'
CONTROL = 'org.neomixer.FakePortal'

XML = f"""
<node>
  <interface name="{SHORTCUTS}">
    <property name="version" type="u" access="read"/>
    <method name="CreateSession">
      <arg type="a{{sv}}" direction="in"/><arg type="o" direction="out"/>
    </method>
    <method name="ListShortcuts">
      <arg type="o" direction="in"/><arg type="a{{sv}}" direction="in"/><arg type="o" direction="out"/>
    </method>
    <method name="BindShortcuts">
      <arg type="o" direction="in"/><arg type="a(sa{{sv}})" direction="in"/><arg type="s" direction="in"/>
      <arg type="a{{sv}}" direction="in"/><arg type="o" direction="out"/>
    </method>
    <method name="ConfigureShortcuts">
      <arg type="o" direction="in"/><arg type="s" direction="in"/><arg type="a{{sv}}" direction="in"/>
    </method>
  </interface>
  <interface name="org.freedesktop.host.portal.Registry">
    <method name="Register"><arg type="s" direction="in"/><arg type="a{{sv}}" direction="in"/></method>
  </interface>
  <interface name="{CONTROL}">
    <method name="Press"><arg type="s" direction="in"/></method>
    <method name="Release"><arg type="s" direction="in"/></method>
    <method name="SetAnswer"><arg type="s" direction="in"/></method>
    <method name="State"><arg type="s" direction="out"/></method>
    <method name="Quit"/>
  </interface>
</node>
"""
SESSION_XML = """<node><interface name="org.freedesktop.portal.Session"><method name="Close"/></interface></node>"""
KGLOBALACCEL_XML = """
<node><interface name="org.kde.KGlobalAccel">
  <method name="unregister"><arg type="s" direction="in"/><arg type="s" direction="in"/><arg type="b" direction="out"/></method>
</interface></node>
"""


def trigger_description(trigger):
    '''As KDE shows it, nothing here should go by how the app itself writes keys.'''
    *modifiers, key = trigger.split('+')
    names = {'CTRL': 'Ctrl', 'ALT': 'Alt', 'SHIFT': 'Shift', 'LOGO': 'Meta'}
    return '+'.join([names[name] for name in modifiers] + [key.upper() if len(key) == 1 else key]) + ' (desktop)'


class FakePortal:

    def __init__(self, args):
        self.args = args
        self.known = {}  # id: keys as the desktop shows them
        if os.path.exists(args.state):
            with open(args.state, encoding='utf-8') as state:
                self.known = json.load(state)
        self.registered = {}
        self.sessions = {}  # path: (sender, ids)
        self.answer = 'accept'
        self.dialogs = []  # the new shortcuts of each dialog shown
        self.empty_binds = 0
        self.configured = 0
        self.bus = Gio.bus_get_sync(Gio.BusType.SESSION)
        nodes = Gio.DBusNodeInfo.new_for_xml(XML)
        for interface in nodes.interfaces:
            self.bus.register_object(PATH, interface, self.on_call, self.on_get_property, None)
        if not args.no_kglobalaccel:
            self.bus.register_object('/kglobalaccel', Gio.DBusNodeInfo.new_for_xml(KGLOBALACCEL_XML).interfaces[0],
                                     self.on_kglobalaccel, None, None)
            Gio.bus_own_name_on_connection(self.bus, 'org.neomixer.FakeKGlobalAccel', Gio.BusNameOwnerFlags.NONE, None, None)
        Gio.bus_own_name_on_connection(self.bus, PORTAL, Gio.BusNameOwnerFlags.NONE, None, None)
        self.loop = GLib.MainLoop()

    def save(self):
        with open(self.args.state, 'w', encoding='utf-8') as state:
            json.dump(self.known, state)

    def on_get_property(self, _bus, _sender, _path, _interface, _name):
        return GLib.Variant('u', self.args.version)

    def on_kglobalaccel(self, _bus, _sender, _path, _interface, _method, parameters, invocation):
        _component, shortcut = parameters.unpack()
        found = self.known.pop(shortcut, None) is not None
        self.save()
        invocation.return_value(GLib.Variant('(b)', (found,)))

    def respond(self, sender, token, response, results, delay=0):
        path = f'{PATH}/request/{sender[1:].replace(".", "_")}/{token}'

        def emit():
            self.bus.emit_signal(sender, path, REQUEST, 'Response', GLib.Variant('(ua{sv})', (response, results)))
            return False

        GLib.timeout_add(delay + 10, emit)
        return path

    def shortcuts_variant(self, ids):
        return GLib.Variant('a(sa{sv})', [
            (shortcut, {'description': GLib.Variant('s', 'a shortcut'),
                        'trigger_description': GLib.Variant('s', self.known.get(shortcut, ''))})
            for shortcut in ids
        ])

    def on_call(self, _bus, sender, _path, interface, method, parameters, invocation):
        getattr(self, method)(sender, parameters.unpack(), invocation)

    def Register(self, sender, parameters, invocation):
        if sender in self.registered:
            invocation.return_dbus_error('org.freedesktop.portal.Error.Failed', 'Already registered')
            return
        self.registered[sender] = parameters[0]
        invocation.return_value(None)

    def CreateSession(self, sender, parameters, invocation):
        if sender not in self.registered:
            invocation.return_dbus_error('org.freedesktop.portal.Error.NotAllowed', 'An app id is required')
            return

        options = parameters[0]
        session = f'{PATH}/session/{sender[1:].replace(".", "_")}/{options["session_handle_token"]}'
        self.sessions[session] = sender
        self.bus.register_object(
            session, Gio.DBusNodeInfo.new_for_xml(SESSION_XML).interfaces[0],
            lambda _b, _s, _p, _i, _m, _a, close: (self.sessions.pop(session, None), close.return_value(None)), None, None
        )
        path = self.respond(sender, options['handle_token'], 0, {'session_handle': GLib.Variant('s', session)})
        invocation.return_value(GLib.Variant('(o)', (path,)))

    def ListShortcuts(self, sender, parameters, invocation):
        path = self.respond(sender, parameters[1]['handle_token'], 0, {'shortcuts': self.shortcuts_variant(self.known)})
        invocation.return_value(GLib.Variant('(o)', (path,)))

    def BindShortcuts(self, sender, parameters, invocation):
        session, shortcuts, _parent, options = parameters
        if not shortcuts:
            self.empty_binds += 1
            # As portals before 1.21 do: gone, along with anything else that was using it
            if self.args.version < 2:
                os._exit(1)

        ids = [shortcut for shortcut, _info in shortcuts]
        new = [(shortcut, info) for shortcut, info in shortcuts if shortcut not in self.known]
        response = 0
        if new:
            self.dialogs.append([shortcut for shortcut, _info in new])
            for shortcut, info in new:
                self.known[shortcut] = trigger_description(info['preferred_trigger']) if self.answer == 'accept' else ''
            if self.answer == 'cancel' and len(new) == len(shortcuts):
                response = 1
            self.save()

        results = {'shortcuts': self.shortcuts_variant(ids)} if response == 0 else {}
        path = self.respond(sender, options['handle_token'], response, results, delay=150 if new else 0)
        invocation.return_value(GLib.Variant('(o)', (path,)))

    def ConfigureShortcuts(self, _sender, _parameters, invocation):
        self.configured += 1
        invocation.return_value(None)

    def signal(self, name, shortcut):
        for session, sender in self.sessions.items():
            self.bus.emit_signal(sender, PATH, SHORTCUTS, name, GLib.Variant('(osta{sv})', (session, shortcut, 0, {})))

    def Press(self, _sender, parameters, invocation):
        self.signal('Activated', parameters[0])
        invocation.return_value(None)

    def Release(self, _sender, parameters, invocation):
        self.signal('Deactivated', parameters[0])
        invocation.return_value(None)

    def SetAnswer(self, _sender, parameters, invocation):
        self.answer = parameters[0]
        invocation.return_value(None)

    def State(self, _sender, _parameters, invocation):
        state = {'known': self.known, 'dialogs': self.dialogs, 'empty_binds': self.empty_binds,
                 'sessions': len(self.sessions), 'configured': self.configured}
        invocation.return_value(GLib.Variant('(s)', (json.dumps(state),)))

    def Quit(self, _sender, _parameters, invocation):
        invocation.return_value(None)
        self.bus.flush_sync(None)
        self.loop.quit()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('state')
    parser.add_argument('--version', type=int, default=2)
    parser.add_argument('--no-kglobalaccel', action='store_true')
    FakePortal(parser.parse_args()).loop.run()


if __name__ == '__main__':
    main()
