from pathlib import Path


ROOT = Path(__file__).parents[1]


def test_launcher_serializes_boot_and_reuses_inflight_pid():
    source = (ROOT / "bin" / "ligar.sh").read_text(encoding="utf-8")

    assert 'START_LOCK="$RUN_DIR/celia-start.lock"' in source
    assert 'PID_FILE="$RUN_DIR/celia.pid"' in source
    assert 'flock -w "$LOCK_WAIT_SECONDS" 9' in source
    assert 'BOOT_WAIT_SECONDS="${CELIA_BOOT_WAIT_SECONDS:-180}"' in source
    assert 'BOOT_PID="$(celia_pid_alive 2>/dev/null || true)"' in source
    assert 'boot já em andamento no PID $BOOT_PID' in source
    assert 'printf \'%s\\n\' "$BOOT_PID" > "$PID_FILE"' in source
    assert 'nohup "$VENV_PY" run_celia_linux.py 9>&-' in source
    assert 'Não iniciarei outra instância enquanto esse PID continuar vivo.' in source


def test_launcher_sobe_tagarela_antes_do_boot_e_nao_conhece_whisper_local():
    source = (ROOT / "bin" / "ligar.sh").read_text(encoding="utf-8")

    # ASR único: TAGARELA (:8011). O Whisper local foi desinstalado em 2026-10-04.
    assert "ensure_tagarela()" in source
    assert "celia-asr-turbo-local" not in source
    assert "8010" not in source
    assert "ensure_local_asr" not in source
    assert "quiesce_local_asr_for_boot" not in source
    spawn_at = source.index('nohup "$VENV_PY" run_celia_linux.py')
    ensure_at = source.rindex("  ensure_tagarela\n", 0, spawn_at)
    assert ensure_at < spawn_at


def test_launcher_preflights_matching_torch_and_torchaudio():
    source = (ROOT / "bin" / "ligar.sh").read_text(encoding="utf-8")

    assert 'BASE_SITE="$("$BASE_PY" -c' in source
    assert 'import torch' in source
    assert 'import torchaudio' in source
    assert 'torch.__version__.split("+", 1)[0] == torchaudio.__version__.split("+", 1)[0]' in source
