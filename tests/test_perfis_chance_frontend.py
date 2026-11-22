"""Editor de perfil (celia-perfis.js) em Chromium real: chance de falar visivel, editor enxuto, nada inventado.

Hermetico: os scripts reais do frontend contra rotas finas que usam o ContinuousBehaviorRuntime REAL (esquema e validacao
verdadeiros, em diretorio temporario). Nao toca a Celia viva nem o estado do Pai. Prova ao vivo: var/debug/perfis-chance-20261020/.
"""
import socket
import threading
import time
from pathlib import Path

import pytest
import uvicorn
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse
from starlette.staticfiles import StaticFiles

from celia.core.continuous_behavior import ContinuousBehaviorRuntime

playwright = pytest.importorskip("playwright.sync_api")
ROOT = Path(__file__).resolve().parents[1]
CHROMIUM = "/usr/bin/chromium"
if not Path(CHROMIUM).exists():
    pytest.skip("system chromium not installed", allow_module_level=True)

HOST = """<!doctype html><html><head><meta charset="utf-8"></head><body style="margin:0;background:#111">
<div><button aria-label="Mode Menu" class="x">m</button></div>
<div id="fake-debug" style="position:fixed;left:0;right:0;bottom:0;height:80px;z-index:2147483645;pointer-events:none">debug</div>
<script src="/celia-perfis.js"></script></body></html>"""


class Central:
    def __init__(self, tmp):
        self.rt = ContinuousBehaviorRuntime(tmp / "profile.json")
        self.fail_reads = False
        self.puts = []
        self.metrics = {"participation_opportunities": 10, "spontaneous_accepted": 3}
        self.series_gets, self.series_commands = 0, []
        self.series = {"ok": True, "profile": "conversation_autonomous", "active_world_id": "celia-serie-s01",
                       "worlds": [{"world_id": "celia-serie-s01", "display_name": "Célia e Criador — Série 01", "managed": True, "active": True, "exists": True},
                                  {"world_id": "celia-iron-run6", "managed": False, "active": False}],
                       "status": {"ok": True, "state": "INTRO_READY", "world_id": "celia-serie-s01", "episode": 2,
                                  "tick_state": {"frozen": True}, "daylight_state": {"cycle": False}, "blocked_reason": None, "degraded": [],
                                  "companion": {"state": "DISABLED", "distance": 5.1, "lease": None}}}
        self.series_reply = lambda body: {"ok": True}
        app = FastAPI()
        me = self

        @app.get("/__host__", response_class=HTMLResponse)
        def host():
            return HOST

        @app.get("/bridge/behavior/profile")
        def active():
            return {"profile": me.rt.profile, "metrics": me.metrics}

        @app.post("/bridge/behavior/profile")
        async def switch(request: Request):
            body = await request.json()
            me.rt.select_profile(body.get("profile"))
            return {"profile": me.rt.profile}

        @app.get("/bridge/behavior/profiles/{profile}")
        def read(profile: str):
            if me.fail_reads:
                raise HTTPException(status_code=500, detail="x")
            return me.rt.profile_settings(profile)

        @app.put("/bridge/behavior/profiles/{profile}")
        async def write(profile: str, request: Request):
            body = await request.json()
            me.puts.append((profile, body))
            return me.rt.update_profile_settings(profile, body["settings"], expected_revision=body.get("expected_revision"))

        @app.get("/bridge/minecraft/series")
        def series_status():
            me.series_gets += 1
            return me.series

        @app.post("/bridge/minecraft/series/command")
        async def series_command(request: Request):
            body = await request.json()
            me.series_commands.append(body)
            return me.series_reply(body)

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


@pytest.fixture()
def central(tmp_path):
    c = Central(tmp_path)
    yield c
    c.server.should_exit = True
    c.thread.join(timeout=5)


@pytest.fixture(scope="module")
def browser():
    with playwright.sync_playwright() as p:
        b = p.chromium.launch(executable_path=CHROMIUM, args=["--no-sandbox"])
        yield b
        b.close()


def open_page(browser, central):
    ctx = browser.new_context(viewport={"width": 1280, "height": 800})
    pg = ctx.new_page()
    pg.goto(central.url + "/__host__")
    pg.wait_for_selector("#celia-perfis-botao")
    return ctx, pg


