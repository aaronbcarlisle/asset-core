"""BroadcastSink retention cap + gap detection, and the NOTIFY size guard.

These pin the documented durability limits of the in-memory spine: the log is
bounded, and a resume from before the horizon is *detectable* (not a silent gap).
"""
import pytest

from assetcore.core.entities import Event
from assetcore.infra.broadcast_sink import BroadcastSink
from assetcore.infra.notify_sink import _notify_payload_fits


def test_log_is_bounded_and_tracks_dropped_seq():
    sink = BroadcastSink(max_log=3)
    for _ in range(5):
        sink.emit(Event(None, "declared"))
    # only the last 3 are retained (seq 3,4,5); seqs 1,2 were evicted
    assert [s for s, _ in sink.history(0)] == [3, 4, 5]
    assert sink.dropped_seq == 2          # highest evicted seq
    assert sink.last_seq == 5


def test_has_gap_true_when_resume_is_behind_horizon():
    sink = BroadcastSink(max_log=3)
    for _ in range(5):
        sink.emit(Event(None, "declared"))   # retains seq 3,4,5
    assert sink.has_gap(1) is True           # wants seq 2+, but earliest is 3 -> gap
    assert sink.has_gap(2) is False          # wants seq 3+, exactly the earliest
    assert sink.has_gap(4) is False          # within the retained window
    assert sink.has_gap(0) is False          # a fresh subscriber never gaps


def test_no_gap_when_within_capacity():
    sink = BroadcastSink(max_log=100)
    for _ in range(5):
        sink.emit(Event(None, "declared"))
    assert sink.has_gap(1) is False
    assert sink.dropped_seq == 0


def test_max_log_must_be_positive():
    with pytest.raises(ValueError):
        BroadcastSink(max_log=0)


def test_notify_payload_fits_guard():
    assert _notify_payload_fits("x" * 100) is True
    assert _notify_payload_fits("x" * 8000) is False
    # multi-byte characters count by encoded length, not char count
    assert _notify_payload_fits("€" * 3000, limit=7500) is False   # 3 bytes each = 9000
