import numpy as np
import pytest

from chesslens.capture import Roi, auto_trim
from chesslens.capture.base import Frame


def test_pixel_round_trip():
    roi = Roi.from_pixels(120, 40, 600, 600, 1084, 681)
    assert roi.to_pixels(1084, 681) == (120, 40, 600, 600)


def test_from_pixels_clips_and_accepts_reversed_drag():
    roi = Roi.from_pixels(900, 700, -400, -300, 1000, 600)  # dragged up-left from below the frame
    assert roi.to_pixels(1000, 600) == (500, 400, 400, 200)


def test_fractions_follow_a_uniformly_resized_window():
    roi = Roi.from_pixels(100, 50, 200, 200, 1000, 500)
    assert roi.to_pixels(2000, 1000) == (200, 100, 400, 400)


@pytest.mark.parametrize("args", [(-0.1, 0, 0.5, 0.5), (0, 0, 0, 0.5), (0.6, 0, 0.5, 0.5)])
def test_rejects_rectangles_outside_the_frame(args):
    with pytest.raises(ValueError):
        Roi(*args)


def test_from_pixels_rejects_empty_rectangle():
    with pytest.raises(ValueError):
        Roi.from_pixels(2000, 2000, 10, 10, 1000, 600)


def test_auto_trim_finds_phone_screen_between_pillarbox_bars():
    image = np.zeros((300, 500, 3), np.uint8)
    rng = np.random.default_rng(0)
    image[:, 150:350] = rng.integers(40, 255, (300, 200, 3), dtype=np.uint8)
    roi = auto_trim(image)
    assert roi is not None
    assert roi.to_pixels(500, 300) == (150, 0, 200, 300)


def test_auto_trim_ignores_stray_pixels_in_the_bars():
    image = np.zeros((300, 500, 3), np.uint8)
    image[50:250, 150:350] = 200
    image[10, 10] = 255  # e.g. a cursor dot in the letterbox
    roi = auto_trim(image)
    assert roi.to_pixels(500, 300) == (150, 50, 200, 200)


def test_auto_trim_ignores_an_overlay_icon_floating_in_the_bars():
    # AirScreen shows a settings button in the top-right bar while the mouse is over its window.
    image = np.zeros((681, 1084, 3), np.uint8)
    image[:, 385:699] = (49, 46, 43)
    image[200:500, 385:699] = (210, 238, 238)
    image[30:70, 940:980] = (90, 90, 90)
    assert auto_trim(image).to_pixels(1084, 681) == (385, 0, 314, 681)


def test_auto_trim_returns_none_without_borders():
    image = np.random.default_rng(1).integers(0, 255, (100, 100, 3), dtype=np.uint8)
    assert auto_trim(image) is None


def test_frame_roi_image_is_the_crop():
    image = np.arange(10 * 20 * 3, dtype=np.uint8).reshape(10, 20, 3)
    frame = Frame(image=image, seq=1, timestamp=0, wall_time=0, source_id="t", roi=Roi(0.5, 0.0, 0.5, 0.5))
    assert frame.roi_image.shape == (5, 10, 3)
    assert np.array_equal(frame.roi_image, image[0:5, 10:20])
    assert Frame(image=image, seq=1, timestamp=0, wall_time=0, source_id="t").roi_image is image
