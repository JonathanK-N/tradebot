# Installation du pont MT5 sur le VPS WINDOWS.  PowerShell en administrateur :
#   Set-ExecutionPolicy -Scope Process Bypass
#   .\scripts\windows\setup_bridge.ps1
#
# Prérequis (via l'app « Windows App » / Bureau à distance depuis le téléphone) :
#   1. Terminal MetaTrader 5 de TON broker installé, connecté au compte DÉMO,
#      Outils > Options > Expert Advisors > « Autoriser le trading algorithmique » coché.
#   2. Tailscale installé et connecté (https://tailscale.com/download/windows).
#   3. git installé (winget install Git.Git).
$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
Set-Location $Root

Write-Host "==> uv (gestionnaire Python, installé pour l'utilisateur courant)"
if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
    powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
    $env:Path = "$env:USERPROFILE\.local\bin;$env:Path"
}

Write-Host "==> Dépendances (dont le paquet MetaTrader5)"
uv sync --frozen --no-dev --extra mt5

if (-not (Test-Path ".env")) {
    Copy-Item ".env.example" ".env"
    Write-Host "⚠️  Remplis .env (MT5_LOGIN, MT5_PASSWORD, MT5_SERVER, REDIS_URL vers l'IP Tailscale du VPS Linux)"
    notepad .env
}
# .env lisible uniquement par l'utilisateur courant
icacls .env /inheritance:r /grant:r "$($env:USERNAME):(R,W)" | Out-Null

New-Item -ItemType Directory -Force "var" | Out-Null
[Environment]::SetEnvironmentVariable("TRADEBOT_LOG_FILE", "$Rootarridge.log", "User")

Write-Host "==> Test de connexion MT5"
uv run tradebot --pretty config-check | Select-String "mt5|redis"

Write-Host "==> Tâche planifiée : pont démarré à l'ouverture de session, relancé s'il s'arrête"
$action = New-ScheduledTaskAction -Execute "$env:USERPROFILE\.local\bin\uv.exe" `
    -Argument "run tradebot bridge" -WorkingDirectory $Root
$trigger = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME
$settings = New-ScheduledTaskSettingsSet -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1) `
    -ExecutionTimeLimit (New-TimeSpan -Days 3650) -AllowStartIfOnBatteries -StartWhenAvailable
Register-ScheduledTask -TaskName "tradebot-bridge" -Action $action -Trigger $trigger -Settings $settings `
    -RunLevel Limited -Force | Out-Null

Write-Host "==> Windows Update : redémarrages hors heures de marché (samedi)"
# Heures actives maximales (18 h) pour éviter les redémarrages en semaine ; mettre à jour le samedi.
Set-ItemProperty -Path "HKLM:\SOFTWARE\Microsoft\WindowsUpdate\UX\Settings" -Name ActiveHoursStart -Value 4
Set-ItemProperty -Path "HKLM:\SOFTWARE\Microsoft\WindowsUpdate\UX\Settings" -Name ActiveHoursEnd -Value 22

Write-Host @"

✅ Pont installé. IMPORTANT :
 - Le terminal MT5 et le pont exigent une session Windows OUVERTE : configure l'ouverture de
   session automatique (netplwiz) et NE ferme PAS la session RDP avec « Déconnexion »
   (ferme simplement la fenêtre de l'app).
 - Lancer maintenant : Start-ScheduledTask -TaskName tradebot-bridge
 - Logs : varridge.log  (ou en direct : uv run tradebot --pretty bridge)
"@
