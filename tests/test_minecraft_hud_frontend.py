"""HUD Minecraft in a real Chromium: the real frontend scripts against a controllable stand-in for the central routes.

Hermetic on purpose (no game, no Célia): it proves the overlay logic, the single visibility state, layering, widths, honest
unavailable states and sprite handling. Live proof against the real world is in var/debug/minecraft-hud-20261019/.
"""
import json
import re
import socket
import threading
import time
from pathlib import Path

import pytest
import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse
from starlette.staticfiles import StaticFiles

playwright = pytest.importorskip("playwright.sync_api")
ROOT = Path(__file__).resolve().parents[1]
CHROMIUM = "/usr/bin/chromium"
if not Path(CHROMIUM).exists():
    pytest.skip("system chromium not installed", allow_module_level=True)

HOST_PAGE = """<!doctype html><html><head><meta charset="utf-8"><style>body{position:fixed;inset:0;margin:0;background:#26402a}
#fake-live2d{position:fixed;inset:0;z-index:5;pointer-events:none}</style></head>
<body><div id="fake-live2d"></div>
<script src="/celia-corpo-config.js"></script><script src="/celia-minecraft-hud.js"></script></body></html>"""


def payload(**over):
    base = {
        "ok": True, "available": True, "at": time.time(), "dimension": "overworld", "world_id": "w",
        "celia": {"name": "Célia", "position": {"x": 138.5, "y": 64.0, "z": 251.3}, "heading": 0.0, "health": 17, "hunger": 16,
                  "xp": {"level": 39, "progress": 0.5}, "held": "diamond_sword"},
        "criador": {"name": "Criador", "online": True, "position": {"x": 142.8, "y": 64.0, "z": 255.7}, "gamemode": "survival"},
        "hotbar": {"slots": [{"name": "diamond_sword", "count": 1}, {"name": "iron_pickaxe", "count": 1}, {"name": "iron_axe", "count": 1},
                             {"name": "iron_shovel", "count": 1}, {"name": "cobblestone", "count": 64}, {"name": "cooked_beef", "count": 12},
                             {"name": "golden_apple", "count": 8}, {"name": "water_bucket", "count": 1}, None], "selected": 0},
        "clock": {"hour": 8, "minute": 39, "day": 124, "phase": "day"},
        "objectives": [{"key": "stone", "icon": "cobblestone", "text": "Pedra", "current": True}, {"key": "furnace", "icon": "furnace", "text": "Fornalha", "current": False}],
        "map": {"terrain": {"size": 4, "step": 4, "x0": 120, "z0": 240, "cls": "2222" "2222" "1111" "0000", "y": [64] * 12 + [None] * 4, "known": 12},
                "waypoints": [{"name": "base", "kind": "base", "x": 130, "y": 64, "z": 245}], "visited_cells": 3},
    }
    base.update(over)
    return base


class Central:
    def __init__(self):
        self.state = {"hud_minecraft": False, "hud": payload(), "status": 200}
        self.hits = {"hud": 0, "options": 0}
        app = FastAPI()
        central = self

        @app.get("/__host__", response_class=HTMLResponse)
        def host():
            return HOST_PAGE

        @app.get("/bridge/corpo-opcoes")
        def get_options():
            central.hits["options"] += 1
            return {"oculos": False, "slime": False, "hud_minecraft": central.state["hud_minecraft"], "boca": 0.0}

        @app.get("/bridge/minecraft/hud")
        def get_hud(request: Request):
            central.hits["hud"] += 1
            if central.state["status"] != 200:
                return JSONResponse({"error": "x"}, status_code=central.state["status"])
            return central.state["hud"]

        app.mount("/avatars", StaticFiles(directory=str(ROOT / "web/avatars")), name="avatars")
        app.mount("/", StaticFiles(directory=str(ROOT / "frontend")), name="frontend")
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            self.port = probe.getsockname()[1]
        self.server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=self.port, log_level="error"))
        self.thread = threading.Thread(target=self.server.run, daemon=True)
        self.thread.start()
        for _ in range(100):
            if self.server.started:
                break
            time.sleep(0.05)
        self.url = f"http://127.0.0.1:{self.port}"


