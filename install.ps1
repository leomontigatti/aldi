# Installs Aldi on Windows, from PowerShell:
#   powershell -ExecutionPolicy ByPass -c "irm https://raw.githubusercontent.com/leomontigatti/aldi/main/install.ps1 | iex"
# It installs uv, then Aldi as a uv tool from the latest Release tag with uv.lock's pins, then `aldi setup` saves
# the reporting token and creates the shortcuts on the desktop and in the Start menu. A step that fails files an
# Error Report (ADR 4) that names only the step and the system. Running it again reinstalls the latest Release.
#
# ALDI_REPORT_TOKEN skips the question for the token; ALDI_SOURCE, a checkout, is installed instead of the
# latest Release (CI's test of this script). Run through `iex`, so it never calls `exit`: that would close the
# window it was pasted into.

function Install-Aldi {
    # No `$ErrorActionPreference = 'Stop'`: Windows PowerShell would then stop on whatever uv prints to stderr.
    $ProgressPreference = 'SilentlyContinue'
    [Net.ServicePointManager]::SecurityProtocol = [Net.ServicePointManager]::SecurityProtocol -bor [Net.SecurityProtocolType]::Tls12
    $repo = 'leomontigatti/aldi'
    $uvVersion = '0.13.0'

    $token = "$env:ALDI_REPORT_TOKEN".Trim()
    while (-not $token) {
        $secure = Read-Host -AsSecureString 'Token para los informes de errores'
        $token = [Net.NetworkCredential]::new('', $secure).Password.Trim()
    }

    function Send-Report([string]$step) {
        $os = [Environment]::OSVersion.Version
        $system = "Windows $($os.Major).$($os.Minor) (build $($os.Build)), $env:PROCESSOR_ARCHITECTURE"
        $body = "Filed by the install line on its own (ADR 4).`n`n- Problem: Install failed: $step`n" +
            "- System: $system`n`nFingerprint: ``install-$step```n"
        $issue = @{ title = "Error Report: Install failed: $step"; body = $body; labels = @('bug', 'needs-triage', 'source:app') }
        try {
            Invoke-RestMethod -Method Post -Uri "https://api.github.com/repos/$repo/issues" -ContentType 'application/json' `
                -Headers @{ Authorization = "Bearer $token"; Accept = 'application/vnd.github+json'; 'User-Agent' = 'aldi-install' } `
                -Body ($issue | ConvertTo-Json) -ErrorAction Stop | Out-Null
            return $true
        } catch {
            return $false
        }
    }

    function Fail([string]$step, [string]$why) {
        if (Send-Report $step) {
            Write-Host 'Ya se envió un informe para que lo arreglen.'
        } else {
            Write-Host 'No se pudo enviar el informe del error: avísale a quien te instaló Aldi.'
        }
        throw "No se pudo instalar Aldi: $why."
    }

    $tmp = Join-Path ([IO.Path]::GetTempPath()) ([Guid]::NewGuid().ToString())
    New-Item -ItemType Directory -Path $tmp -ErrorAction Stop | Out-Null
    try {
        $uv = (Get-Command uv -ErrorAction SilentlyContinue).Source
        if (-not $uv) {
            Write-Host 'Instalando uv…'
            try {
                $env:UV_INSTALL_DIR = Join-Path $HOME '.local\bin'
                Invoke-RestMethod "https://astral.sh/uv/$uvVersion/install.ps1" -ErrorAction Stop | Invoke-Expression
            } catch {
                Fail 'uv' 'no se pudo instalar uv'
            } finally {
                Remove-Item Env:UV_INSTALL_DIR -ErrorAction SilentlyContinue
            }
            $uv = Join-Path $HOME '.local\bin\uv.exe'
            if (-not (Test-Path $uv)) { Fail 'uv' 'no se pudo instalar uv' }
        }

        $constraints = Join-Path $tmp 'constraints.txt'
        if ($env:ALDI_SOURCE) {
            $spec = $env:ALDI_SOURCE
            Copy-Item (Join-Path $env:ALDI_SOURCE 'install-constraints.txt') $constraints -ErrorAction Stop
        } else {
            try {
                $tag = (Invoke-RestMethod "https://api.github.com/repos/$repo/releases/latest" -ErrorAction Stop).tag_name
                Invoke-WebRequest -UseBasicParsing -ErrorAction Stop -OutFile $constraints `
                    "https://raw.githubusercontent.com/$repo/$tag/install-constraints.txt"
            } catch {
                Fail 'release' 'no se encontró la última versión'
            }
            $spec = "aldi @ https://github.com/$repo/archive/refs/tags/$tag.tar.gz"
        }

        Write-Host 'Instalando Aldi…'
        & $uv tool install --force --managed-python --python 3.12 --constraints $constraints $spec
        if ($LASTEXITCODE -ne 0) { Fail 'tool-install' 'uv no pudo instalarlo' }

        # `aldi setup` files its own Error Report when it fails.
        $aldi = Join-Path (& $uv tool dir --bin) 'aldi.exe'
        $env:ALDI_REPORT_TOKEN = $token
        try {
            & $aldi setup
        } finally {
            Remove-Item Env:ALDI_REPORT_TOKEN -ErrorAction SilentlyContinue
        }
        if ($LASTEXITCODE -ne 0) { throw 'No se pudo terminar de instalar Aldi.' }
    } finally {
        Remove-Item -Recurse -Force $tmp -ErrorAction SilentlyContinue
    }
}

Install-Aldi
