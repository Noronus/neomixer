import pytest

from neomixer.clients.gtk.text_size import logical_dpi, text_px


def test_real_monitors():
    # a 27" 1440p and a 24" 1200p monitor, both at 100 %
    assert logical_dpi(2560, 1440, 597, 336) == pytest.approx(108.9, abs=0.1)
    assert logical_dpi(1920, 1200, 518, 324) == pytest.approx(94.1, abs=0.1)


def test_same_points_are_the_same_physical_size_on_any_monitor():
    for geometry in ((2560, 1440, 597, 336), (1920, 1200, 518, 324), (1920, 1080, 344, 194), (3840, 2160, 697, 392)):
        width_px, height_px, width_mm, height_mm = geometry
        px = text_px(9, logical_dpi(*geometry))
        mm = px * width_mm / width_px
        assert mm == pytest.approx(9 / 72 * 25.4, rel=0.01)


def test_scale_factor_of_the_desktop_is_accounted_for():
    # 4K at 200 % has half the logical pixels, and the same size on screen needs half the px
    full = text_px(9, logical_dpi(3840, 2160, 697, 392))
    scaled = text_px(9, logical_dpi(1920, 1080, 697, 392))
    assert scaled == pytest.approx(full / 2, rel=0.001)


def test_rotated_monitor_gives_the_same_size():
    # the geometry rotates, while what the monitor reports in mm does not
    assert logical_dpi(1440, 2560, 597, 336) == pytest.approx(logical_dpi(2560, 1440, 597, 336))


@pytest.mark.parametrize('geometry', [
    (1920, 1080, 0, 0),  # no EDID
    (1920, 1080, 16, 9),  # an aspect ratio instead of a size
    (1920, 1080, 160, 90),  # still an aspect ratio
    (3840, 2160, 10, 10),
    (1920, 1080, 5000, 3000),  # nonsense or a very large wall
])
def test_impossible_monitor_data_is_not_trusted(geometry):
    assert logical_dpi(*geometry) is None


def test_without_monitor_data_it_is_what_gtk_would_do():
    assert text_px(10, None) == pytest.approx(13.33, abs=0.01)