@pytest.fixture(scope="module")
def central():
    c = Central()
    yield c
    c.server.should_exit = True
    c.thread.join(timeout=5)


@pytest.fixture(scope="module")
def browser():
    with playwright.sync_playwright() as p:
        b = p.chromium.launch(executable_path=CHROMIUM, args=["--no-sandbox"])
        yield b
        b.close()


@pytest.fixture()
def page(browser, central):
    central.state.update(hud_minecraft=False, hud=payload(), status=200)
    central.hits.update(hud=0, options=0)
    ctx = browser.new_context(viewport={"width": 1672, "height": 941})
    pg = ctx.new_page()
    pg.goto(central.url + "/__host__?pet=1")  # pet mode: follows the server state every 250 ms, like the real Pet
    pg.wait_for_function("window.celiaMinecraftHud && window.celiaCorpoConfig")
    yield pg
    ctx.close()


def visible(pg):
    return pg.evaluate("() => { const e = document.getElementById('celia-mc-hud'); return !!e && getComputedStyle(e).display !== 'none'; }")


def wait_for(pg, predicate, seconds=6.0):
    end = time.time() + seconds
    while time.time() < end:
        if predicate():
            return True
        time.sleep(0.1)
    return False


def turn_on(central, pg):
    central.state["hud_minecraft"] = True
    assert wait_for(pg, lambda: visible(pg))
    assert wait_for(pg, lambda: pg.evaluate("() => window.celiaMinecraftHud.estado().disponivel === true"))
    pg.wait_for_function("() => document.querySelectorAll('#mc-inv .cx img').length >= 8")


PANELS = ["mc-status", "mc-inv", "mc-obj", "mc-cen", "mc-bus", "mc-clk", "mc-map"]


def test_hidden_by_default_and_costs_nothing_while_off(page, central):
    time.sleep(1.5)
    assert visible(page) is False
    assert central.hits["hud"] == 0, "a hidden HUD must not poll the game facts"
    assert page.evaluate("() => document.querySelectorAll('#celia-mc-hud').length") == 1


def test_one_state_shows_all_seven_areas_together_with_real_values(page, central):
    turn_on(central, page)
    widths = page.evaluate(f"() => {PANELS}.map(id => [id, document.getElementById(id).getBoundingClientRect().width])")
    assert all(w > 0 for _, w in widths), widths
    text = page.evaluate("() => document.getElementById('celia-mc-hud').innerText")
    assert "STATUS - CÉLIA (IA)" in text and "NÍVEL 39" in text and "INVENTÁRIO" in text and "AÇÕES DO BOT" in text and "MAPA TOPOGRÁFICO" not in text
    assert "08:39" in text and "Dia 124" in text and "CÉLIA" in text and "CRIADOR" in text
    assert "X 138.5" in text.replace("\n", " ") or "X\n138.5" in text or "138.5" in text
    assert "255.7" in text and "251.3" in text
    hotbar = page.evaluate("() => [...document.querySelectorAll('#mc-inv .sl')].map(s => [s.dataset.sel, s.querySelector('img') ? s.querySelector('img').getAttribute('src') : null, (s.querySelector('.q') || {}).textContent || null])")
    assert len(hotbar) == 9
    assert [h[0] for h in hotbar] == ["1"] + ["0"] * 8, "slot 1 (index 0) is the selected one"
    assert hotbar[0][1].endswith("/minecraft-items/1.21.10/diamond_sword.png") and hotbar[8][1] is None  # empty slot stays empty
    assert [h[2] for h in hotbar] == [None, None, None, None, "64", "12", "8", None, None]
    loaded = page.evaluate("() => [...document.querySelectorAll('#mc-inv img, #mc-obj img')].every(i => i.complete && i.naturalWidth > 0)")
    assert loaded, "every sprite is a real local PNG"
    assert page.evaluate("() => document.querySelectorAll('#mc-status .hrt').length") == 10
    assert page.evaluate("() => document.querySelectorAll('#mc-status .fm').length") == 10
    assert not re.search("[\U0001F300-\U0001FAFF☀-➿]", text), "no emoji anywhere on the HUD"


