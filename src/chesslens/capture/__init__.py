"""Frame sources and the capture thread. Must not import the chess engine or UI (spec §11)."""

from chesslens.capture.base import Frame, FrameSource, SourceLostError, SourceState, SourceStatus
from chesslens.capture.manager import CaptureManager, LatestFrameSlot
from chesslens.capture.recorded_video import VideoFileSource
from chesslens.capture.roi import Roi, auto_trim

__all__ = [
    "CaptureManager",
    "Frame",
    "FrameSource",
    "LatestFrameSlot",
    "Roi",
    "SourceLostError",
    "SourceState",
    "SourceStatus",
    "VideoFileSource",
    "auto_trim",
]
