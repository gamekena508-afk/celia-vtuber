// celia-3d-curadoria.js — curadoria das acoes da bancada 3D (Kurt, 2026-10-06, rodada 9, v2).
// Pedido do Pai: cada gesto/acao recebe tres escolhas — V (fica, aprovada), ? (fica, mas precisa ser consertada) e X (descartada).
//  - sem marca: aparecem as tres opcoes;  V ou ?: o botao fica no lugar so com a marca (clicar na marca desmarca e as tres voltam);
//  - X: o botao vai para a caixa "Descartadas" (clicar na marca X restaura e as tres opcoes voltam);
//  - a lista dos "?" e copiavel/baixavel: e o que o Pai manda de volta para o Kurt consertar.
// E um mini-app de decisao (CODEX.md secao 14): cada clique persiste NA HORA (localStorage, chave com esquema), a lista e regerada a cada mudanca,
// baixavel em JSON, restaurada ao recarregar, e qualquer falha de leitura/gravacao/copia aparece na tela (nunca finge que salvou).
// Marcar NAO apaga arquivo nenhum. Estado antigo e migrado: a v1 (so "descartadas") vira estado "descartada"; campo ausente herda o padrao (`typeof x === "undefined"`).
const CHAVE = "celia3d-bancada:curadoria:v2", CHAVE_V1 = "celia3d-bancada:curadoria:v1", VERSAO = 2;
const ESTADOS = ["ok", "melhorar", "descartada"];
const SIMBOLO = { ok: "✓", melhorar: "?", descartada: "✗" };
const NOME = { ok: "aprovada", melhorar: "para melhorar", descartada: "descartada" };

