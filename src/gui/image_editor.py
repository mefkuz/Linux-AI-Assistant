"""Native, local image markup. All geometry is relative to the original image."""
from PyQt6.QtCore import QPointF, QRectF, Qt, pyqtSignal
from PyQt6.QtGui import QPainter
from PyQt6.QtWidgets import (QWidget, QDialog, QVBoxLayout, QHBoxLayout, QComboBox,
                            QLineEdit, QPushButton, QDialogButtonBox, QLabel)
from src.tools.image_annotations import render_annotations, validate_annotations
from src.core.i18n import tr


class AnnotationCanvas(QWidget):
    changed = pyqtSignal()

    def __init__(self, image, marks=(), parent=None, limit=12):
        super().__init__(parent)
        self.original = image.copy()
        self.base = image.copy()
        self.marks = validate_annotations(list(marks))
        self.mode, self.label, self.limit = 'circle', '', limit
        self._start = self._draft = None
        self.setMinimumSize(280, 200)
        self.setCursor(Qt.CursorShape.CrossCursor)

    def image_rect(self):
        scale = min(self.width()/self.base.width(), self.height()/self.base.height())
        w, h = self.base.width()*scale, self.base.height()*scale
        return QRectF((self.width()-w)/2, (self.height()-h)/2, w, h)

    def normalized_position(self, position):
        rect = self.image_rect()
        return [min(1,max(0,(position.x()-rect.left())/rect.width())),
                min(1,max(0,(position.y()-rect.top())/rect.height()))]

    def paintEvent(self, event):
        rect = self.image_rect()
        scaled = self.base.scaled(max(1,int(rect.width())), max(1,int(rect.height())),
                                  Qt.AspectRatioMode.IgnoreAspectRatio, Qt.TransformationMode.SmoothTransformation)
        marks = self.marks + ([self._draft] if self._draft else [])
        shown = render_annotations(scaled, marks) if marks else scaled
        painter = QPainter(self)
        painter.drawImage(rect, shown)
        painter.end()

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton and self.image_rect().contains(event.position()) and len(self.marks) < self.limit:
            self._start = self.normalized_position(event.position())

    def mouseMoveEvent(self, event):
        if self._start is None:
            return
        mark = {'shape': self.mode, 'image': 0, 'label': self.label,
                'points': self._start + self.normalized_position(event.position())}
        try:
            validate_annotations([mark])
            self._draft = mark
        except ValueError:
            self._draft = None
        self.update()

    def mouseReleaseEvent(self, event):
        if event.button() != Qt.MouseButton.LeftButton or self._start is None:
            return
        self.mouseMoveEvent(event)
        if self._draft:
            self.marks.append(self._draft)
            self.changed.emit()
        self._start = self._draft = None
        self.update()

    def undo(self):
        if self.marks:
            self.marks.pop()
            self.changed.emit()
            self.update()

    def clear(self):
        self.marks = []
        self._start = self._draft = None
        self.changed.emit()
        self.update()


class ImageAnnotationDialog(QDialog):
    def __init__(self, image, marks=(), parent=None, limit=12):
        super().__init__(parent)
        self.setWindowTitle(tr('Görseli işaretle'))
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel(tr('Sürükleyerek işaretle. (Yalnızca görsel destekleyen modeller içindir.)')))
        toolbar = QHBoxLayout()
        self.shape_combo = QComboBox()
        for name, mode in [('Daire', 'circle'), ('Ok', 'arrow'), ('Kutu', 'box')]:
            self.shape_combo.addItem(tr(name), mode)
        toolbar.addWidget(self.shape_combo)
        self.label_input = QLineEdit()
        self.label_input.setMaxLength(80)
        self.label_input.setPlaceholderText(tr('Etiket (isteğe bağlı)'))
        toolbar.addWidget(self.label_input, 1)
        self.undo_button = QPushButton(tr('Geri al'))
        self.clear_button = QPushButton(tr('İşaretleri temizle'))
        for button in (self.undo_button, self.clear_button):
            button.setAutoDefault(False)
            toolbar.addWidget(button)
        layout.addLayout(toolbar)
        self.canvas = AnnotationCanvas(image, marks, self, limit)
        layout.addWidget(self.canvas, 1)
        self.shape_combo.currentIndexChanged.connect(lambda: setattr(self.canvas, 'mode', self.shape_combo.currentData()))
        self.label_input.textChanged.connect(lambda text: setattr(self.canvas, 'label', text))
        self.undo_button.clicked.connect(self.canvas.undo)
        self.clear_button.clicked.connect(self.canvas.clear)
        self.canvas.changed.connect(self._update_buttons)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText(tr('İşaretleri kullan'))
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        screen = self.screen().availableGeometry()
        self.resize(min(image.width() + 80, int(screen.width() * .90)),
                    min(image.height() + 160, int(screen.height() * .90)))
        self._update_buttons()

    def _update_buttons(self):
        self.undo_button.setEnabled(bool(self.canvas.marks))
        self.clear_button.setEnabled(bool(self.canvas.marks))
