APP_STYLESHEET = """
QMainWindow, QDialog, QWidget {
    background: #f7f8fb;
    color: #182230;
    font-family: "Segoe UI";
    font-size: 10pt;
}
QFrame#Sidebar {
    background: #ffffff;
    border-right: 1px solid #e4e7ec;
}
QLabel#AppTitle {
    font-size: 17pt;
    font-weight: 700;
    color: #101828;
}
QLabel#SectionTitle {
    font-size: 10pt;
    font-weight: 650;
    color: #344054;
}
QLabel#StatusPill {
    background: #ecfdf3;
    color: #067647;
    border: 1px solid #abefc6;
    border-radius: 12px;
    padding: 4px 10px;
    font-weight: 600;
}
QLabel#PrivacyPill {
    background: #eff8ff;
    color: #175cd3;
    border: 1px solid #b2ddff;
    border-radius: 11px;
    padding: 3px 8px;
}
QPushButton {
    min-height: 32px;
    padding: 0 13px;
    border-radius: 6px;
    border: 1px solid #d0d5dd;
    background: #ffffff;
    color: #344054;
    font-weight: 600;
}
QPushButton:hover { background: #f9fafb; border-color: #98a2b3; }
QPushButton:pressed { background: #f2f4f7; }
QPushButton:disabled { color: #98a2b3; background: #f2f4f7; }
QPushButton#PrimaryButton {
    background: #7f56d9;
    color: white;
    border-color: #7f56d9;
}
QPushButton#PrimaryButton:hover { background: #6941c6; }
QPushButton#DangerButton { color: #b42318; }
QLineEdit, QComboBox, QSpinBox, QDoubleSpinBox, QPlainTextEdit {
    background: #ffffff;
    border: 1px solid #d0d5dd;
    border-radius: 6px;
    padding: 6px 8px;
    selection-background-color: #d6bbfb;
}
QLineEdit:focus, QComboBox:focus, QPlainTextEdit:focus {
    border: 1px solid #7f56d9;
}
QPlainTextEdit#TranscriptEditor, QPlainTextEdit#NotesEditor {
    font-family: "Segoe UI", sans-serif;
    font-size: 11pt;
    line-height: 1.4;
    border: none;
    border-radius: 0;
    padding: 18px;
}
QListWidget {
    background: transparent;
    border: none;
    outline: none;
}
QListWidget::item {
    border-radius: 6px;
    padding: 9px 8px;
    margin: 1px 0;
}
QListWidget::item:selected { background: #f4ebff; color: #53389e; }
QListWidget::item:hover:!selected { background: #f9fafb; }
QTabWidget::pane {
    border: 1px solid #e4e7ec;
    background: #ffffff;
    border-radius: 8px;
}
QTabBar::tab {
    background: transparent;
    padding: 9px 16px;
    margin-right: 3px;
    color: #667085;
}
QTabBar::tab:selected {
    color: #6941c6;
    border-bottom: 2px solid #7f56d9;
    font-weight: 650;
}
QProgressBar {
    border: none;
    background: #eaecf0;
    height: 5px;
    border-radius: 2px;
}
QProgressBar::chunk { background: #7f56d9; border-radius: 2px; }
QScrollBar:vertical { width: 11px; background: transparent; }
QScrollBar::handle:vertical { background: #d0d5dd; border-radius: 5px; min-height: 30px; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
"""
