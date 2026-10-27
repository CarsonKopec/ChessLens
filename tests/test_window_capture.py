"""Live Win32 tests: they open small topmost Tk windows on the desktop and capture them."""

import subprocess
import sys
import uuid

import numpy as np
import pytest
from conftest import wait_for

pytestmark = [
    pytest.mark.desktop,
    pytest.mark.skipif(sys.platform != "win32", reason="Win32 window capture"),
]

if sys.platform == "win32":
    import win32con
    import win32gui

    from chesslens.capture import SourceLostError, SourceState
    from chesslens.capture.window_capture import WindowCaptureSource, find_window, list_windows

# Stand-in for the mirroring app: a separate process showing a coloured window,
# optionally with a black 80×80 square in the middle so the picture is not blank.
_WINDOW_SCRIPT = r"""
import sys, tkinter as tk
root = tk.Tk()
root.title(sys.argv[1])
root.configure(bg=sys.argv[2])
root.geometry(sys.argv[3])
if sys.argv[4] == "marker":
    tk.Frame(root, bg="black", width=80, height=80).place(relx=0.5, rely=0.5, anchor="center")
root.attributes("-topmost", True)
root.mainloop()
"""
GREEN_BGR = np.array([0x40, 0xC0, 0x20])


def open_window(title: str, color: str = "#20c040", geometry: str = "320x240+240+240", marker: bool = True):
    args = [sys.executable, "-c", _WINDOW_SCRIPT, title, color, geometry, "marker" if marker else "plain"]
    proc = subprocess.Popen(args)
    window = wait_for(lambda: find_window(title), timeout=10)
    return proc, window


def close(proc: subprocess.Popen) -> None:
    proc.kill()
    proc.wait(timeout=5)


@pytest.fixture
def mirror_window():
    title = f"chesslens-test-{uuid.uuid4().hex[:8]}"
    proc, window = open_window(title)
    source = WindowCaptureSource(window)
    source.start()
    yield proc, window, source
    source.stop()
    close(proc)


def is_helper_picture(image: np.ndarray) -> bool:
    h, w = image.shape[:2]
    return bool(np.abs(image[5, 5] - GREEN_BGR).max() < 6 and image[h // 2, w // 2].max() < 10)


def helper_frame(source) -> np.ndarray:
    """Next settled frame of the helper window (a new or restored window animates in for a moment)."""
    previous = [None]

    def attempt():
        image = source.read()
        # Ignore a 10 px border: Windows 11's rounded corners show whatever is behind the window,
        # which may be a playing video.
        settled = (
            image is not None
            and previous[0] is not None
            and np.array_equal(image[10:-10, 10:-10], previous[0][10:-10, 10:-10])
        )
        previous[0] = image
        return image if settled and is_helper_picture(image) else None

    try:
        return wait_for(attempt, interval=0.05)
    except AssertionError:
        last = previous[0]
        seen = "no frame" if last is None else f"shape {last.shape}, corner {last[5, 5]}, centre {last[last.shape[0] // 2, last.shape[1] // 2]}"
        raise AssertionError(f"helper window never showed a settled picture; status {source.status}; last {seen}") from None


def test_window_is_listed_with_title_and_process(mirror_window):
    _, window, _ = mirror_window
    listed = list_windows(window.title)
    assert listed[0].hwnd == window.hwnd  # hint match sorts first
    assert listed[0].process_name.lower().startswith("python")


def test_captures_exactly_the_client_area(mirror_window):
    _, _, source = mirror_window
    image = helper_frame(source)
    assert source.status.ok
    # Exact client size, green right up to the edges, and the marker centred:
    # no title bar or border made it into the frame.
    assert image.shape == (240, 320, 3)
    # Skip the corners: Windows 11 rounds them and the desktop shows through.
    inner = slice(10, -10)
    edges = np.concatenate([image[0, inner], image[-1, inner], image[inner, 0], image[inner, -1]])
    assert np.abs(edges - GREEN_BGR).max() < 6
    marker = np.argwhere(image[inner, inner].max(axis=2) < 10) + 10
    assert marker.min(axis=0).tolist() == [80, 120]
    assert marker.max(axis=0).tolist() == [159, 199]


def test_uniform_window_is_reported_blank():
    proc, window = open_window(f"chesslens-blank-{uuid.uuid4().hex[:8]}", marker=False)
    source = WindowCaptureSource(window)
    source.start()
    try:
        wait_for(lambda: source.read() is None and source.status.state is SourceState.BLANK)
    finally:
        source.stop()
        close(proc)


def test_minimized_window_yields_no_frames(mirror_window):
    _, window, source = mirror_window
    helper_frame(source)
    win32gui.ShowWindow(window.hwnd, win32con.SW_MINIMIZE)
    wait_for(lambda: win32gui.IsIconic(window.hwnd))
    assert source.read() is None
    assert source.status.state is SourceState.MINIMIZED

    win32gui.ShowWindow(window.hwnd, win32con.SW_RESTORE)
    helper_frame(source)


def test_covered_window_is_reported_not_captured(mirror_window):
    _, _, source = mirror_window
    helper_frame(source)
    cover_title = f"chesslens-cover-{uuid.uuid4().hex[:8]}"
    cover, _ = open_window(cover_title, color="#c02020", geometry="200x200+300+280")
    try:
        wait_for(lambda: source.read() is None and source.status.state is SourceState.OCCLUDED)
        assert cover_title in source.status.detail
    finally:
        close(cover)
    helper_frame(source)


def test_closed_window_is_lost_and_reacquired_when_reopened(mirror_window):
    proc, window, source = mirror_window
    close(proc)
    wait_for(lambda: not win32gui.IsWindow(window.hwnd))
    with pytest.raises(SourceLostError):
        source.read()
    source.stop()
    with pytest.raises(SourceLostError):
        source.start()

    # The app comes back with a new window handle; the source finds it again.
    reopened, new_window = open_window(window.title)
    try:
        source.start()
        assert source.window.hwnd == new_window.hwnd
        helper_frame(source)
    finally:
        source.stop()
        close(reopened)
