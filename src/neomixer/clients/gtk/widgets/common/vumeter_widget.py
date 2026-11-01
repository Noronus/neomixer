import math
import time
import logging

import cairo

# pylint: disable=wrong-import-order,wrong-import-position
from gi import require_version as gi_require_version
gi_require_version('Gtk', '4.0')
from gi.repository import Gtk, GLib  # noqa: E402
# pylint: enable=wrong-import-order,wrong-import-position

LOG = logging.getLogger("generic")

DB_FLOOR = -50.0
FALL_PER_SECOND = 0.9
HOLD_SECONDS = 1.2
HOLD_FALL_PER_SECOND = 0.35
FRAME_MS = 40
REDRAW_THRESHOLD = 0.01
SEGMENT = 3
GAP = 1

# Colors along the meter, from silence to full scale
SEGMENT_COLORS = (
    (0.00, (0.16, 0.75, 0.47)),
    (0.70, (0.20, 0.83, 0.60)),
    (0.76, (0.98, 0.75, 0.14)),
    (0.92, (0.98, 0.57, 0.24)),
    (0.97, (0.94, 0.27, 0.27)),
)
OFF_COLOR = (1, 1, 1, 0.06)


def _color_at(position):
    color = SEGMENT_COLORS[0][1]
    for start, stop_color in SEGMENT_COLORS:
        if position >= start:
            color = stop_color
    return color


def peak_to_fraction(peak: float) -> float:
    if peak <= 0:
        return 0.0
    return max(0.0, min(1.0, (20.0 * math.log10(peak) - DB_FLOOR) / -DB_FLOOR))


class _MeterClock:
    '''
    One timer for all meters, running only while one of them shows something. A tick callback
    per meter would keep the whole window's frame clock spinning at the display's rate.
    '''

    def __init__(self):
        self.active = set()
        self.source = None
        self.last_tick = 0.0

    def add(self, meter):
        self.active.add(meter)
        if self.source is None:
            self.last_tick = time.monotonic()
            self.source = GLib.timeout_add(FRAME_MS, self._tick)

    def discard(self, meter):
        self.active.discard(meter)

    def _tick(self):
        now = time.monotonic()
        elapsed, self.last_tick = now - self.last_tick, now
        for meter in list(self.active):
            if not meter.advance(now, elapsed):
                self.active.discard(meter)

        if not self.active:
            self.source = None
            return GLib.SOURCE_REMOVE
        return GLib.SOURCE_CONTINUE


_clock = _MeterClock()


class VumeterWidget(Gtk.DrawingArea):
    '''
    LED style peak meter. Lit and unlit LEDs are drawn once per size and only get clipped to
    the level per frame, and with nothing playing it does not draw at all: a mixer has many
    of these open all the time.
    '''

    def __init__(self, orientation=Gtk.Orientation.VERTICAL, thickness=8, **kwargs):
        super().__init__(**kwargs)
        self.orientation = orientation
        if orientation == Gtk.Orientation.VERTICAL:
            self.set_content_width(thickness)
        else:
            self.set_content_height(thickness)

        self.level = 0.0
        self.hold = 0.0
        self._hold_until = 0.0
        self._pending_peak = 0.0
        self._waking = False
        self._drawn = (0.0, 0.0)
        self._strips = None
        self._strips_size = None
        self.set_draw_func(self._draw)
        self.connect('unrealize', self._on_unrealize)

    async def update_peak(self, peak):
        '''
        Runs on the vumeter thread, for every peak of every open meter. It only leaves the peak
        for the next frame, waking the main thread for each one is what costs the most.
        '''
        self._pending_peak = max(self._pending_peak, peak)
        if self not in _clock.active and not self._waking and peak > 0:
            self._waking = True
            GLib.idle_add(self._wake)

    def _wake(self):
        self._waking = False
        _clock.add(self)
        return False

    def reset(self):
        '''Go dark right away.'''
        self.level = self.hold = self._pending_peak = 0.0
        _clock.discard(self)
        self.queue_draw()

    def _on_unrealize(self, *_):
        _clock.discard(self)

    def advance(self, now, elapsed):
        '''Next frame of the meter. Returns False once it has gone dark.'''
        fraction = peak_to_fraction(self._pending_peak)
        self._pending_peak = 0.0
        self.level = max(fraction, self.level - FALL_PER_SECOND * elapsed)
        if fraction >= self.hold:
            self.hold = fraction
            self._hold_until = now + HOLD_SECONDS
        elif now > self._hold_until:
            self.hold = max(0.0, self.hold - HOLD_FALL_PER_SECOND * elapsed)

        # less than a LED of difference is not worth a redraw
        if abs(self.level - self._drawn[0]) > REDRAW_THRESHOLD or abs(self.hold - self._drawn[1]) > REDRAW_THRESHOLD:
            self._drawn = (self.level, self.hold)
            self.queue_draw()

        return self.level > 0 or self.hold > 0

    def _build_strips(self, target, width, height):
        '''Render the meter fully lit and fully dark, once per size.'''
        vertical = self.orientation == Gtk.Orientation.VERTICAL
        length = height if vertical else width
        step = SEGMENT + GAP
        strips = []
        for lit in (False, True):
            surface = target.create_similar(cairo.CONTENT_COLOR_ALPHA, width, height)
            cr = cairo.Context(surface)
            for offset in range(0, length, step):
                position = offset / max(length - 1, 1)
                if lit:
                    cr.set_source_rgb(*_color_at(position))
                else:
                    cr.set_source_rgba(*OFF_COLOR)
                if vertical:
                    cr.rectangle(0, height - offset - SEGMENT, width, SEGMENT)
                else:
                    cr.rectangle(offset, 0, SEGMENT, height)
                cr.fill()
            strips.append(surface)
        self._strips = strips
        self._strips_size = (width, height)

    def _draw(self, _area, cr, width, height):
        if width <= 0 or height <= 0:
            return
        if self._strips_size != (width, height):
            self._build_strips(cr.get_target(), width, height)

        off, lit = self._strips
        cr.set_source_surface(off, 0, 0)
        cr.paint()

        vertical = self.orientation == Gtk.Orientation.VERTICAL
        if self.level > 0:
            cr.save()
            if vertical:
                top = height * (1 - self.level)
                cr.rectangle(0, top, width, height - top)
            else:
                cr.rectangle(0, 0, width * self.level, height)
            cr.clip()
            cr.set_source_surface(lit, 0, 0)
            cr.paint()
            cr.restore()

        if self.hold > 0:
            cr.save()
            if vertical:
                position = round(height * (1 - self.hold))
                cr.rectangle(0, position, width, 2)
            else:
                position = round(width * self.hold)
                cr.rectangle(max(position - 2, 0), 0, 2, height)
            cr.clip()
            cr.set_source_surface(lit, 0, 0)
            cr.paint()
            cr.restore()
