"""Visual tokens for the desktop interface."""

APP_STYLESHEET = r"""
* {
    font-family: "Microsoft YaHei UI", "Segoe UI Variable Text", sans-serif;
    font-size: 13px;
    color: #222a31;
}

QMainWindow, QWidget#appRoot, QStackedWidget#pageStack {
    background: #f2f0ea;
}

QScrollArea#settingsScroll, QScrollArea#settingsScroll > QWidget > QWidget,
QWidget#settingsScrollContent {
    background: transparent;
    border: 0;
}

QWidget#sidebar {
    background: #171b20;
    border: 0;
}

QLabel#brand {
    color: #fffdf7;
    font-family: "Bahnschrift", "Microsoft YaHei UI";
    font-size: 20px;
    font-weight: 700;
    letter-spacing: 1px;
}

QLabel#tagline {
    color: #89929b;
    font-size: 9px;
    letter-spacing: 1px;
}

QLabel#brandMark {
    color: #171b20;
    background: #ff6542;
    border-radius: 9px;
    font-family: "Bahnschrift SemiBold";
    font-size: 14px;
    font-weight: 700;
}

QListWidget#navigation {
    background: transparent;
    border: 0;
    outline: 0;
    padding: 0;
}

QListWidget#navigation::item {
    color: #9ea7b0;
    background: transparent;
    border-left: 3px solid transparent;
    border-radius: 6px;
    padding: 10px 8px;
    margin: 3px 0;
}

QListWidget#navigation::item:hover {
    color: #fffdf7;
    background: #20262d;
}

QListWidget#navigation::item:selected {
    color: #fffdf7;
    background: #262d35;
    border-left: 3px solid #ff6542;
    font-weight: 600;
}

QLabel#sidebarLabel {
    color: #5f6973;
    font-size: 10px;
    font-weight: 700;
    letter-spacing: 2px;
}

QFrame#privacyBadge {
    background: #20262c;
    border: 1px solid #2c343c;
    border-radius: 10px;
}

QLabel#privacyTitle { color: #d7dde2; font-weight: 600; }
QLabel#sideNotice { color: #7f8993; font-size: 11px; }
QLabel#privacyDot { color: #45c8a9; font-size: 18px; }

QLabel#eyebrow {
    color: #d94e30;
    font-family: "Bahnschrift SemiBold", "Microsoft YaHei UI";
    font-size: 10px;
    font-weight: 700;
    letter-spacing: 2px;
}

QLabel#pageTitle {
    color: #171d22;
    font-family: "Microsoft YaHei UI";
    font-size: 23px;
    font-weight: 700;
}

QLabel#pageSubtitle {
    color: #727b82;
    font-size: 12px;
}

QFrame#card, QFrame#heroCard, QFrame#settingsCard, QFrame#transcriptCard,
QFrame#scenarioCard, QFrame#controlCard, QFrame#fileToolbar {
    background: #fffdf8;
    border: 1px solid #dedbd2;
    border-radius: 14px;
}

QFrame#heroCard {
    background: #20262c;
    border: 1px solid #2f373f;
}

QFrame#scenarioCard {
    background: #20262c;
    border: 1px solid #313941;
}
QFrame#scenarioCard QLabel#cardTitle { color: #fffdf8; }
QFrame#scenarioCard QLabel#heroLabel { color: #ff8265; }
QFrame#scenarioCard QComboBox {
    color: #e9edf0;
    background: #2a3138;
    border-color: #3b454e;
}

QLabel#cardTitle {
    color: #21282e;
    font-size: 14px;
    font-weight: 700;
}

QLabel#cardCaption { color: #808890; font-size: 11px; }
QLabel#fieldLabel {
    color: #68727a;
    font-size: 10px;
    font-weight: 700;
    letter-spacing: 0.4px;
}
QLabel#documentTitle {
    color: #171d22;
    font-size: 13px;
    font-weight: 700;
}
QLabel#recordingDuration {
    color: #20272d;
    font-family: "Bahnschrift", "Microsoft YaHei UI";
    font-size: 20px;
    font-weight: 600;
    min-width: 72px;
}
QLabel#recordingDot {
    color: #9ba2a7;
    font-size: 18px;
}
QLabel#recordingDot[active="true"] { color: #e44836; }

QLabel#pageFooterStatus {
    color: #747d84;
    background: transparent;
    border: 0;
    padding: 2px 4px 0 4px;
    font-size: 10px;
}
QLabel#pageFooterStatus[tone="live"] { color: #c3482f; }
QLabel#pageFooterStatus[tone="success"] { color: #1d7863; }
QLabel#pageFooterStatus[tone="warning"] { color: #8a641d; }
QLabel#pageFooterStatus[tone="danger"] { color: #ad3933; }
QLabel#heroLabel { color: #8d98a2; font-size: 11px; letter-spacing: 1px; }
QLabel#heroStatus { color: #fffdf8; font-size: 17px; font-weight: 600; }
QLabel#heroDuration {
    color: #fffdf8;
    font-family: "Bahnschrift Light", "Microsoft YaHei UI";
    font-size: 39px;
    font-weight: 300;
}
QLabel#heroHint { color: #7f8992; font-size: 11px; }

QLabel#statusChip {
    border-radius: 10px;
    padding: 4px 10px;
    font-size: 11px;
    font-weight: 600;
}
QLabel#statusChip[tone="idle"] { color: #5e6870; background: #e9e7e0; }
QLabel#statusChip[tone="live"] { color: #ab341d; background: #ffe4dc; }
QLabel#statusChip[tone="success"] { color: #16765f; background: #daf3eb; }
QLabel#statusChip[tone="warning"] { color: #896218; background: #f8eccb; }
QLabel#statusChip[tone="danger"] { color: #a22e2e; background: #f8dddd; }

QPushButton {
    min-height: 20px;
    color: #30383f;
    background: #f9f7f1;
    border: 1px solid #d5d1c8;
    border-radius: 8px;
    padding: 8px 14px;
    font-weight: 600;
}

QPushButton:hover {
    color: #171d22;
    background: #ffffff;
    border-color: #a8a39a;
}

QPushButton:pressed { background: #ece9e1; padding-top: 9px; padding-bottom: 7px; }
QPushButton:disabled { color: #abb0b4; background: #eceae5; border-color: #e0ddd6; }

QPushButton#primaryButton {
    color: #fffdf9;
    background: #f05b3a;
    border: 1px solid #f05b3a;
    padding-left: 20px;
    padding-right: 20px;
}
QPushButton#primaryButton:hover { background: #ff6a46; border-color: #ff6a46; }
QPushButton#primaryButton:pressed { background: #d94b2d; border-color: #d94b2d; }
QPushButton#primaryButton:disabled { color: #e7cbc4; background: #b9968d; border-color: #b9968d; }

QPushButton#dangerButton { color: #b23e30; background: #fff8f5; border-color: #e9b9ae; }
QPushButton#dangerButton:hover { color: #8f281c; background: #ffebe5; border-color: #d98572; }
QPushButton#quietButton { background: transparent; border-color: transparent; color: #69737b; }
QPushButton#quietButton:hover { background: #ebe8e0; color: #252d33; }

QPushButton#iconButton, QPushButton#primaryIconButton,
QPushButton#dangerIconButton {
    min-width: 34px;
    max-width: 34px;
    min-height: 34px;
    max-height: 34px;
    padding: 0;
    border-radius: 9px;
}
QPushButton#iconButton { background: #f3f1eb; border-color: #d8d4cb; }
QPushButton#primaryIconButton {
    background: #f05b3a;
    border-color: #f05b3a;
}
QPushButton#primaryIconButton:hover { background: #ff6a46; border-color: #ff6a46; }
QPushButton#dangerIconButton { background: #fff3ef; border-color: #e8b6aa; }
QPushButton#dangerIconButton:hover { background: #ffe4dc; border-color: #d98270; }
QPushButton#iconButton:disabled, QPushButton#primaryIconButton:disabled,
QPushButton#dangerIconButton:disabled {
    background: #ebe9e3;
    border-color: #dfdcd5;
}
QPushButton#compactButton, QPushButton#compactDangerButton {
    min-height: 17px;
    padding: 4px 8px;
    border-radius: 7px;
    font-size: 11px;
}
QPushButton#compactDangerButton {
    color: #a93c30;
    background: #fff7f4;
    border-color: #ebbbb0;
}

QPushButton#pipelineStep {
    min-height: 18px;
    color: #858d93;
    background: #f1efe9;
    border: 1px solid transparent;
    border-radius: 9px;
    padding: 4px 7px;
    font-size: 10px;
}
QPushButton#pipelineStep[state="running"] {
    color: #9f361f;
    background: #ffe4dc;
    border-color: #f1b8a8;
}
QPushButton#pipelineStep[state="completed"] {
    color: #176b59;
    background: #dff2eb;
    border-color: #b8ddd1;
}
QPushButton#pipelineStep[state="failed"] {
    color: #98382f;
    background: #f8dfdb;
    border-color: #e8b8b0;
}
QPushButton#pipelineStep:checked {
    color: #fffdf8;
    background: #283038;
    border-color: #283038;
}
QPushButton#pipelineStep:disabled {
    color: #adb1b3;
    background: #efede7;
    border-color: transparent;
}
QLabel#pipelineArrow {
    color: #aaa69e;
    font-family: "Bahnschrift Light";
    font-size: 16px;
}

QLineEdit, QComboBox, QSpinBox {
    min-height: 24px;
    color: #252c32;
    background: #f9f8f3;
    border: 1px solid #d7d4cc;
    border-radius: 8px;
    padding: 7px 10px;
    selection-background-color: #f05b3a;
}
QLineEdit:hover, QComboBox:hover, QSpinBox:hover { border-color: #b9b4aa; }
QLineEdit:focus, QComboBox:focus, QSpinBox:focus {
    background: #fffefa;
    border: 1px solid #f05b3a;
}
QLineEdit:disabled, QComboBox:disabled, QSpinBox:disabled {
    color: #a1a6aa; background: #eeece7; border-color: #e2dfd8;
}
QComboBox::drop-down { border: 0; width: 28px; }
QComboBox QAbstractItemView {
    background: #fffdf8;
    border: 1px solid #d3cfc6;
    selection-background-color: #ffe1d8;
    selection-color: #2a3035;
    padding: 4px;
    outline: 0;
}

QPlainTextEdit {
    color: #30373d;
    background: #fbfaf6;
    border: 0;
    border-radius: 10px;
    padding: 10px;
    font-size: 13px;
    line-height: 1.55;
    selection-background-color: #ffd6ca;
    selection-color: #201f1d;
}
QPlainTextEdit:focus { background: #fffefb; }
QPlainTextEdit#promptEditor {
    background: #f8f6f0;
    border: 1px solid #d7d4cc;
    padding: 10px;
    font-size: 12px;
}
QPlainTextEdit#promptEditor:focus {
    background: #fffefa;
    border-color: #f05b3a;
}

QListWidget#historyList {
    background: transparent;
    border: 0;
    outline: 0;
}
QListWidget#historyList::item {
    color: #59636b;
    background: #f6f4ed;
    border: 1px solid transparent;
    border-radius: 9px;
    padding: 8px;
    margin: 2px 0;
}
QListWidget#historyList::item:hover { background: #f0ede5; }
QListWidget#historyList::item:selected {
    color: #22292e;
    background: #ffe8df;
    border-color: #f3c4b7;
}

QProgressBar {
    min-height: 7px;
    max-height: 7px;
    background: #e5e2db;
    border: 0;
    border-radius: 3px;
    text-align: center;
}
QProgressBar::chunk { background: #f05b3a; border-radius: 3px; }
QFrame#heroCard QProgressBar { background: #343c43; }
QFrame#heroCard QProgressBar::chunk { background: #45c8a9; }

QCheckBox { spacing: 8px; color: #596168; }
QCheckBox::indicator { width: 17px; height: 17px; border: 1px solid #bdb8ae; border-radius: 5px; background: #faf9f4; }
QCheckBox::indicator:checked { background: #f05b3a; border-color: #f05b3a; }

QFrame#fileToolbar { background: #faf8f2; border: 1px dashed #bdb7aa; border-radius: 12px; }
QFrame#fileToolbar[dragActive="true"] { background: #fff0e9; border: 1px solid #f05b3a; }

QScrollBar:vertical { background: transparent; width: 10px; margin: 2px; }
QScrollBar::handle:vertical { background: #c9c5bc; border-radius: 4px; min-height: 30px; }
QScrollBar::handle:vertical:hover { background: #a9a49b; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
QToolTip { color: #fffdf8; background: #20262c; border: 1px solid #343c44; padding: 5px; }
"""
