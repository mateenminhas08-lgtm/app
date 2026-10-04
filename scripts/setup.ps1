$ErrorActionPreference = "Stop"

Write-Host "Setting up AI Transcript..." -ForegroundColor Cyan

if (-not (Get-Command py -ErrorAction SilentlyContinue)) {
    throw "Python launcher not found. Install 64-bit Python 3.11 from python.org and enable 'Add Python to PATH'."
}

py -3.11 -m venv .venv
& .\.venv\Scripts\python.exe -m pip install --upgrade pip wheel
& .\.venv\Scripts\python.exe -m pip install -e .

Write-Host ""
Write-Host "Setup complete." -ForegroundColor Green
Write-Host "Run the app with: .\.venv\Scripts\python.exe -m ai_transcript"
Write-Host "The first transcription downloads the selected Whisper model."
