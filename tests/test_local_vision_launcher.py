"""Exercise the resource-bounded launcher without starting a model."""
import os
from pathlib import Path
import subprocess


LAUNCHER = Path(__file__).resolve().parents[1] / "bin/ligar-visao-local.sh"


def test_launcher_shell_syntax():
    subprocess.run(["bash", "-n", str(LAUNCHER)], check=True)


def test_existing_unit_is_reused_without_duplicate_model(tmp_path):
    for name, text in {
        "systemctl": "exit 0",
        "curl": "exit 0",
        "systemd-run": "exit 99",
    }.items():
        script = tmp_path / name
        script.write_text("#!/bin/sh\n" + text + "\n")
        script.chmod(0o755)
    result = subprocess.run(["bash", str(LAUNCHER)],
        env={**os.environ, "PATH": str(tmp_path) + ":/usr/bin:/bin"},
        capture_output=True, text=True, timeout=3)
    assert result.returncode == 0 and "ready" in result.stdout


def test_model_launch_retains_memory_bounds_and_disables_ram_cache():
    source = LAUNCHER.read_text()
    assert "--cache-ram 0" in source
    assert "--property=MemoryMax=4G --property=MemorySwapMax=512M" in source
    assert "--parallel 1" in source and "-c 4096" in source
    assert "LFM2-VL-1.6B-Q4_0.gguf" in source
    assert "mmproj-LFM2-VL-1.6B-Q8_0.gguf" in source
    assert "--alias celia-lfm2-vl-1.6b," in source
    assert "celia-qwen3.5-2b" not in source
