"""
Sovelluksen aloituspiste (QApplication + komponenttien kasaaminen).
Kutsu tätä moduulia: python -m src.app
"""

import sys
import os

# Ohjelman juuren lisääminen polkuun jos ajetaan suoraan
_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.abspath(os.path.join(_HERE, os.pardir))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)


SKYHANNI_THEME_QSS = """
/* ===== SkyHanni-inspired teema (tumma Minecraft-tyylinen GUI) ===== */

QWidget {
    background-color: #151515;
    color: #d9d9d9;
    /* Monospace/pixel-tyylinen fontti, kuten Minecraft/SkyHanni GUI:ssa */
    font-family: "Consolas", "Courier New", "DejaVu Sans Mono", monospace;
    font-size: 12px;
}

/* Pääikkunan tausta (kuten SkyHanni Categories-paneeli) */
QMainWindow, #MainWindow {
    background-color: #131313;
}

/* Tabs (välilehdet) */
QTabWidget::pane {
    border: 2px solid #3a3a3a;
    background: #1a1a1a;
    top: -1px;
}
QTabBar::tab {
    background: #242424;
    border: 2px solid #3a3a3a;
    border-bottom: none;
    padding: 6px 16px;
    margin-right: 4px;
    color: #c8c8c8;
    min-width: 80px;
}
QTabBar::tab:selected {
    background: #2e2e2e;
    border-color: #555555;
    color: #ffffff;
}

/* GroupBox / Form */
QGroupBox {
    border: 2px solid #3a3a3a;
    margin-top: 12px;
    padding-top: 8px;
    background-color: #1e1e1e;
}
QGroupBox::title {
    subcontrol-origin: margin;
    left: 10px;
    padding: 0 6px;
    color: #b886ff;  /* SkyHanni-violetti */
}

/* Labels */
QLabel {
    background: transparent;
    color: #d9d9d9;
}

/* Syöttökentät */
QLineEdit, QPlainTextEdit, QTextEdit {
    background: #080808;
    border: 2px solid #2e2e2e;
    color: #ffffff;
    selection-background-color: #5a4b8a;
    selection-color: #ffffff;
    padding: 4px 6px;
}
QLineEdit:focus, QPlainTextEdit:focus, QTextEdit:focus {
    border-color: #b886ff;  /* violetti focus */
}

/* Spinboxit & sliderit */
QSpinBox, QDoubleSpinBox, QComboBox {
    background: #0d0d0d;
    border: 2px solid #2e2e2e;
    color: #ffffff;
    min-height: 22px;
    padding: 2px 4px;
}
QSpinBox:focus, QDoubleSpinBox:focus, QComboBox:focus {
    border-color: #7ad0ff;  /* cyan focus kuten SkyHanni */
}
QComboBox::drop-down {
    border-left: 2px solid #2e2e2e;
    width: 20px;
}
QComboBox QAbstractItemView {
    background: #0d0d0d;
    border: 2px solid #3a3a3a;
    color: #ffffff;
    selection-background-color: #44356a;
}
QSlider::groove:horizontal {
    border: 1px solid #3a3a3a;
    background: #0a0a0a;
    height: 8px;
}
QSlider::handle:horizontal {
    background: #b886ff;
    border: 2px solid #7a58c8;
    width: 14px;
    margin: -6px 0;
}

/* Yleinen nappi – 3D-ilme (kuten SkyHanni Add-nappi kuvassa) */
QPushButton {
    background-color: #2b2b2b;
    border-top:    2px solid #4a4a4a;
    border-left:   2px solid #4a4a4a;
    border-right:  2px solid #151515;
    border-bottom: 2px solid #151515;
    color: #ffffff;
    padding: 6px 14px;
    font-weight: 700;
}
QPushButton:hover {
    background-color: #333333;
}
QPushButton:pressed {
    /* Painettuna -> reunat kääntyvät = 3D painallus */
    background-color: #242424;
    border-top:    2px solid #151515;
    border-left:   2px solid #151515;
    border-right:  2px solid #4a4a4a;
    border-bottom: 2px solid #4a4a4a;
}
QPushButton:disabled {
    color: #777777;
}

/* CheckBox + Radio */
QCheckBox, QRadioButton {
    spacing: 6px;
    color: #e5e5e5;
}
QCheckBox::indicator, QRadioButton::indicator {
    width: 14px;
    height: 14px;
    border: 2px solid #555555;
    background: #080808;
}
QCheckBox::indicator:checked, QRadioButton::indicator:checked {
    background: #7ad0ff;   /* SkyHanni-cyan tarkastusmerkki */
    border-color: #b5e7ff;
}

/* ScrollBar */
QScrollBar:vertical {
    background: #0a0a0a;
    border: 2px solid #2a2a2a;
    width: 14px;
    margin: 0;
}
QScrollBar::handle:vertical {
    background: #3a3a3a;
    border: 1px solid #555555;
    min-height: 24px;
}
QScrollBar::handle:vertical:hover { background: #b886ff; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
QScrollBar:horizontal {
    background: #0a0a0a;
    border: 2px solid #2a2a2a;
    height: 14px;
}
QScrollBar::handle:horizontal { background: #3a3a3a; min-width: 24px; }
QScrollBar::handle:horizontal:hover { background: #b886ff; }

/* Frame / separaattori */
QFrame[frameShape="4"], QFrame[frameShape="5"] {   /* HLine / VLine */
    background: #333333;
    max-height: 2px;
}

/* Toolbar */
QToolBar {
    background: #181818;
    border: 1px solid #333333;
    spacing: 6px;
    padding: 4px;
}
QToolBar QLabel {
    padding-left: 10px;
    padding-right: 4px;
}

/* Status-label (palkin päällä) – tumma laatikko */
QFrame#StatusLabel {
    border: 2px solid #3a3a3a;
    background: #0f0f0f;
}
"""


