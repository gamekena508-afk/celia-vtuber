"""Núcleo do modo Live: ranking, roteamento, configurações. Offline, sem TikTok."""
import json

import pytest

from celia.live_mode.ranking import GiftLedger
from celia.live_mode.router import detect_explicit, route
from celia.live_mode.settings import DEFAULTS, SettingsStore, validate


def gift(msg, user="u1", value=10, units=1, streakable=False, end=False, group="g1", gid="rose", origin="real", **kw):
    return {"type": "gift", "origin": origin, "msg_id": msg, "user_id": user, "display_id": kw.get("display", user),
            "nickname": kw.get("nick", user.upper()), "gift_id": gid, "gift_name": gid, "diamond_count": value,
            "repeat_count": units, "repeat_end": end, "streakable": streakable, "group_id": group}


# ---------------- ranking ----------------
def test_value_comes_from_event_and_sums_per_user():
    led = GiftLedger("real")
    led.apply(gift("m1", "a", 5), now=1)
    led.apply(gift("m2", "a", 7), now=2)
    led.apply(gift("m3", "b", 20), now=3)
    assert [(d.user_id, d.total) for d in led.top(3)] == [("b", 20), ("a", 12)]


def test_unknown_or_zero_value_is_never_counted():
    led = GiftLedger("real")
    assert led.apply(gift("m1", value=None))["credited"] == 0
    assert led.apply(gift("m2", value=0))["credited"] == 0
    assert led.apply(gift("m3", user=""))["credited"] == 0
    assert led.top() == [] and led.counters["invalid"] == 3


def test_duplicate_msg_id_is_ignored():
    led = GiftLedger("real")
    assert led.apply(gift("same", value=9))["credited"] == 9
    assert led.apply(gift("same", value=9))["reason"] == "duplicate"
    assert led.top()[0].total == 9


def test_combo_accumulated_updates_are_not_independent_gifts():
    led = GiftLedger("real")
    # combo de 5 rosas (1 moeda): o TikTok manda 1,2,3,4,5 acumulado, com o último repeat_end
    for i, units in enumerate((1, 2, 3, 4, 5), 1):
        led.apply(gift(f"c{i}", value=1, units=units, streakable=True, end=(units == 5)), now=i)
    assert led.top()[0].total == 5  # 5, não 1+2+3+4+5 = 15
    # reentrega do mesmo combo com msg_id novo não soma de novo
    assert led.apply(gift("c9", value=1, units=5, streakable=True, end=True), now=6)["reason"] == "combo_already_counted"
    assert led.top()[0].total == 5


def test_new_combo_with_new_group_counts_again_and_negative_control():
    led = GiftLedger("real")
    led.apply(gift("a1", value=1, units=3, streakable=True, end=True, group="g1"))
    led.apply(gift("a2", value=1, units=3, streakable=True, end=True, group="g2"))
    assert led.top()[0].total == 6
    # controle negativo: sem a lógica de combo (não-streakable) cada evento soma inteiro
    led2 = GiftLedger("real")
    for i, u in enumerate((1, 2, 3), 1):
        led2.apply(gift(f"n{i}", value=1, units=u, streakable=False))
    assert led2.top()[0].total == 6  # 1+2+3: prova de que o teste de combo acima discrimina


def test_tie_goes_to_whoever_reached_first_and_rank_changes():
    led = GiftLedger("real")
    led.apply(gift("m1", "a", 10), now=1)
    led.apply(gift("m2", "b", 10), now=2)
    assert [d.user_id for d in led.top(2)] == ["a", "b"]
    led.apply(gift("m3", "b", 1), now=3)
    assert [d.user_id for d in led.top(2)] == ["b", "a"]


def test_sliding_window_drops_old_gifts():
    led = GiftLedger("real", window_minutes=1)
    led.apply(gift("m1", "a", 100), now=1000)
    led.apply(gift("m2", "b", 5), now=1100)
    assert [d.user_id for d in led.top(2, now=1100)] == ["b"]  # a saiu da janela de 60 s
    led.window_minutes = 0
    assert [d.user_id for d in led.top(2, now=1100)] == ["a", "b"]


def test_real_and_simulated_never_mix():
    led = GiftLedger("real")
    assert led.apply(gift("s1", origin="simulated", value=999))["reason"] == "origin_mismatch"
    assert led.top() == []


def test_state_roundtrip_keeps_combo_dedup():
    led = GiftLedger("real")
    led.apply(gift("c1", value=2, units=2, streakable=True))
    again = GiftLedger.from_state(json.loads(json.dumps(led.to_state())))
    assert again.apply(gift("c2", value=2, units=2, streakable=True))["reason"] == "combo_already_counted"
    assert again.top()[0].total == 4


# ---------------- roteamento ----------------
@pytest.mark.parametrize("text,expected", [
    ("slime, tudo bem?", "slime"), ("Oi ursinho, gostei", "ursinho"), ("@slime fala algo", "slime"),
    ("tudo bem, ursinho?", "ursinho"), ("Ô Slime!", "slime"), ("célia, canta", "celia"),
    ("o slime da Célia é fofo", None), ("adorei a live", None), ("ursinhos de pelúcia são legais", None),
    ("ursinho você gosta de mel?", "ursinho"), ("slime fala algo engraçado", "slime"), ("slime é muito fofo", None),
    ("O slime é fofo", None), ("ursinho gente fina", None), ("slime futuro brilhante", None), ("ursinho!", "ursinho"),
])
def test_explicit_detection(text, expected):
    assert detect_explicit(text) == expected


def test_rank_forwarding_and_defaults():
    top = ["u1", "u2"]
    assert route("oi", "u1", top).character == "slime"
    assert route("oi", "u2", top).character == "celia"
    assert route("oi", "u3", top).character == "celia"
    assert route("oi", "u1", []).character == "celia"          # sem doadores: Célia
    assert route("oi", "u1", ["u9"]).character == "celia"      # só Top1 existe e não é ele


def test_explicit_beats_rank_and_disabled_flags():
    top = ["u1", "u2"]
    d = route("ursinho, oi", "u1", top)       # Top1 fala com o ursinho: explícito vence
    assert (d.character, d.reason) == ("ursinho", "explicit")
    assert route("slime, oi", "u3", top).character == "slime"  # qualquer um pode chamar
    assert route("oi", "u1", top, ranking_enabled=False).character == "celia"
    assert route("oi", "u1", top, enabled={"slime": False}).character == "celia"
    assert route("slime, oi", "u3", top, enabled={"slime": False}).character == "celia"


# ---------------- configurações ----------------
def test_settings_validation_persistence_and_corruption(tmp_path):
    store = SettingsStore(tmp_path / "s.json")
    out = store.update({"volumes": {"slime": 5}, "per_user_interval_s": -3, "bogus": 1,
                        "characters": {"ursinho": {"scale": 99}}})
    assert out["volumes"]["slime"] == 1.0 and out["per_user_interval_s"] == 0.0
    assert out["characters"]["ursinho"]["scale"] == 4.0 and "bogus" not in out
    assert SettingsStore(tmp_path / "s.json").get()["volumes"]["slime"] == 1.0   # persistiu
    (tmp_path / "bad.json").write_text("{nao e json", encoding="utf-8")
    broken = SettingsStore(tmp_path / "bad.json")
    assert broken.get() == validate({}) and broken.load_error == "JSONDecodeError"
    assert DEFAULTS["ranking_characters_enabled"] is True
