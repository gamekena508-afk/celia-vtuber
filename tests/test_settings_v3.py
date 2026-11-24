"""Settings V3, offline browser fixture; no service, camera, audio or LLM."""
from pathlib import Path
import os
import shutil
import subprocess
import tempfile
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from celia.observability_routes import init_observability_routes

ROOT = Path(__file__).resolve().parents[1]

def test_settings_status_is_local_and_uses_live_priority_contract():
    app = FastAPI()
    app.include_router(init_observability_routes())
    result = TestClient(app).get("/debug/observability/settings-status")
    assert result.status_code == 200
    body = result.json()
    assert body["priorities"]["direct_user"] > body["priorities"]["ambient"]
    assert body["priorities"]["discord_direct"] == body["priorities"]["direct_user"]
    assert set(body["speech"]) == {"pending", "generated", "dispatched", "playing", "cancel_requested"}
    assert "queue_state" in body["asr"]
    with TestClient(app, client=("203.0.113.2", 12345)) as remote:
        assert remote.get("/debug/observability/settings-status").status_code == 403

def test_settings_bundle_contains_real_locale_and_public_links():
    bundle=(ROOT/"frontend/assets/main-nu7uwxNJ.js").read_text(encoding="utf-8")
    assert '{label:"Português (Brasil)",value:"pt-BR"}' in bundle
    assert '"pt-BR":{translation:' in bundle
    assert 'o("https://github.com/gamekena508-afk/celia-vtuber")' in bundle
    assert 'o("https://gamekena508-afk.github.io/celia-vtuber/")' in bundle
    assert 'o("https://github.com/Open-LLM-VTuber/Open-LLM-VTuber")' in bundle
    for tab in ("General","Skin","ASR","TTS","Agent","About"):
        assert f'children:"{tab}"' in bundle
    index=(ROOT/"frontend/index.html").read_text(encoding="utf-8")
    assert 'src="./celia-roteiro-live.js' not in index
    assert (ROOT/"frontend/celia-roteiro-live.js").exists()

def test_settings_navigation_and_existing_controls_offline():
    chrome=shutil.which("chromium") or shutil.which("google-chrome") or str(Path(os.environ.get("ProgramFiles",r"C:\Program Files"))/"Google/Chrome/Application/chrome.exe")
    if not Path(chrome).is_file():
        pytest.skip("Chrome not installed")
    with tempfile.TemporaryDirectory() as temp:
        profile=Path(temp)/"browser"
        fixture=Path(temp)/"fixture.html"
        scripts="\n".join('<script src="'+(ROOT/"frontend"/name).as_uri()+'"></script>' for name in ("celia-settings-v3.js","celia-runtime-settings.js","celia-config-extra.js","celia-corpo-config.js"))
        fixture.write_text("""<!doctype html><meta charset="utf-8"><body><div id="result">WAIT</div><div><div role="dialog">
<button role="tab" data-value="agent" aria-selected="true">Agent</button><button role="tab" data-value="tts" aria-selected="true">TTS</button>
<div role="tabpanel" id="content-general"></div>
<div role="tabpanel" id="content-live2d"><div><div class="chakra-field__root"><label><p>Tracking</p><input type="checkbox"></label></div></div></div>
<div role="tabpanel" id="content-asr"><div>Native ASR</div></div><div role="tabpanel" id="content-tts"><div>Native TTS</div></div><div role="tabpanel" id="content-agent"><div>Native Agent</div></div>
</div></div>
<script>
window.requestAnimationFrame=fn=>setTimeout(()=>fn(performance.now()),16);const errors=[];window.addEventListener('error',e=>errors.push(e.message+' @ '+e.lineno));const posts=[];window.fetch=async (url,opts={})=>{if(opts.method&&opts.method!=="GET"&&opts.method!=="HEAD")posts.push(String(url));return {ok:true,json:async()=>({mode:"auto",source:"window",follow:true,voice:{},agent:{},priorities:{direct_user:400},speech:{pending:0}})}};
window.__celiaCorpo="2d"; window.celiaEscolha={trocar:()=>{throw Error("unexpected body switch")}};
window.celiaPerfis={listar:()=>[{id:"chat",nome:"Conversa",travado:false},{id:"roteiro",nome:"Roteiro",travado:false}],ler:async()=>"chat",trocar:async()=>true,editar:()=>{}};
</script>"""+scripts+"""<script>
function run(){
 const q=(id)=>document.getElementById(id), check=(c,m)=>{if(!c)throw Error(m)};
 try{
  check(q("celia-v3-capture")&&q("celia-v3-vad"),"ASR groups");
  check(q("celia-v3-live-voice")&&q("celia-v3-presets"),"TTS groups");
  check(q("celia-v3-call")&&q("celia-v3-live-game")&&q("celia-v3-profiles")&&q("celia-v3-runtime"),"Agent groups");
  check(q("content-live2d").querySelector('[data-celia-opcao="hud_minecraft"]'),"HUD retained");
  check(q("content-live2d").querySelector('[data-celia-opcao="oculos"]'),"glasses retained");
  check(q("content-live2d").querySelector('[data-celia-opcao="slime"]'),"slime retained");
  check(!q("content-live2d").querySelector('[data-celia-opcao="modo_minecraft"]'),"mode moved");
  check(q("content-agent").querySelector('[data-celia-opcao="modo_minecraft"]'),"mode preserved");
  check(q("content-tts").querySelector('[data-celia-opcao="voz_publico"]'),"voice preserved");
  check(!q("celia-v3-profiles").textContent.includes("Roteiro"),"Roteiro absent");
  const page=q("celia-v3-capture"),panel=q("content-asr");
  panel.querySelector('[data-celia-v3-target="celia-v3-capture"]').click();
  check(!page.hidden&&panel.classList.contains("celia-v3-open"),"open subpage");
  check(getComputedStyle(panel.firstElementChild).display==="none","main hidden");
  page.querySelector(".celia-v3-back").click();
  check(page.hidden&&!panel.classList.contains("celia-v3-open"),"back");
  check(document.querySelectorAll("#celia-v3-capture").length===1,"no duplicate");
  check(posts.length===0,"unexpected writes: "+posts.join(","));
  q("result").textContent="SETTINGS_V3_PASS";
 }catch(e){q("result").textContent="SETTINGS_V3_FAIL: "+e.message+" ERRORS: "+errors.join("; ");}
}
setTimeout(run,1000);
</script>""",encoding="utf-8")
        args=[chrome,"--headless=new","--disable-gpu","--no-first-run","--disable-background-networking","--disable-extensions","--disable-sync","--enable-automation","--no-default-browser-check","--allow-file-access-from-files","--user-data-dir="+str(profile),"--virtual-time-budget=2500","--dump-dom",fixture.as_uri()]
        completed=subprocess.run(args,capture_output=True,text=True,encoding="utf-8",errors="replace",timeout=25)
        assert completed.returncode==0, completed.stderr[-2000:]
        assert '<div id="result">SETTINGS_V3_PASS</div>' in completed.stdout, completed.stdout[:2500]
