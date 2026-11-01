import math

from neomixer.model.eq_model import GAINLESS_TYPES, MIN_FREQ, MAX_FREQ, MAX_GAIN, MIN_Q, MAX_Q
from neomixer.utils.eq_math import band_coefficients, magnitude_db

# pylint: disable=wrong-import-order,wrong-import-position
import gi
gi.require_version('Gtk', '4.0')
from gi.repository import Gtk, GObject  # noqa: E402
# pylint: enable=wrong-import-order,wrong-import-position

BAND_COLORS = (
    (0.22, 0.74, 0.97), (0.65, 0.55, 0.98), (0.20, 0.83, 0.60), (0.98, 0.75, 0.14),
    (0.98, 0.57, 0.24), (0.96, 0.45, 0.71), (0.45, 0.85, 0.95), (0.75, 0.90, 0.35),
)
GRID_FREQS = (20, 50, 100, 200, 500, 1000, 2000, 5000, 10000, 20000)
GRID_GAINS = (-24, -18, -12, -6, 0, 6, 12, 18, 24)
LEFT, RIGHT, TOP, BOTTOM = 34, 12, 10, 22
GRAB_RADIUS = 18
HANDLE_RADIUS = 9


def format_freq(freq):
    if freq >= 1000:
        text = f'{freq / 1000:.1f}'.removesuffix('.0')
        return f'{text}k'
    return f'{freq:.0f}'