def main() -> int:
    """Sovelluksen entry point. Palauttaa exit-koodin."""

    # TÄRKEÄ TURVALLISUUSVARMISTUS (Hypixel-yhteensopivuus):
    #
    # Tämä sovellus on ILMOITUSTYÖKALU. Se EI MISSÄÄN NÄYTÄ
    # pysty simuloimaan näppäinpainalluksia, klikkauksia tai mitään
    # muuta syötettä Minecraftiin tai mihinkään muuhun ohjelmaan.
    #
    # Sallitut toimenpiteet:
    #   ✅ Lue lokitiedostoja (latest.log)
    #   ✅ Näytä oma ikkuna (overlay) päällä
    #   ✅ Toista ääni (QSoundEffect, vain .wav)
    #   ✅ Hyväksy käyttäjän napinpainallukset omassa GUI:ssa
    #
    # Kiellettyjä toimenpiteitä (eivät ole koodissa mukana, eikä niitä
    # saa lisätä ilman erillistä lupaa):
    #   ❌ SendInput / keybd_event / mouse_event
    #   ❌ PostMessage/SendMessage toisten ikkunoiden hallintaan
    #   ❌ muistiin kirjoittaminen (readProcessMemory ok, write kielletty)
    #   ❌ mikä tahansa muu syötteiden generointi

    from PySide6.QtWidgets import QApplication
    from PySide6.QtGui import QFont

    from src.config import ConfigManager
    from src.sound_manager import SoundManager
    from src.overlay import OverlayWindow
    from src.detector import LaneDetector
    from src.main_window import MainWindow

    app = QApplication(sys.argv)
    app.setApplicationName("Skyblock Lane Overlay")
    app.setOrganizationName("SkyblockTools")
    app.setQuitOnLastWindowClosed(False)  # System tray pitää yllä

    # Soveltaa SkyHanni-tyylinen tumma GUI-teema (mukautetun QSS:n kautta)
    app.setStyle("Fusion")               # tarvitaan että QSS käyttäytyy ennustettavasti
    app.setStyleSheet(SKYHANNI_THEME_QSS)
    try:
        # Monospace fontti koko sovellukselle
        f = QFont("Consolas", 10)
        f.setStyleStrategy(QFont.PreferAntialias)
        app.setFont(f)
    except Exception:
        pass

    # 1) Asetukset
    cfg_mgr = ConfigManager()
    config = cfg_mgr.load()

    # 2) Äänet (alustetaan aikaisin, jotta äänet ovat käytössä)
    sound_mgr = SoundManager()
    sound_mgr.set_volume(config.sound_volume)
    sound_mgr.set_enabled(config.sound_enabled)
    # Lataa mahdolliset custom äänitiedostot konfigista
    sl = getattr(config, "sound_file_left", "") or None
    sr = getattr(config, "sound_file_right", "") or None
    if sl or sr:
        sound_mgr.set_custom_sound(sl, sr)

    # 3) Overlay (ei vielä näytetä)
    overlay = OverlayWindow(config, sound_mgr)

    # 4) Tunnistus (vain Minecraftin logi)
    detector = LaneDetector(config)

    # 5) Pääikkuna (sisältää system tray:n)
    main_w = MainWindow(cfg_mgr, overlay, detector, sound_mgr)
    main_w.show()

    ret = app.exec()

    return ret


if __name__ == "__main__":
    sys.exit(main())