def open_menu(pg):
    pg.click("#celia-perfis-botao")
    pg.wait_for_selector("#celia-perfis-menu")


def open_editor(pg, nome="Co-host"):
    open_menu(pg)
    pg.wait_for_selector("#celia-perfis-menu [data-chance]")
    pg.click(f'button[aria-label="Configurar {nome}"]')
    pg.wait_for_selector("#celia-perfil-chance .meter, #celia-perfil-chance [role=meter]")


def test_menu_shows_each_profiles_real_chance_with_the_server_band(browser, central):
    ctx, pg = open_page(browser, central)
    try:
        open_menu(pg)
        pg.wait_for_selector("#celia-perfis-menu [data-chance]")
        texts = {p: pg.inner_text(f'[data-chance="{p}"]') for p in ("chat", "roteiro", "gravacao", "cohost", "autonomous")}
        for pid, text in texts.items():
            real = central.rt.profile_settings(pid)["settings"]["social"]["participation"]
            band = "Só quando chamada" if real == 0 else "Discreta" if real < 35 else "Moderada" if real < 60 else "Falante"
            assert f"{band} · {real}%" in text, (pid, text)
            assert "(padrão)" in text  # nothing saved yet: honest about it
    finally:
        ctx.close()


def test_menu_invents_no_chance_when_the_server_does_not_answer(browser, central):
    central.fail_reads = True  # negative control for the test above
    ctx, pg = open_page(browser, central)
    try:
        open_menu(pg)
        pg.wait_for_function("document.querySelectorAll('#celia-perfis-menu [data-perfil]').length === 6")
        time.sleep(0.6)
        assert pg.evaluate("document.querySelectorAll('#celia-perfis-menu [data-chance]').length") == 0
    finally:
        ctx.close()


def test_editor_has_one_chance_card_and_a_short_essential_tab(browser, central):
    ctx, pg = open_page(browser, central)
    try:
        open_editor(pg)
        assert pg.evaluate("document.querySelectorAll('#celia-perfil-chance').length") == 1
        # the essential tab: card slider + 3 numeric/range + 4 switches = 8 visible controls; 2 more live inside a closed "Mais opções"
        visible = pg.evaluate("""() => [...document.querySelectorAll('#celia-perfil-v6-editor input')]
            .filter(e => !e.closest('details:not([open])')).length""")
        assert visible == 8, visible
        assert pg.evaluate("document.querySelector('#celia-perfil-v6-editor details').open") is False
        # before the change this tab alone had 13 inputs
        pg.click('#celia-perfil-v6-editor button:text-is("Gatilhos")')
        assert pg.evaluate("document.querySelectorAll('#celia-perfil-v6-editor [data-gatilho]').length") == 14
    finally:
        ctx.close()


def test_moving_the_slider_updates_the_card_live_and_saves_only_that_field(browser, central):
    ctx, pg = open_page(browser, central)
    try:
        before = central.rt.profile_settings("cohost")["settings"]
        open_editor(pg)
        assert "60% · Falante" in pg.inner_text("#celia-perfil-chance")
        pg.eval_on_selector('#celia-perfil-chance input[type=range]', "(e) => { e.value = '20'; e.dispatchEvent(new Event('input', {bubbles: true})); }")
        text = pg.inner_text("#celia-perfil-chance")
        assert "20% · Discreta" in text
        assert pg.get_attribute('#celia-perfil-chance [role=meter]', "aria-valuenow") == "20"
        assert "Padrão do perfil" in text
        pg.click('#celia-perfil-v6-editor button:text-is("Salvar")')
        pg.wait_for_function("document.querySelector('#celia-perfil-chance').innerText.includes('Valendo agora')")
        assert len(central.puts) == 1
        saved = central.rt.profile_settings("cohost")
        assert saved["customized"] is True and saved["settings"]["social"]["participation"] == 20
        saved["settings"]["social"]["participation"] = before["social"]["participation"]
        assert saved["settings"] == before, "nothing but participation may change"
    finally:
        ctx.close()


