from __future__ import annotations

import logging
import platform
from pathlib import Path

from PySide6.QtCore import QObject, Qt, QThread, QTimer, Signal, Slot
from PySide6.QtGui import (
    QAction,
    QCloseEvent,
    QFont,
    QKeySequence,
    QShortcut,
    QTextCursor,
)
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMenu,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QStyle,
    QSystemTrayIcon,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from ai_transcript.audio.windows_loopback import WindowsWasapiLoopbackCapture
from ai_transcript.config import SettingsStore
from ai_transcript.models import (
    ProcessingMode,
    SessionStatus,
    TranscriptSegment,
    TranscriptSession,
)
from ai_transcript.services.export_service import ExportError, ExportService
from ai_transcript.services.gemini_service import GeminiError, GeminiNotesService
from ai_transcript.services.pipeline import LiveTranscriptionPipeline, PipelineCallbacks
from ai_transcript.storage.repository import SessionRepository
from ai_transcript.transcription.whisper_local import LocalWhisperProvider
from ai_transcript.ui.settings_dialog import SettingsDialog
from ai_transcript.utils import format_timestamp, safe_filename

logger = logging.getLogger(__name__)


class PipelineBridge(QObject):
    state_changed = Signal(str, str)
    segment_ready = Signal(object)
    level_changed = Signal(float)
    language_detected = Signal(str, object)
    warning = Signal(str)
    error = Signal(str)


class NotesWorker(QObject):
    finished = Signal(str)
    failed = Signal(str)
    progress = Signal(int, int)

    def __init__(
        self,
        api_key: str,
        model: str,
        transcript: str,
        mode: ProcessingMode,
        output_language: str,
        custom_instructions: str,
    ) -> None:
        super().__init__()
        self.api_key = api_key
        self.model = model
        self.transcript = transcript
        self.mode = mode
        self.output_language = output_language
        self.custom_instructions = custom_instructions

    @Slot()
    def run(self) -> None:
        try:
            service = GeminiNotesService(self.api_key, self.model)
            notes = service.generate(
                self.transcript,
                self.mode,
                self.output_language,
                self.custom_instructions,
                lambda current, total: self.progress.emit(current, total),
            )
            self.finished.emit(notes)
        except GeminiError as exc:
            self.failed.emit(str(exc))
        except Exception:
            logger.exception("Unexpected Gemini worker error")
            self.failed.emit("AI notes could not be generated because of an unexpected error.")


