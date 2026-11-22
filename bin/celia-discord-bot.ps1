# Escolhe/consulta qual bot do Discord a Célia usa (um por vez).
#   .\bin\celia-discord-bot.ps1 status
#   .\bin\celia-discord-bot.ps1 secundario   (padrao; testes)   | secondary
#   .\bin\celia-discord-bot.ps1 primario     (so quando o dono quiser) | primary
# Usa a rota da central (127.0.0.1:12393) se estiver no ar; senao grava var\discord_bot_choice.json direto.
# O bot em execucao troca sozinho em ate ~4 s. Nunca le nem mostra tokens.
param(
    [Parameter(Position = 0)][string]$Acao = "status",
    [string]$Central = "http://127.0.0.1:12393",
    [string]$VarDir = ""
)
$ErrorActionPreference = "Stop"
$raiz = Split-Path -Parent $PSScriptRoot
if (-not $VarDir) { $VarDir = Join-Path $raiz "var" }
$arquivo = Join-Path $VarDir "discord_bot_choice.json"
$estado = Join-Path $VarDir "discord_bot_status.json"

$mapa = @{ primario = "primary"; "primário" = "primary"; primary = "primary"; secundario = "secondary"; "secundário" = "secondary"; secondary = "secondary" }
$acaoN = $Acao.Trim().ToLower()

function Get-Rota {
    try { return Invoke-RestMethod -Uri "$Central/bridge/discord/bot" -TimeoutSec 3 } catch { return $null }
}

function Show-Estado($d) {
    $st = $d.status
    $conn = if ($st -and $st.connected) { "conectado (...$($st.bot_user_id_suffix))" } else { "desconectado/desconhecido" }
    Write-Output ("Escolha: {0} | efetivo: {1} | token configurado: {2} | {3}" -f $d.bot, $d.effective_bot, $d.configured, $conn)
    if ($d.forced_secondary) { Write-Output "Aviso: CELIA_DISCORD_FORCE_SECONDARY=1 forca o secundario." }
}

function Read-Arquivo {
    $bot = "secondary"
    if (Test-Path $arquivo) {
        try { $j = Get-Content $arquivo -Raw -Encoding UTF8 | ConvertFrom-Json; if ($j.bot -in @("primary", "secondary")) { $bot = $j.bot } } catch { }
    }
    $conectado = $false; $suf = $null
    if (Test-Path $estado) {
        try {
            $s = Get-Content $estado -Raw -Encoding UTF8 | ConvertFrom-Json
            $idade = ([DateTime]::UtcNow - [DateTime]::Parse($s.updated_at).ToUniversalTime()).TotalSeconds
            if ($idade -le 20) { $conectado = [bool]$s.connected; $suf = $s.bot_user_id_suffix }
        } catch { }
    }
    return [pscustomobject]@{ bot = $bot; effective_bot = $bot; configured = "?"; forced_secondary = $false
        status = [pscustomobject]@{ connected = $conectado; bot_user_id_suffix = $suf } }
}

if ($acaoN -eq "status") {
    $d = Get-Rota
    if (-not $d) { Write-Output "(central fora do ar: lendo o arquivo)"; $d = Read-Arquivo }
    Show-Estado $d
    exit 0
}

if (-not $mapa.ContainsKey($acaoN)) {
    Write-Error "Uso: celia-discord-bot.ps1 status | primario | secundario"
    exit 2
}
$alvo = $mapa[$acaoN]

$feito = $false
try {
    $corpo = @{ bot = $alvo } | ConvertTo-Json -Compress
    Invoke-RestMethod -Uri "$Central/bridge/discord/bot" -Method Post -ContentType "application/json" `
        -Headers @{ "X-Celia-Source" = "cli" } -Body $corpo -TimeoutSec 3 | Out-Null
    $feito = $true
    Write-Output "Escolha gravada pela central: $alvo"
} catch { }

if (-not $feito) {
    New-Item -ItemType Directory -Force -Path $VarDir | Out-Null
    $tmp = "$arquivo.$PID.tmp"
    $reg = @{ bot = $alvo; changed_at = (Get-Date).ToUniversalTime().ToString("yyyy-MM-ddTHH:mm:ss+00:00"); changed_by = "cli" } | ConvertTo-Json -Compress
    [System.IO.File]::WriteAllText($tmp, $reg, (New-Object System.Text.UTF8Encoding($false)))
    Move-Item -Force -Path $tmp -Destination $arquivo
    Write-Output "Central fora do ar: escolha gravada direto em $arquivo -> $alvo"
}
Write-Output "O bot em execucao troca sozinho em ate ~4 s (ou vale no proximo inicio)."
