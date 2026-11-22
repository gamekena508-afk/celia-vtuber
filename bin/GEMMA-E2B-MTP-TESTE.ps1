<#
.PURPOSE
  Staging/benchmarking Gemma 4 E2B MTP without changing the Célia production server.
.EXAMPLES
  powershell -File bin\GEMMA-E2B-MTP-TESTE.ps1
  powershell -File bin\GEMMA-E2B-MTP-TESTE.ps1 -Download
  powershell -File bin\GEMMA-E2B-MTP-TESTE.ps1 -Run
  powershell -File bin\GEMMA-E2B-MTP-TESTE.ps1 -Run -Baseline
.NOTES
  Port 8032 only. The production port 8031 must not be interrupted.
  Requires at least 4 GiB free VRAM. Never auto-stops other processes.
#>
[CmdletBinding()]
param(
    [switch]$Download,
    [switch]$Run,
    [switch]$Baseline,
    [int]$Port = 8032
)
$ErrorActionPreference = "Stop"
$modelDir = if ($env:CELIA_GEMMA_E2B_DIR) { $env:CELIA_GEMMA_E2B_DIR } else { Join-Path $HOME "Documents\IA\Modelos\gemma4-e2b" }
$target = Join-Path $modelDir "gemma-4-E2B-it-Q4_0.gguf"
$projector = Join-Path $modelDir "mmproj-gemma-4-E2B-it-Q8_0.gguf"
$drafter = Join-Path $modelDir "mtp-gemma-4-E2B-it.gguf"
$expectedSha256 = "9eba819938efccfd6044f8af84e3bbfddc639a2bcf32ebc36420e6a649191919"
$url = "https://huggingface.co/unsloth/gemma-4-E2B-it-GGUF/resolve/main/mtp-gemma-4-E2B-it.gguf"
if (-not (Test-Path -LiteralPath $target)) { throw "Modelo Gemma E2B nao encontrado: $target" }
if (-not (Test-Path -LiteralPath $projector)) { throw "Projetor multimodal nao encontrado: $projector" }
$exe = (Get-Command "llama-server.exe" -ErrorAction Stop).Source
Write-Output ("Servidor instalado: " + $exe)
if ($Download -and -not (Test-Path -LiteralPath $drafter)) {
    $part = "$drafter.part"
    if (Test-Path -LiteralPath $part) {
        throw "Ha um download parcial anterior. Nao vou sobrescrever: $part"
    }
    Write-Output "Obtendo apenas o MTP E2B (~98 MB), sem instalar ou reiniciar modelos..."
    & curl.exe --fail --location --retry 1 --connect-timeout 10 --max-time 80 --output "$part" "$url"
    if ($LASTEXITCODE -ne 0) { throw "Download falhou. Confira o arquivo .part antes de repetir." }
    $digest = (Get-FileHash -LiteralPath $part -Algorithm SHA256).Hash.ToLowerInvariant()
    if ($digest -ne $expectedSha256) { throw "SHA256 divergiu; nao habilitar o drafter. Arquivo .part preservado." }
    Move-Item -LiteralPath $part -Destination $drafter
    Write-Output "Drafter MTP E2B verificado e preparado."
}
$present = Test-Path -LiteralPath $drafter
if ($present) {
    $digest = (Get-FileHash -LiteralPath $drafter -Algorithm SHA256).Hash.ToLowerInvariant()
    if ($digest -ne $expectedSha256) { throw "MTP existente tem SHA256 incorreto. Nao vou usar." }
}
Write-Output ("MTP E2B instalado e verificado: " + $present)
if (-not $Run) {
    Write-Output "SIMULACAO. Para usar: -Download para preparar; -Run -Baseline para baseline ou -Run para MTP."
    Write-Output "Nao muda nem reinicia a Gemma da Célia em 8031."
    exit 0
}
if (-not $Baseline -and -not $present) { throw "Drafter ausente. Rode com -Download primeiro." }
if ($Port -eq 8031) { throw "A porta 8031 pertence a Célia: nao usar para benchmark." }
$portInUse = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue
if ($portInUse) { throw "Porta $Port ocupada. Abortando sem alterar processos." }
$line = (& nvidia-smi --query-gpu=memory.free --format=csv,noheader,nounits 2>$null | Select-Object -First 1)
if (-not $line -or $line -notmatch '^\s*(\d+)') { throw "Nao foi possivel medir VRAM livre com nvidia-smi." }
$freeMb = [int]$Matches[1]
Write-Output ("VRAM livre: $freeMb MiB")
if ($freeMb -lt 4300) { throw "VRAM livre insuficiente para um segundo processo GPU (minimo conservador 4300 MiB)." }
$args = @(
    "--model", $target, "--mmproj", $projector,
    "--alias", "celia-gemma4-e2b-benchmark", "--host", "127.0.0.1",
    "--port", "$Port", "--ctx-size", "8192", "--parallel", "1",
    "--n-gpu-layers", "99", "--flash-attn", "on",
    "--threads", "6", "--threads-batch", "8",
    "--reasoning", "off", "--reasoning-budget", "0"
)
if (-not $Baseline) {
    $args += @("--model-draft", $drafter, "--spec-type", "draft-mtp", "--spec-draft-n-max", "3")
    Write-Output "Modo: MTP (3 tokens de draft); endpoint isolado /v1 em 8032."
} else {
    Write-Output "Modo: baseline SEM MTP; endpoint isolado /v1 em 8032."
}
Write-Output "Apenas este servidor de benchmark sera iniciado. Ctrl+C encerra apenas o teste."
& $exe @args
