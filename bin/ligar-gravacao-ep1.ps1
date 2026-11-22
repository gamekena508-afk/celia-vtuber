<#
ligar-gravacao-ep1.ps1 — liga a pilha do Episódio 1 no Windows (equivalente ao bin/ligar-gravacao-ep1.sh do Linux).

Dois ambientes que NUNCA dividem estado:
  ensaio  (padrão)  servidor Serie-Minecraft\Teste-Windows, mundo celia-serie-s04, porta 25566, estado var\minecraft-ensaio-win
  oficial           servidor Serie-Minecraft\Servidor,      mundo celia-serie-s03, porta 25565, estado var\minecraft
O oficial exige -ConfirmarMundoOficial. Nada é apagado. Não liga a persona (a voz/OmniVoice sobe pelo LIGAR-CELIA.bat).

Uso:
  powershell -ExecutionPolicy Bypass -File bin\ligar-gravacao-ep1.ps1
  powershell -ExecutionPolicy Bypass -File bin\ligar-gravacao-ep1.ps1 -Ambiente oficial -ConfirmarMundoOficial
#>
[CmdletBinding()]
param(
    [ValidateSet('ensaio', 'oficial')][string]$Ambiente = 'ensaio',
    [switch]$ConfirmarMundoOficial,
    [switch]$SemCorpo
)
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version 2

$Raiz = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$Celia = (Resolve-Path (Join-Path $Raiz '..\..')).Path            # C:\Users\...\Documents\Celia
$Py = Join-Path $Raiz '.venv\Scripts\python.exe'
$Logs = Join-Path $Raiz 'var\logs'
$Runtime = Join-Path $Raiz 'var\runtime'
New-Item -ItemType Directory -Force $Logs, $Runtime | Out-Null

if ($Ambiente -eq 'oficial') {
    if (-not $ConfirmarMundoOficial) { Write-Error 's03 é o mundo OFICIAL. Use -ConfirmarMundoOficial para ligá-lo.'; exit 2 }
    $Mundo = 'celia-serie-s03'; $ServerDir = Join-Path $Celia 'Serie-Minecraft\Servidor'
    $StateDir = Join-Path $Raiz 'var\minecraft'; $McPort = 25565
} else {
    $Mundo = 'celia-serie-s04'; $ServerDir = Join-Path $Celia 'Serie-Minecraft\Teste-Windows'
    $StateDir = Join-Path $Raiz 'var\minecraft-ensaio-win'; $McPort = 25566
}
$Java = Get-ChildItem (Join-Path $Celia 'Runtime-Windows\jre') -Recurse -Filter java.exe -ErrorAction SilentlyContinue | Select-Object -First 1 -ExpandProperty FullName
$Falhas = 0
function Ok($m) { Write-Host "[OK] $m" -ForegroundColor Green }
function Falha($m) { Write-Host "[!!] $m" -ForegroundColor Red; $script:Falhas++ }
function Ouvindo($porta) { [bool](Get-NetTCPConnection -LocalPort $porta -State Listen -ErrorAction SilentlyContinue) }
# Só aceita a porta do Jev se /health responder service == "jev" (outro programa pode ocupar a porta).
function JevDaEsperada($porta) {
    try {
        $r = Invoke-RestMethod -Uri "http://127.0.0.1:$porta/health" -TimeoutSec 2
        return [bool]($r.PSObject.Properties['service'] -and $r.service -eq 'jev')
    } catch { return $false }
}
# Roda series-status e devolve o valor de "state:" ('' se não houver). Erros nativos não encerram o script.
function SerieStatus {
    $ErrorActionPreference = 'Continue'
    return @(& $Py (Join-Path $Raiz 'bin\celia-minecraft-mundos.py') series-status 2>&1 | ForEach-Object { "$_" })
}
function EstadoSerie {
    foreach ($l in (SerieStatus)) { if ($l -match '^state:\s*(\S+)') { return $matches[1] } }
    return ''
}
function Prop($arquivo, $chave) {
    $l = Get-Content $arquivo -Encoding utf8 | Where-Object { $_ -like "$chave=*" } | Select-Object -First 1
    if ($l) { $l.Substring($chave.Length + 1) } else { $null }
}
function SetProp($arquivo, $chave, $valor) {
    $linhas = [System.Collections.Generic.List[string]](Get-Content $arquivo -Encoding utf8)
    $i = $linhas.FindIndex({ param($x) $x -like "$chave=*" })
    if ($i -ge 0) { $linhas[$i] = "$chave=$valor" } else { $linhas.Add("$chave=$valor") }
    [IO.File]::WriteAllLines($arquivo, $linhas, (New-Object Text.UTF8Encoding $false))
}

