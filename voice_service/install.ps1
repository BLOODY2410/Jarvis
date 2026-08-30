$ErrorActionPreference = 'Stop'
$serviceRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
& (Join-Path (Split-Path -Parent $serviceRoot) 'install.ps1')