def test_turning_the_single_state_off_hides_every_area_at_once_and_stops_polling(page, central):
    turn_on(central, page)
    central.state["hud_minecraft"] = False
    assert wait_for(page, lambda: not visible(page))
    widths = page.evaluate(f"() => {PANELS}.map(id => document.getElementById(id).getBoundingClientRect().width)")
    assert widths == [0] * 7, "no panel survives on its own"
    before = central.hits["hud"]
    time.sleep(2.2)
    assert central.hits["hud"] == before, "off means no more fact polling"
    central.state["hud_minecraft"] = True
    assert wait_for(page, lambda: visible(page))
    assert page.evaluate("() => document.querySelectorAll('#celia-mc-hud').length") == 1, "toggling never duplicates the overlay"


@pytest.mark.parametrize("size", [(1672, 941), (1920, 1080), (1280, 720), (800, 900), (500, 400)])
def test_status_and_inventory_keep_the_same_outer_width_at_any_window_size(page, central, size):
    page.set_viewport_size({"width": size[0], "height": size[1]})
    turn_on(central, page)
    page.wait_for_timeout(300)
    status, inventory = page.evaluate("() => ['mc-status', 'mc-inv'].map(id => document.getElementById(id).getBoundingClientRect().width)")
    assert abs(status - inventory) < 0.01 and status > 0
    inside = page.evaluate("() => { const r = document.getElementById('mc-inv').getBoundingClientRect(); return [r.left, r.right, innerWidth]; }")
    assert 0 <= inside[0] and inside[1] <= inside[2]


def test_overlay_is_click_through_and_sits_below_the_live2d_layer(page, central):
    turn_on(central, page)
    info = page.evaluate("""() => {
      const hud = document.getElementById('celia-mc-hud'), live = document.getElementById('fake-live2d');
      const r = document.getElementById('mc-status').getBoundingClientRect();
      const under = document.elementFromPoint(r.left + r.width / 2, r.top + r.height / 2);
      return {hudZ: Number(getComputedStyle(hud).zIndex), liveZ: Number(getComputedStyle(live).zIndex), hudPE: getComputedStyle(hud).pointerEvents,
              childPE: [...hud.querySelectorAll('*')].every(e => getComputedStyle(e).pointerEvents === 'none'), insideHud: !!(under && under.closest('#celia-mc-hud')), hit: under ? under.tagName : null};
    }""")
    assert info["hudZ"] < info["liveZ"], info
    assert info["hudPE"] == "none" and info["childPE"], "the overlay must never block clicks meant for Minecraft"
    assert info["hit"] == "BODY" and info["insideHud"] is False, info  # the click lands on whatever is under the HUD (here, the page body)


def test_without_a_fresh_game_snapshot_every_value_is_unavailable_not_invented(page, central):
    central.state["hud"] = {"ok": True, "available": False, "reason": "no_fresh_state", "at": time.time(), "objectives": []}
    central.state["hud_minecraft"] = True
    assert wait_for(page, lambda: visible(page))
    assert wait_for(page, lambda: page.evaluate("() => window.celiaMinecraftHud.estado().motivo === 'no_fresh_state'"))
    text = page.evaluate("() => document.getElementById('celia-mc-hud').innerText")
    assert "NÍVEL —" in text and "--:--" in text and "Dia —" in text and "sem sinal do corpo" in text and "indisponível" in text
    assert "Sem ação do corpo" in text
    assert page.evaluate("() => document.querySelectorAll('#mc-inv .cx img, #mc-inv .cx .q').length") == 0, "no item, no quantity"
    assert page.evaluate("() => getComputedStyle(document.querySelector('#mc-map .semsinal')).display") == "flex"
    assert page.evaluate("() => document.getElementById('celia-mc-hud').dataset.disp") == "0"
    # the heart row is all-empty, not a default of full life
    fills = page.evaluate("() => [...document.querySelectorAll('#mc-status .hrt rect')].map(r => r.getAttribute('fill')).filter(f => f === '#e63a36').length")
    assert fills == 0


