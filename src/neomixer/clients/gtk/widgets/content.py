import gettext

from neomixer.model.types import DEVICE_TYPE_PRETTY, DEVICE_TYPE_SHORT, DEVICE_TYPE_DESCRIPTION
from neomixer.clients.gtk.widgets.utils.widget_box import WidgetBox
from neomixer.clients.gtk.widgets.app.app_picker import AppPicker
from neomixer.clients.gtk.widgets.popovers.device_settings_popover import DeviceSettingsPopover
from neomixer.clients.gtk.widgets.containers.settings_menu_box import SettingsMenuBox
from neomixer.clients.gtk.widgets.utils.popover_utils import dismiss_on_outside_click

# pylint: disable=wrong-import-order,wrong-import-position
import gi
gi.require_version('Gtk', '4.0')
from gi.repository import Gtk, GObject, Pango  # noqa: E402
# pylint: enable=wrong-import-order,wrong-import-position

_ = gettext.gettext

SECTION_ORDER = ('hi', 'vi', 'a', 'b')
CARD_MIN_WIDTH = 300
APPS_MIN_HEIGHT = 110
MIXER_SHARE = 0.62


class Section(Gtk.Box):
    '''A group of channel strips with a title and an add button.'''

    def __init__(self, device_type, button):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=6, hexpand=True)
        self.add_css_class('section')
        self.add_css_class(f'section-{device_type}')
        title = Gtk.Label(
            label=DEVICE_TYPE_SHORT[device_type].upper(), xalign=0, hexpand=True,
            ellipsize=Pango.EllipsizeMode.END, max_width_chars=4
        )
        title.add_css_class('section-title')
        title.set_tooltip_text(DEVICE_TYPE_DESCRIPTION.get(device_type))
        header = Gtk.Box(spacing=4, margin_start=4)
        header.append(title)
        header.append(button)
        self.append(header)


