# Puts a launcher for run-bot.bat in the current user's Startup folder, so the
# attendance bot starts at every logon. run-bot.bat's own loop keeps it alive
# after a crash, so no "restart on failure" scheduler is needed. No admin rights
# required (unlike schtasks, which was denied).
#
# Run once, from this folder:
#   powershell -ExecutionPolicy Bypass -File install-autostart.ps1
# Undo: delete the .cmd it reports below.

$bat     = Join-Path $PSScriptRoot 'run-bot.bat'
$startup = [Environment]::GetFolderPath('Startup')
$link    = Join-Path $startup 'PNL Attendance Bot.cmd'

Set-Content -Path $link -Value "start `"PNL Attendance Bot`" /min `"$bat`"" -Encoding ascii
Write-Host "Installed: $link"
Write-Host "It will run at next logon. Start it now with:  `"$bat`""
