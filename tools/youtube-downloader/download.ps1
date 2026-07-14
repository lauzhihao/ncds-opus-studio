$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$LinksFile = Join-Path $ScriptDir "links.txt"
$CookiesFile = Join-Path $ScriptDir "cookies.txt"
$OutputDir = Join-Path $ScriptDir "outputs"
$RuntimeDir = Join-Path $ScriptDir ".runtime"
$BinDir = Join-Path $ScriptDir "bin"
$ChromeProfileDir = if ($env:CHROME_PROFILE_DIR) { $env:CHROME_PROFILE_DIR } else { Join-Path $ScriptDir ".chrome-profile" }
$ChromeDebugPort = if ($env:CHROME_DEBUG_PORT) { $env:CHROME_DEBUG_PORT } else { "9222" }
$Mode = if ($args.Count -gt 0) { $args[0] } else { "download" }

New-Item -ItemType Directory -Force -Path $OutputDir, $RuntimeDir, $BinDir | Out-Null
if (-not (Test-Path $LinksFile)) { New-Item -ItemType File -Path $LinksFile | Out-Null }
if (-not (Test-Path $CookiesFile)) { New-Item -ItemType File -Path $CookiesFile | Out-Null }

function Get-ToolPath {
    param([string[]] $Names)

    foreach ($Name in $Names) {
        $Command = Get-Command $Name -ErrorAction SilentlyContinue
        if ($Command) {
            return $Command.Source
        }
    }
    return $null
}

function Get-UsefulLines {
    param([string] $Path)

    if (-not (Test-Path $Path)) {
        return @()
    }

    return @(Get-Content -LiteralPath $Path | Where-Object {
        $Line = $_.Trim()
        $Line -ne "" -and -not $Line.StartsWith("#")
    })
}