Write-Host "[ligar-gravacao-ep1] episódio=1 ambiente=$Ambiente mundo=$Mundo porta=$McPort" -ForegroundColor Cyan
foreach ($p in @($Py, $ServerDir, (Join-Path $ServerDir 'fabric-server-launch.jar'), (Join-Path $ServerDir $Mundo))) {
    if (-not (Test-Path $p)) { Write-Error "não encontrado: $p (nada foi iniciado)"; exit 1 }
}
if (-not $Java) { Write-Error "Java 21 não encontrado em $Celia\Runtime-Windows\jre (nada foi iniciado)"; exit 1 }
if (-not (Get-Command node -ErrorAction SilentlyContinue)) { Write-Error 'node não está no PATH (nada foi iniciado)'; exit 1 }

# Variáveis herdadas por todos os processos iniciados daqui.
$env:CELIA_WORKSPACE = $Raiz
$env:PYTHONUTF8 = '1'; $env:PYTHONUNBUFFERED = '1'
$env:CELIA_MC_SERVER_DIR = $ServerDir
$env:CELIA_MC_STATE_DIR = $StateDir
$env:CELIA_JEV_DB = Join-Path $StateDir 'jev.sqlite'
$env:CELIA_MC_JAVA = $Java
$env:MINECRAFT_HOST = '127.0.0.1'; $env:MINECRAFT_PORT = "$McPort"
$env:CELIA_WORLD = $Mundo; $env:CELIA_MUNDO = $Mundo; $env:MUNDO = $Mundo
if (-not $env:CELIA_SERIES_COHOST) { $env:CELIA_SERIES_COHOST = '0' }
$env:CELIA_MINECRAFT_CONTROL_FILE = Join-Path $Runtime 'minecraft-control.token'
$env:CELIA_JEV_PORT = '8784'

Write-Host '== 1. propriedades do servidor'
$props = Join-Path $ServerDir 'server.properties'
$levelName = Prop $props 'level-name'
if ($levelName -ne $Mundo) { Write-Error "server.properties está em '$levelName', esperado '$Mundo'. Nada foi iniciado."; exit 1 }
if ($Ambiente -eq 'ensaio' -and -not (Ouvindo $McPort)) {
    # Só na cópia de ensaio: porta própria (o bot de ensaio recusa 25565) e RCON ligado (a série precisa dele).
    SetProp $props 'server-port' $McPort
    SetProp $props 'rcon.port' 25576
    SetProp $props 'server-ip' '127.0.0.1'
    if ((Prop $props 'enable-rcon') -ne 'true' -or -not (Prop $props 'rcon.password')) {
        SetProp $props 'enable-rcon' 'true'
        $bytes = New-Object byte[] 18; [Security.Cryptography.RandomNumberGenerator]::Create().GetBytes($bytes)
        SetProp $props 'rcon.password' ([Convert]::ToBase64String($bytes).TrimEnd('=').Replace('+', 'A').Replace('/', 'B'))
    }
}
if ((Prop $props 'enable-rcon') -ne 'true') { Write-Error 'RCON desligado no servidor; a série precisa dele. Nada foi iniciado.'; exit 1 }
if ((Prop $props 'server-port') -ne "$McPort") { Write-Error "server-port diferente de $McPort em $props. Nada foi iniciado."; exit 1 }
Ok "server.properties: $Mundo, porta $McPort, RCON ligado"

