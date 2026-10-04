from __future__ import annotations

import ctypes
import logging
import os
import sys
import traceback

from PySide6.QtCore import QCoreApplication, Qt
from PySide6.QtWidgets import QApplication, QMessageBox

from ai_transcript import __version__
from ai_transcript.config import SettingsStore
from ai_transcript.storage.repository import SessionRepository
from ai_transcript.ui.main_window import MainWindow
from ai_transcript.ui.styles import APP_STYLESHEET


def _configure_logging(store: SettingsStore) -> None:
    level_name = os.environ.get("AI_TRANSCRIPT_LOG_LEVEL", "INFO").upper()
    level = getattr(logging, level_name, logging.INFO)
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        handlers=[
            logging.FileHandler(store.log_path, encoding="utf-8"),
            logging.StreamHandler(sys.stderr),
        ],
    )


def _set_windows_app_id() -> None:
    if sys.platform == "win32":
        try:
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(
                "AITranscript.Desktop.0.1"
            )
        except Exception:
            pass


def main() -> int:
    store = SettingsStore()
    _configure_logging(store)
    _set_windows_app_id()

    QCoreApplication.setApplicationName("AI Transcript")
    QCoreApplication.setOrganizationName("AI Transcript")
    QCoreApplication.setApplicationVersion(__version__)
    QApplication.setAttribute(Qt.ApplicationAttribute.AA_DontUseNativeMenuBar, True)
    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)
    app.setStyle("Fusion")
    app.setStyleSheet(APP_STYLESHEET)

    def exception_hook(exception_type, value, tb) -> None:
        logging.getLogger(__name__).critical(
            "Uncaught exception", exc_info=(exception_type, value, tb)
        )
        details = "".join(traceback.format_exception_only(exception_type, value)).strip()
        QMessageBox.critical(
            None,
            "AI Transcript encountered a problem",
            "The application encountered an unexpected error. Existing transcript segments "
            f"remain saved locally.\n\n{details}",
        )

    sys.excepthook = exception_hook

    try:
        repository = SessionRepository(store.database_path, store.sessions_dir)
        window = MainWindow(store, repository)
        window.show()
        return app.exec()
    except Exception:
        logging.getLogger(__name__).exception("Application startup failed")
        QMessageBox.critical(
            None,
            "AI Transcript could not start",
            f"The local data directory could not be initialized.\n\n{store.data_dir}",
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