function Ensure-YtDlp {
    if ($env:YTDLP -and (Test-Path $env:YTDLP)) {
        return $env:YTDLP
    }

    $Existing = Get-ToolPath @("yt-dlp.exe", "yt-dlp")
    if ($Existing) {
        return $Existing
    }

    $Local = Join-Path $BinDir "yt-dlp.exe"
    if (-not (Test-Path $Local)) {
        Write-Host "[setup] downloading yt-dlp.exe"
        [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
        Invoke-WebRequest `
            -Uri "https://github.com/yt-dlp/yt-dlp/releases/latest/download/yt-dlp.exe" `
            -OutFile $Local
    }
    return $Local
}

function Show-Usage {
    Write-Host "Usage:"
    Write-Host "  download.bat                Download links.txt, opening Chrome login if needed"
    Write-Host "  download.bat download       Download links.txt"
    Write-Host "  download.bat login          Open Chrome login profile"
    Write-Host "  download.bat login-download Open Chrome login profile, wait, then download"
}

function Test-CookieFileHasContent {
    $Lines = Get-UsefulLines $CookiesFile
    return $Lines.Count -gt 0
}

function Get-ChromePath {
    $Existing = Get-ToolPath @("chrome.exe", "chrome")
    if ($Existing) {
        return $Existing
    }

    $Candidates = @(
        (Join-Path $env:ProgramFiles "Google\Chrome\Application\chrome.exe"),
        (Join-Path ${env:ProgramFiles(x86)} "Google\Chrome\Application\chrome.exe"),
        (Join-Path $env:LocalAppData "Google\Chrome\Application\chrome.exe")
    )

    foreach ($Candidate in $Candidates) {
        if ($Candidate -and (Test-Path $Candidate)) {
            return $Candidate
        }
    }

    return $null
}

function Open-LoginBrowser {
    New-Item -ItemType Directory -Force -Path $ChromeProfileDir | Out-Null

    $Chrome = Get-ChromePath
    if (-not $Chrome) {
        Write-Host "[error] Google Chrome was not found"
        exit 1
    }

    Write-Host "[login] profile: $ChromeProfileDir"
    Write-Host "[login] remote debugging port: $ChromeDebugPort"

    $ChromeArgs = @(
        "--user-data-dir=$ChromeProfileDir",
        "--remote-debugging-port=$ChromeDebugPort",
        "--no-first-run",
        "https://accounts.google.com/",
        "https://www.youtube.com/"
    )
    Start-Process -FilePath $Chrome -ArgumentList $ChromeArgs | Out-Null
}

function Wait-ForLogin {
    Read-Host "[login] finish Google/YouTube login in Chrome, then press Enter here to continue" | Out-Null
}

function Ensure-LoginSource {
    if ((Test-CookieFileHasContent) -or (Test-Path $ChromeProfileDir)) {
        return
    }

    Write-Host "[login] no cookies.txt content or saved Chrome profile found; opening Chrome login."
    Open-LoginBrowser
    Wait-ForLogin
}

function Build-CookieArgs {
    $Lines = Get-UsefulLines $CookiesFile
    if ($Lines.Count -eq 0) {
        if (Test-Path $ChromeProfileDir) {
            return @("--cookies-from-browser", "chrome:$ChromeProfileDir")
        }
        return @()
    }

    $IsNetscape = $false
    $FirstLine = (Get-Content -LiteralPath $CookiesFile -TotalCount 1)
    if ($FirstLine -match "Netscape HTTP Cookie File") {
        $IsNetscape = $true
    } else {
        foreach ($Line in $Lines) {
            if (($Line -split "`t").Count -ge 7) {
                $IsNetscape = $true
                break
            }
        }
    }

    if ($IsNetscape) {
        return @("--cookies", $CookiesFile)
    }

    $CookieJar = Join-Path $RuntimeDir "cookies.netscape.txt"
    Set-Content -LiteralPath $CookieJar -Encoding ASCII -Value "# Netscape HTTP Cookie File"

    $Raw = ($Lines -join ";")
    foreach ($Part in ($Raw -split ";")) {
        $Trimmed = $Part.Trim()
        if ($Trimmed -eq "" -or -not $Trimmed.Contains("=")) {
            continue
        }

        $Index = $Trimmed.IndexOf("=")
        $Name = $Trimmed.Substring(0, $Index).Trim()
        $Value = $Trimmed.Substring($Index + 1).Trim()
        if ($Name -eq "") {
            continue
        }

        $Secure = "FALSE"
        if ($Name.StartsWith("__Secure-") -or $Name -in @("SID", "HSID", "SSID", "APISID", "SAPISID", "SIDCC")) {
            $Secure = "TRUE"
        }

        Add-Content -LiteralPath $CookieJar -Encoding ASCII -Value ".youtube.com`tTRUE`t/`t$Secure`t0`t$Name`t$Value"
    }

    return @("--cookies", $CookieJar)
}

switch ($Mode) {
    "login" {
        Open-LoginBrowser
        exit 0
    }
    "login-download" {
        Open-LoginBrowser
        Wait-ForLogin
    }
    "download" {
        Ensure-LoginSource
    }
    "help" {
        Show-Usage
        exit 0
    }
    "-h" {
        Show-Usage
        exit 0
    }
    "--help" {
        Show-Usage
        exit 0
    }
    default {
        Show-Usage
        exit 1
    }
}

function Build-JsArgs {
    $Node = Get-ToolPath @("node.exe", "node")
    if ($Node) {
        return @("--js-runtimes", "node:$Node", "--remote-components", "ejs:github")
    }

    $Deno = Get-ToolPath @("deno.exe", "deno")
    if ($Deno) {
        return @("--js-runtimes", "deno:$Deno", "--remote-components", "ejs:github")
    }

    Write-Host "[warn] no node or deno found; YouTube challenge solving may fail"
    return @("--remote-components", "ejs:github")
}

$CleanLinks = Join-Path $RuntimeDir "links.clean.txt"
$LinkLines = Get-UsefulLines $LinksFile
if ($LinkLines.Count -eq 0) {
    Write-Host "[error] links.txt has no URLs"
    exit 1
}
Set-Content -LiteralPath $CleanLinks -Encoding ASCII -Value $LinkLines

$YtDlp = Ensure-YtDlp
$Ffmpeg = Get-ToolPath @("ffmpeg.exe", "ffmpeg")
if (-not $Ffmpeg) {
    $LocalFfmpeg = Join-Path $BinDir "ffmpeg.exe"
    if (Test-Path $LocalFfmpeg) {
        $Ffmpeg = $LocalFfmpeg
    } else {
        Write-Host "[warn] ffmpeg not found; merged downloads may fail"
    }
}

$YtArgs = @()
$YtArgs += (Build-CookieArgs)
$YtArgs += (Build-JsArgs)
if ($Ffmpeg) {
    $YtArgs += @("--ffmpeg-location", (Split-Path -Parent $Ffmpeg))
}
$YtArgs += @(
    "-f", "bestvideo+bestaudio/best",
    "--merge-output-format", "mkv",
    "-a", $CleanLinks,
    "-o", (Join-Path $OutputDir "%(id)s.%(ext)s")
)

Write-Host "[run] downloading to $OutputDir"
& $YtDlp @YtArgs
exit $LASTEXITCODE
