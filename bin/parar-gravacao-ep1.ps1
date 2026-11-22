<#
parar-gravacao-ep1.ps1 — desliga corpo, Jev e (opcional) o servidor Minecraft do ambiente. Nunca apaga nada.
O servidor é parado por RCON "stop" (salva o mundo), nunca matando o Java. A persona (voz) não é tocada.

  powershell -ExecutionPolicy Bypass -File bin\parar-gravacao-ep1.ps1                 # ensaio: corpo + Jev + servidor
  powershell -ExecutionPolicy Bypass -File bin\parar-gravacao-ep1.ps1 -ManterServidor
  powershell -ExecutionPolicy Bypass -File bin\parar-gravacao-ep1.ps1 -Ambiente oficial
#>
[CmdletBinding()]
param([ValidateSet('ensaio', 'oficial')][string]$Ambiente = 'ensaio', [switch]$ManterServidor)
$ErrorActionPreference = 'Stop'
$Raiz = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$Celia = (Resolve-Path (Join-Path $Raiz '..\..')).Path
$Py = Join-Path $Raiz '.venv\Scripts\python.exe'
$ServerDir = if ($Ambiente -eq 'oficial') { Join-Path $Celia 'Serie-Minecraft\Servidor' } else { Join-Path $Celia 'Serie-Minecraft\Teste-Windows' }

function PararPorta($porta, $nome) {
    $donos = Get-NetTCPConnection -LocalPort $porta -State Listen -ErrorAction SilentlyContinue | Select-Object -ExpandProperty OwningProcess -Unique
    if (-not $donos) { Write-Host "[--] $nome já estava parado"; return }
    foreach ($id in $donos) {
        $p = Get-CimInstance Win32_Process -Filter "ProcessId=$id"
        # Só derruba o que é nosso: comando com o caminho deste projeto.
        if ($p.CommandLine -notlike "*$Raiz*" -and $p.CommandLine -notlike '*body_server.js*') { Write-Host "[!!] porta $porta é de outro programa ($($p.Name)); não mexi" -ForegroundColor Red; continue }
        $pai = Get-CimInstance Win32_Process -Filter "ProcessId=$($p.ParentProcessId)" -ErrorAction SilentlyContinue
        Stop-Process -Id $id -Force -ErrorAction SilentlyContinue
        if ($pai -and $pai.CommandLine -like '*start_body.py*') { Stop-Process -Id $pai.ProcessId -Force -ErrorAction SilentlyContinue }
        Write-Host "[OK] $nome parado (PID $id)" -ForegroundColor Green
    }
}
# O supervisor do corpo relança o node: pare o supervisor primeiro.
Get-CimInstance Win32_Process -Filter "Name='python.exe'" | Where-Object { $_.CommandLine -like '*start_body.py*' -and $_.CommandLine -like "*$Raiz*" } |
    ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue; Write-Host "[OK] supervisor do corpo parado (PID $($_.ProcessId))" -ForegroundColor Green }
PararPorta 8080 'corpo'
PararPorta 8784 'Jev'

if (-not $ManterServidor) {
    $env:PYTHONUTF8 = '1'
    & $Py -c @"
import sys, pathlib
sys.path.insert(0, str(pathlib.Path(sys.argv[1]) / 'integrations' / 'minecraft'))
from celia_minecraft.rcon_gateway import RconClient, RconSettings
try:
    with RconClient(RconSettings.from_properties(pathlib.Path(sys.argv[2]) / 'server.properties')) as c:
        c.command('save-all flush'); c.command('stop')
    print('[OK] servidor recebeu save-all + stop por RCON')
except Exception as e:
    print('[--] servidor não respondeu ao RCON (já parado?):', type(e).__name__)
"@ $Raiz $ServerDir
}
