param([string]$OutputDirectory)
$ErrorActionPreference = 'Stop'
$sourceDir = Join-Path (Split-Path $PSScriptRoot) 'desktop\renderer'
if (!$OutputDirectory) { $OutputDirectory = $sourceDir }
$compiler = (Get-Command gcc.exe -ErrorAction Stop).Source
$targetTriple = & $compiler -dumpmachine
if ($LASTEXITCODE -ne 0 -or $targetTriple -notlike 'x86_64-w64-mingw32*') {
    throw 'Use the x86_64 MinGW-w64 gcc.exe (e.g. MSYS2 UCRT64).'
}
[void][IO.Directory]::CreateDirectory($OutputDirectory)
foreach ($target in @(@('warp_rgba.c','warp_rgba.dll'), @('kick_surface.c','kick_sampler.dll'))) {
    $source = Join-Path $sourceDir $target[0]
    $output = Join-Path $OutputDirectory $target[1]
    $temporary = Join-Path ([IO.Path]::GetTempPath()) ('atri-render-' + [guid]::NewGuid() + '.dll')
    try {
        & $compiler -std=c11 -O3 -m64 -shared -static-libgcc -o $temporary $source -lm
        if ($LASTEXITCODE -ne 0) { throw "Compilation failed for $source" }
        Copy-Item -LiteralPath $temporary -Destination $output -Force
        Write-Output $output
    } finally {
        if (Test-Path -LiteralPath $temporary) { Remove-Item -LiteralPath $temporary }
    }
}
