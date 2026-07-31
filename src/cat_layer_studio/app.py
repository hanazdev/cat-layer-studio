from __future__ import annotations

import sys

from PySide6.QtWidgets import QApplication

from cat_layer_studio.constants import APP_NAME
from cat_layer_studio.main_window import MainWindow


def main() -> int:
    application = QApplication(sys.argv)
    application.setApplicationName(APP_NAME)
    application.setOrganizationName("hanazdev")
    window = MainWindow()
    window.show()
    return application.exec()
