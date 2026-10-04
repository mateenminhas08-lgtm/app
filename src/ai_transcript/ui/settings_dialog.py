from __future__ import annotations

import os

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
)

from ai_transcript.audio.windows_loopback import WindowsWasapiLoopbackCapture
from ai_transcript.config import AppSettings, SettingsStore
from ai_transcript.services.gemini_service import GeminiError, GeminiNotesService


class SettingsDialog(QDialog):
    def __init__(
        self,
        store: SettingsStore,
        settings: AppSettings,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Settings — AI Transcript")
        self.setMinimumWidth(540)
        self.store = store
        self.settings = settings
        self._api_key_edited = False
        self._build_ui()
        self._load_values()

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(22, 22, 22, 18)
        root.setSpacing(16)

        privacy = QLabel(
            "Audio is transcribed locally. Audio is never sent to Gemini; only transcript text "
            "is sent when you click Generate."
        )
        privacy.setWordWrap(True)
        privacy.setObjectName("PrivacyPill")
        root.addWidget(privacy)

        transcription_group = QGroupBox("Local transcription")
        transcription_form = QFormLayout(transcription_group)
        transcription_form.setSpacing(11)

        self.provider_combo = QComboBox()
        self.provider_combo.addItem("Local Whisper (on device)", "Local Whisper")
        transcription_form.addRow("Provider", self.provider_combo)

        self.model_combo = QComboBox()
        self.model_combo.addItems(["tiny", "base", "small", "medium", "large-v3"])
        self.model_combo.setToolTip(
            "Small is a good default. Larger models are more accurate but require more RAM/GPU."
        )
        transcription_form.addRow("Whisper model", self.model_combo)

        self.device_combo = QComboBox()
        self.device_combo.addItem("Automatic", "auto")
        self.device_combo.addItem("CPU", "cpu")
        self.device_combo.addItem("NVIDIA GPU (CUDA)", "cuda")
        transcription_form.addRow("Compute device", self.device_combo)

        self.chunk_spin = QDoubleSpinBox()
        self.chunk_spin.setRange(2.0, 20.0)
        self.chunk_spin.setSingleStep(1.0)
        self.chunk_spin.setSuffix(" seconds")
        transcription_form.addRow("Live chunk size", self.chunk_spin)

        audio_row = QHBoxLayout()
        self.audio_combo = QComboBox()
        self.audio_combo.setMinimumWidth(300)
        self.refresh_audio_button = QPushButton("Refresh")
        self.refresh_audio_button.clicked.connect(self._load_audio_devices)
        audio_row.addWidget(self.audio_combo, 1)
        audio_row.addWidget(self.refresh_audio_button)
        transcription_form.addRow("Windows output", audio_row)
        root.addWidget(transcription_group)

        ai_group = QGroupBox("Gemini notes")
        ai_form = QFormLayout(ai_group)
        ai_form.setSpacing(11)
        self.api_key_edit = QLineEdit()
        self.api_key_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self.api_key_edit.setPlaceholderText("Stored securely in Windows Credential Manager")
        self.api_key_edit.textEdited.connect(self._mark_key_edited)
        self.api_key_edit.setClearButtonEnabled(True)
        ai_form.addRow("Gemini API key", self.api_key_edit)

        self.gemini_model_edit = QLineEdit()
        ai_form.addRow("Gemini model", self.gemini_model_edit)

        ai_actions = QHBoxLayout()
        self.test_button = QPushButton("Test connection")
        self.test_button.clicked.connect(self._test_connection)
        key_hint = QLabel("GEMINI_API_KEY takes priority when set.")
        key_hint.setStyleSheet("color: #667085;")
        ai_actions.addWidget(self.test_button)
        ai_actions.addWidget(key_hint)
        ai_actions.addStretch()
        ai_form.addRow("", ai_actions)
        root.addWidget(ai_group)

        self.buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel
        )
        self.buttons.accepted.connect(self._save)
        self.buttons.rejected.connect(self.reject)
        root.addWidget(self.buttons)

    def _mark_key_edited(self) -> None:
        self._api_key_edited = True

    def _load_values(self) -> None:
        self.model_combo.setCurrentText(self.settings.whisper_model)
        device_index = self.device_combo.findData(self.settings.whisper_device)
        self.device_combo.setCurrentIndex(max(0, device_index))
        self.chunk_spin.setValue(self.settings.chunk_seconds)
        self.gemini_model_edit.setText(self.settings.gemini_model)
        if self.store.get_gemini_api_key():
            self.api_key_edit.setPlaceholderText("API key is configured — type to replace it")
        if os.environ.get("GEMINI_API_KEY", "").strip():
            self.api_key_edit.setEnabled(False)
            self.api_key_edit.setPlaceholderText("Configured by GEMINI_API_KEY environment variable")
        self._load_audio_devices()

    def _load_audio_devices(self) -> None:
        selected = self.settings.audio_device_id
        self.audio_combo.clear()
        self.audio_combo.addItem("Default Windows output (recommended)", "default")
        try:
            devices = WindowsWasapiLoopbackCapture().list_devices()
            for device in devices:
                label = f"{device.name}{' — default' if device.is_default else ''}"
                self.audio_combo.addItem(label, device.id)
        except Exception as exc:
            self.audio_combo.setToolTip(str(exc))
        index = self.audio_combo.findData(selected)
        self.audio_combo.setCurrentIndex(max(0, index))

    def _candidate_api_key(self) -> str:
        if os.environ.get("GEMINI_API_KEY", "").strip():
            return os.environ["GEMINI_API_KEY"].strip()
        if self._api_key_edited:
            return self.api_key_edit.text().strip()
        return self.store.get_gemini_api_key()

    def _test_connection(self) -> None:
        api_key = self._candidate_api_key()
        self.test_button.setEnabled(False)
        self.test_button.setText("Testing…")
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        QApplication.processEvents()
        try:
            service = GeminiNotesService(
                api_key, self.gemini_model_edit.text().strip() or "gemini-2.5-flash"
            )
            service.test_connection()
            QMessageBox.information(self, "Gemini", "Connection successful.")
        except GeminiError as exc:
            QMessageBox.warning(self, "Gemini connection", str(exc))
        finally:
            QApplication.restoreOverrideCursor()
            self.test_button.setEnabled(True)
            self.test_button.setText("Test connection")

    def _save(self) -> None:
        self.settings.whisper_model = self.model_combo.currentText()
        self.settings.whisper_device = self.device_combo.currentData()
        self.settings.chunk_seconds = self.chunk_spin.value()
        self.settings.audio_device_id = self.audio_combo.currentData()
        self.settings.gemini_model = (
            self.gemini_model_edit.text().strip() or "gemini-2.5-flash"
        )
        try:
            if self._api_key_edited and not os.environ.get("GEMINI_API_KEY", "").strip():
                self.store.set_gemini_api_key(self.api_key_edit.text())
            self.store.save(self.settings)
        except RuntimeError as exc:
            QMessageBox.critical(self, "Settings could not be saved", str(exc))
            return
        self.accept()
