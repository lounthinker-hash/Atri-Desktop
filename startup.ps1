param([ValidateSet('Enable','Disable','Status')][string]$Mode='Status')
$ErrorActionPreference = 'Stop'
$projectDirectory = [IO.Path]::GetFullPath($PSScriptRoot)
$appFile = Join-Path $projectDirectory 'desktop_atri.pyw'
$startupDirectory = [Environment]::GetFolderPath([Environment+SpecialFolder]::Startup)
$linkFile = Join-Path $startupDirectory 'Atri - My Dear Moments.lnk'
$shell = New-Object -ComObject WScript.Shell
$launcher = $shell.CreateShortcut($linkFile)
$appArgument = '"' + $appFile + '"'
$owned = (Test-Path -LiteralPath $linkFile) -and $launcher.Arguments -eq $appArgument
if ($Mode -eq 'Status') {
    if ($owned) { 'Enabled' } else { 'Disabled' }
    return
}
if ((Test-Path -LiteralPath $linkFile) -and !$owned) {
    throw "A different startup shortcut already uses $linkFile"
}
if ($Mode -eq 'Disable') {
    if ($owned) { Remove-Item -LiteralPath $linkFile }
    'Disabled'
    return
}
$pythonWindow = Join-Path $projectDirectory '.venv\Scripts\pythonw.exe'
if (!(Test-Path -LiteralPath $pythonWindow) -and $env:VIRTUAL_ENV) {
    $pythonWindow = Join-Path $env:VIRTUAL_ENV 'Scripts\pythonw.exe'
}
if (!(Test-Path -LiteralPath $pythonWindow)) {
    $pythonWindow = (Get-Command pythonw.exe -ErrorAction Stop).Source
}
if (!(Test-Path -LiteralPath $appFile)) { throw "Missing application: $appFile" }
$launcher.TargetPath = $pythonWindow
$launcher.Arguments = $appArgument
$launcher.WorkingDirectory = $projectDirectory
$launcher.Description = 'Start the local Atri desktop companion at sign-in'
$launcher.Save()
$saved = $shell.CreateShortcut($linkFile)
if ($saved.Arguments -ne $appArgument -or $saved.TargetPath -ne $pythonWindow) {
    throw 'Could not verify the startup shortcut'
}
'Enabled'
