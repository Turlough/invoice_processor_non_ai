"""Entry point for the invoice processor PoC."""

import sys

from PyQt6.QtWidgets import QApplication

from invoice_app.main_window import MainWindow


def main() -> None:
    app = QApplication(sys.argv)
    window = MainWindow()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