class EqCurve(Gtk.DrawingArea):
    '''
    Draws what the EQ does to the audio. Each band has a handle: drag it to change frequency
    and gain, scroll to change Q, double click to zero its gain.
    '''

    __gsignals__ = {
        'band_selected': (GObject.SignalFlags.RUN_FIRST, GObject.TYPE_NONE, (int,)),
        'changed': (GObject.SignalFlags.RUN_FIRST, GObject.TYPE_NONE, ()),
    }

    def __init__(self, eq, **kwargs):
        super().__init__(content_width=640, content_height=280, hexpand=True, vexpand=True, **kwargs)
        self.eq = eq
        self.selected = 0
        self._dragging = None
        self._drag_start = (0, 0)
        self.set_draw_func(self._draw)

        drag = Gtk.GestureDrag()
        drag.connect('drag-begin', self._on_drag_begin)
        drag.connect('drag-update', self._on_drag_update)
        drag.connect('drag-end', self._on_drag_end)
        self.add_controller(drag)

        click = Gtk.GestureClick()
        click.connect('pressed', self._on_pressed)
        self.add_controller(click)

        scroll = Gtk.EventControllerScroll(flags=Gtk.EventControllerScrollFlags.VERTICAL)
        scroll.connect('scroll', self._on_scroll)
        self.add_controller(scroll)

    def select(self, index):
        self.selected = index
        self.queue_draw()

    # Axes
    def _freq_to_x(self, freq, width):
        span = math.log10(MAX_FREQ / MIN_FREQ)
        return LEFT + (width - LEFT - RIGHT) * math.log10(freq / MIN_FREQ) / span

    def _x_to_freq(self, x, width):
        span = math.log10(MAX_FREQ / MIN_FREQ)
        fraction = min(max((x - LEFT) / max(width - LEFT - RIGHT, 1), 0), 1)
        return MIN_FREQ * 10 ** (fraction * span)

    def _gain_to_y(self, gain, height):
        return TOP + (height - TOP - BOTTOM) * (MAX_GAIN - gain) / (2 * MAX_GAIN)

    def _y_to_gain(self, y, height):
        fraction = min(max((y - TOP) / max(height - TOP - BOTTOM, 1), 0), 1)
        return MAX_GAIN - fraction * 2 * MAX_GAIN

    def handle_position(self, band, width, height):
        '''Where a band's handle is. Filters without gain move along the 0 dB line.'''
        gain = 0.0 if band.type in GAINLESS_TYPES else band.gain
        return self._freq_to_x(band.freq, width), self._gain_to_y(gain, height)

    def band_at(self, x, y):
        width, height = self.get_width(), self.get_height()
        best, best_distance = None, GRAB_RADIUS
        for index, band in enumerate(self.eq.bands):
            handle_x, handle_y = self.handle_position(band, width, height)
            distance = math.hypot(handle_x - x, handle_y - y)
            if distance < best_distance:
                best, best_distance = index, distance
        return best

    # Interaction
    def _on_pressed(self, _gesture, n_press, x, y):
        index = self.band_at(x, y)
        if index is None:
            return
        self.selected = index
        self.emit('band_selected', index)
        if n_press == 2:
            self.eq.bands[index].gain = 0.0
            self._changed()
        self.queue_draw()

    def _on_drag_begin(self, _gesture, x, y):
        self._dragging = self.band_at(x, y)
        self._drag_start = (x, y)

    def _on_drag_update(self, _gesture, offset_x, offset_y):
        if self._dragging is None:
            return
        width, height = self.get_width(), self.get_height()
        band = self.eq.bands[self._dragging]
        x, y = self._drag_start[0] + offset_x, self._drag_start[1] + offset_y
        band.freq = min(max(self._x_to_freq(x, width), MIN_FREQ), MAX_FREQ)
        if band.type not in GAINLESS_TYPES:
            band.gain = round(self._y_to_gain(y, height), 1)
        self._changed()

    def _on_drag_end(self, *_):
        self._dragging = None

    def _on_scroll(self, _controller, _dx, dy):
        band = self.eq.bands[self.selected]
        band.q = min(max(band.q * (1.1 ** -dy), MIN_Q), MAX_Q)
        self._changed()
        return True

    def _changed(self):
        self.eq.preset = ''
        self.queue_draw()
        self.emit('changed')

    # Drawing
    def _draw(self, _area, cr, width, height):
        self._draw_grid(cr, width, height)

        points = max(min((width - LEFT - RIGHT) // 3, 260), 2)
        freqs = [MIN_FREQ * (MAX_FREQ / MIN_FREQ) ** (index / (points - 1)) for index in range(points)]
        coefficients = [
            (band, band_coefficients(band.type, band.freq, band.gain, band.q)) for band in self.eq.bands
        ]

        # Each band's own effect, faint
        if self.eq.enabled:
            for index, (band, coefficient) in enumerate(coefficients):
                if not band.enabled or index != self.selected:
                    continue
                cr.set_source_rgba(*BAND_COLORS[index % len(BAND_COLORS)], 0.45)
                cr.set_line_width(1)
                self._trace(cr, freqs, [magnitude_db(coefficient, freq) for freq in freqs], width, height)
                cr.stroke()

        total = [
            self.eq.preamp + sum(magnitude_db(coefficient, freq) for band, coefficient in coefficients if band.enabled)
            for freq in freqs
        ]
        accent = (0.98, 0.75, 0.14) if self.eq.enabled else (0.45, 0.48, 0.55)

        zero_y = self._gain_to_y(0, height)
        self._trace(cr, freqs, total, width, height)
        cr.line_to(self._freq_to_x(freqs[-1], width), zero_y)
        cr.line_to(self._freq_to_x(freqs[0], width), zero_y)
        cr.close_path()
        cr.set_source_rgba(*accent, 0.14)
        cr.fill()

        self._trace(cr, freqs, total, width, height)
        cr.set_source_rgb(*accent)
        cr.set_line_width(2.2)
        cr.stroke()

        self._draw_handles(cr, width, height)

    def _trace(self, cr, freqs, gains, width, height):
        for index, (freq, gain) in enumerate(zip(freqs, gains)):
            x = self._freq_to_x(freq, width)
            y = self._gain_to_y(min(max(gain, -MAX_GAIN - 6), MAX_GAIN + 6), height)
            if index == 0:
                cr.move_to(x, y)
            else:
                cr.line_to(x, y)

    def _draw_grid(self, cr, width, height):
        cr.set_source_rgb(0.055, 0.067, 0.09)
        cr.paint()

        cr.set_font_size(10)
        for freq in GRID_FREQS:
            x = self._freq_to_x(freq, width)
            cr.set_source_rgba(1, 1, 1, 0.07)
            cr.set_line_width(1)
            cr.move_to(round(x) + 0.5, TOP)
            cr.line_to(round(x) + 0.5, height - BOTTOM)
            cr.stroke()
            label = format_freq(freq)
            extents = cr.text_extents(label)
            cr.set_source_rgba(1, 1, 1, 0.45)
            cr.move_to(min(max(x - extents.width / 2, 0), width - extents.width - 1), height - 7)
            cr.show_text(label)

        for gain in GRID_GAINS:
            y = self._gain_to_y(gain, height)
            cr.set_source_rgba(1, 1, 1, 0.2 if gain == 0 else 0.07)
            cr.set_line_width(1)
            cr.move_to(LEFT, round(y) + 0.5)
            cr.line_to(width - RIGHT, round(y) + 0.5)
            cr.stroke()
            label = f'{gain:+d}' if gain else '0'
            extents = cr.text_extents(label)
            cr.set_source_rgba(1, 1, 1, 0.45)
            cr.move_to(LEFT - extents.width - 6, y + extents.height / 2)
            cr.show_text(label)

    def _draw_handles(self, cr, width, height):
        cr.set_font_size(10)
        for index, band in enumerate(self.eq.bands):
            x, y = self.handle_position(band, width, height)
            color = BAND_COLORS[index % len(BAND_COLORS)]
            alpha = 1.0 if band.enabled and self.eq.enabled else 0.35

            if index == self.selected:
                cr.arc(x, y, HANDLE_RADIUS + 3, 0, 2 * math.pi)
                cr.set_source_rgba(*color, 0.3)
                cr.fill()

            cr.arc(x, y, HANDLE_RADIUS, 0, 2 * math.pi)
            cr.set_source_rgba(*color, alpha)
            cr.fill()

            label = str(index + 1)
            extents = cr.text_extents(label)
            cr.set_source_rgba(0.05, 0.06, 0.08, min(alpha + 0.3, 1))
            cr.move_to(x - extents.width / 2 - extents.x_bearing, y + extents.height / 2)
            cr.show_text(label)
