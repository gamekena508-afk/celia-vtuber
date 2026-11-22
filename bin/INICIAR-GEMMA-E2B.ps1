<#
Célia production model starter: E2B GGUF + multimodal projector + matching MTP.
The Gemma runs with Célia. Never launches a second instance if 8031 is in use.
If the speculative head fails before being ready, fall back to ordinary Gemma.
#>
[CmdletBinding()]
param(
    [switch]$SemMTP,
    [int]$Port=8031
)
$ErrorActionPreference='Stop'
$root=(Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$files=if ($env:CELIA_GEMMA_E2B_DIR) { $env:CELIA_GEMMA_E2B_DIR } else { Join-Path $HOME 'Documents\IA\Modelos\gemma4-e2b' }
$base=Join-Path $files 'gemma-4-E2B-it-Q4_0.gguf'
$proj=Join-Path $files 'mmproj-gemma-4-E2B-it-Q8_0.gguf'
$head=Join-Path $files 'mtp-gemma-4-E2B-it.gguf'
$expectedHash='9eba819938efccfd6044f8af84e3bbfddc639a2bcf32ebc36420e6a649191919'
$exe=(Get-Command 'llama-server.exe' -ErrorAction Stop).Source
if (-not (Test-Path $base) -or -not (Test-Path $proj)) {
    throw 'Faltam pesos GGUF ou projetor multimodal da Gemma E2B.'
}
$portBusy=Get-NetTCPConnection -State Listen -LocalPort $Port -ErrorAction SilentlyContinue
if ($portBusy) { throw "Porta $Port ocupada (PID $($portBusy.OwningProcess)). Nao duplicar modelo." }
$arguments=@('--model',$base,'--mmproj',$proj,'--alias','celia-gemma4-e2b',
    '--host','127.0.0.1','--port',"$Port",'--ctx-size','8192','--parallel','1',
    '--n-gpu-layers','99','--flash-attn','on','--threads','6','--threads-batch','8',
    '--reasoning','off','--reasoning-budget','0')
$canMTP=(-not $SemMTP) -and (Test-Path $head)
if ($canMTP) {
    $hash=(Get-FileHash -LiteralPath $head -Algorithm SHA256).Hash.ToLowerInvariant()
    if ($hash -ne $expectedHash) {
        Write-Warning 'MTP GGUF tem hash diferente do validado. Inicializando Gemma padrao.'
        $canMTP=$false
    }
}
if ($canMTP) {
    $mtpArgs=$arguments+@('--model-draft',$head,'--spec-type','draft-mtp','--spec-draft-n-max','3')
    Write-Output "[celia-gemma] Inicializando Gemma E2B com MTP + mmproj; porta $Port."
    $startTime=Get-Date
    & $exe @mtpArgs
    $exitCode=$LASTEXITCODE
    $elapsed=((Get-Date)-$startTime).TotalSeconds
    if ($exitCode -eq 0) { exit 0 }
    if ($elapsed -gt 80) { throw "Gemma+MTP interrompida apos $([int]$elapsed)s; nao reiniciar automaticamente sem contexto." }
    Write-Warning "Gemma MTP nao conseguiu inicializar: exit $exitCode, $([int]$elapsed)s. Fallback padrao."
}
Write-Output "[celia-gemma] Inicializando Gemma E2B sem MTP, mantendo a mesma porta e projetor."
& $exe @arguments
exit $LASTEXITCODE