def test_a_stale_snapshot_is_treated_as_unavailable(page, central):
    central.state["hud"] = payload(at=time.time() - 60)
    central.state["hud_minecraft"] = True
    assert wait_for(page, lambda: visible(page))
    assert wait_for(page, lambda: page.evaluate("() => window.celiaMinecraftHud.estado().motivo === 'dado_velho'"))
    assert "NÍVEL —" in page.evaluate("() => document.getElementById('celia-mc-hud').innerText")


def test_central_failure_shows_no_signal_after_two_misses_and_recovers(page, central):
    central.state["status"] = 500
    central.state["hud_minecraft"] = True
    assert wait_for(page, lambda: page.evaluate("() => window.celiaMinecraftHud.estado().motivo === 'central_sem_resposta'"), 8)
    central.state["status"] = 200
    central.state["hud"] = payload(at=time.time())
    assert wait_for(page, lambda: page.evaluate("() => window.celiaMinecraftHud.estado().disponivel === true"), 8)


def test_unknown_item_gets_a_neutral_slot_and_is_recorded_never_an_invented_icon(page, central):
    hud = payload()
    hud["hotbar"]["slots"][8] = {"name": "totally_unknown_item", "count": 3}
    central.state["hud"] = hud
    turn_on(central, page)
    assert wait_for(page, lambda: "totally_unknown_item" in page.evaluate("() => window.celiaMinecraftHud.estado().faltando"))
    slot = page.evaluate("() => { const s = document.querySelectorAll('#mc-inv .sl')[8]; return [!!s.querySelector('img'), !!s.querySelector('.nf'), (s.querySelector('.q') || {}).textContent]; }")
    assert slot == [False, True, "3"]


def test_criador_is_never_placed_without_a_real_position(page, central):
    for criador, expect_text in [
        ({"name": "Criador", "online": False, "position": None, "gamemode": None}, "fora do jogo"),
        ({"name": "Criador", "online": True, "position": None, "gamemode": None}, "online · posição indisponível"),
    ]:
        central.state["hud"] = payload(criador=criador)
        central.state["hud_minecraft"] = True
        assert wait_for(page, lambda: expect_text in page.evaluate("() => document.getElementById('celia-mc-hud').innerText"), 8), expect_text
        text = page.evaluate("() => document.querySelector('#mc-cen [data-k=criador]').innerText")
        assert not re.search(r"\d+\.\d", text), text


def test_celia_heading_drives_the_compass_and_unknown_heading_says_so(page, central):
    turn_on(central, page)
    fingerprint = lambda: page.evaluate("() => document.querySelector('#mc-bus canvas').toDataURL()")
    north = fingerprint()
    hud = payload(); hud["celia"]["heading"] = 90.0
    central.state["hud"] = hud
    assert wait_for(page, lambda: fingerprint() != north), "a different heading must redraw the compass"
    hud = payload(); hud["celia"]["heading"] = None
    central.state["hud"] = hud
    assert wait_for(page, lambda: fingerprint() not in (north,))


