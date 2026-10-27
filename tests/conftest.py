import os
import sys
import time
from collections.abc import Callable
from typing import TypeVar

import numpy as np
import pytest

# Qt widgets in tests render offscreen; nothing is shown on the desktop.
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
if sys.platform == "win32":
    os.environ.setdefault("QT_QPA_FONTDIR", r"C:\Windows\Fonts")

T = TypeVar("T")


def wait_for(predicate: Callable[[], T], timeout: float = 3.0, interval: float = 0.01) -> T:
    """Poll until predicate() is truthy (any array counts) and return its value; fail the test on timeout."""
    deadline = time.monotonic() + timeout
    while True:
        value = predicate()
        if isinstance(value, np.ndarray) or value:
            return value
        if time.monotonic() > deadline:
            raise AssertionError(f"condition not met within {timeout}s")
        time.sleep(interval)


@pytest.fixture(autouse=True)
def isolated_appdata(monkeypatch, tmp_path_factory):
    """Point %APPDATA% at a temp folder so tests never read or write the user's settings or piece sets,
    and the Game Bar feed at another, so they never write into an installed widget's folder."""
    folder = tmp_path_factory.mktemp("appdata")
    monkeypatch.setenv("APPDATA", str(folder))
    monkeypatch.setattr("chesslens.gamebar.packages_dir", lambda: folder / "Packages")
    return folder


@pytest.fixture(scope="session")
def qapp():
    from PySide6.QtWidgets import QApplication

    return QApplication.instance() or QApplication([])


def dispose(widget) -> None:
    """Close and destroy a top-level widget now, as pytest-qt's qtbot does.

    Widgets with signal connections sit in reference cycles; left to the
    garbage collector they may be freed during interpreter shutdown, after
    the QApplication, which crashes Qt intermittently on exit.
    """
    from PySide6.QtCore import QCoreApplication, QEvent

    widget.close()
    widget.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


def process_until(app, predicate: Callable[[], object], timeout: float = 5.0) -> None:
    """Run the Qt event loop until predicate() is truthy."""
    deadline = time.monotonic() + timeout
    while not predicate():
        if time.monotonic() > deadline:
            raise AssertionError("condition not met")
        app.processEvents()
        time.sleep(0.01)
