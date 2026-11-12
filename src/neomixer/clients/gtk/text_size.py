'''
Text size in real points: 1/72 of an inch on the monitor, whatever its resolution.

GTK's own sizes assume every monitor has 96 logical pixels per inch, which makes the same text
a third bigger on a 24" 1080p monitor than on a 27" 1440p one. So font sizes are worked out per
window from the size its monitor reports, and follow the window to another monitor.
'''
import math
import logging

from neomixer.clients.gtk.style import STYLE_PRIORITY

# pylint: disable=wrong-import-order,wrong-import-position
from gi import require_version as gi_require_version
gi_require_version('Gtk', '4.0')
from gi.repository import Gdk, GLib, Gtk  # noqa: E402
# pylint: enable=wrong-import-order,wrong-import-position

LOG = logging.getLogger("generic")

MIN_POINTS = 6.0
MAX_POINTS = 24.0

# What the layout was designed around, to scale the things that do not follow the text by themselves
DESIGN_PX = 13.33

ASSUMED_DPI = 96.0
MM_PER_INCH = 25.4
# Monitors without an EDID report 0, projectors and some TVs only an aspect ratio such as 16x9 mm
MIN_DIAGONAL_MM = 200
MIN_DPI, MAX_DPI = 50.0, 400.0

UPDATE_DELAY_MS = 100


def logical_dpi(width_px, height_px, width_mm, height_mm):
    '''
    Logical pixels per inch of a monitor, None if what it reports can't be right.
    Monitor geometry is in logical pixels, so its scale factor is already part of this. The
    diagonal because the size in mm does not rotate with the monitor, the geometry does.
    '''
    diagonal_mm = math.hypot(width_mm, height_mm)
    if diagonal_mm < MIN_DIAGONAL_MM:
        return None

    dpi = math.hypot(width_px, height_px) / (diagonal_mm / MM_PER_INCH)
    return dpi if MIN_DPI <= dpi <= MAX_DPI else None


def text_px(points, dpi):
    '''Size in logical pixels of text of this many points on a monitor of this dpi.'''
    return points / 72 * (dpi or ASSUMED_DPI)


def monitor_dpi(monitor):
    geometry = monitor.get_geometry()
    return logical_dpi(geometry.width, geometry.height, monitor.get_width_mm(), monitor.get_height_mm())


class TextScaler:
    '''
    Sets the font size of each window it tracks. CSS and not GTK's dpi setting, since that is
    the same for the whole display while windows can be on different monitors.
    '''

    def __init__(self, points):
        self.points = points
        self.windows = {}
        self.next_id = 1
        self.update_source = None
        self.css = None
        self.provider = Gtk.CssProvider()
        Gtk.StyleContext.add_provider_for_display(Gdk.Display.get_default(), self.provider, STYLE_PRIORITY)

    def track(self, window, on_change=None):
        '''
        Keep a window's text at the right size.
            "on_change" gets how big the text is compared to the size the layout was designed
            around, for what does not follow the text by itself
        '''
        css_class = f'nm-text-{self.next_id}'
        self.next_id += 1
        window.add_css_class(css_class)
        self.windows[window] = {'class': css_class, 'on_change': on_change, 'px': None}
        window.connect('realize', self._on_realize)
        window.connect('map', self._schedule_update)
        window.connect('destroy', self.untrack)
        if window.get_realized():
            self._on_realize(window)
        self.update()

    def untrack(self, window):
        self.windows.pop(window, None)

    def set_points(self, points):
        self.points = points
        self.update()

    def px_of(self, window):
        return self.windows[window]['px']

    def _on_realize(self, window):
        surface = window.get_surface()
        surface.connect('enter-monitor', self._schedule_update)
        surface.connect('leave-monitor', self._schedule_update)

    def _schedule_update(self, *_):
        # moving a window over gives an enter and a leave, and it is only needed once
        if self.update_source is None:
            self.update_source = GLib.timeout_add(UPDATE_DELAY_MS, self._delayed_update)

    def _delayed_update(self):
        self.update_source = None
        self.update()
        return GLib.SOURCE_REMOVE

    @staticmethod
    def _monitor_of(window):
        '''The window's monitor. Before it is on one, the first: it gets corrected when mapped.'''
        display = Gdk.Display.get_default()
        surface = window.get_surface()
        monitor = display.get_monitor_at_surface(surface) if surface is not None else None
        return monitor or display.get_monitors().get_item(0)

    def update(self):
        rules = []
        changed = []
        for window, state in self.windows.items():
            monitor = self._monitor_of(window)
            px = round(text_px(self.points, monitor_dpi(monitor) if monitor else None), 2)
            if px != state['px']:
                state['px'] = px
                changed.append(state)

            # Themes reset the font on popovers and tooltips, which are toplevels of their own
            selector = f"window.{state['class']}"
            rules.append(
                f'{selector}, {selector} popover.background, {selector} popover.background > contents, '
                f'{selector} tooltip {{ font-size: {px}px; }}'
            )

        css = '\n'.join(rules)
        if css != self.css:
            self.css = css
            # load_from_string is GTK 4.12, Debian 12 has 4.8
            if hasattr(self.provider, 'load_from_string'):
                self.provider.load_from_string(css)
            else:
                self.provider.load_from_data(css.encode())

        for state in changed:
            if state['on_change'] is not None:
                state['on_change'](state['px'] / DESIGN_PX)
