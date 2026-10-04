# AI Transcript for Windows

AI Transcript is a Windows desktop lecture assistant that captures the audio currently playing through the selected Windows output device, transcribes it locally in short chunks, autosaves timestamped sessions, and optionally turns a completed transcript into structured notes with Google Gemini.

This is a real system-audio implementation—not an upload-only mockup. On Windows it opens a **WASAPI loopback** endpoint through SoundCard, so Chrome, Edge, YouTube, VLC, course players, and other applications using the selected output device can be captured without playing sound through a microphone.

## What is included

### Working MVP

- Windows 10/11 desktop UI built with PySide6
- WASAPI system-output loopback capture (the microphone is not opened)
- Separate capture and inference threads, so audio continues to be buffered while Whisper is transcribing
- Local multilingual speech-to-text with `faster-whisper`
- Auto Detect, English, Urdu, and Hindi controls; the provider supports Whisper's other languages too
- Progressive transcript updates every few seconds
- Timestamped segments and session continuation
- Crash-resistant autosave to SQLite, plain text, and Markdown after every completed segment
- Session create, rename, open, edit, delete, and export
- Transcript search, pause/resume, system tray controls, and app shortcuts
- TXT and Markdown transcript export

### AI notes and export

- Gemini API key from `GEMINI_API_KEY` or Windows Credential Manager
- Clean Transcript, Smart Notes, Summary, Detailed Notes, and Custom Prompt modes
- English, Roman Urdu, Hindi, Urdu, Hinglish, or source-language output
- Grounding instructions that preserve technical English terms and prohibit invented details
- Long-transcript map/reduce processing instead of one unbounded request
- Locally saved Markdown notes and MD/TXT/DOCX export

## Privacy behavior

1. **Audio stays local.** Local Whisper receives audio in memory. Audio chunks are not written to disk and are not sent to Gemini.
2. **The physical microphone is not used.** `WindowsWasapiLoopbackCapture` lists and opens only devices marked as loopback endpoints.
3. **Nothing is uploaded silently.** Transcript text is sent to Gemini only after the user clicks **Generate with Gemini**.
4. Transcripts, notes, settings, and the session database are stored under the current Windows user's application-data directory.
5. A key entered in Settings is stored by `keyring` in **Windows Credential Manager**, not in `settings.json` or source code. `GEMINI_API_KEY` takes precedence when present.

Default data location:

```text
%LOCALAPPDATA%\AI Transcript\
├── app.log
├── settings.json              # no API key
├── sessions.db
└── sessions\
    └── <session-uuid>\
        ├── transcript.txt
        ├── transcript.md
        ├── edited-transcript.txt   # only after manual editing
        └── notes.md
```

The exact directory reported by `platformdirs` can vary with Windows/Python packaging. Set `AI_TRANSCRIPT_DATA_DIR` if a custom location is required.

## Requirements

- 64-bit Windows 10 or Windows 11
- 64-bit Python **3.10 or 3.11** for source installation (3.11 recommended)
- An active playback device (speakers, headphones, HDMI audio, etc.)
- About 1–5 GB of free disk space, depending on the chosen Whisper model
- Internet on first use to download a Whisper model; transcription is offline afterward
- Internet and a Gemini API key only for AI notes

CPU transcription works without a GPU. An NVIDIA CUDA setup can improve speed, but the required CUDA runtime compatibility depends on the installed `ctranslate2` wheel. Leave **Compute device = Automatic** unless CUDA is already working.

## Install and run on Windows

### Easiest source setup (PowerShell)

