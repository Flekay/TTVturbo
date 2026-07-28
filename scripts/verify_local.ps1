<#
.SYNOPSIS
    TTVturbo lokale Verifikation.

.DESCRIPTION
    Fuehrt nacheinander aus:
      - Python-Compilecheck (compileall)
      - Backendtests (pytest, ohne GPU-E2E)
      - Frontend-Typecheck (tsc --noEmit)
      - Frontendtests (vitest run)
      - Frontendbuild (vite build)
      - Runtime-Diagnose (python -m ttvturbo.voice_clone.diagnostics)
      - FFmpeg-Diagnose

    Optional mit GPU-Test:
      .\scripts\verify_local.ps1 -IncludeGpuTest

    Der GPU-Test laeuft nur bei ausdruecklicher Aktivierung und laedt KEIN
    Modell; er prueft lediglich die CUDA-Runtime-Voraussetzungen. Die echte
    Qwen3-TTS-E2E-Generierung bleibt separaten manuellen Tests vorbehalten und
    wird hier nie ausgefuehrt.

    Exitcode 0 = alle Pflichtschritte PASS; != 0 bei mindestens einem FAIL.
    SKIPPED wird im Bericht ausgewiesen, gilt aber nicht als Fehler.

.PARAMETER IncludeGpuTest
    Schaltet die zusaetzliche GPU-Runtime-Diagnose ein.
#>

[CmdletBinding()]
param(
    [switch]$IncludeGpuTest
)

$ErrorActionPreference = "Continue"
$script:results = [System.Collections.Generic.List[pscustomobject]]::new()

function Invoke-Step {
    param(
        [string]$Name,
        [scriptblock]$Block,
        [switch]$Skippable
    )

    Write-Host ""
    Write-Host "==> $Name" -ForegroundColor Cyan
    # Default status; the block may overwrite $script:stepStatus to PASS/FAIL
    # explicitly when the verdict cannot be derived from $LASTEXITCODE alone
    # (e.g. FFmpeg-Diagnose, where `Select-Object -First 1` breaks the pipe
    # and leaves a non-zero exit code even on success).
    $script:stepStatus = $null
    try {
        & $Block
        if ($null -ne $script:stepStatus) {
            $script:results.Add([pscustomobject]@{ Step = $Name; Status = $script:stepStatus })
            if ($script:stepStatus -eq "PASS") {
                Write-Host "    PASS" -ForegroundColor Green
            } else {
                Write-Host "    FAIL" -ForegroundColor Red
            }
        } elseif ($LASTEXITCODE -eq 0) {
            $script:results.Add([pscustomobject]@{ Step = $Name; Status = "PASS" })
            Write-Host "    PASS" -ForegroundColor Green
        } else {
            $script:results.Add([pscustomobject]@{ Step = $Name; Status = "FAIL" })
            Write-Host "    FAIL (exit $LASTEXITCODE)" -ForegroundColor Red
        }
    } catch {
        $script:results.Add([pscustomobject]@{ Step = $Name; Status = "FAIL" })
        Write-Host "    FAIL (exception: $($_.Exception.Message))" -ForegroundColor Red
    }
}

function Skip-Step {
    param([string]$Name, [string]$Reason)
    $script:results.Add([pscustomobject]@{ Step = $Name; Status = "SKIPPED" })
    Write-Host ""
    Write-Host "==> $Name" -ForegroundColor Cyan
    Write-Host "    SKIPPED ($Reason)" -ForegroundColor Yellow
}

$repoRoot = Split-Path -Parent $PSScriptRoot
Set-Location $repoRoot
# conftest.py importiert `app` aus dem Repo-Root; ohne diese Variable schlagen
# die Backendtests mit ModuleNotFoundError fehl, wenn pytest aus einem anderen
# Verzeichnis aufgerufen wird.
$existingPp = if ($env:PYTHONPATH) { $env:PYTHONPATH } else { "" }
$env:PYTHONPATH = $repoRoot + [System.IO.Path]::PathSeparator + $existingPp

