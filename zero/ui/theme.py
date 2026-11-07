"""Dark theme: design tokens, bundled fonts, Lucide icons and the app stylesheet.

Rules followed (ui-ux-pro-max): text contrast >= 4.5:1, visible keyboard focus,
36-40 px control height on desktop, 4/8 px spacing, one icon family (Lucide, 2 px stroke),
status never shown by colour alone.
"""
from __future__ import annotations

from functools import lru_cache

from PySide6.QtCore import QByteArray, QRectF, Qt
from PySide6.QtGui import QColor, QFont, QFontDatabase, QIcon, QPainter, QPalette, QPixmap

C = {
    "bg": "#0b0c0f",          # the "void"
    "surface": "#121418",
    "surface2": "#181b21",
    "surface3": "#20242c",
    "border": "#262a33",      # decorative dividers
    "control": "#3b4250",     # input and button outlines
    "text": "#e6e8ee",        # 15:1 on bg
    "muted": "#9aa3b5",       # 7.3:1 on surface
    "faint": "#7a8293",       # 4.5:1 on surface2, captions only
    "disabled": "#6b7385",
    "accent": "#49b265",      # brand green, 6.9:1 on surface
    "accent_hi": "#5cc878",
    "accent_dim": "#173a22",
    "track": "#2e3440",       # progress bar track, visible on selected rows too
    "ok": "#9be3b4",
    "warn": "#f5b942",
    "bad": "#ff6b6f",         # 6.7:1 on surface
    "on_accent": "#0b1a10",   # dark text on green: 6.7:1 (white would be 2.7:1)
}

SANS = "IBM Plex Sans"
MONO = "JetBrains Mono"
FONT_PT = 10

# type scale (pt): caption 8.5, body 10, label 10 semibold, title 12.5, h1 18; spacing 4/8/12/16/24/32


def _load_fonts() -> None:
    from ..paths import resource_path
    d = resource_path("assets/fonts")
    if not d.is_dir():
        return
    for f in sorted(d.glob("*.ttf")):
        QFontDatabase.addApplicationFont(str(f))


def mono_font(pt: float = 9.5) -> QFont:
    f = QFont(MONO)
    f.setPointSizeF(pt)
    f.setStyleHint(QFont.Monospace)
    return f


@lru_cache(maxsize=256)
def _pixmap(name: str, color: str, size: int, ratio: float) -> QPixmap:
    from PySide6.QtSvg import QSvgRenderer
    from ..paths import resource_path
    p = resource_path(f"assets/icons/{name}.svg")
    pm = QPixmap(int(size * ratio), int(size * ratio))
    pm.fill(Qt.transparent)
    if not p.exists():
        return pm
    svg = p.read_text(encoding="utf-8").replace("currentColor", color)
    r = QSvgRenderer(QByteArray(svg.encode("utf-8")))
    painter = QPainter(pm)
    painter.setRenderHint(QPainter.Antialiasing)
    r.render(painter, QRectF(0, 0, size * ratio, size * ratio))
    painter.end()
    pm.setDevicePixelRatio(ratio)
    return pm


def icon(name: str, color: str | None = None, size: int = 18, on_color: str | None = None) -> QIcon:
    """Lucide icon tinted with a token colour; `on_color` is used for checked buttons."""
    ic = QIcon()
    col = color or C["text"]
    for ratio in (1.0, 2.0):
        ic.addPixmap(_pixmap(name, col, size, ratio), QIcon.Normal, QIcon.Off)
        ic.addPixmap(_pixmap(name, C["disabled"], size, ratio), QIcon.Disabled, QIcon.Off)
        if on_color:
            ic.addPixmap(_pixmap(name, on_color, size, ratio), QIcon.Normal, QIcon.On)
            ic.addPixmap(_pixmap(name, on_color, size, ratio), QIcon.Active, QIcon.On)
    return ic


def keyboard_focus_only(root) -> None:
    """Buttons and checkboxes take focus from Tab, not from clicks, so the focus ring means 'keyboard is here'."""
    from PySide6.QtWidgets import QAbstractButton
    for b in root.findChildren(QAbstractButton):
        b.setFocusPolicy(Qt.TabFocus)


