"""Run the actual launcher in an isolated tree; no live services or worlds."""
import os
from pathlib import Path
import shutil
import subprocess

import pytest

ROOT = Path(__file__).resolve().parents[1]


def launch(tmp_path, *, mode="ok", args=(), world="celia-serie-s04", env_overrides=None):
    for directory in ("bin", ".venv/bin", "fakebin", "var/logs", "var/runtime/minecraft-test-1.21.10"):
        (tmp_path / directory).mkdir(parents=True, exist_ok=True)
    shutil.copyfile(ROOT / "bin/ligar-gravacao-ep1.sh", tmp_path / "bin/ligar-gravacao-ep1.sh")
    (tmp_path / "var/logs/ultimo.log").write_text("celia-gemma4-e2b\n")
    (tmp_path / "var/runtime/minecraft-test-1.21.10/server.properties").write_text(f"level-name={world}\n")
    scripts = {
        "fakebin/curl": "echo celia-gemma4-e2b",
        "fakebin/sleep": ":",
        "fakebin/pkill": ":",
        "fakebin/pgrep": "echo 999999",
        "bin/ligar-jev.sh": "echo jev >> trace",
        "bin/ligar.sh": "echo persona >> trace",
        "bin/celia-cota": "echo quota-ok",
        ".venv/bin/python": """echo "$*" >> trace
if [ "$1" = -c ]; then exec /usr/bin/python3 "$@"; fi
case "$2" in
--json) if [ "$MODE" = missing_world ]; then echo '{"worlds": []}'; else printf '{"worlds":[{"world_id":"%s","exists":true,"valid":true}]}' "$MUNDO"; fi ;;
select) [ "$MODE" != select_fail ] ;;
switch) echo 'status: ok'; [ "$MODE" != switch_fail ] ;;
series-status) [ "$MODE" = empty_state ] || echo 'state: INTRO_READY' ;;
esac""",
    }
    for name, source in scripts.items():
        path = tmp_path / name
        path.write_text("#!/usr/bin/env bash\n" + source + "\n")
        path.chmod(0o755)
    env = {**os.environ, "PATH": str(tmp_path / "fakebin") + ":" + os.environ["PATH"], "MODE": mode, "MUNDO": world}
    for key in ("CELIA_WORLD", "CELIA_MUNDO"):
        env.pop(key, None)
    for key, value in (env_overrides or {}).items():
        if value is None:
            env.pop(key, None)
        else:
            env[key] = value
    result = subprocess.run(["bash", "bin/ligar-gravacao-ep1.sh", "--sem-persona", *args], cwd=tmp_path, env=env, capture_output=True, text=True, timeout=10)
    trace = (tmp_path / "trace").read_text() if (tmp_path / "trace").exists() else ""
    return result, trace


@pytest.mark.parametrize("mode", ["select_fail", "switch_fail"])
def test_world_failure_aborts_before_starting_jev(tmp_path, mode):
    result, trace = launch(tmp_path, mode=mode)
    assert result.returncode != 0
    assert "jev\n" not in trace
    assert "PRONTO." not in result.stdout


def test_empty_series_state_is_a_failure_exit(tmp_path):
    result, _ = launch(tmp_path, mode="empty_state")
    assert result.returncode != 0
    assert "PRONTO." not in result.stdout


@pytest.mark.parametrize("args", [("--typo",), ("--mundo",)])
def test_invalid_arguments_do_not_start_services(tmp_path, args):
    result, trace = launch(tmp_path, args=args)
    assert result.returncode != 0
    assert not trace


def test_explicit_world_overrides_environment(tmp_path):
    result, trace = launch(tmp_path, args=("--mundo", "celia-serie-s04"), world="celia-serie-s03")
    assert "select celia-serie-s04" in trace
    assert result.returncode != 0  # properties deliberately still point to s03
    assert "jev\n" not in trace


def test_success_accepts_environment_world(tmp_path):
    result, trace = launch(tmp_path)
    assert result.returncode == 0, result.stderr
    assert "PRONTO." in result.stdout
    assert "select celia-serie-s04" in trace


@pytest.mark.parametrize("args", [
    ("celia-serie-s04",), ("-m", "celia-serie-s04"),
    ("--world=celia-serie-s04",), ("--mundo=celia-serie-s04",),
])
def test_supported_world_syntaxes(tmp_path, args):
    result, trace = launch(tmp_path, args=args, env_overrides={"MUNDO": None})
    assert result.returncode == 0, result.stderr
    assert "select celia-serie-s04" in trace


@pytest.mark.parametrize("args", [
    ("positional", "--mundo", "celia-serie-s04"),
    ("--mundo", "celia-serie-s04", "positional"),
])
def test_flag_beats_positional_in_either_order(tmp_path, args):
    result, trace = launch(tmp_path, args=args)
    assert result.returncode == 0, result.stderr
    assert "select celia-serie-s04" in trace


@pytest.mark.parametrize("values", [
    {"MUNDO": None},
    {"MUNDO": None, "CELIA_MUNDO": "celia-serie-s04"},
    {"MUNDO": "legacy", "CELIA_MUNDO": "other", "CELIA_WORLD": "celia-serie-s04"},
    {"MUNDO": "legacy", "CELIA_MUNDO": "celia-serie-s04"},
])
def test_default_and_environment_precedence(tmp_path, values):
    result, trace = launch(tmp_path, env_overrides=values)
    assert result.returncode == 0, result.stderr
    assert "select celia-serie-s04" in trace


@pytest.mark.parametrize("args", [
    ("--mundo=",), ("--mundo", "--sem-persona"), ("--mundo", "../official"),
    ("--mundo", "white space"), ("--mundo", "-bad"),
    ("one", "two"), ("--", "--unknown"),
])
def test_bad_input_is_rejected_before_any_service(tmp_path, args):
    result, trace = launch(tmp_path, args=args)
    assert result.returncode != 0
    assert not trace


def test_help_does_not_start_services(tmp_path):
    result, trace = launch(tmp_path, args=("--help",))
    assert result.returncode == 0
    assert "celia-serie-s04" in result.stdout
    assert not trace


@pytest.mark.parametrize("args,values", [
    (("--mundo", "celia-serie-s03"), {}),
    ((), {"CELIA_WORLD": "celia-serie-s03"}),
])
def test_official_world_requires_explicit_guard_before_services(tmp_path, args, values):
    result, trace = launch(tmp_path, args=args, env_overrides=values)
    assert result.returncode != 0 and not trace
    assert "--confirmar-mundo-oficial" in result.stderr


def test_explicit_official_confirmation_reaches_the_selected_world(tmp_path):
    result, trace = launch(tmp_path, world="celia-serie-s03", args=("--mundo", "celia-serie-s03", "--confirmar-mundo-oficial"))
    assert result.returncode == 0
    assert "select celia-serie-s03" in trace and "ambiente=oficial" in result.stdout


def test_missing_world_fails_before_stopping_rehearsal_or_starting_services(tmp_path):
    result, trace = launch(tmp_path, mode="missing_world")
    assert result.returncode != 0 and "inexistente ou inválido" in result.stderr
    assert "select celia" not in trace and "jev\n" not in trace
    assert "== 1." not in result.stdout