Write-Host '== 2. estado da série deste ambiente'
New-Item -ItemType Directory -Force $StateDir | Out-Null
$managed = Join-Path $StateDir 'managed-server.json'
$desc = [ordered]@{
    cwd = $ServerDir
    argv = @($Java, '-Xms512M', '-Xmx2G', '-XX:ActiveProcessorCount=2', '-jar', 'fabric-server-launch.jar', 'nogui')
    properties = $props
    pid_file = (Join-Path $ServerDir 'managed-server.pid')
    log_file = (Join-Path $ServerDir 'logs\managed-server.out')
    rcon_host = '127.0.0.1'
}
if (Test-Path $managed) {
    $atual = Get-Content $managed -Raw -Encoding utf8
    if ($atual -match '/home/') { Copy-Item $managed "$managed.linux-$(Get-Date -Format yyyyMMddTHHmmss).bak"; Ok 'managed-server.json do Linux guardado em .bak' }
}
[IO.File]::WriteAllText($managed, ($desc | ConvertTo-Json -Depth 4), (New-Object Text.UTF8Encoding $false))
$registry = Join-Path $StateDir 'world_registry.json'
if (-not (Test-Path $registry)) {
    # Ensaio: registro próprio só com o mundo de ensaio, copiado do registro oficial (o oficial não é tocado).
    & $Py -c @"
import json, sys, pathlib
src = pathlib.Path(sys.argv[1]); dst = pathlib.Path(sys.argv[2]); w = sys.argv[3]
data = json.loads(src.read_text(encoding='utf-8'))
entry = dict(data['worlds'][w]); entry['backup_list'] = []; entry['active_status'] = 'running'
data['worlds'] = {w: entry}
data.update(active_world_id=w, selected_world_id=w, last_series_world_id=w, pending_switch=None, requests={})
dst.write_text(json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True), encoding='utf-8')
"@ (Join-Path $Raiz 'var\minecraft\world_registry.json') $registry $Mundo
    if ($LASTEXITCODE -ne 0) { Write-Error 'não consegui criar o registro do ensaio'; exit 1 }
    Ok "registro do ensaio criado em $registry"
}
Ok "estado: $StateDir"

Write-Host '== 3. credencial de controle (corpo + Jev + persona)'
$tok = $env:CELIA_MINECRAFT_CONTROL_FILE
if (-not (Test-Path $tok)) {
    & $Py -c "import secrets,sys; open(sys.argv[1],'w',encoding='utf-8').write(secrets.token_urlsafe(48))" $tok
}
if ((Get-Item $tok).Attributes -band [IO.FileAttributes]::ReparsePoint) { Write-Error "$tok é link simbólico; recusado"; exit 1 }
icacls $tok /inheritance:r /grant:r "$($env:USERNAME):(R,W)" | Out-Null
$acl = (Get-Acl $tok).Access | Where-Object { $_.IdentityReference -notlike "*\$($env:USERNAME)" }
if ($acl) { Falha "credencial com acesso de outras contas: $($acl.IdentityReference -join ', ')" } else { $env:CELIA_WINDOWS_ACL_VERIFIED = '1'; Ok 'credencial só acessível por esta conta' }