def apply(app):
    _load_fonts()
    f = QFont(SANS)
    f.setPointSize(FONT_PT)
    f.setHintingPreference(QFont.PreferNoHinting)
    app.setFont(f)
    app.setStyle("Fusion")
    pal = QPalette()
    pal.setColor(QPalette.Window, QColor(C["bg"]))
    pal.setColor(QPalette.WindowText, QColor(C["text"]))
    pal.setColor(QPalette.Base, QColor(C["surface"]))
    pal.setColor(QPalette.AlternateBase, QColor(C["surface2"]))
    pal.setColor(QPalette.Text, QColor(C["text"]))
    pal.setColor(QPalette.Button, QColor(C["surface2"]))
    pal.setColor(QPalette.ButtonText, QColor(C["text"]))
    pal.setColor(QPalette.Highlight, QColor(C["accent"]))
    pal.setColor(QPalette.HighlightedText, QColor(C["on_accent"]))
    pal.setColor(QPalette.ToolTipBase, QColor(C["surface3"]))
    pal.setColor(QPalette.ToolTipText, QColor(C["text"]))
    pal.setColor(QPalette.PlaceholderText, QColor(C["faint"]))
    pal.setColor(QPalette.Link, QColor(C["accent"]))
    for role in (QPalette.Text, QPalette.ButtonText, QPalette.WindowText):
        pal.setColor(QPalette.Disabled, role, QColor(C["disabled"]))
    app.setPalette(pal)
    from ..paths import resource_path
    check = str(resource_path("assets/check.png")).replace("\\", "/")
    chevron = str(resource_path("assets/chevron.png")).replace("\\", "/")
    app.setStyleSheet(QSS.replace("__CHECK__", check).replace("__CHEVRON__", chevron))