class MainWindow(QMainWindow):
    def __init__(self, store: SettingsStore, repository: SessionRepository) -> None:
        super().__init__()
        self.store = store
        self.repository = repository
        self.settings = store.load()
        self.current_session: TranscriptSession | None = None
        self.pipeline: LiveTranscriptionPipeline | None = None
        self.recording_session_id: str | None = None
        self.recording_time_offset = 0.0
        self._capture_clock_started = False
        self._force_exit = False
        self._closing = False
        self._tray_close_hint_shown = False
        self._programmatic_text_change = False
        self._transcript_user_edited = False
        self._notes_user_edited = False
        self._notes_thread: QThread | None = None
        self._notes_worker: NotesWorker | None = None
        self._notes_session_id: str | None = None

        self.setWindowTitle("AI Transcript")
        self.setMinimumSize(1020, 680)
        self.resize(1250, 790)
        self._build_ui()
        self._connect_pipeline_bridge()
        self._create_tray()
        self._create_shortcuts()
        self._load_sessions()

        self.timer = QTimer(self)
        self.timer.setInterval(500)
        self.timer.timeout.connect(self._update_elapsed)
        self.timer.start()

    def _build_ui(self) -> None:
        root = QWidget()
        self.setCentralWidget(root)
        layout = QHBoxLayout(root)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        sidebar = QFrame()
        sidebar.setObjectName("Sidebar")
        sidebar.setFixedWidth(268)
        sidebar_layout = QVBoxLayout(sidebar)
        sidebar_layout.setContentsMargins(17, 20, 17, 16)
        sidebar_layout.setSpacing(10)

        app_title = QLabel("AI Transcript")
        app_title.setObjectName("AppTitle")
        sidebar_layout.addWidget(app_title)
        local_badge = QLabel("🔒  Local audio • Private by default")
        local_badge.setObjectName("PrivacyPill")
        sidebar_layout.addWidget(local_badge)
        sidebar_layout.addSpacing(11)

        sessions_header = QHBoxLayout()
        sessions_label = QLabel("SESSIONS")
        sessions_label.setObjectName("SectionTitle")
        self.new_button = QPushButton("+ New")
        self.new_button.setToolTip("Create a new transcript session")
        self.new_button.clicked.connect(self._new_session)
        sessions_header.addWidget(sessions_label)
        sessions_header.addStretch()
        sessions_header.addWidget(self.new_button)
        sidebar_layout.addLayout(sessions_header)

        self.session_list = QListWidget()
        self.session_list.setSpacing(2)
        self.session_list.currentItemChanged.connect(self._session_selected)
        sidebar_layout.addWidget(self.session_list, 1)

        session_actions = QHBoxLayout()
        self.rename_button = QPushButton("Rename")
        self.delete_button = QPushButton("Delete")
        self.delete_button.setObjectName("DangerButton")
        self.rename_button.clicked.connect(self._rename_session)
        self.delete_button.clicked.connect(self._delete_session)
        session_actions.addWidget(self.rename_button)
        session_actions.addWidget(self.delete_button)
        sidebar_layout.addLayout(session_actions)

        self.settings_button = QPushButton("⚙  Settings")
        self.settings_button.clicked.connect(self._open_settings)
        sidebar_layout.addWidget(self.settings_button)
        layout.addWidget(sidebar)

        content = QWidget()
        content_layout = QVBoxLayout(content)
        content_layout.setContentsMargins(24, 18, 24, 18)
        content_layout.setSpacing(12)

        top = QHBoxLayout()
        heading_box = QVBoxLayout()
        self.session_title = QLabel("Transcript")
        self.session_title.setStyleSheet("font-size: 16pt; font-weight: 700; color: #101828;")
        self.session_subtitle = QLabel("Saved locally as you transcribe")
        self.session_subtitle.setStyleSheet("color: #667085;")
        heading_box.addWidget(self.session_title)
        heading_box.addWidget(self.session_subtitle)
        top.addLayout(heading_box)
        top.addStretch()
        self.status_label = QLabel("●  Ready")
        self.status_label.setObjectName("StatusPill")
        top.addWidget(self.status_label)
        self.elapsed_label = QLabel("00:00:00")
        elapsed_font = QFont("Consolas", 11)
        elapsed_font.setBold(True)
        self.elapsed_label.setFont(elapsed_font)
        top.addWidget(self.elapsed_label)
        content_layout.addLayout(top)

        control_bar = QFrame()
        control_bar.setStyleSheet(
            "QFrame { background: white; border: 1px solid #e4e7ec; border-radius: 8px; } "
            "QLabel, QPushButton, QComboBox, QProgressBar { border: none; }"
        )
        controls = QHBoxLayout(control_bar)
        controls.setContentsMargins(12, 10, 12, 10)
        controls.addWidget(QLabel("Language"))
        self.language_combo = QComboBox()
        self.language_combo.addItem("Auto Detect", "auto")
        self.language_combo.addItem("English", "en")
        self.language_combo.addItem("Urdu", "ur")
        self.language_combo.addItem("Hindi", "hi")
        self.language_combo.insertSeparator(self.language_combo.count())
        for name, code in (
            ("Arabic", "ar"),
            ("Bengali", "bn"),
            ("Chinese", "zh"),
            ("French", "fr"),
            ("German", "de"),
            ("Indonesian", "id"),
            ("Italian", "it"),
            ("Japanese", "ja"),
            ("Korean", "ko"),
            ("Persian", "fa"),
            ("Portuguese", "pt"),
            ("Punjabi", "pa"),
            ("Russian", "ru"),
            ("Spanish", "es"),
            ("Turkish", "tr"),
        ):
            self.language_combo.addItem(name, code)
        self.language_combo.setMinimumWidth(132)
        controls.addWidget(self.language_combo)
        self.provider_label = QLabel("Local Whisper")
        self.provider_label.setObjectName("PrivacyPill")
        controls.addWidget(self.provider_label)
        controls.addStretch()
        self.audio_level = QProgressBar()
        self.audio_level.setRange(0, 100)
        self.audio_level.setValue(0)
        self.audio_level.setFixedWidth(80)
        self.audio_level.setTextVisible(False)
        self.audio_level.setToolTip("System audio level")
        controls.addWidget(self.audio_level)
        self.start_button = QPushButton("▶  Start")
        self.start_button.setObjectName("PrimaryButton")
        self.pause_button = QPushButton("Pause")
        self.stop_button = QPushButton("Stop")
        self.pause_button.setEnabled(False)
        self.stop_button.setEnabled(False)
        self.start_button.clicked.connect(self._start_transcription)
        self.pause_button.clicked.connect(self._toggle_pause)
        self.stop_button.clicked.connect(self._stop_transcription)
        controls.addWidget(self.start_button)
        controls.addWidget(self.pause_button)
        controls.addWidget(self.stop_button)
        content_layout.addWidget(control_bar)

        self.tabs = QTabWidget()
        self.tabs.addTab(self._build_transcript_tab(), "Live Transcript")
        self.tabs.addTab(self._build_notes_tab(), "AI Notes")
        content_layout.addWidget(self.tabs, 1)

        self.message_label = QLabel(
            "Ready. Play audio in any Windows app, then click Start. Your microphone is not used."
        )
        self.message_label.setStyleSheet("color: #667085; padding: 2px;")
        self.message_label.setWordWrap(True)
        content_layout.addWidget(self.message_label)
        layout.addWidget(content, 1)

    def _build_transcript_tab(self) -> QWidget:
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        search_bar = QHBoxLayout()
        search_bar.setContentsMargins(12, 10, 12, 10)
        self.search_edit = QLineEdit()
        self.search_edit.setPlaceholderText("Search this transcript…")
        self.search_edit.setClearButtonEnabled(True)
        self.search_edit.textChanged.connect(self._search_transcript)
        self.search_edit.returnPressed.connect(self._find_next)
        self.search_result_label = QLabel("")
        self.search_result_label.setStyleSheet("color: #667085;")
        self.clear_button = QPushButton("Clear")
        self.export_transcript_button = QPushButton("Export TXT / MD")
        self.clear_button.clicked.connect(self._clear_transcript)
        self.export_transcript_button.clicked.connect(self._export_transcript)
        search_bar.addWidget(self.search_edit, 1)
        search_bar.addWidget(self.search_result_label)
        search_bar.addWidget(self.clear_button)
        search_bar.addWidget(self.export_transcript_button)
        layout.addLayout(search_bar)

        self.transcript_editor = QPlainTextEdit()
        self.transcript_editor.setObjectName("TranscriptEditor")
        self.transcript_editor.setPlaceholderText(
            "Live transcription will appear here…\n\n"
            "Tip: the first Start may download the selected Whisper model."
        )
        self.transcript_editor.textChanged.connect(self._transcript_text_changed)
        layout.addWidget(self.transcript_editor, 1)
        return tab

    def _build_notes_tab(self) -> QWidget:
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        bar = QHBoxLayout()
        bar.setContentsMargins(12, 10, 12, 10)
        bar.addWidget(QLabel("Mode"))
        self.mode_combo = QComboBox()
        for mode in ProcessingMode:
            self.mode_combo.addItem(mode.value, mode)
        self.mode_combo.currentIndexChanged.connect(self._mode_changed)
        bar.addWidget(self.mode_combo)
        bar.addWidget(QLabel("Output"))
        self.output_language_combo = QComboBox()
        self.output_language_combo.addItems(
            ["Same as transcript", "English", "Roman Urdu", "Hindi", "Urdu", "Hinglish"]
        )
        self.output_language_combo.setEditable(True)
        self.output_language_combo.setToolTip(
            "Choose a preset or type any output language/style."
        )
        bar.addWidget(self.output_language_combo)
        bar.addStretch()
        self.generate_button = QPushButton("✦  Generate with Gemini")
        self.generate_button.setObjectName("PrimaryButton")
        self.generate_button.clicked.connect(self._generate_notes)
        self.export_notes_button = QPushButton("Export MD / DOCX")
        self.export_notes_button.clicked.connect(self._export_notes)
        bar.addWidget(self.generate_button)
        bar.addWidget(self.export_notes_button)
        layout.addLayout(bar)

        self.custom_prompt = QPlainTextEdit()
        self.custom_prompt.setMaximumHeight(95)
        self.custom_prompt.setPlaceholderText(
            "Custom instructions, e.g. Make beginner-friendly study notes and keep technical "
            "terms in English."
        )
        self.custom_prompt.hide()
        layout.addWidget(self.custom_prompt)

        self.notes_progress = QProgressBar()
        self.notes_progress.setTextVisible(False)
        self.notes_progress.hide()
        layout.addWidget(self.notes_progress)

        self.notes_editor = QPlainTextEdit()
        self.notes_editor.setObjectName("NotesEditor")
        self.notes_editor.setPlaceholderText(
            "After a transcript is collected, choose a mode and generate grounded AI notes.\n\n"
            "Only transcript text—not audio—is sent to Gemini after you click Generate."
        )
        self.notes_editor.textChanged.connect(self._notes_text_changed)
        layout.addWidget(self.notes_editor, 1)
        return tab

    def _connect_pipeline_bridge(self) -> None:
        self.bridge = PipelineBridge(self)
        self.bridge.state_changed.connect(self._on_pipeline_state)
        self.bridge.segment_ready.connect(self._on_segment)
        self.bridge.level_changed.connect(
            lambda level: self.audio_level.setValue(round(level * 100))
        )
        self.bridge.language_detected.connect(self._on_language_detected)
        self.bridge.warning.connect(self._show_warning)
        self.bridge.error.connect(self._show_error)

    def _create_shortcuts(self) -> None:
        for sequence, callback in (
            ("Ctrl+Shift+S", self._start_transcription),
            ("Ctrl+Shift+P", self._toggle_pause),
            ("Ctrl+Shift+X", self._stop_transcription),
            ("Ctrl+F", self.search_edit.setFocus),
        ):
            shortcut = QShortcut(QKeySequence(sequence), self)
            shortcut.activated.connect(callback)

    def _create_tray(self) -> None:
        icon = self.style().standardIcon(QStyle.StandardPixmap.SP_ComputerIcon)
        self.setWindowIcon(icon)
        self.tray = QSystemTrayIcon(icon, self)
        self.tray.setToolTip("AI Transcript — Ready")
        menu = QMenu(self)
        self.tray_open_action = QAction("Open AI Transcript", self)
        self.tray_pause_action = QAction("Pause", self)
        self.tray_stop_action = QAction("Stop", self)
        exit_action = QAction("Exit", self)
        self.tray_open_action.triggered.connect(self._restore_window)
        self.tray_pause_action.triggered.connect(self._toggle_pause)
        self.tray_stop_action.triggered.connect(self._stop_transcription)
        exit_action.triggered.connect(self._exit_application)
        menu.addAction(self.tray_open_action)
        menu.addSeparator()
        menu.addAction(self.tray_pause_action)
        menu.addAction(self.tray_stop_action)
        menu.addSeparator()
        menu.addAction(exit_action)
        self.tray.setContextMenu(menu)
        self.tray.activated.connect(
            lambda reason: self._restore_window()
            if reason == QSystemTrayIcon.ActivationReason.DoubleClick
            else None
        )
        self.tray.show()

    def _load_sessions(self, select_id: str | None = None) -> None:
        sessions = self.repository.list_sessions()
        if not sessions:
            sessions = [self.repository.create_session()]
        target_id = select_id or (self.current_session.id if self.current_session else sessions[0].id)
        self.session_list.blockSignals(True)
        self.session_list.clear()
        target_item = None
        for session in sessions:
            date = session.updated_at.astimezone().strftime("%b %d, %Y  %H:%M")
            item = QListWidgetItem(f"{session.title}\n{date}")
            item.setData(Qt.ItemDataRole.UserRole, session.id)
            item.setToolTip(session.title)
            self.session_list.addItem(item)
            if session.id == target_id:
                target_item = item
        self.session_list.blockSignals(False)
        self.session_list.setCurrentItem(target_item or self.session_list.item(0))
        if self.current_session is None or self.current_session.id != target_id:
            self._display_session(self.repository.get_session(target_id))

    def _save_current_edits(self) -> None:
        if not self.current_session:
            return
        if self._transcript_user_edited:
            self.repository.save_edited_transcript(
                self.current_session.id, self.transcript_editor.toPlainText()
            )
            self._transcript_user_edited = False
        if self._notes_user_edited:
            self.repository.save_notes(self.current_session.id, self.notes_editor.toPlainText())
            self._notes_user_edited = False

    def _session_selected(self, current: QListWidgetItem | None, _previous=None) -> None:
        if current is None:
            return
        self._save_current_edits()
        session_id = current.data(Qt.ItemDataRole.UserRole)
        self._display_session(self.repository.get_session(session_id))

    def _display_session(self, session: TranscriptSession) -> None:
        self.current_session = session
        self.session_title.setText(session.title)
        self.session_subtitle.setText(
            f"{session.updated_at.astimezone().strftime('%B %d, %Y at %H:%M')}  •  saved locally"
        )
        edited_path = session.directory / "edited-transcript.txt"
        text = (
            edited_path.read_text(encoding="utf-8")
            if edited_path.exists()
            else self.repository.transcript_text(session.id)
        )
        notes = self.repository.load_notes(session.id)
        self._programmatic_text_change = True
        self.transcript_editor.setPlainText(text)
        self.notes_editor.setPlainText(notes)
        self._programmatic_text_change = False
        self._transcript_user_edited = False
        self._notes_user_edited = False
        self.elapsed_label.setText(format_timestamp(session.duration_seconds))
        language_index = self.language_combo.findData(session.language)
        self.language_combo.setCurrentIndex(max(0, language_index))
        self._search_transcript(self.search_edit.text())

    def _new_session(self) -> None:
        self._save_current_edits()
        title, accepted = QInputDialog.getText(self, "New session", "Session name (optional)")
        if not accepted:
            return
        session = self.repository.create_session(
            title.strip() or None, self.language_combo.currentData()
        )
        self._load_sessions(session.id)

    def _rename_session(self) -> None:
        if not self.current_session or (self.pipeline and self.pipeline.is_running):
            return
        self._save_current_edits()
        title, accepted = QInputDialog.getText(
            self,
            "Rename session",
            "Session name",
            text=self.current_session.title,
        )
        if accepted and title.strip():
            session = self.repository.rename_session(self.current_session.id, title)
            self.current_session = session
            self._load_sessions(session.id)

    def _delete_session(self) -> None:
        if not self.current_session or (self.pipeline and self.pipeline.is_running):
            return
        if self.settings.confirm_session_delete:
            answer = QMessageBox.question(
                self,
                "Delete session?",
                f"Delete “{self.current_session.title}” and its local transcript?\n\n"
                "This cannot be undone.",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
                QMessageBox.StandardButton.Cancel,
            )
            if answer != QMessageBox.StandardButton.Yes:
                return
        session_id = self.current_session.id
        self.current_session = None
        self.repository.delete_session(session_id)
        self._load_sessions()

    def _set_recording_controls(self, active: bool) -> None:
        self.start_button.setEnabled(not active)
        self.pause_button.setEnabled(active)
        self.stop_button.setEnabled(active)
        self.language_combo.setEnabled(not active)
        self.session_list.setEnabled(not active)
        self.new_button.setEnabled(not active)
        self.rename_button.setEnabled(not active)
        self.delete_button.setEnabled(not active)
        self.settings_button.setEnabled(not active)
        self.clear_button.setEnabled(not active)
        self.generate_button.setEnabled(not active and self._notes_thread is None)
        self.tray_pause_action.setEnabled(active)
        self.tray_stop_action.setEnabled(active)

    def _start_transcription(self) -> None:
        if not self.current_session or (self.pipeline and self.pipeline.is_running):
            return
        if platform.system() != "Windows":
            QMessageBox.warning(
                self,
                "Windows required",
                "WASAPI loopback capture runs on Windows 10/11. You can inspect the UI on "
                "this system, but live system-audio capture must be started on Windows.",
            )
            return
        self._save_current_edits()
        self.settings = self.store.load()
        selected_language = self.language_combo.currentData()
        self.settings.language = selected_language
        self.store.save(self.settings)
        self.repository.set_language(self.current_session.id, selected_language)
        self.current_session = self.repository.get_session(self.current_session.id)
        self.recording_session_id = self.current_session.id
        self.recording_time_offset = self.current_session.duration_seconds
        self._capture_clock_started = False
        provider = LocalWhisperProvider(
            self.settings.whisper_model,
            self.settings.whisper_device,
            self.settings.compute_type,
        )
        callbacks = PipelineCallbacks(
            on_state=self.bridge.state_changed.emit,
            on_segment=self._persist_and_emit_segment,
            on_level=self.bridge.level_changed.emit,
            on_language=self.bridge.language_detected.emit,
            on_warning=self.bridge.warning.emit,
            on_error=self.bridge.error.emit,
        )
        self.pipeline = LiveTranscriptionPipeline(
            WindowsWasapiLoopbackCapture(),
            provider,
            callbacks,
            capture_sample_rate=self.settings.capture_sample_rate,
            transcription_sample_rate=self.settings.sample_rate,
            chunk_seconds=self.settings.chunk_seconds,
            device_id=self.settings.audio_device_id,
            language=selected_language,
            no_audio_warning_seconds=self.settings.no_audio_warning_seconds,
            time_offset=self.recording_time_offset,
        )
        self.repository.set_status(self.recording_session_id, SessionStatus.RECORDING)
        self._set_recording_controls(True)
        self.pipeline.start()

    def _toggle_pause(self) -> None:
        if not self.pipeline or not self.pipeline.is_running:
            return
        if self.pipeline.is_paused:
            self.pipeline.resume()
            self.pause_button.setText("Pause")
            self.tray_pause_action.setText("Pause")
        else:
            self.pipeline.pause()
            self.pause_button.setText("Resume")
            self.tray_pause_action.setText("Resume")

    def _stop_transcription(self) -> None:
        if self.pipeline and self.pipeline.is_running:
            self.stop_button.setEnabled(False)
            self.pause_button.setEnabled(False)
            self.pipeline.stop()

    @Slot(str, str)
    def _on_pipeline_state(self, state: str, message: str) -> None:
        labels = {
            "loading": "◌  Loading model",
            "recording": "●  Listening",
            "paused": "Ⅱ  Paused",
            "stopping": "◌  Saving",
            "stopped": "●  Ready",
        }
        self.status_label.setText(labels.get(state, state.title()))
        self.message_label.setText(message)
        self.tray.setToolTip(f"AI Transcript — {labels.get(state, state).replace('●  ', '')}")
        if state == "recording" and self.recording_session_id:
            if not self._capture_clock_started:
                self._capture_clock_started = True
            self.repository.set_status(self.recording_session_id, SessionStatus.RECORDING)
        elif state == "paused" and self.recording_session_id:
            self.repository.set_status(self.recording_session_id, SessionStatus.PAUSED)
        elif state == "stopped":
            if self.recording_session_id:
                if self._capture_clock_started and self.pipeline:
                    self.repository.set_duration(
                        self.recording_session_id, self.pipeline.captured_until
                    )
                self.repository.set_status(self.recording_session_id, SessionStatus.STOPPED)
                if self.current_session and self.current_session.id == self.recording_session_id:
                    self.current_session = self.repository.get_session(self.recording_session_id)
            self._set_recording_controls(False)
            self.pause_button.setText("Pause")
            self.tray_pause_action.setText("Pause")
            self.recording_session_id = None
            self._load_sessions(self.current_session.id if self.current_session else None)

    def _persist_and_emit_segment(self, segment: TranscriptSegment) -> None:
        """Autosave on the worker thread before asking Qt to repaint the editor."""
        session_id = self.recording_session_id
        if not session_id or self._closing:
            return
        self.repository.add_segment(session_id, segment)
        self.bridge.segment_ready.emit(segment)

    @Slot(object)
    def _on_segment(self, segment: TranscriptSegment) -> None:
        if not self.recording_session_id or self._closing:
            return
        if self.current_session and self.current_session.id == self.recording_session_id:
            block = f"[{format_timestamp(segment.start_seconds)}]\n{segment.text}"
            self._programmatic_text_change = True
            if self.transcript_editor.toPlainText().strip():
                self.transcript_editor.appendPlainText("\n" + block)
            else:
                self.transcript_editor.setPlainText(block)
            self._programmatic_text_change = False
            scrollbar = self.transcript_editor.verticalScrollBar()
            scrollbar.setValue(scrollbar.maximum())
            edited_path = self.current_session.directory / "edited-transcript.txt"
            if edited_path.exists() or self._transcript_user_edited:
                self.repository.save_edited_transcript(
                    self.recording_session_id, self.transcript_editor.toPlainText()
                )
                self._transcript_user_edited = False
            self.current_session = self.repository.get_session(self.recording_session_id)

    @Slot(str, object)
    def _on_language_detected(self, language: str, probability: float | None) -> None:
        confidence = f" ({probability:.0%})" if probability is not None else ""
        self.provider_label.setText(f"Local Whisper • {language.upper()}{confidence}")

    @Slot(str)
    def _show_warning(self, message: str) -> None:
        self.message_label.setText(f"⚠ {message}")
        if self.isHidden():
            self.tray.showMessage("AI Transcript", message, QSystemTrayIcon.MessageIcon.Warning)

    @Slot(str)
    def _show_error(self, message: str) -> None:
        self.message_label.setText(message)
        if self.recording_session_id:
            self.repository.set_status(self.recording_session_id, SessionStatus.ERROR)
        QMessageBox.critical(self, "Transcription stopped", message)

    def _update_elapsed(self) -> None:
        if self.pipeline and self.pipeline.is_running and self._capture_clock_started:
            self.elapsed_label.setText(format_timestamp(self.pipeline.captured_until))

    def _transcript_text_changed(self) -> None:
        if not self._programmatic_text_change:
            self._transcript_user_edited = True

    def _notes_text_changed(self) -> None:
        if not self._programmatic_text_change:
            self._notes_user_edited = True

    def _search_transcript(self, query: str) -> None:
        self.transcript_editor.setExtraSelections([])
        query = query.strip()
        if not query:
            self.search_result_label.clear()
            return
        text = self.transcript_editor.toPlainText()
        count = text.lower().count(query.lower())
        self.search_result_label.setText(f"{count} result{'s' if count != 1 else ''}")
        cursor = self.transcript_editor.document().find(query)
        if not cursor.isNull():
            self.transcript_editor.setTextCursor(cursor)
            self.transcript_editor.ensureCursorVisible()

    def _find_next(self) -> None:
        query = self.search_edit.text().strip()
        if not query:
            return
        found = self.transcript_editor.find(query)
        if not found:
            cursor = self.transcript_editor.textCursor()
            cursor.movePosition(QTextCursor.MoveOperation.Start)
            self.transcript_editor.setTextCursor(cursor)
            self.transcript_editor.find(query)

    def _clear_transcript(self) -> None:
        if not self.current_session:
            return
        answer = QMessageBox.question(
            self,
            "Clear transcript?",
            "Remove all transcript segments in this session? This cannot be undone.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel,
        )
        if answer == QMessageBox.StandardButton.Yes:
            self.repository.clear_transcript(self.current_session.id)
            self._programmatic_text_change = True
            self.transcript_editor.clear()
            self._programmatic_text_change = False
            self._transcript_user_edited = False
            self.current_session = self.repository.get_session(self.current_session.id)
            self.elapsed_label.setText("00:00:00")

    def _export_transcript(self) -> None:
        if not self.current_session:
            return
        self._save_current_edits()
        suggested = safe_filename(self.current_session.title) + ".txt"
        path, selected_filter = QFileDialog.getSaveFileName(
            self,
            "Export transcript",
            suggested,
            "Text file (*.txt);;Markdown file (*.md)",
        )
        if not path:
            return
        destination = Path(path)
        if not destination.suffix:
            destination = destination.with_suffix(
                ".md" if "Markdown" in selected_filter else ".txt"
            )
        try:
            ExportService.export_transcript(self.repository.get_session(self.current_session.id), destination)
            self.message_label.setText(f"Exported to {destination}")
        except ExportError as exc:
            QMessageBox.warning(self, "Export failed", str(exc))

    def _mode_changed(self) -> None:
        mode = self.mode_combo.currentData()
        self.custom_prompt.setVisible(mode is ProcessingMode.CUSTOM)

    def _generate_notes(self) -> None:
        if not self.current_session or self._notes_thread is not None:
            return
        self._save_current_edits()
        transcript = self.transcript_editor.toPlainText().strip()
        if not transcript:
            QMessageBox.information(self, "No transcript", "Transcribe some audio first.")
            return
        api_key = self.store.get_gemini_api_key()
        if not api_key:
            answer = QMessageBox.question(
                self,
                "Gemini API key required",
                "Add a Gemini API key in Settings. Only transcript text is sent when you "
                "generate notes. Open Settings now?",
            )
            if answer == QMessageBox.StandardButton.Yes:
                self._open_settings()
            return
        self.settings = self.store.load()
        self.settings.output_language = self.output_language_combo.currentText()
        self.store.save(self.settings)
        self._notes_session_id = self.current_session.id
        self.generate_button.setEnabled(False)
        self.generate_button.setText("Generating…")
        self.start_button.setEnabled(False)
        self.settings_button.setEnabled(False)
        self.session_list.setEnabled(False)
        self.new_button.setEnabled(False)
        self.rename_button.setEnabled(False)
        self.delete_button.setEnabled(False)
        self.notes_progress.setRange(0, 0)
        self.notes_progress.show()

        self._notes_thread = QThread(self)
        self._notes_worker = NotesWorker(
            api_key,
            self.settings.gemini_model,
            transcript,
            self.mode_combo.currentData(),
            self.output_language_combo.currentText(),
            self.custom_prompt.toPlainText(),
        )
        self._notes_worker.moveToThread(self._notes_thread)
        self._notes_thread.started.connect(self._notes_worker.run)
        self._notes_worker.progress.connect(self._on_notes_progress)
        self._notes_worker.finished.connect(self._notes_finished)
        self._notes_worker.failed.connect(self._notes_failed)
        self._notes_worker.finished.connect(self._notes_thread.quit)
        self._notes_worker.failed.connect(self._notes_thread.quit)
        self._notes_worker.finished.connect(self._notes_worker.deleteLater)
        self._notes_worker.failed.connect(self._notes_worker.deleteLater)
        self._notes_thread.finished.connect(self._notes_cleanup)
        self._notes_thread.start()

    @Slot(int, int)
    def _on_notes_progress(self, current: int, total: int) -> None:
        self.notes_progress.setRange(0, total)
        self.notes_progress.setValue(current)
        self.message_label.setText(f"Processing transcript section {current} of {total}…")

    @Slot(str)
    def _notes_finished(self, notes: str) -> None:
        if self._notes_session_id:
            self.repository.save_notes(self._notes_session_id, notes)
        if self.current_session and self.current_session.id == self._notes_session_id:
            self._programmatic_text_change = True
            self.notes_editor.setPlainText(notes)
            self._programmatic_text_change = False
            self._notes_user_edited = False
            self.tabs.setCurrentIndex(1)
        self.message_label.setText("AI notes generated and saved locally.")

    @Slot(str)
    def _notes_failed(self, message: str) -> None:
        QMessageBox.warning(self, "Gemini notes", message)
        self.message_label.setText(message)

    @Slot()
    def _notes_cleanup(self) -> None:
        if self._notes_thread:
            self._notes_thread.deleteLater()
        self._notes_worker = None
        self._notes_thread = None
        self._notes_session_id = None
        self.notes_progress.hide()
        active = bool(self.pipeline and self.pipeline.is_running)
        self.generate_button.setText("✦  Generate with Gemini")
        self.generate_button.setEnabled(not active)
        self.start_button.setEnabled(not active)
        self.settings_button.setEnabled(not active)
        self.session_list.setEnabled(not active)
        self.new_button.setEnabled(not active)
        self.rename_button.setEnabled(not active)
        self.delete_button.setEnabled(not active)

    def _export_notes(self) -> None:
        if not self.current_session or not self.notes_editor.toPlainText().strip():
            QMessageBox.information(self, "No notes", "Generate or write notes first.")
            return
        suggested = safe_filename(self.current_session.title) + " - notes.md"
        path, selected_filter = QFileDialog.getSaveFileName(
            self,
            "Export notes",
            suggested,
            "Markdown file (*.md);;Word document (*.docx);;Text file (*.txt)",
        )
        if not path:
            return
        destination = Path(path)
        if not destination.suffix:
            extension = ".docx" if "Word" in selected_filter else ".txt" if "Text" in selected_filter else ".md"
            destination = destination.with_suffix(extension)
        try:
            ExportService.export_notes(
                self.notes_editor.toPlainText(), self.current_session.title, destination
            )
            self.message_label.setText(f"Exported notes to {destination}")
        except ExportError as exc:
            QMessageBox.warning(self, "Export failed", str(exc))

    def _open_settings(self) -> None:
        dialog = SettingsDialog(self.store, self.store.load(), self)
        if dialog.exec():
            self.settings = self.store.load()
            self.provider_label.setText(
                f"Local Whisper • {self.settings.whisper_model}"
            )
            index = self.output_language_combo.findText(self.settings.output_language)
            if index >= 0:
                self.output_language_combo.setCurrentIndex(index)
            self.message_label.setText("Settings saved.")

    def _restore_window(self) -> None:
        self.showNormal()
        self.activateWindow()
        self.raise_()

    def _exit_application(self) -> None:
        self._force_exit = True
        self.close()

    def closeEvent(self, event: QCloseEvent) -> None:
        if self.settings.minimize_to_tray and not self._force_exit:
            event.ignore()
            self.hide()
            if not self._tray_close_hint_shown:
                self.tray.showMessage(
                    "AI Transcript is still running",
                    "Use the tray icon to open, pause, stop, or exit.",
                    QSystemTrayIcon.MessageIcon.Information,
                )
                self._tray_close_hint_shown = True
            return
        self._save_current_edits()
        if self.pipeline and self.pipeline.is_running:
            self.pipeline.stop(wait=True)
        self._closing = True
        if self._notes_thread and self._notes_thread.isRunning():
            QMessageBox.information(
                self,
                "Notes are still generating",
                "Please wait for Gemini processing to finish before exiting.",
            )
            event.ignore()
            self._force_exit = False
            self._closing = False
            return
        self.tray.hide()
        self.repository.close()
        event.accept()
        QApplication.instance().quit()
