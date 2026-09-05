import sys
from PyQt6.QtWidgets import QApplication, QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton
from PyQt6.QtCore import Qt

class TestWindow(QWidget):
    def __init__(self):
        super().__init__()
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setStyleSheet("background: transparent;")
        
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(10)
        
        self.ctx = QWidget()
        ctx_l = QVBoxLayout(self.ctx)
        ctx_l.setContentsMargins(0, 0, 0, 0)
        
        b1 = QPushButton("▶ YouTube Videosunu Ekle")
        b1.setStyleSheet("""
            QPushButton {
                background-color: rgba(20, 20, 25, 180); color: white;
                border-radius: 12px; padding: 8px 14px; font-size: 12px;
                border: 1px solid rgba(255,255,255,30); text-align: left;
            }
        """)
        ctx_l.addWidget(b1)
        outer.addWidget(self.ctx)
        
        self.main = QWidget()
        self.main.setStyleSheet("background: rgba(16, 16, 20, 235); border-radius: 16px; border: 1px solid rgba(255,255,255,30);")
        self.main.setFixedWidth(260)
        self.main.setMinimumHeight(60)
        outer.addWidget(self.main)
        
if __name__ == '__main__':
    app = QApplication(sys.argv)
    w = TestWindow()
    w.show()
    # sys.exit(app.exec())