def test_card_sentences_come_from_the_real_settings_and_zero_means_never_alone(browser, central):
    ctx, pg = open_page(browser, central)
    try:
        open_editor(pg)
        text = pg.inner_text("#celia-perfil-chance")
        s = central.rt.profile_settings("cohost")["settings"]["social"]
        assert f"depois de {s['silence_seconds']} s de silêncio" in text
        assert f"No máximo {s['max_spontaneous_per_10m']} falas" in text
        pg.click('#celia-perfil-v6-editor button:text-is("Essencial")')
        cap = pg.locator('#celia-perfil-v6-editor label:has-text("Falas espontâneas / 10 min") input')
        cap.fill("0")
        assert "Nunca fala por conta própria" in pg.inner_text("#celia-perfil-chance")
    finally:
        ctx.close()


def test_measured_line_uses_only_the_active_profiles_counters(browser, central):
    ctx, pg = open_page(browser, central)
    try:
        central.rt.select_profile("cohost")
        open_editor(pg, "Co-host")
        assert "10 oportunidades registradas, 3 falas por conta própria" in pg.inner_text("#celia-perfil-chance")
        pg.click('#celia-perfil-v6-editor button[aria-label="Fechar configurações"]')
        open_editor(pg, "Roteiro")
        assert "ative este perfil para medir" in pg.inner_text("#celia-perfil-chance")
        assert "10 oportunidades" not in pg.inner_text("#celia-perfil-chance")
    finally:
        ctx.close()


def test_trigger_row_edits_reach_the_saved_settings(browser, central):
    ctx, pg = open_page(browser, central)
    try:
        open_editor(pg)
        pg.click('#celia-perfil-v6-editor button:text-is("Gatilhos")')
        row = pg.locator('[data-gatilho="danger"]')
        row.locator('input[type=checkbox]').uncheck()
        row.locator('input[type=number]').fill("90")
        assert pg.evaluate("document.querySelector('[data-gatilho=danger]').style.opacity") == "0.5"
        pg.click('#celia-perfil-v6-editor button:text-is("Salvar")')
        deadline = time.time() + 5
        while not central.puts and time.time() < deadline:
            time.sleep(0.1)
        trig = central.rt.profile_settings("cohost")["settings"]["triggers"]["danger"]
        assert trig["enabled"] is False and trig["cooldown_seconds"] == 90
    finally:
        ctx.close()


def test_editor_sits_above_the_debug_boxes_so_save_is_never_covered(browser, central):
    ctx, pg = open_page(browser, central)
    try:
        open_editor(pg)
        z = pg.evaluate("Number(getComputedStyle(document.getElementById('celia-perfil-v6-editor')).zIndex)")
        assert z > 2147483645
        box = pg.locator('#celia-perfil-v6-editor button:text-is("Salvar")').bounding_box()
        top = pg.evaluate("([x, y]) => document.elementFromPoint(x, y).closest('button')?.textContent", [box["x"] + 5, box["y"] + 5])
        assert top == "Salvar"
    finally:
        ctx.close()



# ------------------------------------------------------------------ Conversa Autonoma: menu, fields, zones, Serie tab -------------------------------
SERIE = "conversation_autonomous"


def open_series_editor(pg):
    open_editor(pg, "Conversa Autônoma")
    pg.click('#celia-perfil-v6-editor button:text-is("Série")')
    pg.wait_for_selector("#celia-serie-status")
    pg.wait_for_function("document.querySelector('#celia-serie-status').innerText.includes('Gravação')")


def button_state(pg, intent):
    return pg.evaluate("(i) => { const b = document.querySelector(`#celia-perfil-v6-editor [data-intent=\"${i}\"]`); return b ? {disabled: b.disabled, title: b.title} : null; }", intent)


def test_the_new_profile_is_the_first_item_of_the_menu_above_conversa_with_its_real_chance(browser, central):
    ctx, pg = open_page(browser, central)
    try:
        open_menu(pg)
        pg.wait_for_selector("#celia-perfis-menu [data-chance]")
        order = pg.evaluate("[...document.querySelectorAll('#celia-perfis-menu [data-perfil]')].map(b => b.dataset.perfil)")
        assert order[:2] == [SERIE, "chat"] and len(order) == 6
        real = central.rt.profile_settings(SERIE)["settings"]["social"]["participation"]
        assert f"{real}%" in pg.inner_text(f'[data-chance="{SERIE}"]')
    finally:
        ctx.close()