def test_corpo_config_migrates_old_storage_and_has_exactly_one_hud_key(browser, central):
    central.state.update(hud_minecraft=False)  # the server is the truth for the HUD flag: start from its default
    ctx = browser.new_context(viewport={"width": 800, "height": 600})
    ctx.add_init_script('localStorage.setItem("celia-corpo-config:v1", JSON.stringify({oculos: true, slime: true}))')  # saved before the HUD existed
    pg = ctx.new_page()
    pg.goto(central.url + "/__host__")
    pg.wait_for_function("window.celiaCorpoConfig")
    estado = pg.evaluate("() => window.celiaCorpoConfig.estado()")
    assert estado["oculos"] is True and estado["slime"] is True and estado["hud_minecraft"] is False
    assert pg.evaluate("() => window.celiaCorpoConfig.definir('inventario', true)") is False
    assert pg.evaluate("() => window.celiaCorpoConfig.definir('mapa', true)") is False
    assert pg.evaluate("() => window.celiaCorpoConfig.definir('bussola', true)") is False
    saved = pg.evaluate("() => { window.celiaCorpoConfig.definir('hud_minecraft', true); return JSON.parse(localStorage.getItem('celia-corpo-config:v1')); }")
    assert set(saved) == {"oculos", "slime", "hud_minecraft", "voz_publico", "modo_minecraft", "enquete_mobile"} and saved["hud_minecraft"] is True
    ctx.close()


def test_sources_have_no_part_level_hud_switches():
    corpo = (ROOT / "frontend/celia-corpo-config.js").read_text(encoding="utf-8")
    assert re.search(r"const PADRAO = \{ oculos: false, slime: false, hud_minecraft: false, voz_publico: false, modo_minecraft: true, enquete_mobile: false \};", corpo)
    assert len(re.findall(r'clonarLinha\(modelo, "HUD Minecraft", "hud_minecraft"\)', corpo)) == 1
    for banned in ("inventario_enabled", "mapa_enabled", "bussola_enabled"):
        assert banned not in corpo
    hud = (ROOT / "frontend/celia-minecraft-hud.js").read_text(encoding="utf-8")
    assert "localStorage" not in hud, "the overlay owns no visibility state of its own"
    palco = (ROOT / "frontend/celia-palco.js").read_text(encoding="utf-8")
    assert '"celia-mc-hud"' in palco, "the stage cleanup must let the HUD live next to #root"


def test_hud_exists_only_in_pet_mode_never_duplicated_in_the_normal_interface(browser, central):
    # positive control: the same server state shows the HUD in Pet mode; negative: the normal interface never draws it
    central.state.update(hud_minecraft=True, hud=payload(), status=200)
    ctx = browser.new_context(viewport={"width": 1672, "height": 941})
    try:
        pet = ctx.new_page()
        pet.goto(central.url + "/__host__?pet=1")
        pet.wait_for_function("window.celiaMinecraftHud && window.celiaCorpoConfig")
        assert wait_for(pet, lambda: visible(pet)), "control: Pet mode must draw the HUD"
        normal = ctx.new_page()
        normal.goto(central.url + "/__host__")
        normal.wait_for_function("window.celiaMinecraftHud && window.celiaCorpoConfig")
        normal.evaluate("() => window.celiaCorpoConfig.estado().hud_minecraft")
        time.sleep(2.5)  # past the 1 s follower and several 250 ms overlay ticks
        assert normal.evaluate("() => window.celiaCorpoConfig.estado().hud_minecraft") is True, "the switch state is still shared"
        assert not visible(normal), "the normal interface must not draw a second HUD"
        assert normal.evaluate("() => document.querySelectorAll('#celia-mc-hud').length") == 0
    finally:
        ctx.close()


# ---------- tempo real, hora do dia, tamanho e retrato (ajuste 2026-10-20) ----------
MAP_MEAN = """() => { const c = document.querySelector('#mc-map canvas'); const g = c.getContext('2d');
  const w = c.width, h = c.height; const px = g.getImageData(0, 0, w, Math.floor(h * 0.4)).data; let r = 0, gg = 0, b = 0, n = 0;
  for (let i = 0; i < px.length; i += 16) { r += px[i]; gg += px[i + 1]; b += px[i + 2]; n += 1; } return [r / n, gg / n, b / n]; }"""


def with_clock(hour, minute):
    d = payload()
    d["clock"] = {"hour": hour, "minute": minute, "day": 124, "phase": "night" if hour >= 19 or hour < 5 else "day"}
    d["map"]["terrain"] = {"size": 8, "step": 4, "x0": 120, "z0": 240, "cls": "2" * 56 + "2" * 8, "y": [64] * 64, "known": 64}
    return d


