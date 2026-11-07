"""Small shared widgets."""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QFont
from PySide6.QtWidgets import (QApplication, QLabel, QStyle, QStyledItemDelegate, QStyleOptionViewItem,
                               QVBoxLayout, QWidget)

from .theme import C, icon


class EmptyState(QWidget):
    """Centered icon + title + one line of help, drawn over an empty table's viewport."""

    def __init__(self, icon_name: str, title: str, text: str, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WA_TransparentForMouseEvents)
        v = QVBoxLayout(self)
        v.setAlignment(Qt.AlignCenter)
        v.setSpacing(8)
        pic = QLabel()
        pic.setPixmap(icon(icon_name, C["faint"], 40).pixmap(40, 40))
        pic.setAlignment(Qt.AlignCenter)
        self.title = QLabel(title)
        self.title.setObjectName("emptyTitle")
        self.title.setAlignment(Qt.AlignCenter)
        self.text = QLabel(text)
        self.text.setObjectName("emptyText")
        self.text.setAlignment(Qt.AlignCenter)
        self.text.setWordWrap(True)
        v.addWidget(pic)
        v.addSpacing(4)
        v.addWidget(self.title)
        v.addWidget(self.text)


class TwoLineDelegate(QStyledItemDelegate):
    """Name on top, a caption line below (Qt.UserRole + 1); red caption when UserRole + 2 is True."""

    def paint(self, painter, option, index):
        opt = QStyleOptionViewItem(option)
        self.initStyleOption(opt, index)
        name, sub = opt.text, index.data(Qt.UserRole + 1) or ""
        opt.text = ""
        w = opt.widget
        (w.style() if w else QApplication.style()).drawControl(QStyle.CE_ItemViewItem, opt, painter, w)
        r = option.rect.adjusted(10, 0, -10, 0)
        painter.save()
        f = QFont(option.font)
        f.setWeight(QFont.Medium)
        painter.setFont(f)
        painter.setPen(QColor(C["text"]))
        top = r.adjusted(0, 8, 0, -r.height() // 2)
        painter.drawText(top, Qt.AlignLeft | Qt.AlignBottom, painter.fontMetrics().elidedText(name, Qt.ElideMiddle, r.width()))
        if sub:
            f2 = QFont(option.font)
            f2.setPointSizeF(8.5)
            painter.setFont(f2)
            painter.setPen(QColor(C["bad"] if index.data(Qt.UserRole + 2) else C["faint"]))
            bottom = r.adjusted(0, r.height() // 2 + 2, 0, -6)
            painter.drawText(bottom, Qt.AlignLeft | Qt.AlignTop, painter.fontMetrics().elidedText(sub, Qt.ElideRight, r.width()))
        painter.restore()
