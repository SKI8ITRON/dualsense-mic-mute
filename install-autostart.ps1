# Adds "DualSense Mute" to Windows startup (shortcut in the Startup folder) and starts it now.
# Uninstall: .\install-autostart.ps1 -Remove
#   stops the script, removes the shortcut, unmutes the mic and turns the LED off.
param([switch]$Remove)

$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$script = Join-Path $here 'dualsense_mute.py'
$lnk = Join-Path ([Environment]::GetFolderPath('Startup')) 'DualSense Mute.lnk'

if ($Remove) {
    Remove-Item $lnk -ErrorAction SilentlyContinue
    $procs = Get-CimInstance Win32_Process -Filter "Name='pythonw.exe' OR Name='python.exe'" |
        Where-Object CommandLine -match 'dualsense_mute\.py'
    foreach ($p in $procs) { Stop-Process -Id $p.ProcessId -Force -ErrorAction SilentlyContinue }
    foreach ($p in $procs) { Wait-Process -Id $p.ProcessId -Timeout 5 -ErrorAction SilentlyContinue }
    # a killed script can't clean up after itself, so undo its mute and LED now
    $py = (Get-Command python.exe -ErrorAction SilentlyContinue).Source
    if ($py) { & $py $script --release }
    "Autostart removed, script stopped, mic unmuted."
    "You can now delete this folder: $here"
    return
}

$pyw = (Get-Command pythonw.exe -ErrorAction Stop).Source
$s = (New-Object -ComObject WScript.Shell).CreateShortcut($lnk)
$s.TargetPath = $pyw
$s.Arguments = '"' + $script + '"'
$s.WorkingDirectory = $here
$s.Description = 'DualSense mic mute button + LED'
$s.Save()
Start-Process $pyw -ArgumentList $s.Arguments -WorkingDirectory $here
"Autostart: $lnk"
