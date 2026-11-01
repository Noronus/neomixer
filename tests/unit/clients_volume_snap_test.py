import pytest

from neomixer.clients.gtk.widgets.common.volume_widget import snap_to_unity


@pytest.mark.parametrize('volume, target, end', [
    (98, 103, 100),   # past it
    (103, 98, 100),
    (90, 110, 100),   # past it from far away, or steps of 20 never hit it
    (95, 100, 100),
    (97, 98, 100),    # lands close, from below
    (102, 101, 100),  # and from above
    (90, 95, 95),     # -1.3 dB is not close
    (110, 105, 105),  # +1.3 dB is not either
    (100, 95, 95),    # away from it is free
    (100, 105, 105),
    (100, 99, 99),    # also within the range: small steps have to be able to leave
    (100, 101, 101),
    (99, 98, 98),     # away from it, within the range
    (101, 102, 102),
    (50, 55, 55),     # nowhere near
    (5, 0, 0),        # silence
    (0, 5, 5),
])
def test_one_db(volume, target, end):
    assert snap_to_unity(volume, target, 1.0) == end


def test_the_range_is_a_setting():
    assert snap_to_unity(90, 95, 1.0) == 95
    assert snap_to_unity(90, 95, 2.0) == 100


@pytest.mark.parametrize('volume, target', [(98, 103), (103, 98), (97, 98), (95, 100)])
def test_no_range_is_no_snapping(volume, target):
    assert snap_to_unity(volume, target, 0) == target