def test_only_the_new_profile_gets_the_serie_tab_and_its_extra_conversation_fields(browser, central):
    ctx, pg = open_page(browser, central)
    try:
        open_editor(pg, "Conversa Autônoma")
        tabs = pg.evaluate("[...document.querySelectorAll('#celia-perfil-v6-editor button')].map(b => b.textContent)")
        assert "Série" in tabs
        text = pg.inner_text("#celia-perfil-v6-editor")
        assert "Intervalo entre falas espontâneas" in text and "Calar quando você começar a falar" in text
        silence = pg.locator('#celia-perfil-v6-editor label:has-text("Reação ao silêncio") input')
        assert silence.get_attribute("min") == "2" and silence.input_value() == "3"
        pg.click('#celia-perfil-v6-editor button[aria-label="Fechar configurações"]')
        open_editor(pg, "Conversa")
        assert "Série" not in pg.evaluate("[...document.querySelectorAll('#celia-perfil-v6-editor button')].map(b => b.textContent)")
        assert "Intervalo entre falas espontâneas" not in pg.inner_text("#celia-perfil-v6-editor")
    finally:
        ctx.close()


def test_the_zones_are_editable_validated_by_the_server_and_saved(browser, central):
    ctx, pg = open_page(browser, central)
    try:
        open_editor(pg, "Conversa Autônoma")
        pg.click('#celia-perfil-v6-editor button:text-is("Minecraft")')
        text = pg.inner_text("#celia-perfil-v6-editor")
        for label in ["Distância ideal", "Zona de trabalho", "Margem excepcional", "Tempo máximo na margem", "Não perseguir além", "Faixa de enquadramento: de", "Faixa de enquadramento: até"]:
            assert label in text, label
        assert "Modo de movimento" not in text, "this profile has no free-roaming mode"
        work = pg.locator('#celia-perfil-v6-editor label:has-text("Zona de trabalho") input')
        exceptional = pg.locator('#celia-perfil-v6-editor label:has-text("Margem excepcional") input')
        assert work.input_value() == "9" and exceptional.input_value() == "12"
        exceptional.fill("8")  # below the work radius: invalid order
        pg.click('#celia-perfil-v6-editor button:text-is("Salvar")')
        pg.wait_for_function("document.querySelector('#celia-perfil-v6-editor').innerText.includes('Falha ao salvar')")
        assert central.puts == [] or central.rt.profile_settings(SERIE)["settings"]["gameplay"]["exceptional_radius"] == 12
        exceptional.fill("14")
        work.fill("10")
        pg.click('#celia-perfil-v6-editor button:text-is("Salvar")')
        pg.wait_for_function("document.querySelector('#celia-perfil-chance').innerText.includes('Valendo agora')")
        saved = central.rt.profile_settings(SERIE)["settings"]["gameplay"]
        assert saved["work_radius"] == 10 and saved["exceptional_radius"] == 14 and saved["band_low"] == 4
    finally:
        ctx.close()


def test_serie_tab_shows_only_real_status_and_enables_buttons_by_state(browser, central):
    ctx, pg = open_page(browser, central)
    try:
        open_series_editor(pg)
        text = pg.inner_text("#celia-serie-status")
        assert "introdução: Célia parada, mundo congelado" in text and "episódio 2" in text and "celia-serie-s01" in text
        assert "sim, confirmado" in text and "parado" in text and "5.1 blocos" in text and "Conversa Autônoma" in text
        mundos = pg.evaluate("document.querySelector('#celia-serie-mundos').textContent")  # the old worlds sit in a closed <details>
        assert "celia-iron-run6" in mundos and "nunca migrados" in mundos
        assert button_state(pg, "series.start_gameplay")["disabled"] is False
        assert button_state(pg, "series.talk_pause")["disabled"] is True and "momento" in button_state(pg, "series.talk_pause")["title"]
        assert button_state(pg, "series.end_episode")["disabled"] is False
        assert button_state(pg, "control.emergency_stop")["disabled"] is False
        assert button_state(pg, "world.create")["disabled"] is True
        central.series["status"]["state"] = "GAMEPLAY"
        pg.wait_for_function("document.querySelector('#celia-serie-status').innerText.includes('jogando')", timeout=6000)
        assert button_state(pg, "series.talk_pause")["disabled"] is False and button_state(pg, "series.start_gameplay")["disabled"] is True
    finally:
        ctx.close()