Write-Host "== 4. servidor Minecraft ($Mundo)"
if (Ouvindo $McPort) {
    Ok "servidor já estava no ar na porta $McPort"
} else {
    $out = Join-Path $ServerDir 'logs\managed-server.out'
    New-Item -ItemType Directory -Force (Split-Path $out) | Out-Null
    $proc = Start-Process -FilePath $Java -ArgumentList '-Xms512M', '-Xmx2G', '-XX:ActiveProcessorCount=2', '-jar', 'fabric-server-launch.jar', 'nogui' `
        -WorkingDirectory $ServerDir -WindowStyle Hidden -RedirectStandardOutput $out -RedirectStandardError "$out.err" -PassThru
    Set-Content -Path (Join-Path $ServerDir 'managed-server.pid') -Value $proc.Id -Encoding ascii
    $t0 = Get-Date
    while (-not (Ouvindo $McPort) -and ((Get-Date) - $t0).TotalSeconds -lt 180) {
        if ($proc.HasExited) { break }
        Start-Sleep -Milliseconds 800
    }
    if (Ouvindo $McPort) { Ok "servidor no ar (PID $($proc.Id), $([int]((Get-Date)-$t0).TotalSeconds) s)" } else { Falha "servidor não abriu a porta $McPort (veja $out)"; exit 1 }
}

Write-Host '== 5. Jev (executor, 8784)'
if (Ouvindo 8784) {
    if (JevDaEsperada 8784) {
        Ok 'Jev já estava no ar (service=jev; se for de outro ambiente, rode bin\parar-gravacao-ep1.ps1 antes)'
    } else {
        Falha 'porta 8784 ocupada por outro programa (/health sem service=jev); não é o Jev'
        exit 1
    }
} else {
    Start-Process -FilePath $Py -ArgumentList '-u', (Join-Path $Raiz 'integrations\minecraft\jev_daemon.py') -WorkingDirectory $Raiz -WindowStyle Hidden `
        -RedirectStandardOutput (Join-Path $Logs "jev-$Ambiente.out.log") -RedirectStandardError (Join-Path $Logs "jev-$Ambiente.err.log") | Out-Null
    $ok = $false
    for ($i = 0; $i -lt 30; $i++) {
        if (JevDaEsperada 8784) { $ok = $true; break }
        Start-Sleep 1
    }
    if ($ok) { Ok 'Jev no ar (service=jev)' } else { Falha "Jev não respondeu /health com service=jev (var\logs\jev-$Ambiente.err.log)" }
}

if (-not $SemCorpo) {
    Write-Host '== 6. corpo (Mineflayer, 8080)'
    if (Ouvindo 8080) {
        Ok 'corpo já estava no ar'
    } else {
        Start-Process -FilePath $Py -ArgumentList '-u', (Join-Path $Raiz 'integrations\minecraft\start_body.py') -WorkingDirectory $Raiz -WindowStyle Hidden `
            -RedirectStandardOutput (Join-Path $Logs "corpo-$Ambiente.out.log") -RedirectStandardError (Join-Path $Logs "corpo-$Ambiente.err.log") | Out-Null
        $t0 = Get-Date
        while (-not (Ouvindo 8080) -and ((Get-Date) - $t0).TotalSeconds -lt 45) { Start-Sleep 1 }
        if (Ouvindo 8080) { Ok 'corpo no ar' } else { Falha "corpo não subiu (var\logs\corpo-$Ambiente.err.log)" }
    }
}

Write-Host '== 7. estado da série'
$estado = EstadoSerie
if ($estado -eq 'BLOCKED') { Write-Host '[--] série BLOCKED: rodando series-recover (não apaga nada)' -ForegroundColor Yellow; & $Py (Join-Path $Raiz 'bin\celia-minecraft-mundos.py') series-recover | Out-Null; $estado = EstadoSerie }
if ($estado -in 'NO_WORLD', 'EPISODE_END', 'INTRO_READY', 'GAMEPLAY') { Ok "série no estado $estado" } else { Falha "estado da série: '$estado'"; (SerieStatus) | Select-Object -First 8 | ForEach-Object { Write-Host "    $_" } }

Write-Host '== 7b. ouvido (Tagarela, 8011)'
function OuvidoNoAr { try { $r = Invoke-RestMethod -Uri 'http://127.0.0.1:8011/health' -TimeoutSec 2; return [bool]($r.PSObject.Properties['status'] -and $r.status -eq 'ok') } catch { return $false } }
$ouvidoLigado = $false
$pyTag = Join-Path $Raiz '.venv-asr-tagarela\Scripts\python.exe'
if (OuvidoNoAr) {
    Ok 'ouvido (Tagarela) já estava no ar (porta 8011)'
    $ouvidoLigado = $true
} elseif (Test-Path $pyTag) {
    $vars = [ordered]@{ CUDA_VISIBLE_DEVICES = ''; CELIA_TAGARELA_PORT = '8011'; CELIA_TAGARELA_THREADS = '6'; CELIA_TAGARELA_SPIN = '0'; CELIA_TAGARELA_MAX_SECONDS = '120'; PYTHONUTF8 = '1'; PYTHONUNBUFFERED = '1' }
    $antes = @{}
    foreach ($k in $vars.Keys) { $antes[$k] = [Environment]::GetEnvironmentVariable($k, 'Process'); [Environment]::SetEnvironmentVariable($k, $vars[$k], 'Process') }
    try {
        Start-Process -FilePath $pyTag -ArgumentList 'tools\asr_tagarela_server.py' -WorkingDirectory $Raiz -WindowStyle Hidden `
            -RedirectStandardOutput (Join-Path $Logs 'tagarela.out.log') -RedirectStandardError (Join-Path $Logs 'tagarela.err.log') | Out-Null
    } finally {
        foreach ($k in $vars.Keys) { [Environment]::SetEnvironmentVariable($k, $antes[$k], 'Process') }
    }
    $t0 = Get-Date
    while (-not (OuvidoNoAr) -and ((Get-Date) - $t0).TotalSeconds -lt 60) { Start-Sleep 1 }
    if (OuvidoNoAr) { Ok 'ouvido (Tagarela) no ar (porta 8011)'; $ouvidoLigado = $true }
}
if (-not $ouvidoLigado) { Write-Host '[--] ouvido (Tagarela) desligado: a Célia só entende texto' -ForegroundColor Yellow }

