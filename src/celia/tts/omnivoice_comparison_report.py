"""Render measured OmniVoice benchmark data as a local listening dashboard."""

from __future__ import annotations

import html
import json
import os
from collections import defaultdict
from pathlib import Path
from statistics import median
from typing import Any


def _escape(value: Any) -> str:
    return html.escape(str(value), quote=True)


def _stage_elapsed(run: dict[str, Any], stage: str) -> float:
    return round(
        sum(float(event.get("elapsed_ms", 0)) for event in run.get("events", []) if event.get("stage") == stage),
        3,
    )


def _audio_source(audio_path: str, output_path: Path) -> str:
    return _escape(os.path.relpath(Path(audio_path), output_path.parent).replace(os.sep, "/"))


def _format_ms(value: float) -> str:
    return f"{value / 1000:.2f} s" if value >= 1000 else f"{value:.0f} ms"


def _mode_summary(runs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[str, list[float]] = defaultdict(list)
    for run in runs:
        grouped[str(run.get("mode", "unknown"))].append(_stage_elapsed(run, "generate_total"))
    return [
        {"mode": mode, "samples": len(values), "median_ms": median(values), "min_ms": min(values), "max_ms": max(values)}
        for mode, values in sorted(grouped.items())
    ]


def render_comparison_html(report: dict[str, Any], output_path: Path) -> str:
    """Return an HTML page; it reports measurements and leaves voice quality to listening."""
    output_path = Path(output_path)
    runs = [run for run in report.get("runs", []) if isinstance(run, dict) and isinstance(run.get("wav"), dict)]
    gpu = report.get("memory_after_model_load", {}).get("gpu", {})
    device = report.get("model_device", "NOT VERIFIED")
    codec_device = report.get("audio_tokenizer_device", "NOT VERIFIED")
    summary_rows = "".join(
        "<tr><td>{mode}</td><td>{samples}</td><td>{median}</td><td>{minimum}</td><td>{maximum}</td></tr>".format(
            mode=_escape(row["mode"]),
            samples=row["samples"],
            median=_format_ms(row["median_ms"]),
            minimum=_format_ms(row["min_ms"]),
            maximum=_format_ms(row["max_ms"]),
        )
        for row in _mode_summary(runs)
    ) or '<tr><td colspan="5">Nenhuma síntese concluída.</td></tr>'

    cards = []
    for index, run in enumerate(runs, start=1):
        wav = run["wav"]
        total = _stage_elapsed(run, "generate_total")
        reference = _stage_elapsed(run, "reference_prompt_total")
        asr = _stage_elapsed(run, "reference_asr_transcribe")
        codec = _stage_elapsed(run, "reference_audio_codec_encode")
        source = _audio_source(str(wav.get("path", "")), output_path)
        cards.append(
            """
            <article class="card">
              <div class="card-top"><span class="index">{index:02d}</span><span class="mode">{mode}</span></div>
              <p class="text">{text}</p>
              <audio controls preload="metadata" src="{source}"></audio>
              <dl>
                <div><dt>Geração total</dt><dd>{total}</dd></div>
                <div><dt>Referência</dt><dd>{reference}</dd></div>
                <div><dt>ASR da referência</dt><dd>{asr}</dd></div>
                <div><dt>Codec da referência</dt><dd>{codec}</dd></div>
                <div><dt>WAV</dt><dd>{seconds:.2f} s · {sample_rate} Hz · mono</dd></div>
              </dl>
              <label>Identidade da Célia (sua escuta) <input class="rating" data-run="{index}" type="range" min="1" max="5" value="3"></label>
              <output class="rating-value" data-output="{index}">3/5</output>
            </article>
            """.format(
                index=index,
                mode=_escape(run.get("mode", "unknown")),
                text=_escape(run.get("text", "")),
                source=source,
                total=_format_ms(total),
                reference=_format_ms(reference),
                asr=_format_ms(asr),
                codec=_format_ms(codec),
                seconds=float(wav.get("seconds", 0)),
                sample_rate=_escape(wav.get("sample_rate", "?")),
            )
        )

    reference = report.get("reference", {})
    nvidia_smi = gpu.get("nvidia_smi", "NOT VERIFIED")
    return """<!doctype html>
<html lang="pt-BR">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>OmniVoice - Célia: velocidade e escuta</title>
<style>
@import url('https://fonts.googleapis.com/css2?family=DM+Mono:wght@400;500&family=Space+Grotesk:wght@500;600;700&display=swap');
:root {{ --ink:#171a1f; --paper:#f4f0e8; --card:#fffdf8; --line:#c9c0b2; --cyan:#007b8a; --orange:#e35c25; --muted:#65615a; }}
* {{ box-sizing:border-box; }} body {{ margin:0; color:var(--ink); background:radial-gradient(circle at 78% 4%,#b7e8dc 0,transparent 30rem),var(--paper); font:15px/1.5 'DM Mono',monospace; }}
main {{ width:min(1240px,calc(100% - 32px)); margin:0 auto; padding:52px 0 72px; }}
h1,h2 {{ font-family:'Space Grotesk',sans-serif; letter-spacing:-.04em; }} h1 {{ max-width:850px; margin:0; font-size:clamp(40px,7vw,84px); line-height:.9; }}
.eyebrow {{ color:var(--cyan); font-weight:500; letter-spacing:.08em; text-transform:uppercase; }} .warning {{ margin:26px 0; padding:14px 16px; border-left:5px solid var(--orange); background:#fff3e8; }}
.facts {{ display:grid; grid-template-columns:repeat(4,minmax(0,1fr)); gap:1px; margin:32px 0; background:var(--line); border:1px solid var(--line); }} .facts div {{ min-height:92px; padding:14px; background:var(--card); }} .facts b {{ display:block; margin-top:7px; overflow-wrap:anywhere; font-size:13px; }}
.table-wrap {{ overflow:auto; margin:40px 0; background:var(--card); border:1px solid var(--line); }} table {{ width:100%; border-collapse:collapse; }} th,td {{ padding:13px; border-bottom:1px solid var(--line); text-align:left; white-space:nowrap; }} th {{ background:#e8e1d5; font:600 12px 'Space Grotesk',sans-serif; text-transform:uppercase; letter-spacing:.06em; }}
.grid {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(310px,1fr)); gap:16px; }} .card {{ min-height:365px; padding:19px; border:1px solid var(--line); background:var(--card); box-shadow:5px 5px 0 rgba(23,26,31,.12); }} .card-top {{ display:flex; justify-content:space-between; align-items:center; }} .index {{ color:var(--orange); font:600 24px 'Space Grotesk',sans-serif; }} .mode {{ color:var(--cyan); font-weight:500; }} .text {{ min-height:48px; font:600 18px/1.25 'Space Grotesk',sans-serif; }} audio {{ width:100%; accent-color:var(--cyan); }} dl {{ margin:18px 0; }} dl div {{ display:flex; justify-content:space-between; gap:8px; border-bottom:1px dashed var(--line); padding:4px 0; }} dt {{ color:var(--muted); }} dd {{ margin:0; text-align:right; }} label {{ display:block; margin-top:16px; color:var(--muted); font-size:12px; }} .rating {{ width:100%; accent-color:var(--orange); }} .rating-value {{ color:var(--orange); font-weight:500; }}
footer {{ margin-top:48px; color:var(--muted); font-size:12px; }} @media (max-width:720px) {{ main {{ width:min(100% - 24px,1240px); padding-top:30px; }} .facts {{ grid-template-columns:1fr 1fr; }} }}
</style>
</head>
<body><main>
<p class="eyebrow">OmniVoice · Célia · evidência local</p>
<h1>Velocidade medida. Qualidade escutada.</h1>
<p class="warning">Nenhum número desta página declara “voz boa” automaticamente. Ouça cada WAV e avalie a identidade da Célia; os tempos e a memória vêm do relatório real.</p>
<section class="facts"><div>Modelo<b>{device}</b></div><div>Codec<b>{codec_device}</b></div><div>GPU / VRAM<b>{gpu}</b></div><div>Referência<b>{reference}</b></div></section>
<h2>Tempo por modo</h2><div class="table-wrap"><table><thead><tr><th>Modo</th><th>Amostras</th><th>Mediana</th><th>Melhor</th><th>Pior</th></tr></thead><tbody>{summary_rows}</tbody></table></div>
<h2>Ouça as amostras</h2><section class="grid">{cards}</section>
<footer>Fonte: <code>{report_path}</code>. A avaliação de escuta é salva somente neste navegador; não altera a Célia nem o benchmark.</footer>
</main><script>
const key='celia-omnivoice-listening:'+location.pathname; document.querySelectorAll('.rating').forEach((input)=>{{ const output=document.querySelector('[data-output="'+input.dataset.run+'"]'); const stored=localStorage.getItem(key+':'+input.dataset.run); if(stored) input.value=stored; const show=()=>{{ output.value=input.value+'/5'; localStorage.setItem(key+':'+input.dataset.run,input.value); }}; input.addEventListener('input',show); show(); }});
</script></body></html>""".format(
        device=_escape(device),
        codec_device=_escape(codec_device),
        gpu=_escape(nvidia_smi),
        reference=_escape(reference.get("sha256", "NOT VERIFIED"))[:24],
        summary_rows=summary_rows,
        cards="".join(cards) or '<p class="warning">O benchmark não registrou WAVs concluídos.</p>',
        report_path=_escape(output_path.with_name("report.json")),
    )


def write_comparison_html(report_path: Path, output_path: Path | None = None) -> Path:
    report_path = Path(report_path)
    output_path = Path(output_path) if output_path else report_path.with_name("comparacao.html")
    report = json.loads(report_path.read_text(encoding="utf-8"))
    output_path.write_text(render_comparison_html(report, output_path), encoding="utf-8")
    return output_path