def test_with_the_jev_off_nothing_is_invented_and_every_button_is_disabled(browser, central):
    central.series = {"ok": False, "status": {"ok": False, "code": "jev_daemon_offline"}, "worlds": None, "active_world_id": None, "profile": SERIE}
    ctx, pg = open_page(browser, central)
    try:
        open_editor(pg, "Conversa Autônoma")
        pg.click('#celia-perfil-v6-editor button:text-is("Série")')
        pg.wait_for_function("document.querySelector('#celia-serie-status').innerText.includes('desligado')")
        assert "Gravação" not in pg.inner_text("#celia-serie-status")
        for intent in ["series.prepare_intro", "series.start_gameplay", "world.checkpoint", "companion.return", "control.emergency_stop", "world.create"]:
            assert button_state(pg, intent)["disabled"] is True, intent
    finally:
        ctx.close()


def test_another_active_profile_disables_the_series_buttons_but_keeps_return_and_stop(browser, central):
    central.series["profile"] = "chat"
    ctx, pg = open_page(browser, central)
    try:
        open_series_editor(pg)
        assert button_state(pg, "series.start_gameplay")["disabled"] is True and "Conversa Autônoma" in button_state(pg, "series.start_gameplay")["title"]
        assert button_state(pg, "companion.return")["disabled"] is False and button_state(pg, "control.emergency_stop")["disabled"] is False
    finally:
        ctx.close()


def test_a_button_sends_one_typed_command_and_shows_the_result_in_plain_language(browser, central):
    ctx, pg = open_page(browser, central)
    try:
        open_series_editor(pg)
        central.series_reply = lambda body: {"ok": True}
        pg.click('[data-intent="series.start_gameplay"]')
        pg.wait_for_function("document.querySelector('#celia-serie-aviso').innerText.includes('Feito')")
        assert central.series_commands == [{"intent": "series.start_gameplay", "params": {}, "confirmed": False}]
        central.series_reply = lambda body: {"ok": False, "code": "no_player_online"}
        pg.click('[data-intent="world.checkpoint"]')
        pg.wait_for_function("document.querySelector('#celia-serie-aviso').innerText.includes('Não foi possível')")
        shown = pg.inner_text("#celia-serie-aviso")
        assert "não há ninguém conectado no jogo" in shown and "no_player_online" not in shown
        central.series_reply = lambda body: {"ok": False, "code": "something_new"}
        pg.click('[data-intent="world.backup"]')
        pg.wait_for_function("document.querySelector('#celia-serie-aviso').innerText.includes('something new')")
    finally:
        ctx.close()


def test_switching_worlds_asks_first_and_only_a_second_click_confirms(browser, central):
    ctx, pg = open_page(browser, central)
    try:
        open_series_editor(pg)
        central.series_reply = lambda body: ({"ok": False, "needs_confirmation": True, "plan": {"needs": "confirm_switch"}} if not body["confirmed"] else {"ok": True})
        pg.click('[data-intent="series.prepare_intro"]')
        pg.wait_for_selector("#celia-serie-confirmacao >> text=reinicia o servidor")
        assert [c["confirmed"] for c in central.series_commands] == [False]
        pg.click('#celia-serie-confirmacao button:text-is("Cancelar")')
        assert "Nada foi feito" in pg.inner_text("#celia-serie-aviso") and len(central.series_commands) == 1
        pg.click('[data-intent="series.prepare_intro"]')
        pg.wait_for_selector("#celia-serie-confirmacao >> text=reinicia o servidor")
        pg.click('#celia-serie-confirmacao button:text-is("Confirmar")')
        pg.wait_for_function("document.querySelector('#celia-serie-aviso').innerText.includes('Feito')")
        assert [c["confirmed"] for c in central.series_commands] == [False, False, True]
    finally:
        ctx.close()


def test_create_world_is_only_offered_when_no_episode_is_running_and_needs_confirmation(browser, central):
    central.series["status"]["state"] = "NO_WORLD"
    ctx, pg = open_page(browser, central)
    try:
        open_series_editor(pg)
        assert button_state(pg, "world.create")["disabled"] is False
        pg.click('#celia-perfil-v6-editor details summary:has-text("Criar um mundo novo")')
        pg.fill('input[aria-label="Nome do mundo"]', "Célia e Criador — Série 02")
        pg.fill('input[aria-label="Número da série"]', "S02")
        central.series_reply = lambda body: {"ok": False, "needs_confirmation": True, "plan": {"world_id": "celia-serie-s02"}}
        pg.click('[data-intent="world.create"]')
        pg.wait_for_selector("#celia-serie-confirmacao >> text=cria um mundo novo")
        assert central.series_commands[-1] == {"intent": "world.create", "params": {"name": "Célia e Criador — Série 02", "series": "s02"}, "confirmed": False}
    finally:
        ctx.close()