export class Curadoria {
  constructor(raiz) {
    this.estado = { versao: VERSAO, marcas: {}, atualizado: null };   // marcas: chave -> "ok" | "melhorar" | "descartada"
    this.chips = new Map(); this.ordem = 0; this.erro = null; this.restaurado = false; this.migrado = false;
    this._montar(raiz); this._carregar(); this._desenhar();
  }
  // ---- estado ----
  marca(k) { return this.estado.marcas[k] || null; }
  descartada(k) { return this.marca(k) === "descartada"; }
  marcar(k, e) { if (!ESTADOS.includes(e)) return; this.estado.marcas[k] = e; this._persistir(); }
  desmarcar(k) { if (k in this.estado.marcas) { delete this.estado.marcas[k]; this._persistir(); } }
  lista(e) { return Object.keys(this.estado.marcas).filter((k) => this.estado.marcas[k] === e); }
  pendentes() { return [...this.chips.keys()].filter((k) => !this.marca(k)); }
  _carregar() {
    let bruto = null, v1 = null;
    try { bruto = localStorage.getItem(CHAVE); if (bruto === null) v1 = localStorage.getItem(CHAVE_V1); } catch (e) { this._falha("sem acesso ao armazenamento do navegador: " + e.message); return; }
    try {
      if (bruto !== null) {
        const e = JSON.parse(bruto);
        if (typeof e.versao === "undefined") e.versao = VERSAO;
        if (typeof e.atualizado === "undefined") e.atualizado = null;
        if (typeof e.marcas === "undefined") e.marcas = {};
        if (e.marcas === null || typeof e.marcas !== "object" || Array.isArray(e.marcas) || Object.values(e.marcas).some((x) => !ESTADOS.includes(x))) throw new Error("marcas invalidas");
        this.estado = { versao: VERSAO, marcas: { ...e.marcas }, atualizado: e.atualizado }; this.restaurado = true;
      } else if (v1 !== null) { // migracao da v1 (so descartadas); a chave v1 NAO e apagada
        const e = JSON.parse(v1), arr = typeof e.descartadas === "undefined" ? [] : e.descartadas;
        if (!Array.isArray(arr) || arr.some((x) => typeof x !== "string")) throw new Error("lista da v1 invalida");
        for (const k of arr) this.estado.marcas[k] = "descartada";
        this.estado.atualizado = typeof e.atualizado === "undefined" ? null : e.atualizado; this.restaurado = true; this.migrado = true; this._persistir();
      }
    } catch (e) { this._falha("a lista salva estava ilegivel e foi ignorada: " + e.message); }
  }
  _persistir() {
    try { this.estado.atualizado = new Date().toISOString(); localStorage.setItem(CHAVE, JSON.stringify(this.estado)); this.erro = null; }
    catch (e) { this._falha("NAO salvou (as marcas valem so ate recarregar): " + e.message); }
    this._desenhar();
  }
  _falha(msg) { this.erro = msg; this._desenhar(); }
  // ---- interface ----
  _montar(raiz) {
    const el = (tag, props = {}, filhos = []) => { const n = document.createElement(tag); Object.assign(n, props); n.append(...filhos); return n; };
    this.status = el("small", { id: "curadoria-status" }); this.status.setAttribute("role", "status");
    this.bCopiar = el("button", { type: "button", id: "curadoria-copiar", textContent: "copiar lista de ? (pra consertar)" });
    this.bBaixar = el("button", { type: "button", id: "curadoria-baixar", textContent: "baixar lista" });
    this.texto = el("textarea", { id: "curadoria-lista", readOnly: true, rows: 8 }); this.texto.setAttribute("aria-label", "lista por marca: aprovadas, para melhorar, descartadas e sem marca");
    this.caixaDescartadas = el("div", { id: "g-descartadas", className: "grupo" }); this.tituloDescartadas = el("b", { textContent: "Descartadas" }); this.caixaDescartadas.append(this.tituloDescartadas);
    this.bCopiar.addEventListener("click", () => this._copiar()); this.bBaixar.addEventListener("click", () => this._baixar());
    raiz.append(this.status, el("div", {}, [this.bCopiar, this.bBaixar]), this.caixaDescartadas, el("details", {}, [el("summary", { textContent: "lista completa" }), this.texto]));
  }
  // cria o botao da acao + as marcas; devolve o chip. `dica` vira o title do botao principal (numeros medidos).
  chip(pai, chave, rotulo, aoClicar, dica = "") {
    const chip = document.createElement("span"); chip.className = "chip"; chip.dataset.chave = chave;
    const principal = document.createElement("button"); principal.type = "button"; principal.className = "principal"; principal.textContent = rotulo; if (dica) principal.title = dica;
    principal.addEventListener("click", aoClicar);
    const marcas = document.createElement("span"); marcas.className = "marcas";
    chip.append(principal, marcas); pai.append(chip);
    this.chips.set(chave, { chip, principal, marcas, rotulo, pai, indice: this.ordem++ }); this._desenhar(); return chip;
  }
  _botaoMarca(chave, rotulo, e, acao, titulo) {
    const b = document.createElement("button"); b.type = "button"; b.className = "m " + (e || "pend"); b.dataset.marca = e || "";
    b.textContent = e ? SIMBOLO[e] : ""; b.title = titulo; b.setAttribute("aria-label", titulo + ": " + rotulo); b.addEventListener("click", acao); return b;
  }
  _desenhar() {
    for (const [k, c] of this.chips) {
      const m = this.marca(k); c.chip.dataset.estado = m || "pendente"; c.marcas.replaceChildren();
      if (!m) { // as tres opcoes
        for (const e of ESTADOS) { const b = this._botaoMarca(k, c.rotulo, e, () => this.marcar(k, e), e === "ok" ? "aprovar (fica)" : e === "melhorar" ? "marcar para melhorar" : "descartar"); b.textContent = SIMBOLO[e]; c.marcas.append(b); }
      } else c.marcas.append(this._botaoMarca(k, c.rotulo, m, () => this.desmarcar(k), "desmarcar (" + NOME[m] + ")"));
      // X vai para a caixa de descartadas; o resto fica no lugar de origem (na ordem original)
      const destino = m === "descartada" ? this.caixaDescartadas : c.pai;
      if (c.chip.parentNode !== destino) {
        if (destino === c.pai) { const prox = [...c.pai.querySelectorAll(":scope > .chip")].find((x) => this.chips.get(x.dataset.chave).indice > c.indice); c.pai.insertBefore(c.chip, prox || null); } else destino.append(c.chip);
      }
    }
    const n = (e) => this.lista(e).filter((k) => this.chips.has(k)).length, hora = this.estado.atualizado ? new Date(this.estado.atualizado).toLocaleTimeString("pt-BR") : null;
    this.status.dataset.s = this.erro ? "erro" : "ok";
    this.status.textContent = this.erro ? this.erro : `✓ ${n("ok")} · ? ${n("melhorar")} · ✗ ${n("descartada")} · sem marca ${this.pendentes().length}` + (hora ? ` · salvo às ${hora}` : this.restaurado ? " · lista restaurada" : " · nada marcado ainda");
    this.tituloDescartadas.textContent = `Descartadas (${n("descartada")})`; this.caixaDescartadas.dataset.vazia = String(n("descartada") === 0);
    this.bCopiar.disabled = this.lista("melhorar").length === 0;
    this.texto.value = this._texto();
  }
  _rot(k) { return this.chips.has(k) ? `${k} — ${this.chips.get(k).rotulo}` : `${k} — (não carregada)`; }
  _texto() {
    const sec = (titulo, ks) => `${titulo} (${ks.length}):\n${ks.map((k) => this._rot(k)).join("\n") || "—"}\n`;
    return [sec("PARA MELHORAR (?)", this.lista("melhorar")), sec("APROVADAS (✓)", this.lista("ok")), sec("DESCARTADAS (✗)", this.lista("descartada")), sec("SEM MARCA", this.pendentes())].join("\n");
  }
  async _copiar() {
    const texto = "Ações da bancada 3D da Célia marcadas com ? (consertar):\n" + this.lista("melhorar").map((k) => this._rot(k)).join("\n");
    try { await navigator.clipboard.writeText(texto); this.status.dataset.s = "ok"; this.status.textContent = `copiada a lista de ? (${this.lista("melhorar").length})`; }
    catch (e) { this._falha("não consegui copiar sozinho (" + e.message + "): abra \"lista completa\" e copie de lá"); }
  }
  _baixar() {
    const dados = { versao: VERSAO, atualizado: this.estado.atualizado, melhorar: this.lista("melhorar"), aprovadas: this.lista("ok"), descartadas: this.lista("descartada"), sem_marca: this.pendentes() };
    const a = document.createElement("a"); a.href = URL.createObjectURL(new Blob([JSON.stringify(dados, null, 2)], { type: "application/json" })); a.download = "celia3d-curadoria.json";
    document.body.append(a); a.click(); a.remove(); setTimeout(() => URL.revokeObjectURL(a.href), 4000);
  }
}
