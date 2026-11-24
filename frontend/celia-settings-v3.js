/* Settings V3: local navigation, existing handlers stay authoritative. */
(function () {
  "use strict";
  if (window.celiaSettingsV3) return;
  const cls = "celia-v3-page";
  function el(tag, text) {
    const n = document.createElement(tag);
    if (text != null) n.textContent = text;
    return n;
  }
  function close(panel) {
    const active = panel.querySelector(":scope > .celia-v3-active");
    panel.classList.remove("celia-v3-open");
    if (active) {
      active.classList.remove("celia-v3-active");
      active.hidden = true;
      const trigger = panel.querySelector('[data-celia-v3-target="' + active.id + '"]');
      if (trigger) trigger.focus();
    }
  }
  function section(panel, id, label) {
    if (!panel) return null;
    const existing = panel.querySelector("#" + id);
    if (existing) return existing.querySelector(".celia-v3-body");
    const trigger = el("button", label + "  ›");
    trigger.type = "button";
    trigger.className = "celia-v3-link";
    trigger.dataset.celiaV3Target = id;
    const page = el("section");
    page.id = id;
    page.className = cls;
    page.hidden = true;
    const back = el("button", "← " + label);
    back.type = "button";
    back.className = "celia-v3-back";
    back.setAttribute("aria-label", "Voltar / Back");
    const body = el("div");
    body.className = "celia-v3-body";
    page.append(back, body);
    trigger.addEventListener("click", () => {
      close(panel);
      panel.classList.add("celia-v3-open");
      page.hidden = false;
      page.classList.add("celia-v3-active");
      back.focus();
      page.dispatchEvent(new Event("celia-v3-open"));
    });
    back.addEventListener("click", () => close(panel));
    panel.append(trigger, page);
    return body;
  }
  const css = el("style");
  css.textContent = ".celia-v3-open>:not(.celia-v3-active),.celia-v3-page[hidden]{display:none!important}.celia-v3-link,.celia-v3-back{display:block;width:100%;text-align:left;border:1px solid #6580ac;border-radius:8px;padding:9px 12px;margin:6px 0;color:inherit;background:transparent;cursor:pointer}.celia-v3-body{display:grid;gap:8px;padding:4px 0}.celia-v3-row{display:flex;justify-content:space-between;gap:8px;overflow-wrap:anywhere;padding:6px 0;border-bottom:1px solid #ffffff20}.celia-v3-row span:last-child{text-align:right}.celia-v3-native[hidden]{display:none!important}.celia-v3-native-stack:has(>.celia-v3-native-open)>:not(.celia-v3-native-open){display:none!important}";
  document.head.append(css);
  function panel(dialog, key) { return dialog.querySelector('[role="tabpanel"][id$="content-' + key + '"]'); }
  function facts(body, endpoint, fields, note) {
    const btn = el("button", "Atualizar / Refresh");
    btn.type = "button"; btn.className = "celia-v3-link";
    const status = el("p", "Abra a seção para consultar o serviço.");
    status.setAttribute("role", "status");
    const rows = el("div");
    if (note) body.append(el("p", note));
    body.append(btn, status, rows);
    let request = 0;
    async function read() {
      const current = ++request;
      status.textContent = "Consultando…";
      rows.replaceChildren();
      try {
        const r = await fetch(endpoint, {cache:"no-store",credentials:"same-origin"});
        if (!r.ok) throw Error("HTTP " + r.status);
        const data = await r.json();
        if (current !== request) return;
        for (const [label, path] of fields) {
          const value = path.reduce((v,k)=>v == null ? undefined : v[k],data);
          const row = el("div"); row.className = "celia-v3-row";
          row.append(el("span",label),el("span",value == null ? "Não informado" : Array.isArray(value) ? value.join(", ") : typeof value === "object" ? "Consultar diagnóstico" : String(value)));
          rows.append(row);
        }
        status.textContent = "Dados consultados (somente leitura).";
      } catch(e) { if(current === request) status.textContent = "Indisponível: " + e.message; }
    }
    btn.addEventListener("click", read);
    body.parentElement.addEventListener("celia-v3-open",read);
  }
  function addFacts(p,id,title,endpoint,fields,note) {
    if (!p || p.querySelector("#"+id)) return;
    facts(section(p,id,title),endpoint,fields,note);
  }
  function setup(dialog) {
    const skin = panel(dialog,"live2d"), agent = panel(dialog,"agent"), asr = panel(dialog,"asr"), tts=panel(dialog,"tts");
    if (skin && !skin.querySelector("#celia-v3-models")) {
      const body=section(skin,"celia-v3-models","Modelos e skins");
      body.append(el("p","Corpo atual: "+(window.__celiaCorpo||"não informado")));
      for (const [value,label] of [["2d","2D — Live2D"],["3d","3D"]]) {
        const b=el("button",label); b.type="button"; b.className="celia-v3-link";
        b.disabled=window.__celiaCorpo===value || new URLSearchParams(location.search).has("pet") || !window.celiaEscolha?.trocar;
        b.addEventListener("click",()=>window.celiaEscolha.trocar(value)); body.append(b);
      }
      body.append(el("p","A troca utiliza o seletor existente e recarrega a interface. O Pet acompanha o corpo publicado."));
    }
    if (skin && !skin.querySelector("#celia-v3-pet")) {
      const body=section(skin,"celia-v3-pet","Pet e apresentação");
      const status=el("p","Estado não consultado.");body.append(status);
      const read=()=>{const s=window.celiaBrowserPet?.state?.();status.textContent=s?"Modo: "+s.mode+" | Pet: "+(s.petAlive===true?"ativo":s.petAlive===false?"inativo":"não confirmado"):"Controles disponíveis no menu de modos.";};
      body.parentElement.addEventListener("celia-v3-open",read);
      body.append(el("p","Use o menu de modos existente para alternar Interface, Pet, Mobile e Live. Zoom e rastreamento continuam em Skin; o Pet não captura microfone."));
    }
    if (agent && !agent.querySelector("#celia-v3-profiles") && window.celiaPerfis) {
      const body=section(agent,"celia-v3-profiles","Perfis e autonomia");
      const status=el("p","Perfil ativo: consultando"); status.setAttribute("role","status"); body.append(status);
      const buttons=[];
      const update=async()=>{const active=await window.celiaPerfis.ler();status.textContent="Perfil ativo: "+(window.celiaPerfis.listar().find(p=>p.id===active)?.nome||active||"indisponível");for(const [b,id] of buttons)b.setAttribute("aria-pressed",String(id===active));};
      for(const p of window.celiaPerfis.listar()){
        if(p.id==="roteiro") continue;
        const row=el("div"); row.className="celia-v3-row";
        const b=el("button",p.nome); b.type="button"; b.className="celia-v3-link"; b.disabled=p.travado;
        b.addEventListener("click",async()=>{b.disabled=true;try{if(!await window.celiaPerfis.trocar(p.id))status.textContent="Não foi possível alterar o perfil.";else await update();}finally{b.disabled=p.travado;}});
        const edit=el("button","Configurar ›");edit.type="button";edit.className="celia-v3-link";edit.disabled=!window.celiaPerfis.editar;
        edit.addEventListener("click",()=>window.celiaPerfis.editar(p.id, dialog));
        buttons.push([b,p.id]);row.append(b,edit);body.append(row);
      }
      body.parentElement.addEventListener("celia-v3-open",update);
    }
    const rt="/debug/observability/runtime", topology="/debug/observability/topology";
    function preferences(p,id,title,isJev) {
      if(!p||p.querySelector("#"+id))return;
      const body=section(p,id,title), status=el("p","Abra para consultar.");
      const control=el("select"), volume=el("input"), save=el("button","Salvar");
      save.type="button";save.className="celia-v3-link";save.disabled=true;
      volume.type="range";volume.min="0";volume.max="100";volume.step="1";
      volume.setAttribute("aria-label","Volume da Célia");
      control.setAttribute("aria-label",isJev?"Modelo preferido do Jev":"Destino das respostas em call");
      const amount=el("span",""), speed=el("input"), rate=el("span","");
      speed.type="range";speed.min="0.8";speed.max="1.25";speed.step="0.05";
      speed.setAttribute("aria-label","Velocidade de reprodução");
      speed.oninput=()=>rate.textContent=Number(speed.value).toFixed(2)+"×";
      const preset=el("select");preset.setAttribute("aria-label","Preset de reprodução");
      for(const [v,t] of [["","Personalizado"],["0.85","Calmo"],["1","Original"],["1.15","Dinâmico"]]){const o=el("option",t);o.value=v;preset.append(o);}
      preset.onchange=()=>{if(preset.value){speed.value=preset.value;speed.oninput();}};
      volume.oninput=()=>amount.textContent=volume.value+"%";
      body.append(status,control);
      if(!isJev){body.append(el("p","Volume da voz da Célia"),volume,amount,el("p","Preset de reprodução"),preset,el("p","Velocidade de reprodução (não altera o motor)"),speed,rate);}
      body.append(save);
      const endpoint="/debug/observability/settings-preferences";
      function render(d){
        control.replaceChildren();
        const options=isJev?[["","Ordem padrão da fila"],...(d.jev_models||[]).map(m=>[m,m])]:[["auto","Automático (Discord em call)"],["discord","Discord"],["window","Janela / superfície visual"]];
        for(const [v,t] of options){const o=el("option",t);o.value=v;control.append(o);}
        control.value=isJev?d.preferences.jev_model:d.preferences.discord_reply_output;
        if(control.selectedIndex<0)control.selectedIndex=0;
        volume.value=d.preferences.volume;amount.textContent=volume.value+"%";speed.value=d.preferences.playback_speed??1;speed.oninput();preset.value=["0.85","1","1.15"].includes(String(Number(speed.value)))?String(Number(speed.value)):"";
        save.disabled=isJev&&!(d.jev_models||[]).length;
        status.textContent=save.disabled?"Fila Jev não carregada.":"Preferências carregadas. Salvar aplica às próximas falas/turnos.";
      }
      async function read(){save.disabled=true;try{const r=await fetch(endpoint,{cache:"no-store"});if(!r.ok)throw Error("HTTP "+r.status);render(await r.json());}catch(e){status.textContent="Indisponível: "+e.message;}}
      body.parentElement.addEventListener("celia-v3-open",read);
      save.onclick=async()=>{save.disabled=true;try{const patch=isJev?{jev_model:control.value}:{volume:Number(volume.value),playback_speed:Number(speed.value),discord_reply_output:control.value};const r=await fetch(endpoint,{method:"PUT",headers:{"Content-Type":"application/json"},body:JSON.stringify(patch)});if(!r.ok)throw Error("HTTP "+r.status);render(await r.json());status.textContent="Preferências salvas.";}catch(e){status.textContent="Não foi possível salvar: "+e.message;save.disabled=false;}};
      body.append(el("p",isJev?"O modelo escolhido é tentado primeiro; cotas e fallback continuam ativos.":"Destino aplicado a respostas iniciadas em call. Conversa local mantém a superfície visual existente. Não altera volume do Windows."));
    }
    preferences(agent,"celia-v3-jev-choice","Seleção de modelo Jev",true);
    preferences(tts,"celia-v3-output-choice","Volume e destino de call",false);
    addFacts(agent,"celia-v3-cognitive","Estado do cérebro pensante","/bridge/debug/state",[["Em execução",["cognitive","running"]],["Recurso cérebro",["resources","brain"]],["Fila de conversa",["queue","pending"]]]);
    addFacts(tts,"celia-v3-playback","Estado da fala",rt,[["Estado de reprodução",["continuous_behavior","active_speech","playback_state"]],["ID da fala",["continuous_behavior","active_speech","speech_id"]]],"O roteamento de áudio permanece inalterado.");
    addFacts(asr,"celia-v3-asr-queue","Fila ASR", "/debug/observability/settings-status", [["Motor",["asr","engine"]],["Modelo configurado",["asr","configured_model"]],["Modelo carregado",["asr","model"]],["Fila de transcrição",["asr","queue_state"]],["Aguardando",["asr","waiting"]],["Transcrevendo",["asr","running"]],["Maior espera (ms)",["asr","oldest_wait_ms"]]],"Métricas da entrada assíncrona padrão do núcleo. Serviços externos e motores que implementam entrada própria têm filas independentes.");
    addFacts(tts,"celia-v3-tts-queue","Fila TTS", "/debug/observability/settings-status", [["Tarefas de síntese em curso/espera",["tts_queue","unfinished_tasks"]],["Payloads na fila",["tts_queue","queued_payloads"]],["Falas pendentes",["speech","pending"]],["Geradas",["speech","generated"]],["Entregues",["speech","dispatched"]],["Em reprodução",["speech","playing"]]],"Tarefas do gerenciador TTS e até 32 intenções do coordenador. A síntese direta de integrações tem fluxo próprio.");
    if(agent&&!agent.querySelector("#celia-v3-priorities")){
      const body=section(agent,"celia-v3-priorities","Prioridades gerais");
      facts(body,"/debug/observability/settings-status",[["Criador",["priorities","direct_user"]],["Discord direto",["priorities","discord_direct"]],["Evento crítico de jogo",["priorities","critical_game_event"]],["Sistema",["priorities","system"]],["Bridge",["priorities","bridge"]],["Jogo",["priorities","game_commentary"]],["Live",["priorities","live"]],["Espontâneo",["priorities","ambient"]]],"Valores reais do coordenador. Somente leitura.");
    }
    if(tts&&!tts.querySelector("#celia-v3-preset-list")){
      const body=section(tts,"celia-v3-preset-list","Catálogo de presets");
      const status=el("p","Abra para consultar."); body.append(status);
      const list=el("div");body.append(list);
      body.parentElement.addEventListener("celia-v3-open",async()=>{
        status.textContent="Consultando…";list.replaceChildren();
        try{const r=await fetch("/tts/presets",{cache:"no-store"});if(!r.ok)throw Error("HTTP "+r.status);const d=await r.json();
          status.textContent="Catálogo legado — preset configurado: "+(d.active||"não informado");
          for(const [id,p] of Object.entries(d.presets||{})){const row=el("div");row.className="celia-v3-row";row.append(el("span",p.label||id),el("span",["Velocidade: "+(p.speed??"—"),"Passos: "+(p.num_step??"—"),"Guidance: "+(p.guidance_scale??"—")].join(" · ")));list.append(row);}
        }catch(e){status.textContent="Indisponível: "+e.message;}
      });
      body.append(el("p","A voz oficial usa o contrato de produção verificado; estes presets legados não alteram sua síntese. Não oferecemos uma troca que o motor de produção ignora."));
    }
    addFacts(asr,"celia-v3-asr-status","Motor ASR",topology,[["Configurado",["subsystems","asr","configured"]],["Motor carregado",["subsystems","asr","engine_loaded"]]],"A migração Parakeet é uma missão separada.");
    addFacts(tts,"celia-v3-tts-status","Motor e reprodução",topology,[["Motor configurado",["subsystems","tts","configured"]],["Motor carregado",["subsystems","tts","engine_loaded"]]],"O destino efetivo e a exclusividade da saída dependem da homologação de áudio. Nenhuma política alterada.");
    addFacts(agent,"celia-v3-brain","Cérebro",rt,[["Modelo de conversa",["llm","model"]],["Provedor",["agent","llm_provider"]],["Temperatura",["llm","temperature"]]],"Modelo de conversa carregado; não comprova um cérebro pensante independente.");
    addFacts(agent,"celia-v3-jev","Jev e ferramentas",rt,[["Agente de ferramentas",["tool_agent","enabled"]],["Modelo de ferramentas",["tool_agent","model"]],["MCP",["agent","mcp_enabled"]]],"Use Seleção de modelo Jev para escolher o primeiro elo da fila carregada.");
    addFacts(agent,"celia-v3-memory","Memória",rt,[["MemoryBook",["agent","memory_book_enabled"]],["Histórico (turnos)",["agent","max_history_turns"]],["Embeddings",["agent","memory_book_embedding_model"]]]);
    if(agent&&!agent.querySelector("#celia-v3-prompts")){
      const body=section(agent,"celia-v3-prompts","Persona e prompts");
      body.append(el("p","Escolha um perfil para editar seu prompt contextual. O editor existente valida, salva e permite cancelar. A identidade central não é sobrescrita."));
      for(const p of window.celiaPerfis?.listar() || []){
        if(p.id==="roteiro")continue;
        const b=el("button",p.nome+" ›"); b.type="button";b.className="celia-v3-link";
        b.addEventListener("click",()=>window.celiaPerfis.editar(p.id,dialog));body.append(b);
      }
    }
  }
  window.celiaSettingsV3={section,panel,close};
  let scheduled=false;
  function scan(){scheduled=false; for(const d of document.querySelectorAll('[role="dialog"]')) if(panel(d,"general"))setup(d);}
  function schedule(){if(!scheduled){scheduled=true;requestAnimationFrame(scan);}}
  new MutationObserver(schedule).observe(document.documentElement,{subtree:true,childList:true});
  schedule();
})();