def map_mean_at(central, page, hour, minute):
    central.state["hud"] = with_clock(hour, minute)
    assert wait_for(page, lambda: page.evaluate("() => window.celiaMinecraftHud.estado().faseDia") == {(8, 50): "dia", (18, 30): "entardecer", (23, 30): "noite", (5, 20): "amanhecer"}[(hour, minute)])
    time.sleep(0.5)
    return page.evaluate(MAP_MEAN)


def test_map_changes_with_the_time_of_day_day_sunset_night_dawn(page, central):
    turn_on(central, page)
    day = map_mean_at(central, page, 8, 50)
    sunset = map_mean_at(central, page, 18, 30)
    night = map_mean_at(central, page, 23, 30)
    dawn = map_mean_at(central, page, 5, 20)
    lum = lambda c: 0.3 * c[0] + 0.59 * c[1] + 0.11 * c[2]
    assert lum(night) < lum(day) * 0.55, (day, night)  # negative control for the tint: same terrain, much darker at night
    assert night[2] > night[0], "night is bluish"
    assert sunset[0] / max(1, sunset[2]) > day[0] / max(1, day[2]) * 1.3, (day, sunset)  # warm at sunset
    assert dawn[0] / max(1, dawn[2]) > day[0] / max(1, day[2]) * 1.15, (day, dawn)
    assert page.evaluate("() => document.getElementById('mc-map').dataset.fase") == "amanhecer"
    # markers must stay readable at night: the tint is applied to the terrain only
    assert page.evaluate("() => window.celiaMinecraftHud.estado().faltando.length") == 0


def test_clock_icon_follows_the_phase_and_unknown_time_stays_dim(page, central):
    turn_on(central, page)
    icons = {}
    for h, m in [(8, 50), (18, 30), (23, 30)]:
        central.state["hud"] = with_clock(h, m)
        assert wait_for(page, lambda: page.evaluate("() => document.getElementById('mc-clk').querySelector('svg') !== null"))
        time.sleep(0.5)
        icons[(h, m)] = page.evaluate("() => document.querySelector('#mc-clk svg').outerHTML")
    assert len(set(icons.values())) == 3, "day, dusk and night must look different"
    d = payload(); d["clock"] = None
    central.state["hud"] = d
    assert wait_for(page, lambda: page.evaluate("() => window.celiaMinecraftHud.estado().faseDia") is None)
    assert page.evaluate("() => getComputedStyle(document.querySelector('#mc-clk svg')).opacity") == "0.22"


def test_hud_polls_at_eight_hertz_and_follows_a_move_in_well_under_a_second(page, central):
    turn_on(central, page)
    hits0, t0 = central.hits["hud"], time.time()
    time.sleep(1.5)
    rate = (central.hits["hud"] - hits0) / (time.time() - t0)
    assert rate >= 6.0, f"real time needs ~8 polls per second, got {rate:.1f}"
    read = lambda: page.evaluate("() => document.querySelector('#mc-cen [data-k=celia] .co').innerText.replace(/\\s+/g, ' ')")
    assert "138.5" in read()
    moved = payload(); moved["celia"]["position"] = {"x": 140.5, "y": 64.0, "z": 251.3}
    central.state["hud"] = moved
    start = time.time()
    seen = []
    while time.time() - start < 2.0:
        seen.append(read())
        if "140.5" in seen[-1]:
            break
        time.sleep(0.02)
    elapsed = time.time() - start
    assert "140.5" in seen[-1] and elapsed < 0.9, (elapsed, seen[-1])
    assert len(set(seen)) >= 3, "the number must glide through intermediate values, not jump"