1. Install 64-bit Python 3.11 from [python.org](https://www.python.org/downloads/windows/). Enable **Add Python to PATH**.
2. Open PowerShell in this project folder.
3. If scripts are blocked for this one terminal, run:

   ```powershell
   Set-ExecutionPolicy -Scope Process Bypass
   ```

4. Create the environment and install dependencies:

   ```powershell
   .\scripts\setup.ps1
   ```

5. Start the app:

   ```powershell
   .\.venv\Scripts\python.exe -m ai_transcript
   ```

Equivalent manual commands:

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip wheel
python -m pip install -e .
python -m ai_transcript
```

### First transcription

1. Open **Settings**. Start with the `small` model, **Automatic** compute device, and **Default Windows output**.
2. Play a YouTube/video/VLC audio source through that Windows output device.
3. Choose **Auto Detect**, **English**, **Urdu**, or **Hindi**.
4. Click **Start**. The first run downloads the chosen model and can take several minutes. The status changes from **Loading model** to **Listening** when WASAPI capture starts.
5. Use **Pause/Resume** or **Stop**. Every completed segment has already been autosaved.

The default five-second chunk is a latency/accuracy trade-off. Smaller values update sooner but can reduce sentence context. The setting supports 2–20 seconds.

## Configure Gemini

Gemini is optional; local transcription does not need an API key.

### Option 1: Windows Credential Manager

Open **Settings**, paste the key into **Gemini API key**, click **Test connection**, then **Save**. The secret is stored through Windows Credential Manager.

### Option 2: environment variable

Set it for the current PowerShell window:

```powershell
$env:GEMINI_API_KEY = "your-key-here"
.\.venv\Scripts\python.exe -m ai_transcript
```

Or save it for future terminals (restart the terminal afterward):

```powershell
setx GEMINI_API_KEY "your-key-here"
```

Never add a real key to `.env.example`, Git, a screenshot, or the frontend source.

To create notes, collect a transcript, open **AI Notes**, select a processing mode and output language, then click **Generate with Gemini**. The app divides long transcripts into bounded sections, processes them, and performs a final grounded synthesis.

## Keyboard and tray controls

These shortcuts work while the application window has focus:

```text
Ctrl + Shift + S    Start
Ctrl + Shift + P    Pause / Resume
Ctrl + Shift + X    Stop
Ctrl + F            Search transcript
```

Closing the window minimizes it to the notification area by default. The tray menu supports Open, Pause/Resume, Stop, and Exit. These are app shortcuts, not system-wide global hotkeys; global configurable hotkeys can be added as a later provider without changing the capture pipeline.

## Build a Windows executable

Run the build **on Windows** in the configured project environment:

```powershell
Set-ExecutionPolicy -Scope Process Bypass
.\scripts\setup.ps1
.\scripts\build.ps1
```

Output:

```text
dist\AI Transcript\AI Transcript.exe
```

Distribute the entire `dist\AI Transcript` directory, not only the `.exe`. Faster Whisper/CT2 native libraries and Qt plugins live beside the executable. The Whisper model is deliberately not bundled; it is downloaded into the user's normal model cache on first use. Code-sign the executable/installer before public distribution to avoid Windows SmartScreen warnings. An installer can later wrap this folder with Inno Setup, WiX, or MSIX.

## Architecture

```text
Windows playback applications
          │
          ▼
WindowsWasapiLoopbackCapture      src/ai_transcript/audio/
          │ float32 blocks
          ▼
LiveTranscriptionPipeline         src/ai_transcript/services/pipeline.py
   capture thread + bounded queue + inference thread
          │
          ▼
TranscriptionProvider             src/ai_transcript/transcription/base.py
          └── LocalWhisperProvider (replaceable by a cloud provider)
          │ timestamped segments
          ├── SessionRepository (SQLite + fsynced TXT/MD autosave)
          └── Qt live transcript
                    │ explicit Generate click
                    ▼
             GeminiNotesService
                    │ chunk → process → grounded synthesis
                    ▼
             notes.md / DOCX export
```

Provider interfaces deliberately isolate audio capture and speech-to-text. A cloud STT provider can implement `TranscriptionProvider` without changing session storage or the UI workflow.

## Accuracy and model selection

| Model | Approximate trade-off | Suggested use |
|---|---|---|
| `tiny` | Fastest, least accurate | Older CPU, rough English notes |
| `base` | Fast | Lightweight use |
| `small` | Balanced default | English/Urdu/Hindi on a modern CPU |
| `medium` | Better multilingual accuracy, slower | Strong CPU or GPU |
| `large-v3` | Best included accuracy, high RAM/VRAM | Capable GPU/workstation |

Whisper supports multilingual and code-switched speech, but no STT model guarantees names, numbers, rare terminology, or difficult mixed-language audio. Manual language selection can improve consistency when automatic detection has too little speech. Gemini cleanup is grounded but should still be reviewed for important work.

## Windows/WASAPI limitations

- Loopback captures the **selected output endpoint**, not magically every hardware endpoint at once. If Chrome plays through speakers while VLC plays through a USB headset, select the endpoint you want or make it the Windows default.
- Changing/disconnecting the playback device while recording can stop capture. Stop, choose the new device in Settings, and start again.
- Apps using exclusive audio mode or protected audio paths may block loopback. Disable exclusive mode for that endpoint or use shared mode.
- DRM-protected content may intentionally prevent capture. This app does not bypass DRM.
- Bluetooth profile changes and HDMI reconnects can replace an endpoint mid-session.
- System sounds on the same output endpoint are included. Per-process capture is a different Windows API and is not used in this MVP.
- The app does not record audio files; it keeps only transcript text. This improves privacy but means missed audio cannot be retranscribed later.

## Troubleshooting

### “No system audio detected”

- Verify Windows Volume Mixer shows movement and the video is not muted.
- Ensure the app's selected output matches the output used by the playing application.
- Select **Default Windows output**, then stop and restart transcription.
- Disconnect/reconnect Bluetooth/HDMI devices before starting.
- Check that Windows Audio is running and disable exclusive mode in the endpoint's advanced sound settings.

### Whisper model cannot load

- The first run needs internet access to download model files.
- Choose `tiny`, `base`, or `small` if RAM is limited.
- Leave compute device on **Automatic**. Selecting CUDA without a compatible NVIDIA runtime will fail.
- Check `%LOCALAPPDATA%\AI Transcript\app.log` (or the custom data directory) for diagnostics. API keys and audio are never logged.

### Transcription falls behind

The queue holds roughly two minutes of 250 ms audio blocks. If inference remains slower than playback and the queue fills, the oldest audio is dropped and a clear warning is shown. Select a smaller model, use CUDA, close intensive applications, or increase chunk size.

### Gemini fails

Use **Settings → Test connection**, verify the key/model, internet connection, and API quota. A Gemini failure does not affect the locally saved raw transcript.

## Development and tests

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
.\.venv\Scripts\python.exe -m pytest
.\.venv\Scripts\ruff.exe check src tests
```

The persistence, chunking, resampling, and utility tests run cross-platform. Actual WASAPI loopback integration must be tested on Windows with a real playback endpoint.

## Project structure

```text
.
├── pyproject.toml
├── requirements.txt
├── scripts/
│   ├── setup.ps1
│   └── build.ps1
├── src/ai_transcript/
│   ├── app.py
│   ├── config.py
│   ├── models.py
│   ├── audio/
│   │   ├── base.py
│   │   └── windows_loopback.py
│   ├── transcription/
│   │   ├── base.py
│   │   └── whisper_local.py
│   ├── services/
│   │   ├── pipeline.py
│   │   ├── gemini_service.py
│   │   └── export_service.py
│   ├── storage/repository.py
│   └── ui/
│       ├── main_window.py
│       ├── settings_dialog.py
│       └── styles.py
└── tests/
```

## Responsible use

Only transcribe media and conversations you are legally allowed to capture. Recording/transcription consent laws vary by location, and meeting platforms or protected media may impose additional terms.
