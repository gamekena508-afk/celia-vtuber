from __future__ import annotations

import asyncio
import json
import os
import re
import shutil
import time
from pathlib import Path
from dataclasses import replace

from ..models import ImageRequest, ImageResult, ProviderHealth
from ..utils import DEFAULT_OUTPUT_DIR, timestamp_name
from .base import ImageProvider


class GrokCliImageProvider(ImageProvider):
    name = "grok"
    max_references = 4
    # CELIA_GROK_CLI > ~/.local/bin/grok (se existir) > `grok` no PATH.
    _padrao = Path.home() / ".local" / "bin" / "grok"
    command = os.environ.get("CELIA_GROK_CLI") or (str(_padrao) if _padrao.exists() else (shutil.which("grok") or str(_padrao)))

    async def health(self) -> ProviderHealth:
        if not Path(self.command).exists() and not shutil.which("grok"):
            return ProviderHealth(self.name, False, "cli_missing", "Grok Build CLI não encontrado")
        try:
            proc = await asyncio.create_subprocess_exec(
                self.command, "inspect", "--json",
                cwd=str(Path(__file__).resolve().parents[5]),
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
            )
            out, err = await asyncio.wait_for(proc.communicate(), timeout=12)
            if proc.returncode != 0:
                return ProviderHealth(self.name, False, "cli_error", (err or out).decode(errors="replace")[-500:])
            return ProviderHealth(self.name, True, message="Grok Build CLI disponível")
        except asyncio.TimeoutError:
            return ProviderHealth(self.name, False, "timeout", "grok inspect excedeu 12s")
        except Exception as exc:
            return ProviderHealth(self.name, False, "cli_error", str(exc))

    def _agent_prompt(self, request: ImageRequest, references: list[Path], output: Path) -> str:
        refs = "\n".join(f"- {p}" for p in references)
        if references:
            tool_instruction = (
                "Use the native image_edit tool with ALL reference image paths below in the image parameter. "
                "Preserve the identities from the references while applying the requested edit/composition."
            )
        else:
            tool_instruction = "Use the native image_gen tool to create the requested image from scratch."
        return f"""You are executing an image job for Célia. {tool_instruction}
Requested visual: {request.prompt}
Aspect ratio: {request.aspect_ratio}
Reference images:\n{refs or '(none)'}
After the image tool succeeds, make sure the final generated image exists at EXACTLY this absolute path:
{output}
If the image tool returns another local file path, copy that image to the exact output path using a shell/file operation.
Do not generate extra variants. Finish with exactly one line: RESULT_PATH={output}
"""

    async def generate(self, request: ImageRequest, references: list[Path], prompt_final: str) -> ImageResult:
        started = time.monotonic()
        output_dir = Path(request.output_dir) if request.output_dir else DEFAULT_OUTPUT_DIR / "grok"
        output_dir.mkdir(parents=True, exist_ok=True)
        output = output_dir / timestamp_name("grok", ".png")
        job = replace(request, prompt=prompt_final)
        agent_prompt = self._agent_prompt(job, references[: self.max_references], output)
        args = [
            self.command, "-p", agent_prompt,
            "--output-format", "json",
            "--always-approve",
            "--max-turns", "6",
        ]
        try:
            proc = await asyncio.create_subprocess_exec(
                *args,
                cwd=str(Path(__file__).resolve().parents[5]),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=os.environ.copy(),
            )
            out, err = await asyncio.wait_for(proc.communicate(), timeout=720)
            stdout = out.decode(errors="replace")
            stderr = err.decode(errors="replace")
            duration = int((time.monotonic() - started) * 1000)
            combined = f"{stdout}\n{stderr}"
            if proc.returncode != 0:
                lower = combined.lower()
                code = "auth_missing" if any(x in lower for x in ("login", "auth", "token", "unauthorized", "401")) else "cli_error"
                return ImageResult(False, self.name, prompt_final, [str(p) for p in references], duration_ms=duration,
                                   error_code=code, error=combined[-1200:])
            candidate = output if output.exists() else None
            if candidate is None:
                match = re.search(r"RESULT_PATH\s*=\s*([^\r\n]+)", stdout)
                if match:
                    p = Path(match.group(1).strip().strip('"\''))
                    if p.exists(): candidate = p
            if candidate is None:
                return ImageResult(False, self.name, prompt_final, [str(p) for p in references], duration_ms=duration,
                                   error_code="output_missing", error="Grok concluiu, mas o arquivo final não foi localizado")
            return ImageResult(True, self.name, prompt_final, [str(p) for p in references], [str(candidate)],
                               duration_ms=duration, status="completed")
        except asyncio.TimeoutError:
            return ImageResult(False, self.name, prompt_final, [str(p) for p in references],
                               duration_ms=int((time.monotonic() - started) * 1000), error_code="timeout", error="Grok excedeu 12 minutos")
        except Exception as exc:
            return ImageResult(False, self.name, prompt_final, [str(p) for p in references],
                               duration_ms=int((time.monotonic() - started) * 1000), error_code="cli_error", error=str(exc))