def test_a_big_jump_snaps_instead_of_sliding_across_the_world(page, central):
    turn_on(central, page)
    far = payload(); far["celia"]["position"] = {"x": 538.5, "y": 70.0, "z": 251.3}
    central.state["hud"] = far
    read = lambda: page.evaluate("() => document.querySelector('#mc-cen [data-k=celia] .co').innerText.replace(/\\s+/g, ' ')")
    assert wait_for(page, lambda: "538.5" in read(), 1.2), "teleport must show the new place at once"


def test_unknown_position_is_shown_as_unknown_immediately_never_smoothed_into_a_value(page, central):
    turn_on(central, page)
    gone = payload(); gone["celia"]["position"] = None
    central.state["hud"] = gone
    assert wait_for(page, lambda: "sem sinal" in page.evaluate("() => document.querySelector('#mc-cen [data-k=celia]').innerText").lower(), 1.5)


def test_hud_is_smaller_than_the_reference_and_celia_criador_portraits_share_one_mold(page, central):
    turn_on(central, page)
    assert page.evaluate("() => window.celiaMinecraftHud.estado().escala") <= 0.8
    boxes = page.evaluate("""() => [...document.querySelectorAll('#mc-cen .jg')].map(m => {
        const q = m.querySelector('.avq'), i = q.querySelector('img'), r = q.getBoundingClientRect(), f = i.getBoundingClientRect();
        return { w: r.width, h: r.height, fit: getComputedStyle(i).objectFit, pos: getComputedStyle(i).objectPosition,
                 dx: Math.abs((f.left + f.width / 2) - (r.left + r.width / 2)), dy: Math.abs((f.top + f.height / 2) - (r.top + r.height / 2)), src: i.getAttribute('src') }; })""")
    assert len(boxes) == 2
    assert boxes[0]["w"] == boxes[1]["w"] and boxes[0]["h"] == boxes[1]["h"], boxes
    for b in boxes:
        assert b["fit"] == "contain" and b["dx"] < 0.6 and b["dy"] < 0.6, b
    assert boxes[1]["src"].endswith("criador-slime-retrato.png")


def test_criador_portrait_asset_is_square_transparent_and_centered_on_its_own_pixels():
    from PIL import Image

    im = Image.open(ROOT / "web/avatars/criador-slime-retrato.png").convert("RGBA")
    assert im.size[0] == im.size[1], im.size
    box = im.getchannel("A").point(lambda a: 255 if a > 8 else 0).getbbox()
    cx, cy = (box[0] + box[2]) / 2 / im.size[0], (box[1] + box[3]) / 2 / im.size[1]
    assert abs(cx - 0.5) < 0.02 and abs(cy - 0.5) < 0.02, (cx, cy)
    assert im.getpixel((2, 2))[3] == 0, "corners transparent (the old file had an opaque black background)"
    # negative control: the old asset was not centered inside a square frame
    old = Image.open(ROOT / "web/avatars/criador-slime.png")
    assert old.size[0] != old.size[1]


def test_the_objective_panel_shows_the_bot_action_debug_and_the_map_has_no_topographic_title(page, central):
    central.state["hud"] = payload(debug_actions=[{"type": "navigate", "brief": "12,-3", "status": "running", "error": "", "ms": None},
                                                    {"type": "mine", "brief": "oak_log", "status": "failed", "error": "no_path", "ms": 1500}])
    central.state["hud_minecraft"] = True
    assert wait_for(page, lambda: visible(page))
    assert wait_for(page, lambda: "navigate 12,-3" in page.evaluate("() => document.getElementById('celia-mc-hud').innerText"))
    text = page.evaluate("() => document.getElementById('celia-mc-hud').innerText")
    assert "AÇÕES DO BOT" in text and "mine oak_log" in text and "falhou 1.5s · no_path" in text and "…" in text
    assert "TOPOGR" not in text.upper() and "Pedra" not in text, "debug replaces the stage list while there are actions"
    assert page.evaluate("() => document.querySelector('#mc-obj .ln[data-cur=\"1\"]') !== null || document.querySelectorAll('.ln[data-cur=\"1\"]').length > 0")
