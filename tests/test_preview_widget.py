import numpy as np
import pytest
from conftest import dispose
from PySide6.QtCore import QPointF

from chesslens.capture.roi import Roi
from chesslens.domain.models import Orientation
from chesslens.ui.preview_widget import PreviewWidget, edge_labels


@pytest.mark.parametrize(
    "orientation, corner_file, corner_rank",
    [(Orientation.WHITE_BOTTOM, "a", "1"), (Orientation.BLACK_BOTTOM, "h", "8")],
)
def test_bottom_left_square_gets_both_its_file_and_rank(orientation, corner_file, corner_rank):
    labels = edge_labels(orientation)
    files = [text for (u, v), text in labels if v > 7.5]
    ranks = [text for (u, v), text in labels if u < 0.5]
    assert files[0] == corner_file and ranks[-1] == corner_rank
    assert sorted(files) == list("abcdefgh") and sorted(ranks) == list("12345678")
    # The two labels in the bottom-left square must not overlap (they are ~0.4 squares wide).
    (fu, fv), (ru, rv) = labels[0][0], labels[-1][0]
    assert abs(fu - ru) > 0.3 or abs(fv - rv) > 0.4


def test_widget_and_frame_coordinates_round_trip_with_a_crop(qapp):
    w = PreviewWidget()
    w.resize(600, 500)
    w.show_frame(np.zeros((681, 1084, 3), np.uint8))
    w.set_view(Roi.from_pixels(385, 0, 314, 681, 1084, 681))
    for x, y in [(385, 0), (699, 681), (500.5, 300.25)]:
        assert w._to_frame(w._to_widget(x, y)) == pytest.approx((x, y), abs=1e-6)
    assert w._to_frame(QPointF(-50, -50)) == (385.0, 0.0)  # clamped to the visible crop
    assert w._to_frame(QPointF(5000, 5000)) == (699.0, 681.0)
    dispose(w)
