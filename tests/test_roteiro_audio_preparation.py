import json
import os
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_audio_pack_preparation_dry_run_reports_the_validated_production_pack(tmp_path):
    output = tmp_path / "dry-run.json"
    environment = {**os.environ, "PYTHONPATH": str(ROOT / "src")}
    result = subprocess.run(
        [
            sys.executable,
            str(ROOT / "tools" / "prepare_roteiro_audio_pack.py"),
            "--dry-run",
            "--output",
            str(output),
        ],
        cwd=ROOT,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    record = json.loads(output.read_text(encoding="utf-8"))
    assert record["status"] == "dry_run"
    assert record["expected_line_count"] == 27
    assert record["prepared_line_count"] == 27
    assert record["missing_line_ids"] == []
    assert record["voice"]["reference_text_configured"] is True
    assert "ref_text" not in json.dumps(record)