# --- 1. Python-Compilecheck -------------------------------------------------
Invoke-Step "Python-Compilecheck (compileall)" {
    python -m compileall -q ttvturbo tests
}

# --- 2. Backendtests --------------------------------------------------------
Invoke-Step "Backendtests (pytest, kein GPU-E2E)" {
    $env:TTVTURBO_RUN_QWEN_TTS_E2E = "0"
    pytest -q
}

# --- 3. Frontend-Typecheck --------------------------------------------------
Invoke-Step "Frontend-Typecheck (tsc --noEmit)" {
    npm --prefix frontend run typecheck
}

# --- 4. Frontendtests -------------------------------------------------------
Invoke-Step "Frontendtests (vitest run)" {
    npm --prefix frontend run test
}

# --- 5. Frontendbuild -------------------------------------------------------
Invoke-Step "Frontendbuild (vite build)" {
    npm --prefix frontend run build
}

# --- 6. Runtime-Diagnose ----------------------------------------------------
# Laedt kein Modell; prueft nur Python/soundfile/FFmpeg/Datenverzeichnis und
# (falls qwen_tts/torch installiert sind) CUDA-Verfuegbarkeit.
Invoke-Step "Runtime-Diagnose (python -m voice_clone.diagnostics)" {
    python -m ttvturbo.voice_clone.diagnostics
}

# --- 7. FFmpeg-Diagnose -----------------------------------------------------
Invoke-Step "FFmpeg-Diagnose" {
    $ff = Get-Command ffmpeg -ErrorAction SilentlyContinue
    $fp = Get-Command ffprobe -ErrorAction SilentlyContinue
    if ($ff -and $fp) {
        Write-Host "    ffmpeg : $($ff.Source)"
        Write-Host "    ffprobe: $($fp.Source)"
        # Capture full output so ffmpeg exits cleanly (piping to Select-Object
        # -First 1 breaks the pipe and leaves a non-zero $LASTEXITCODE).
        $ver = & ffmpeg -version 2>&1
        if ($ver -is [string]) { Write-Host "    $ver" } else { Write-Host "    $($ver[0])" }
        $script:stepStatus = "PASS"
    } else {
        if (-not $ff) { Write-Host "    ffmpeg nicht im PATH" -ForegroundColor Red }
        if (-not $fp) { Write-Host "    ffprobe nicht im PATH" -ForegroundColor Red }
        $script:stepStatus = "FAIL"
    }
}

# --- 8. Optionaler GPU-Test -------------------------------------------------
if ($IncludeGpuTest) {
    Invoke-Step "GPU-Runtime-Diagnose (-IncludeGpuTest)" {
        # Die normale Runtime-Diagnose meldet cuda_available/qwen_tts_importable
        # bereits. Mit dem Flag wird sie explizit noch einmal als eigener
        # Pflichtschritt ausgewiesen und mit nicht-null Exitcode bewertet,
        # damit ein fehlendes CUDA nicht unbemerkt bleibt.
        $report = python -m ttvturbo.voice_clone.diagnostics
        Write-Host $report
        if ($LASTEXITCODE -ne 0) {
            throw "ttvturbo.voice_clone.diagnostics exit $LASTEXITCODE"
        }
    }
} else {
    Skip-Step "GPU-Runtime-Diagnose" "nur mit -IncludeGpuTest aktiv"
}

# --- Zusammenfassung --------------------------------------------------------
Write-Host ""
Write-Host "================ Zusammenfassung ================" -ForegroundColor Cyan
$pass = @($script:results | Where-Object { $_.Status -eq "PASS" }).Count
$fail = @($script:results | Where-Object { $_.Status -eq "FAIL" }).Count
$skip = @($script:results | Where-Object { $_.Status -eq "SKIPPED" }).Count
$script:results | Format-Table -AutoSize
Write-Host ("PASS={0}  FAIL={1}  SKIPPED={2}" -f $pass, $fail, $skip)

if ($fail -gt 0) {
    Write-Host "Gesamt: FAIL" -ForegroundColor Red
    exit 1
} else {
    Write-Host "Gesamt: PASS" -ForegroundColor Green
    exit 0
}
