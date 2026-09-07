"""Integration-style tests for agent/services/video_reviewer.py's
_create_contact_sheets: real ffmpeg frame extraction + REVIEW_MAX_FRAMES cap +
chunking into contact sheets. No mocking of ffmpeg subprocess calls here —
these tests generate a real short synthetic video and verify the chunking
math against real ffmpeg output.
"""
import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest

from agent.services.video_reviewer import _create_contact_sheets


@pytest.fixture(scope="module")
def synthetic_video():
    """A real ~2-second synthetic test video generated once for this test module."""
    tmp_dir = tempfile.mkdtemp()
    video_path = Path(tmp_dir) / "synthetic.mp4"
    result = subprocess.run(
        ["ffmpeg", "-y", "-f", "lavfi", "-i", "testsrc=duration=2:size=320x240:rate=30",
         str(video_path)],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        pytest.skip(f"ffmpeg unavailable or failed to generate test video: {result.stderr[-300:]}")
    yield video_path


class TestCreateContactSheetsChunking:
    def test_2s_video_at_4fps_produces_correct_sheet_count(self, synthetic_video):
        # 2s * 4fps = 8 frames -> ceil(8/9) = 1 sheet
        with tempfile.TemporaryDirectory() as out_dir:
            sheets, total_frames = _create_contact_sheets(str(synthetic_video), 4, out_dir)
            assert total_frames == 8
            assert len(sheets) == 1
            assert all(s.exists() and s.stat().st_size > 0 for s in sheets)

    def test_2s_video_at_8fps_produces_correct_sheet_count(self, synthetic_video):
        # 2s * 8fps = 16 frames -> ceil(16/9) = 2 sheets
        with tempfile.TemporaryDirectory() as out_dir:
            sheets, total_frames = _create_contact_sheets(str(synthetic_video), 8, out_dir)
            assert total_frames == 16
            assert len(sheets) == 2
            assert all(s.exists() and s.stat().st_size > 0 for s in sheets)

    def test_review_max_frames_caps_total_and_sheet_count(self, synthetic_video, monkeypatch):
        import agent.services.video_reviewer as vr
        monkeypatch.setattr(vr, "REVIEW_MAX_FRAMES", 5)
        with tempfile.TemporaryDirectory() as out_dir:
            sheets, total_frames = _create_contact_sheets(str(synthetic_video), 8, out_dir)
            # 16 natural frames capped to 5 -> ceil(5/9) = 1 sheet
            assert total_frames == 5
            assert len(sheets) == 1

    def test_downsampling_selects_correct_nonconsecutive_frames_across_chunks(
        self, synthetic_video, monkeypatch
    ):
        """The single most important correctness property of this function: after
        REVIEW_MAX_FRAMES downsampling, each chunk must be tiled from the CORRECT
        (possibly non-contiguous) subset of frames, not a contiguous slice of the
        original numbering. A naive -start_number/-frames:v approach against the
        original frame_%04d.jpg sequence would silently tile the wrong frames here.
        """
        import agent.services.video_reviewer as vr

        # 2s * 30fps = 60 natural frames; capping to 20 spans 3 chunks (ceil(20/9)=3)
        # and is genuinely non-contiguous (step = 60/20 = 3.0, picks frames 0,3,6,...).
        monkeypatch.setattr(vr, "REVIEW_MAX_FRAMES", 20)
        out_dir = tempfile.mkdtemp()
        try:
            sheets, total_frames = _create_contact_sheets(str(synthetic_video), 30, out_dir)
            assert total_frames == 20
            assert len(sheets) == 3

            all_frames = sorted((Path(out_dir) / "frames").glob("frame_*.jpg"))
            assert len(all_frames) == 60
            step = len(all_frames) / 20
            expected_selection = [all_frames[int(i * step)] for i in range(20)]

            per_sheet = 9
            for sheet_idx, start in enumerate(range(0, 20, per_sheet)):
                expected_chunk = expected_selection[start:start + per_sheet]
                chunk_dir = Path(out_dir) / f"_chunk_{sheet_idx:02d}"
                symlinks = sorted(chunk_dir.glob("f_*.jpg"))
                assert len(symlinks) == len(expected_chunk)
                for link, expected_target in zip(symlinks, expected_chunk):
                    assert link.resolve() == expected_target.resolve(), (
                        f"chunk {sheet_idx} symlink {link.name} points to "
                        f"{link.resolve()}, expected {expected_target.resolve()}"
                    )
        finally:
            shutil.rmtree(out_dir, ignore_errors=True)

    @pytest.mark.parametrize(
        "chunk_size,expected_cols,expected_rows",
        [
            (1, 1, 1), (2, 2, 1), (3, 3, 1), (4, 2, 2), (5, 1, 5),
            (6, 3, 2), (7, 1, 7), (8, 2, 4), (9, 3, 3),
        ],
    )
    def test_partial_trailing_chunk_uses_zero_waste_layout(
        self, synthetic_video, monkeypatch, chunk_size, expected_cols, expected_rows
    ):
        """Every possible chunk size (1-9) must produce a tile grid with EXACTLY that
        many cells -- zero unfilled cells. A layout with any unfilled cell renders that
        cell as a solid color block (not blank), which vision models can and do misread
        as a defect in the source video (confirmed via a live review call during
        development: a 3x2 layout with 1 blank cell out of 6 was enough to trigger a
        fabricated "HIGH severity" corruption finding). Asserting the EXACT output
        dimensions (not just "smaller than the old fixed 3x3") is what catches a
        regression to a min/ceil-style approximate shrink that still leaves waste for
        some chunk sizes (e.g. size 4 under min(4,3)=3,ceil(4/3)=2 gives 3x2=6 cells,
        2 wasted -- this exact regression was caught by this test during development).
        """
        import agent.services.video_reviewer as vr

        monkeypatch.setattr(vr, "REVIEW_MAX_FRAMES", chunk_size)
        out_dir = tempfile.mkdtemp()
        try:
            sheets, total_frames = _create_contact_sheets(str(synthetic_video), 30, out_dir)
            assert total_frames == chunk_size
            assert len(sheets) == 1
            probe = subprocess.run(
                ["ffprobe", "-v", "error", "-select_streams", "v:0",
                 "-show_entries", "stream=width,height", "-of", "csv=p=0", str(sheets[0])],
                capture_output=True, text=True,
            )
            width, height = (int(x) for x in probe.stdout.strip().split(","))
            frame_w, frame_h = 320, 240  # scale=320:-1 applied to the 320x240 synthetic_video
            assert (width, height) == (expected_cols * frame_w, expected_rows * frame_h), (
                f"chunk_size={chunk_size}: expected a {expected_cols}x{expected_rows} "
                f"zero-waste layout ({expected_cols*frame_w}x{expected_rows*frame_h}), "
                f"got {width}x{height} -- some cells are unfilled/wasted"
            )
        finally:
            shutil.rmtree(out_dir, ignore_errors=True)