QSS = f"""
* {{ font-family: "{SANS}"; font-size: {FONT_PT}pt; }}
QMainWindow, QWidget#root {{ background: {C['bg']}; }}
QToolTip {{ background: {C['surface3']}; color: {C['text']}; border: 1px solid {C['control']}; padding: 6px 8px; border-radius: 6px; }}

/* sidebar */
QWidget#sidebar {{ background: {C['surface']}; border-right: 1px solid {C['border']}; }}
QLabel#brand {{ font-size: 16pt; font-weight: 700; color: {C['text']}; padding: 24px 20px 4px 20px; }}
QLabel#tagline {{ color: {C['accent']}; font-size: 7.5pt; font-weight: 600; padding: 0 22px 12px 22px; }}
QLabel#navSection {{ color: {C['faint']}; font-size: 8pt; font-weight: 600; padding: 12px 22px 4px 22px; }}
QPushButton#nav {{
    text-align: left; padding: 0 12px 0 13px; margin: 2px 12px; min-height: 40px;
    border: none; border-left: 3px solid transparent; border-radius: 8px;
    color: {C['muted']}; background: transparent; font-weight: 500;
}}
QPushButton#nav:hover {{ background: {C['surface2']}; color: {C['text']}; }}
QPushButton#nav:checked {{ background: {C['surface3']}; color: {C['text']}; border-left: 3px solid {C['accent']}; font-weight: 600; }}
QPushButton#nav:focus {{ background: {C['surface2']}; color: {C['accent_hi']}; }}
QLabel#navBadge {{ background: {C['accent']}; color: {C['on_accent']}; border-radius: 9px; padding: 1px 7px; font-size: 8pt; font-weight: 700; }}
QLabel#sideFoot {{ color: {C['faint']}; font-size: 8.5pt; padding: 12px 22px 16px 22px; }}

/* cards and text */
QFrame#card {{ background: {C['surface']}; border: 1px solid {C['border']}; border-radius: 12px; }}
QFrame#divider {{ background: {C['border']}; max-height: 1px; min-height: 1px; border: none; }}
QLabel#h1 {{ font-size: 18pt; font-weight: 600; }}
QLabel#h2 {{ font-size: 12pt; font-weight: 600; }}
QLabel#title {{ font-size: 12.5pt; font-weight: 600; }}
QLabel#section {{ color: {C['faint']}; font-size: 8pt; font-weight: 600; padding-top: 4px; }}
QLabel#fieldLabel {{ color: {C['muted']}; font-size: 9pt; font-weight: 500; }}
QLabel#muted {{ color: {C['muted']}; }}
QLabel#caption {{ color: {C['faint']}; font-size: 8.5pt; }}
QLabel#error {{ color: {C['bad']}; }}
QLabel#warn {{ color: {C['warn']}; padding: 2px 4px; }}
QLabel#thumb {{ background: {C['surface2']}; border-radius: 8px; color: {C['muted']}; }}
QLabel#pill {{
    background: {C['surface3']}; color: {C['muted']}; border-radius: 9px; padding: 2px 9px; font-size: 8.5pt;
}}
QLabel#emptyTitle {{ color: {C['text']}; font-size: 12pt; font-weight: 600; }}
QLabel#emptyText {{ color: {C['muted']}; }}
QFrame#summary {{ background: {C['surface2']}; border: 1px solid {C['border']}; border-radius: 8px; }}

/* inputs: 38 px tall */
QLineEdit, QComboBox, QSpinBox, QPlainTextEdit {{
    background: {C['surface2']}; border: 1px solid {C['control']}; border-radius: 8px;
    padding: 0 11px; min-height: 36px; color: {C['text']};
    selection-background-color: {C['accent']}; selection-color: {C['on_accent']};
}}
QPlainTextEdit {{ padding: 8px 10px; }}
QLineEdit:hover, QComboBox:hover {{ border-color: #4d5566; }}
QLineEdit:focus, QComboBox:focus, QSpinBox:focus, QPlainTextEdit:focus {{ border: 2px solid {C['accent']}; padding: 0 10px; }}
QPlainTextEdit:focus {{ padding: 7px 9px; }}
QLineEdit:disabled, QComboBox:disabled {{ color: {C['disabled']}; background: {C['surface']}; border-color: {C['border']}; }}
QLineEdit#url {{ font-size: 11pt; min-height: 42px; padding: 0 14px; }}
QLineEdit#url:focus {{ padding: 0 13px; }}
QComboBox::drop-down {{ border: none; width: 28px; }}
QComboBox::down-arrow {{ image: url(__CHEVRON__); width: 12px; height: 12px; }}
QComboBox QAbstractItemView {{
    background: {C['surface2']}; border: 1px solid {C['control']}; outline: none; padding: 4px;
    selection-background-color: {C['surface3']}; selection-color: {C['text']};
}}
QComboBox QAbstractItemView::item {{ min-height: 30px; padding: 0 8px; border-radius: 6px; }}
QComboBox QAbstractItemView::item:selected {{ background: {C['accent']}; color: {C['on_accent']}; }}
QCheckBox {{ spacing: 10px; min-height: 28px; }}
QCheckBox::indicator {{ width: 18px; height: 18px; border-radius: 5px; border: 1px solid {C['control']}; background: {C['surface2']}; }}
QCheckBox::indicator:hover {{ border-color: {C['accent']}; }}
QCheckBox::indicator:checked {{ background: {C['accent']}; border-color: {C['accent']}; image: url(__CHECK__); }}
QCheckBox:focus {{ color: {C['accent_hi']}; }}
QCheckBox::indicator:focus {{ border: 2px solid {C['accent_hi']}; }}
QCheckBox:disabled {{ color: {C['disabled']}; }}

/* buttons: 38 px tall, focus = 2 px green ring */
QPushButton {{
    background: {C['surface2']}; border: 1px solid {C['control']}; border-radius: 8px;
    padding: 0 14px; min-height: 36px; color: {C['text']}; font-weight: 500;
}}
QPushButton:hover {{ background: {C['surface3']}; border-color: #4d5566; }}
QPushButton:pressed {{ background: {C['border']}; }}
QPushButton:focus {{ border: 2px solid {C['accent']}; padding: 0 13px; }}
QPushButton:disabled {{ color: {C['disabled']}; border-color: {C['border']}; background: {C['surface']}; }}
QPushButton#primary {{ background: {C['accent']}; border: 1px solid {C['accent']}; color: {C['on_accent']}; font-weight: 600; padding: 0 20px; }}
QPushButton#primary:hover {{ background: {C['accent_hi']}; border-color: {C['accent_hi']}; }}
QPushButton#primary:pressed {{ background: #3f9d58; }}
QPushButton#primary:focus {{ border: 2px solid {C['text']}; padding: 0 19px; }}
QPushButton#primary:disabled {{ background: {C['surface3']}; border-color: {C['border']}; color: {C['disabled']}; }}
QPushButton#danger {{ color: {C['bad']}; }}
QPushButton#danger:hover {{ border-color: {C['bad']}; }}
QPushButton#ghost {{ background: transparent; border: 1px solid transparent; color: {C['muted']}; padding: 0 10px; }}
QPushButton#ghost:hover {{ color: {C['text']}; background: {C['surface2']}; }}
QPushButton#ghost:focus {{ border: 2px solid {C['accent']}; padding: 0 9px; }}
QPushButton#icon {{ background: transparent; border: 1px solid transparent; padding: 0; min-width: 36px; max-width: 36px; }}
QPushButton#icon:hover {{ background: {C['surface3']}; }}
QPushButton#icon:focus {{ border: 2px solid {C['accent']}; }}

/* tables & lists */
QTableView, QListWidget {{
    background: {C['surface']}; border: 1px solid {C['border']}; border-radius: 12px;
    gridline-color: transparent; alternate-background-color: {C['surface']}; outline: none;
}}
QTableView:focus, QListWidget:focus {{ border-color: {C['control']}; }}
QTableView::item {{ padding: 0 10px; border-bottom: 1px solid {C['border']}; }}
QTableView::item:hover {{ background: {C['surface2']}; }}
QTableView::item:selected {{ background: {C['surface3']}; color: {C['text']}; }}
QListWidget::item {{ padding: 6px 8px; min-height: 28px; }}
QListWidget::item:selected {{ background: {C['surface3']}; color: {C['text']}; }}
QHeaderView::section {{
    background: {C['surface']}; color: {C['faint']}; border: none; border-bottom: 1px solid {C['border']};
    padding: 10px 10px; font-weight: 600; font-size: 8pt;
}}
QHeaderView {{ background: transparent; border: none; }}
QTableCornerButton::section {{ background: {C['surface']}; border: none; }}

QScrollBar:vertical {{ background: transparent; width: 12px; margin: 4px 2px; }}
QScrollBar::handle:vertical {{ background: {C['surface3']}; border-radius: 4px; min-height: 30px; }}
QScrollBar::handle:vertical:hover {{ background: {C['control']}; }}
QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; width: 0; }}
QScrollBar::add-page, QScrollBar::sub-page {{ background: transparent; }}
QScrollBar:horizontal {{ background: transparent; height: 12px; margin: 2px 4px; }}
QScrollBar::handle:horizontal {{ background: {C['surface3']}; border-radius: 4px; min-width: 30px; }}

QGroupBox {{
    background: {C['surface']}; border: 1px solid {C['border']}; border-radius: 12px;
    margin-top: 16px; padding: 20px 16px 14px 16px; font-weight: 600;
}}
QGroupBox::title {{ subcontrol-origin: margin; left: 14px; padding: 0 6px; color: {C['muted']}; }}

QProgressBar {{ background: {C['surface3']}; border: none; border-radius: 4px; height: 8px; text-align: center; color: transparent; }}
QProgressBar::chunk {{ background: {C['accent']}; border-radius: 4px; }}

QMenu {{ background: {C['surface2']}; border: 1px solid {C['control']}; padding: 6px; border-radius: 10px; }}
QMenu::item {{ padding: 8px 20px 8px 12px; border-radius: 6px; }}
QMenu::item:selected {{ background: {C['accent']}; color: {C['on_accent']}; }}
QMenu::item:disabled {{ color: {C['disabled']}; }}
QMenu::separator {{ height: 1px; background: {C['border']}; margin: 6px 8px; }}
QMessageBox {{ background: {C['surface']}; }}
QScrollArea {{ border: none; background: transparent; }}
QScrollArea > QWidget > QWidget {{ background: transparent; }}
QSpinBox {{ padding: 0 10px; }}
QListWidget::indicator {{ width: 16px; height: 16px; border-radius: 4px; border: 1px solid {C['control']}; background: {C['surface2']}; }}
QListWidget::indicator:checked {{ background: {C['accent']}; border-color: {C['accent']}; image: url(__CHECK__); }}
QSplitter::handle {{ background: transparent; }}
"""
