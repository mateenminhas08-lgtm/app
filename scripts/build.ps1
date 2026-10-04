$ErrorActionPreference = "Stop"

if (-not (Test-Path .\.venv\Scripts\python.exe)) {
    throw "Virtual environment not found. Run scripts\setup.ps1 first."
}

& .\.venv\Scripts\python.exe -m pip install "pyinstaller>=6.11,<7"

# Collect metadata/data for ML and API packages that use dynamic imports.
& .\.venv\Scripts\pyinstaller.exe `
    --noconfirm `
    --clean `
    --windowed `
    --name "AI Transcript" `
    --collect-all faster_whisper `
    --collect-all ctranslate2 `
    --collect-all tokenizers `
    --collect-all soundcard `
    --collect-all google.genai `
    --hidden-import keyring.backends.Windows `
    src\ai_transcript\__main__.py

Write-Host ""
Write-Host "Build complete: dist\AI Transcript\AI Transcript.exe" -ForegroundColor Green
Write-Host "Copy the entire 'dist\AI Transcript' folder when distributing the app."