def test_the_status_polling_runs_only_while_the_serie_tab_is_open(browser, central):
    ctx, pg = open_page(browser, central)
    try:
        open_series_editor(pg)
        time.sleep(1.9)
        assert central.series_gets >= 2, "polls every 1.5 s while the tab is open"
        pg.click('#celia-perfil-v6-editor button:text-is("Essencial")')
        time.sleep(0.3)
        parked = central.series_gets
        time.sleep(2.2)
        assert central.series_gets == parked, "leaving the tab stops the polling"
        pg.click('#celia-perfil-v6-editor button:text-is("Série")')
        pg.wait_for_function("document.querySelector('#celia-serie-status').innerText.includes('Gravação')")
        pg.click('#celia-perfil-v6-editor button[aria-label="Fechar configurações"]')
        time.sleep(0.3)
        closed = central.series_gets
        time.sleep(2.2)
        assert central.series_gets == closed, "closing the editor stops the polling"
    finally:
        ctx.close()


def test_a_number_outside_the_limit_is_marked_then_clamped_and_never_reaches_the_server_as_422(browser, central):
    ctx, pg = open_page(browser, central)
    try:
        open_editor(pg)
        box = pg.locator("label", has_text="Falas espontâneas / 10 min").locator("input")
        box.fill("999")  # fires input
        assert pg.evaluate("(e) => getComputedStyle(e).borderColor", box.element_handle()) == "rgb(255, 92, 108)"  # marked while typing
        box.blur()  # fires change
        assert box.input_value() == "60"
        pg.click('#celia-perfil-v6-editor button:text-is("Salvar")')
        pg.wait_for_function("document.querySelector('#celia-perfil-chance').innerText.includes('Valendo agora')")
        assert central.rt.profile_settings("cohost")["settings"]["social"]["max_spontaneous_per_10m"] == 60
        # negative control: an in-range value is left alone and not marked
        box.fill("7")
        assert pg.evaluate("(e) => getComputedStyle(e).borderColor", box.element_handle()) != "rgb(255, 92, 108)"
        box.blur()
        assert box.input_value() == "7"
    finally:
        ctx.close()


@pytest.mark.parametrize("nome,perfil,padrao", [("Autônoma", "autonomous", 6), ("Conversa Autônoma", "conversation_autonomous", 4)])
def test_the_continuous_profiles_are_edited_per_minute_and_the_old_caps_are_gone_from_the_screen(browser, central, nome, perfil, padrao):
    ctx, pg = open_page(browser, central)
    try:
        open_editor(pg, nome)
        pg.click('#celia-perfil-v6-editor button:text-is("Social")') if pg.locator('#celia-perfil-v6-editor button:text-is("Social")').count() else None
        box = pg.locator("label", has_text="Falas por conta própria / minuto").locator("input")
        assert box.input_value() == str(padrao)  # the profile default is shown, not blank
        assert pg.locator("label", has_text="Falas espontâneas / 10 min").count() == 0
        assert "falas por minuto" in pg.inner_text("#celia-perfil-chance")
        box.fill("12"); box.blur()
        pg.click('#celia-perfil-v6-editor button:text-is("Salvar")')
        pg.wait_for_function("document.querySelector('#celia-perfil-chance').innerText.includes('Valendo agora')")
        assert central.rt.profile_settings(perfil)["settings"]["social"]["max_spontaneous_per_minute"] == 12
        assert "no máximo 12 falas por minuto" in pg.inner_text("#celia-perfil-chance")
    finally:
        ctx.close()


def test_the_other_profiles_still_show_the_ten_minute_cap_not_the_minute_field(browser, central):
    ctx, pg = open_page(browser, central)
    try:
        open_editor(pg)  # Co-host
        assert pg.locator("label", has_text="Falas espontâneas / 10 min").count() == 1
        assert pg.locator("label", has_text="Falas por conta própria / minuto").count() == 0
    finally:
        ctx.close()