Write-Host '== 8. persona (Célia, 12393)'
if (Ouvindo 12393) {
    Ok 'persona no ar'
    # A série só aceita comandos do dono no perfil Conversa Autônoma (minecraft_series.py: profile_is_not_conversation_autonomous).
    try {
        $snap = Invoke-RestMethod -Uri http://127.0.0.1:12393/bridge/behavior/profile -TimeoutSec 5
        $perfil = if ($snap.PSObject.Properties['profile']) { $snap.profile } else { $snap.selected_profile }
        if ($perfil -ne 'conversation_autonomous') {
            Write-Host "[--] perfil anterior: $perfil, trocando para conversation_autonomous (a série exige)" -ForegroundColor Yellow
            Invoke-RestMethod -Uri http://127.0.0.1:12393/bridge/behavior/profile -Method Post -ContentType 'application/json' -Body '{"profile":"conversation_autonomous"}' -TimeoutSec 10 | Out-Null
            $snap = Invoke-RestMethod -Uri http://127.0.0.1:12393/bridge/behavior/profile -TimeoutSec 5
            $perfil = if ($snap.PSObject.Properties['profile']) { $snap.profile } else { $snap.selected_profile }
        }
        if ($perfil -eq 'conversation_autonomous') { Ok 'perfil Conversa Autônoma ativo' } else { Falha "perfil da persona é '$perfil' (a série recusa comandos fora do Conversa Autônoma)" }
    } catch { Falha "não consegui ler/ajustar o perfil da persona: $($_.Exception.Message)" }
} else {
    Write-Host '[--] persona desligada: ligue pelo LIGAR-CELIA.bat (é ela que tem a voz) e RODE ESTE SCRIPT DE NOVO' -ForegroundColor Yellow
    Write-Host '     (ele ajusta o perfil para Conversa Autônoma; sem isso a série recusa os comandos).' -ForegroundColor Yellow
    $Falhas++
}

Write-Host ''
if (-not $ouvidoLigado) { Write-Host '[--] ouvido (Tagarela) desligado: a Célia só entende texto' -ForegroundColor Yellow }
if ($Falhas -eq 0) {
    Write-Host "PRONTO ($Ambiente). Entre no Minecraft 1.21.10, servidor 127.0.0.1:$McPort, e fale: ""Célia, prepara a introdução""." -ForegroundColor Green
} else {
    Write-Host "ATENÇÃO: $Falhas passo(s) falharam acima. Não grave antes de resolver." -ForegroundColor Red; exit 1
}
