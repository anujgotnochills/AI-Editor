"""Tests for the StyleCut FastAPI Engine Server."""
from pathlib import Path
from unittest.mock import MagicMock, patch
import pytest
from fastapi.testclient import TestClient

from src.server.app import app, job_manager
from src.server.models import JobStatus

client = TestClient(app)


def test_health_endpoint():
    response = client.get("/health")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ok"
    assert data["version"] == "0.1.0"
    assert data["engine"] == "StyleCut Engine"
    assert "cuda_available" in data
    assert "device" in data
    assert "ffmpeg_available" in data


def test_caption_job_validation_missing_file():
    payload = {
        "media_path": "Z:/definitely_non_existent_clip_12345.mp4",
        "language": "en",
        "hinglish": True,
    }
    response = client.post("/jobs/captions", json=payload)
    assert response.status_code == 400
    assert "not found" in response.json()["detail"].lower()


def test_job_progress_not_found():
    response = client.get("/jobs/non_existent_job_id/progress")
    assert response.status_code == 404


def test_job_result_not_found():
    response = client.get("/jobs/non_existent_job_id/result")
    assert response.status_code == 404


def test_caption_job_lifecycle_mocked(tmp_path):
    # Create a dummy media file
    dummy_media = tmp_path / "sample.mp4"
    dummy_media.write_text("dummy media content")

    dummy_transcript = {
        "clip": "sample.mp4",
        "duration_sec": 4.5,
        "segments": [
            {
                "start": 0.0,
                "end": 2.0,
                "text": "Hello world",
                "words": [
                    {"word": "Hello", "start": 0.0, "end": 0.8, "probability": 0.95},
                    {"word": "world", "start": 0.9, "end": 2.0, "probability": 0.98},
                ],
            }
        ],
        "words": [
            {"word": "Hello", "start": 0.0, "end": 0.8, "probability": 0.95},
            {"word": "world", "start": 0.9, "end": 2.0, "probability": 0.98},
        ],
    }

    with patch("src.server.jobs.transcribe_clip", return_value=dummy_transcript), \
         patch("src.server.jobs.convert_transcript_to_hinglish", return_value=dummy_transcript):

        payload = {
            "media_path": str(dummy_media),
            "language": "en",
            "hinglish": True,
            "words_per_line": 3,
            "max_lines": 2,
        }
        res = client.post("/jobs/captions", json=payload)
        assert res.status_code == 200
        job_id = res.json()["job_id"]

        # Poll progress until finished
        import time
        max_wait = 15.0
        start_t = time.time()
        final_status = None
        while time.time() - start_t < max_wait:
            p_res = client.get(f"/jobs/{job_id}/progress")
            assert p_res.status_code == 200
            data = p_res.json()
            if data["status"] in ["completed", "failed"]:
                final_status = data["status"]
                break
            time.sleep(0.1)

        assert final_status == "completed"

        # Check result
        r_res = client.get(f"/jobs/{job_id}/result")
        assert r_res.status_code == 200
        r_data = r_res.json()
        assert r_data["status"] == "completed"
        assert r_data["srt_en"] is not None
        assert Path(r_data["srt_en"]).exists()
        assert r_data["srt_hi"] is not None
        assert Path(r_data["srt_hi"]).exists()


def test_rough_cut_job_lifecycle_mocked(tmp_path):
    dummy_media = tmp_path / "talking_head.mp4"
    dummy_media.write_text("video binary")

    dummy_transcript = {
        "clip": "talking_head.mp4",
        "duration_sec": 5.0,
        "segments": [
            {
                "start": 0.5,
                "end": 2.0,
                "text": "Welcome to my video.",
                "words": [
                    {"word": "Welcome", "start": 0.5, "end": 1.0, "probability": 0.95},
                    {"word": "to", "start": 1.0, "end": 1.2, "probability": 0.95},
                    {"word": "my", "start": 1.2, "end": 1.5, "probability": 0.95},
                    {"word": "video", "start": 1.5, "end": 2.0, "probability": 0.95},
                ],
            }
        ],
        "words": [
            {"word": "Welcome", "start": 0.5, "end": 1.0, "probability": 0.95},
            {"word": "to", "start": 1.0, "end": 1.2, "probability": 0.95},
            {"word": "my", "start": 1.2, "end": 1.5, "probability": 0.95},
            {"word": "video", "start": 1.5, "end": 2.0, "probability": 0.95},
        ],
    }

    from unittest.mock import MagicMock
    from src.cut_engine.qa import QASuiteReport

    fake_report = QASuiteReport(
        overall_status="PASS",
        checks=[],
        metrics={"duration_saved_sec": 1.5, "reduction_percentage": 30.0},
        flags=[],
    )
    fake_cut_res = MagicMock(
        cut_list_path=tmp_path / "cut_list.json",
        xml_path=tmp_path / "sequence.xml",
        report_path=tmp_path / "cut_report.md",
        cut_list={
            "total_original_duration_sec": 5.0,
            "total_cut_duration_sec": 3.5,
            "duration_saved_sec": 1.5,
            "reduction_percentage": 30.0,
            "cuts_count": 3,
        },
        qa_report=fake_report,
    )
    fake_cut_res.cut_list_path.write_text('{"cuts": []}')
    fake_cut_res.xml_path.write_text('<xmeml></xmeml>')
    fake_cut_res.report_path.write_text('# Cut Report\n\nAll clear!')

    with patch("src.server.jobs.transcribe_clip", return_value=dummy_transcript), \
         patch("src.cut_engine.engine.CutEngine.process", return_value=fake_cut_res):

        payload = {
            "media_path": str(dummy_media),
            "preset": "reel",
            "tighten": True,
            "remove_fillers": True,
            "remove_retakes": True,
        }
        res = client.post("/jobs/rough_cut", json=payload)
        assert res.status_code == 200
        job_id = res.json()["job_id"]

        import time
        max_wait = 10.0
        start_t = time.time()
        final_status = None
        while time.time() - start_t < max_wait:
            p_res = client.get(f"/jobs/{job_id}/progress")
            assert p_res.status_code == 200
            data = p_res.json()
            if data["status"] in ["completed", "failed"]:
                final_status = data["status"]
                break
            time.sleep(0.1)

        assert final_status == "completed"

        # Check rough cut result
        r_res = client.get(f"/jobs/{job_id}/rough_cut_result")
        assert r_res.status_code == 200
        r_data = r_res.json()
        assert r_data["status"] == "completed"
        assert r_data["cuts_count"] == 3
        assert r_data["duration_saved"] == 1.5
        assert r_data["reduction_pct"] == 30.0
        assert r_data["xml_path"] is not None
        assert Path(r_data["xml_path"]).exists()

        # Check report endpoint
        rep_res = client.get(f"/jobs/{job_id}/report")
        assert rep_res.status_code == 200
        assert "Cut Report" in rep_res.json()["markdown"]
