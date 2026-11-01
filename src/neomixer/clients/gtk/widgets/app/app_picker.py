import gettext

from neomixer.clients.gtk.widgets.app.app_icons import installed_apps

# pylint: disable=wrong-import-order,wrong-import-position
import gi
gi.require_version('Gtk', '4.0')
from gi.repository import Gtk, GObject, Pango  # noqa: E402
# pylint: enable=wrong-import-order,wrong-import-position

_ = gettext.gettext


class AppPicker(Gtk.Popover):
    '''
    Lists installed programs, to set one up before it ever played anything.
    '''

    __gsignals__ = {
        # key, label, binary
        'picked': (GObject.SignalFlags.RUN_FIRST, GObject.TYPE_NONE, (str, str, str)),
    }

    def __init__(self):
        super().__init__()
        self.search = Gtk.SearchEntry(placeholder_text=_('Search programs'))
        self.search.connect('search-changed', lambda _: self.list.invalidate_filter())
        self.list = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE, activate_on_single_click=True)
        self.list.set_filter_func(self._filter)
        self.list.connect('row-activated', self._on_activated)
        scrolled = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER, min_content_height=320, min_content_width=280)
        scrolled.set_child(self.list)

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        box.append(self.search)
        box.append(scrolled)
        self.set_child(box)
        self.connect('show', self._on_show)
        self._filled = False

    def _on_show(self, *_):
        if not self._filled:
            self._filled = True
            for key, label, icon, binary in installed_apps():
                row = Gtk.ListBoxRow()
                row.app = (key, label, binary or '')
                content = Gtk.Box(spacing=8, margin_top=3, margin_bottom=3, margin_start=4, margin_end=4)
                image = Gtk.Image(pixel_size=20)
                if icon is not None:
                    image.set_from_gicon(icon)
                content.append(image)
                content.append(Gtk.Label(label=label, xalign=0, ellipsize=Pango.EllipsizeMode.END))
                row.set_child(content)
                self.list.append(row)
        self.search.set_text('')
        self.search.grab_focus()

    def _filter(self, row):
        text = self.search.get_text().lower()
        return not text or text in row.app[1].lower() or text in row.app[0]

    def _on_activated(self, _list, row):
        self.popdown()
        self.emit('picked', *row.app)