class Content(Gtk.Box):
    '''
    The main window's content: the mixer on top, the programs below it. Each has its own
    space, however many programs there are the faders stay put and nothing needs scrolling
    sideways.
    '''

    __gsignals__ = {
        "device_new": (GObject.SIGNAL_RUN_FIRST, GObject.TYPE_NONE, (GObject.TYPE_PYOBJECT,)),
        "add_device_pressed": (GObject.SIGNAL_RUN_FIRST, GObject.TYPE_NONE, (GObject.TYPE_PYOBJECT, str, str)),
        "settings_pressed": (GObject.SIGNAL_RUN_FIRST, GObject.TYPE_NONE, ()),
        "settings_change": (GObject.SIGNAL_RUN_FIRST, GObject.TYPE_NONE, (GObject.TYPE_PYOBJECT,)),
        "help_pressed": (GObject.SIGNAL_RUN_FIRST, GObject.TYPE_NONE, ()),
        "hotkeys_pressed": (GObject.SIGNAL_RUN_FIRST, GObject.TYPE_NONE, ()),
        "quit_pressed": (GObject.SIGNAL_RUN_FIRST, GObject.TYPE_NONE, ()),
        "app_picked": (GObject.SIGNAL_RUN_FIRST, GObject.TYPE_NONE, (str, str, str)),
        "apps_view_changed": (GObject.SIGNAL_RUN_FIRST, GObject.TYPE_NONE, ()),
    }

    def __init__(self, apps_filter='all', show_offline=True, mixer_height=0, **kwargs):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, **kwargs)
        self.apps_filter = apps_filter
        self.show_offline = show_offline
        self.text_factor = 1.0
        self.settings_button = Gtk.MenuButton(icon_name='emblem-system-symbolic')
        self.settings_box = SettingsMenuBox()

        settings_popover = Gtk.Popover()
        settings_popover.set_child(self.settings_box)
        dismiss_on_outside_click(settings_popover)
        self.settings_button.set_popover(settings_popover)
        self.settings_button.set_tooltip_text(_('Open settings menu'))
        Gtk.Accessible.update_property(
            self.settings_button,
            [
                Gtk.AccessibleProperty.LABEL,
                Gtk.AccessibleProperty.DESCRIPTION,
                Gtk.AccessibleProperty.HAS_POPUP,
            ],
            [
                _('Settings menu'),
                _('Open settings menu'),
                True
            ]
        )

        gesture = Gtk.GestureClick.new()
        gesture.connect("pressed", self._on_settings_pressed)
        self.settings_button.add_controller(gesture)
        self.settings_box.connect('settings_change', self._on_settings_changed)
        self.settings_box.connect('help_pressed', self._on_help_pressed)
        self.settings_box.connect('hotkeys_pressed', self._on_hotkeys_pressed)
        self.settings_box.connect('quit_pressed', self._on_quit_pressed)

        # Every strip lines its fader up with the others
        self.routes_group = Gtk.SizeGroup(mode=Gtk.SizeGroupMode.VERTICAL)

        self.create_device_button = {}
        self.device_box = {}
        self.sections = {}
        for device_type in SECTION_ORDER:
            self.device_box[device_type] = WidgetBox(spacing=6, homogeneous=True, vexpand=True)
            self.create_device_button[device_type] = self._new_device_button(device_type)
            self.sections[device_type] = Section(device_type, self.create_device_button[device_type])
            self.sections[device_type].append(self.device_box[device_type])

        self.mixer_grid = Gtk.Grid(column_spacing=8, column_homogeneous=True, hexpand=True, vexpand=True,
                                   margin_start=8, margin_end=8, margin_top=8, margin_bottom=2)
        mixer_scroll = Gtk.ScrolledWindow(hexpand=True, vexpand=True, propagate_natural_height=True)
        mixer_scroll.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.NEVER)
        mixer_scroll.set_child(self.mixer_grid)

        self.paned = Gtk.Paned(orientation=Gtk.Orientation.VERTICAL, hexpand=True, vexpand=True, wide_handle=True,
                               resize_start_child=True, resize_end_child=True,
                               shrink_start_child=False, shrink_end_child=False)
        self.paned.add_css_class('mixer-split')
        self.paned.set_start_child(mixer_scroll)
        self.paned.set_end_child(self._build_apps_panel())
        if mixer_height > 0:
            self.paned.set_position(mixer_height)
        self.append(self.paned)
        self.relayout_sections()

    def default_split(self, window_height):
        '''Where the divider goes until the user moved it.'''
        self.paned.set_position(int(window_height * MIXER_SHARE))

    def relayout_sections(self):
        '''
        Sections are as wide as the strips they hold, so all strips have the same width and use
        all there is. Takes effect when devices are added or removed.
        '''
        for section in self.sections.values():
            if section.get_parent() is not None:
                self.mixer_grid.remove(section)

        column = 0
        for device_type in SECTION_ORDER:
            width = max(len(self.device_box[device_type].widgets), 1)
            self.mixer_grid.attach(self.sections[device_type], column, 0, width, 1)
            column += width

    #
    # # Apps panel
    #
    def _build_apps_panel(self):
        panel = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6, margin_start=8, margin_end=8, margin_bottom=8)
        panel.add_css_class('section')
        panel.add_css_class('section-apps')
        panel.set_size_request(-1, APPS_MIN_HEIGHT)

        title = Gtk.Label(label=_('PROGRAMS'), xalign=0)
        title.add_css_class('section-title')
        self.apps_count = Gtk.Label(xalign=0)
        self.apps_count.add_css_class('apps-count')

        self.filter_buttons = {}
        filters = Gtk.Box(css_classes=['linked'])
        group = None
        for name, label in (('all', _('All')), ('sink_input', _('Playback')), ('source_output', _('Recording'))):
            button = Gtk.ToggleButton(label=label, group=group, active=name == self.apps_filter)
            button.add_css_class('filter-toggle')
            button.connect('toggled', self._on_filter_toggled, name)
            group = group or button
            filters.append(button)
            self.filter_buttons[name] = button

        self.offline_toggle = Gtk.ToggleButton(label=_('Not running'), active=self.show_offline)
        self.offline_toggle.add_css_class('filter-toggle')
        self.offline_toggle.set_tooltip_text(_('Also list programs that are not running right now'))
        self.offline_toggle.connect('toggled', self._on_offline_toggled)

        self.add_app_button = Gtk.MenuButton(icon_name='list-add-symbolic')
        self.add_app_button.add_css_class('icon-small')
        self.add_app_button.set_tooltip_text(_('Set up a program before it ever played anything'))
        self.app_picker = AppPicker()
        self.app_picker.connect('picked', self._on_app_picked)
        self.add_app_button.set_popover(self.app_picker)

        header = Gtk.Box(spacing=8, margin_start=4)
        header.append(title)
        header.append(self.apps_count)
        header.append(Gtk.Box(hexpand=True))
        header.append(filters)
        header.append(self.offline_toggle)
        header.append(self.add_app_button)
        panel.append(header)

        self.app_flow = Gtk.FlowBox(
            homogeneous=True, selection_mode=Gtk.SelectionMode.NONE, activate_on_single_click=False,
            column_spacing=6, row_spacing=6, min_children_per_line=1, max_children_per_line=16,
            valign=Gtk.Align.START
        )
        self.app_flow.set_sort_func(self._sort_cards)
        self.app_flow.set_filter_func(self._filter_card)
        self.app_cards = {}

        self.apps_hint = Gtk.Label(label=_('No programs yet. They show up here once they connect to the sound server.'))
        self.apps_hint.add_css_class('empty-hint')

        apps_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        apps_box.append(self.app_flow)
        apps_box.append(self.apps_hint)

        apps_scroll = Gtk.ScrolledWindow(hexpand=True, vexpand=True)
        apps_scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        apps_scroll.set_child(apps_box)
        panel.append(apps_scroll)
        return panel

    def set_text_factor(self, factor):
        '''Cards grow with their text. Everything else takes its size from its content.'''
        self.text_factor = factor
        for card in self.app_cards.values():
            card.set_size_request(int(CARD_MIN_WIDTH * factor), -1)

    def add_app_card(self, card):
        card.set_size_request(int(CARD_MIN_WIDTH * self.text_factor), -1)
        self.app_cards[(card.entry.app_type, card.entry.key)] = card
        self.app_flow.append(card)
        self.refresh_apps_view()

    def remove_app_card(self, app_type, key):
        card = self.app_cards.pop((app_type, key), None)
        if card is not None:
            self.app_flow.remove(card)
            self.refresh_apps_view()
        return card

    def refresh_apps_view(self):
        '''Call when a program's name or state changed.'''
        self.app_flow.invalidate_sort()
        self.app_flow.invalidate_filter()
        visible = [card for card in self.app_cards.values() if self._card_visible(card)]
        running = sum(card.entry.state == 'active' for card in self.app_cards.values())
        self.apps_count.set_label(_('%d active of %d') % (running, len(self.app_cards)))
        self.apps_hint.set_visible(not visible)

    @staticmethod
    def _sort_cards(first, second):
        '''By name: a program stays where one expects it, whatever it is doing.'''
        first_entry, second_entry = first.get_child().entry, second.get_child().entry
        first_sort = (first_entry.label.lower(), first_entry.app_type)
        second_sort = (second_entry.label.lower(), second_entry.app_type)
        return (first_sort > second_sort) - (first_sort < second_sort)

    def _card_visible(self, card):
        entry = card.entry
        if self.apps_filter != 'all' and entry.app_type != self.apps_filter:
            return False
        return self.show_offline or entry.state != 'offline'

    def _filter_card(self, child):
        return self._card_visible(child.get_child())

    def _on_filter_toggled(self, button, name):
        if button.get_active():
            self.apps_filter = name
            self.refresh_apps_view()
            self.emit('apps_view_changed')

    def _on_offline_toggled(self, button):
        self.show_offline = button.get_active()
        self.refresh_apps_view()
        self.emit('apps_view_changed')

    def _on_app_picked(self, _picker, key, label, binary):
        self.emit('app_picked', key, label, binary)

    #
    # # Devices
    #
    def _on_device_create(self, _, popover):
        schema = popover.to_schema()
        if len(schema['nick'].strip()) == 0:
            return
        if popover.device_type in ('vi', 'b') and len(schema['name'].strip()) == 0:
            return
        if popover.device_type in ('a', 'hi') and schema['channels'] == 0:
            return
        self.emit('device_new', schema)

    def _on_settings_pressed(self, *_):
        self.emit('settings_pressed')

    def _on_help_pressed(self, _):
        self.emit('help_pressed')

    def _on_hotkeys_pressed(self, _):
        self.settings_button.get_popover().popdown()
        self.emit('hotkeys_pressed')

    def _on_quit_pressed(self, _):
        self.emit('quit_pressed')

    def _on_settings_changed(self, _, schema):
        self.emit('settings_change', schema)

    def _on_add_device_pressed(self, _, __, ___, ____, device_type):
        popover = self.create_device_button[device_type].get_popover()
        self.emit('add_device_pressed', popover, device_type, None)

    def _new_device_button(self, device_type):
        button = Gtk.MenuButton(icon_name='list-add-symbolic')
        button.add_css_class('icon-small')
        popover = DeviceSettingsPopover(device_type)
        popover.confirm_button.connect('clicked', self._on_device_create, popover)
        button.set_popover(popover)
        gesture = Gtk.GestureClick.new()
        gesture.connect("pressed", self._on_add_device_pressed, device_type)
        button.add_controller(gesture)
        accessible_description = _('Create new %s') % DEVICE_TYPE_PRETTY[device_type][:-1]
        button.set_tooltip_text(accessible_description)
        Gtk.Accessible.update_property(
            button,
            [
                Gtk.AccessibleProperty.LABEL,
                Gtk.AccessibleProperty.DESCRIPTION,
                Gtk.AccessibleProperty.HAS_POPUP,
            ],
            [
                _('Create device'),
                accessible_description,
                True
            ]
        )
        return button
