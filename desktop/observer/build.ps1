param(
    [Parameter(Mandatory=$true)][string]$Contracts
)
$ErrorActionPreference='Stop'
$framework=Join-Path $env:windir 'Microsoft.NET\Framework64\v4.0.30319'
$references=@('System.Drawing.dll','System.Core.dll','WPF\UIAutomationClient.dll',
    'WPF\UIAutomationTypes.dll','WPF\WindowsBase.dll','System.Runtime.dll',
    'System.Runtime.WindowsRuntime.dll','System.Runtime.InteropServices.WindowsRuntime.dll') |
    ForEach-Object { Join-Path $framework $_ }
$metadata=Get-ChildItem -LiteralPath $Contracts -File -Filter '*.winmd'
if(-not $metadata){throw 'Pass the ref/netstandard2.0 directory of Microsoft.Windows.SDK.Contracts.'}
$references+=@($metadata | Select-Object -ExpandProperty FullName)
$arguments=$references | ForEach-Object {"/reference:$_"}
$working=Join-Path ([IO.Path]::GetTempPath()) ('atri-observer-'+[guid]::NewGuid().ToString('N')+'.exe')
try {
    & (Join-Path $framework 'csc.exe') /nologo /utf8output /codepage:65001 /optimize+ /platform:x64 /target:exe "/out:$working" @arguments (Join-Path $PSScriptRoot 'ScreenContextWorker.cs')
    if($LASTEXITCODE -ne 0){throw "Compiler exited with $LASTEXITCODE"}
    Copy-Item -LiteralPath $working -Destination (Join-Path $PSScriptRoot 'ScreenContextWorker.exe')
} finally {
    if(Test-Path -LiteralPath $working){Remove-Item -LiteralPath $working}
}
