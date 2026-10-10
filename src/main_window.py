"""
Pääikkuna: asetukset, testi-napit, monitorin valinta, debug-näyttö, pest-cooldown näyttö.
"""

import os
from typing import Optional

from PySide6.QtCore import Qt, QTimer, Signal, QSize, QPoint, QRect
try:
    from PySide6.QtCore import QPolygon
except ImportError:
    from PySide6.QtGui import QPolygon  # type: ignore[attr-defined, no-redef]

from PySide6.QtGui import QIcon, QPixmap, QPainter, QColor, QAction, QFont
from PySide6.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QComboBox, QGroupBox, QFormLayout, QSpinBox, QDoubleSpinBox, QLineEdit,
    QCheckBox, QColorDialog, QFileDialog, QSlider, QMessageBox, QTabWidget,
    QPlainTextEdit, QSystemTrayIcon, QMenu, QApplication, QFrame, QToolBar,
    QProgressBar, QRadioButton, QScrollArea,
)

from .config import AppConfig, ConfigManager, LOADOUT_KILL, LOADOUT_SPAWN
from .overlay import OverlayWindow, DIRECTION_LEFT, DIRECTION_RIGHT
from .detector import (
    LaneDetector, DetectionResult, PestEvent, PestEventType, DetectionSource,
    LoadoutEvent, LoadoutEventType,
)
from .sound_manager import SoundManager

try:
    import win32con
    import win32gui
except ImportError:
    win32con = None  # type: ignore[assignment]
    win32gui = None  # type: ignore[assignment]


_MINECRAFT_WINDOW_TITLE_KEYWORDS = (
    "minecraft",
    "badlion",
    "lunar client",
    "feather client",
    "feather",
    "skyclient",
)
_MINECRAFT_WINDOW_CLASS_KEYWORDS = (
    "lwjgl",
    "glfw30",
)


def _fmt_ms(ms: int) -> str:
    """Muunna ms 'min:sek' tai 'sek' muotoon."""
    if ms <= 0:
        return "0:00"
    s_total = ms // 1000
    m, s = divmod(s_total, 60)
    return f"{m}:{s:02d}"


class MainWindow(QMainWindow):
    """Pääikkuna, koko sovelluksen kontrolli täältä käsin."""

    lane_triggered = Signal(str)
    pest_event = Signal(object)   # PestEvent
    loadout_event = Signal(object)  # LoadoutEvent

    def __init__(self, config_mgr: ConfigManager, overlay: OverlayWindow,
                 detector: LaneDetector,
                 sound_mgr: SoundManager):
        super().__init__()
        self._cfg_mgr = config_mgr
        self._config: AppConfig = config_mgr.config
        self._overlay = overlay
        self._detector = detector
        self._sound = sound_mgr

        # Tarkistus timer (logi)
        self._scan_timer = QTimer(self)
        self._scan_timer.timeout.connect(self._on_scan_tick)
        self._running = False

        # Pest cooldown näyttö timer (päivittää GUI:n progressbar + label 4x / s)
        self._pest_ui_timer = QTimer(self)
        self._pest_ui_timer.setInterval(200)
        self._pest_ui_timer.timeout.connect(self._update_pest_ui_state)
        self._pest_ui_timer.start()

        # System tray
        self._tray: Optional[QSystemTrayIcon] = None

        # Pending-pest eventti: mikä SPAWN/READY -tapahtuma odottaa että käyttäjä
        # vaihtaa oikeaan loadoutiin (loadout_block_until_correct päällä).
        # Tämä näytetään jälkeen kun oikea loadout tunnistetaan.
        self._pending_pest_event: Optional[PestEvent] = None

        self.setWindowTitle("Skyblock Lane Overlay")
        self.resize(880, 680)
        self.setMinimumSize(780, 600)

        self._build_ui()
        self._init_tray()
        self._load_ui_from_config()
        self._update_monitor_list()
        self._update_run_state()
        self._update_counter_label()
        self._update_pest_ui_state()

        self.lane_triggered.connect(self._on_lane_triggered)
        self.pest_event.connect(self._on_pest_event)
        self.loadout_event.connect(self._on_loadout_event)

    # ============================================================
    # UI-rakenne
    # ============================================================

    def _build_ui(self) -> None:
        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setContentsMargins(14, 14, 14, 14)
        root.setSpacing(10)

        # Yläpalkki: tila, on/off, testi-napit
        top = QHBoxLayout()
        self._lbl_status = QLabel("● Seis")
        self._lbl_status.setStyleSheet(
            "color:#aaa; font-weight:600; font-size:14px; padding:4px 10px;"
            "background:#222; border-radius:8px;"
        )
        self._btn_run = QPushButton("▶  Käynnistä logi tarkistus")
        self._btn_run.setMinimumHeight(36)
        self._btn_run.clicked.connect(self._toggle_running)
        self._btn_test_left = QPushButton("⬅  Testaa vasen")
        self._btn_test_left.clicked.connect(
            lambda: self._manual_trigger(DIRECTION_LEFT))
        self._btn_test_right = QPushButton("Testaa oikea  ➡")
        self._btn_test_right.clicked.connect(
            lambda: self._manual_trigger(DIRECTION_RIGHT))
        self._lbl_counter = QLabel("Lane-vaihdot: 0")
        self._lbl_counter.setFont(QFont("Segoe UI", 11, QFont.DemiBold))
        self._btn_reset_counter = QPushButton("Nollaa")
        self._btn_reset_counter.setMaximumWidth(72)
        self._btn_reset_counter.clicked.connect(self._reset_counter)

        top.addWidget(self._lbl_status)
        top.addSpacing(8)
        top.addWidget(self._btn_run, 1)
        top.addWidget(self._btn_test_left)
        top.addWidget(self._btn_test_right)
        top.addSpacing(16)
        top.addWidget(self._lbl_counter)
        top.addWidget(self._btn_reset_counter)
        root.addLayout(top)

        # Tabs
        self._tabs = QTabWidget()
        self._tabs.addTab(self._wrap_in_scroll(self._build_tab_log()), "1. Logi & Tunnistus")
        self._tabs.addTab(self._wrap_in_scroll(self._build_tab_overlay()), "2. Overlay")
        self._tabs.addTab(self._wrap_in_scroll(self._build_tab_pest()), "3. Pestit")
        self._tabs.addTab(self._wrap_in_scroll(self._build_tab_sound()), "4. Äänet")
        self._tabs.addTab(self._wrap_in_scroll(self._build_tab_misc()), "5. Muuta")
        self._tabs.addTab(self._build_tab_debug(), "Debug")
        root.addWidget(self._tabs, 1)

        # Alapalkki: Tallenna, Lataa, Nollaa
        bottom = QHBoxLayout()
        self._btn_save = QPushButton("💾 Tallenna asetukset")
        self._btn_save.clicked.connect(self._save_and_reload)
        self._btn_load = QPushButton("🔄 Lataa tiedostosta")
        self._btn_load.clicked.connect(self._reload_from_disk)
        self._btn_reset = QPushButton("⚠  Nollaa oletuksiin")
        self._btn_reset.clicked.connect(self._reset_defaults)
        bottom.addStretch(1)
        bottom.addWidget(self._btn_save)
        bottom.addWidget(self._btn_load)
        bottom.addWidget(self._btn_reset)
        root.addLayout(bottom)

    def _wrap_in_scroll(self, inner: QWidget) -> QScrollArea:
        """Kääri asetussivu QScrollArea:iin, jotta voi scrollata pientä ruutua."""
        sa = QScrollArea()
        sa.setWidgetResizable(True)
        sa.setFrameShape(QFrame.NoFrame)
        sa.setWidget(inner)
        return sa

    # ---------------------------------------------------------------
    # Tab 1: Logi & Tunnistus
    # ---------------------------------------------------------------

    def _build_tab_log(self) -> QWidget:
        w = QWidget()
        f = QFormLayout(w)
        f.setContentsMargins(14, 14, 14, 14)
        f.setSpacing(10)

        self._spin_interval = QSpinBox()
        self._spin_interval.setRange(30, 2000); self._spin_interval.setSuffix(" ms")
        self._spin_interval.setSingleStep(10)

        self._chk_detect = QCheckBox("Logi tarkistus päällä (detektio)")
        self._chk_detect.setChecked(True)

        # Loki
        self._chk_log = QCheckBox("💎 Lue Minecraftin lokia (100% luotettava — SkyHanni chat)")
        self._chk_log.setChecked(True)
        self._ed_logpath = QLineEdit()
        self._ed_logpath.setPlaceholderText(
            r"esim. %APPDATA%\.minecraft\logs\latest.log — tyhjä = auto-detect")
        self._btn_browse_log = QPushButton("Selaa...")
        self._btn_browse_log.clicked.connect(self._browse_log)
        self._btn_auto_log = QPushButton("🔍 Auto-detect")
        self._btn_auto_log.clicked.connect(self._auto_log_path)
        log_row = QHBoxLayout(); log_row.addWidget(self._ed_logpath, 1)
        log_row.addWidget(self._btn_browse_log); log_row.addWidget(self._btn_auto_log)
        log_wrap = QWidget(); log_wrap.setLayout(log_row)

        # Alternating direction + initial direction
        self._chk_alt_dir = QCheckBox(
            "🔁 Vaihtoehtoinen suunta (A↔D): käännä vasen ↔ oikea JOKAISEN "
            "Lane Switch -viestin jälkeen (suositeltu 2-linjaisessa farmingissa)")
        self._chk_alt_dir.setChecked(True)
        self._radio_initial_left = QRadioButton("🔺 Alkusuunta = VASEN (A)")
        self._radio_initial_right = QRadioButton("🔻 Alkusuunta = OIKEA (D)")
        self._radio_initial_left.setChecked(True)
        init_dir_row = QHBoxLayout()
        init_dir_row.addWidget(self._radio_initial_left); init_dir_row.addSpacing(20)
        init_dir_row.addWidget(self._radio_initial_right); init_dir_row.addStretch(1)
        init_dir_wrap = QWidget(); init_dir_wrap.setLayout(init_dir_row)

        set_cur_title = QLabel("<b>Aseta nykyinen odotettu suunta (session aikana):</b>")
        self._btn_cur_left = QPushButton("⬅  Nykyinen = VASEN")
        self._btn_cur_right = QPushButton("Nykyinen = OIKEA ➡")
        self._btn_cur_left.setStyleSheet(self._btn_style_solid("#ff3b30"))
        self._btn_cur_right.setStyleSheet(self._btn_style_solid("#34c759"))
        self._btn_cur_left.clicked.connect(lambda: self._set_current_direction("left"))
        self._btn_cur_right.clicked.connect(lambda: self._set_current_direction("right"))
        cur_row = QHBoxLayout()
        cur_row.addWidget(self._btn_cur_left, 1)
        cur_row.addSpacing(10)
        cur_row.addWidget(self._btn_cur_right, 1)
        cur_wrap = QWidget(); cur_wrap.setLayout(cur_row)

        self._lbl_current_dir = QLabel("Seuraava: VASEN (A)")
        self._lbl_current_dir.setStyleSheet(
            "font-weight:600; font-size:14px; padding:8px 14px;"
            "background:#1a2638; color:#ff9e96; border-radius:8px;"
        )

        f.addRow("Tarkistusväli:", self._spin_interval)
        f.addRow("", self._chk_detect)
        sep1 = QFrame(); sep1.setFrameShape(QFrame.HLine); sep1.setStyleSheet("color:#444;")
        f.addRow(sep1)
        f.addRow(QLabel("<b>🏅 Minecraft-logi (Ainoa tunnistustapa):</b>"))
        f.addRow("", self._chk_log)
        f.addRow("latest.log polku:", log_wrap)
        sep2 = QFrame(); sep2.setFrameShape(QFrame.HLine); sep2.setStyleSheet("color:#444;")
        f.addRow(sep2)
        f.addRow(QLabel("<b>🧭 Suunnan hallinta (Garden 2-key A ↔ D farming):</b>"))
        f.addRow("", self._chk_alt_dir)
        f.addRow("Alkusuunta (1. vaihto):", init_dir_wrap)
        f.addRow(set_cur_title)
        f.addRow(cur_wrap)
        f.addRow("Seuraava suunta:", self._lbl_current_dir)
        return w

    # ---------------------------------------------------------------
    # Tab 2: Overlay
    # ---------------------------------------------------------------

    def _build_tab_overlay(self) -> QWidget:
        w = QWidget()
        f = QFormLayout(w)
        f.setContentsMargins(14, 14, 14, 14); f.setSpacing(10)

        info = QLabel(
            "✅ <b>Kaikki alla olevat asetukset koskevat <u>kaikkia</u> overlay-"
            "ilmoitustyylejä:</b><br>"
            "&nbsp;&nbsp;• <b>Lane-nuoli</b> (vasen/oikea vaihto)<br>"
            "&nbsp;&nbsp;• <b>Pest-spawn</b> (pestit tulossa -ilmoitus)<br>"
            "&nbsp;&nbsp;• <b>Pest-cooldown ohi</b> (READY -ilmoitus)<br>"
            "&nbsp;&nbsp;• <b>Väärä loadout</b> -varoitus<br><br>"
            "📌 Ilmoitukset näytetään <b>samanaikaisesti allekkain</b> (korteissa) "
            "valittuun sijaintiin – uusi ilmoitus <b>ei poista</b> vanhaa ennen "
            "sen aikakatkaisua."
        )
        info.setWordWrap(True)
        info.setStyleSheet(
            "padding:10px 14px; background:#0e1a28; color:#bfe2ff;"
            "border:1px solid #2e5e8e; border-radius:8px;"
        )

        self._cmb_overlay_monitor = QComboBox()
        self._cmb_position = QComboBox()
        for key, label in [("center", "Keskelle ruutua"),
                           ("top", "Yläreuna (keskellä)"),
                           ("bottom", "Alareuna (keskellä)"),
                           ("left", "Vasen reuna (keskellä)"),
                           ("right", "Oikea reuna (keskellä)"),
                           ("custom", "Mukautettu (offset x/y = suhteellinen ruutuun)")]:
            self._cmb_position.addItem(label, key)

        self._spin_offset_x = QSpinBox(); self._spin_offset_x.setRange(-10000, 10000)
        self._spin_offset_y = QSpinBox(); self._spin_offset_y.setRange(-10000, 10000)
        offs = QHBoxLayout()
        offs.addWidget(QLabel("X:")); offs.addWidget(self._spin_offset_x, 1)
        offs.addSpacing(8); offs.addWidget(QLabel("Y:")); offs.addWidget(self._spin_offset_y, 1)
        offs_w = QWidget(); offs_w.setLayout(offs)

        # overlay_size = 'perus' koko, josta kaikki kortit skaalautuvat
        self._spin_size = QSpinBox(); self._spin_size.setRange(80, 600); self._spin_size.setSuffix(" px")
        self._spin_size.setToolTip(
            "Peruskoko (px). Lane-nuoli = tämä suoraan. Pest/loadout-kortti "
            "= leveämpi mutta korkeus perustuu tähän.")
        self._spin_timeout = QSpinBox(); self._spin_timeout.setRange(500, 15000); self._spin_timeout.setSuffix(" ms")
        self._spin_timeout.setSingleStep(100)
        self._spin_delay = QSpinBox(); self._spin_delay.setRange(0, 10000); self._spin_delay.setSuffix(" ms")
        self._spin_delay.setSingleStep(50)
        self._spin_delay.setToolTip(
            "Viive (Lane-nuolille) ennen overlay-näyttämistä. SkyHanni:ssa voi haluta "
            "esim. 800 ms jotta overlay synkronoituu oikeaan ajanhetkeen.")
        self._slide_opacity = QSlider(Qt.Horizontal); self._slide_opacity.setRange(30, 100)
        self._chk_flash = QCheckBox("✨ Käytä flash-efektiä kaikissa korteissa (uusi ilmoitus välkkyy hetken)")
        self._chk_show_text = QCheckBox("🗒  Näytä teksti Lane-nuolen alla")
        self._ed_text = QLineEdit()

        # Värivalitsimet
        self._btn_color_left = self._make_color_button("#ff3b30")
        self._btn_color_right = self._make_color_button("#34c759")
        self._btn_color_text = self._make_color_button("#ffffff")
        self._btn_color_left.clicked.connect(lambda: self._pick_color(self._btn_color_left, "left"))
        self._btn_color_right.clicked.connect(lambda: self._pick_color(self._btn_color_right, "right"))
        self._btn_color_text.clicked.connect(lambda: self._pick_color(self._btn_color_text, "text"))

        f.addRow(info)
        sep_info = QFrame(); sep_info.setFrameShape(QFrame.HLine); sep_info.setStyleSheet("color:#444;")
        f.addRow(sep_info)
        f.addRow(QLabel("<b>📍 Sijainti & Ruutu (kaikille ilmoituksille yhteinen):</b>"))
        f.addRow("Overlay-ruutu:", self._cmb_overlay_monitor)
        f.addRow("Sijainti (kaikki kortit menevät tähän allekkain):", self._cmb_position)
        f.addRow("Hienosäädös (offset X/Y):", offs_w)
        f.addRow(QLabel("<b>📏 Koko & Aika:</b>"))
        f.addRow("Peruskoko (nuoli + kaikkien korttien skaalaus):", self._spin_size)
        f.addRow("Peittävyys / Läpinäkyvyys (kaikki kortit):", self._slide_opacity)
        f.addRow("", self._chk_flash)
        sep1 = QFrame(); sep1.setFrameShape(QFrame.HLine); sep1.setStyleSheet("color:#444;")
        f.addRow(sep1)
        f.addRow(QLabel("<b>🟢 Lane-switch (nuoli) – erikoisasetukset:</b>"))
        f.addRow("Näkyvyysaika (nuoli jää kauanko):", self._spin_timeout)
        f.addRow("Viive ennen näyttämistä (sync SkyHanni):", self._spin_delay)
        f.addRow("", self._chk_show_text)
        f.addRow("Overlay-teksti (Lane-nuolen alla):", self._ed_text)
        cl_row = QHBoxLayout(); cl_row.addWidget(QLabel("Vasen nuoli (A):"), 0); cl_row.addWidget(self._btn_color_left, 1)
        cr_row = QHBoxLayout(); cr_row.addWidget(QLabel("Oikea nuoli (D):"), 0); cr_row.addWidget(self._btn_color_right, 1)
        ct_row = QHBoxLayout(); ct_row.addWidget(QLabel("Kaikkien tekstien väri:"), 0); ct_row.addWidget(self._btn_color_text, 1)
        cl = QWidget(); cl.setLayout(cl_row); cr = QWidget(); cr.setLayout(cr_row); ct = QWidget(); ct.setLayout(ct_row)
        f.addRow(cl); f.addRow(cr); f.addRow(ct)
        return w

    # ---------------------------------------------------------------
    # Tab 3: Pestit
    # ---------------------------------------------------------------

    def _build_tab_pest(self) -> QWidget:
        w = QWidget()
        f = QFormLayout(w); f.setContentsMargins(14,14,14,14); f.setSpacing(10)

        # --- Tila ---
        self._lbl_pest_state = QLabel("Cooldown: EI KÄYNNISSÄ")
        self._lbl_pest_state.setStyleSheet(
            "font-size:15px; font-weight:800; padding:10px 14px;"
            "background:#222; border:1px solid #444; border-radius:8px; color:#ccc;"
        )
        self._lbl_pest_remaining = QLabel("Jäljellä: 2:15")
        self._lbl_pest_remaining.setStyleSheet(
            "font-size:22px; font-weight:800; padding:10px 14px;"
            "color:#00c7ff; background:#0b1e26; border:2px solid #00c7ff; border-radius:10px;"
        )
        self._progress_pest = QProgressBar()
        self._progress_pest.setRange(0, 1000)
        self._progress_pest.setValue(0)
        self._progress_pest.setTextVisible(False)
        self._progress_pest.setFormat("")
        self._progress_pest.setStyleSheet("""
            QProgressBar {
                border: 2px solid #444;
                border-radius: 8px;
                background: #111;
                height: 22px;
            }
            QProgressBar::chunk {
                border-radius: 6px;
                background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
                    stop:0 #00c7ff, stop:1 #b886ff);
            }
        """)

        # --- Asetukset ---
        self._chk_pest_enabled = QCheckBox("🔔 Pest-seuranta päällä (spawn + cooldown ilmoitukset)")
        self._chk_pest_enabled.setChecked(True)
        self._chk_pest_show_spawn = QCheckBox("Näytä ilmoitus KUN pestit spawnaavat (SPAWN)")
        self._chk_pest_show_spawn.setChecked(True)
        self._chk_pest_show_ready = QCheckBox("Näytä ilmoitus KUN cooldown loppuu (READY / seuraavat voi tulla)")
        self._chk_pest_show_ready.setChecked(True)
        self._chk_activate_minecraft_on_spawn = QCheckBox(
            "🎮 Tuo Minecraft eteen kun pestit spawnaavat (vain oikeasta logi-eventistä)"
        )
        self._chk_move_minecraft_on_spawn = QCheckBox(
    "🎮 Siirrä Minecraft toiselle ruudulle kun pestit spawnaavat"
    )
        self._chk_move_minecraft_on_spawn.setChecked(True)

        self._cmb_minecraft_move_mode = QComboBox()
        self._cmb_minecraft_move_mode.addItem("Vaihda nykyinen ↔ toinen (1 ↔ 2)", "swap")
        self._cmb_minecraft_move_mode.addItem("Siirrä aina ruudulle 1", "to_1")
        self._cmb_minecraft_move_mode.addItem("Siirrä aina ruudulle 2", "to_2")
        self._cmb_minecraft_move_mode.addItem("Siirrä aina sille ruudulle jossa Minecraft ei ole", "to_other")
        self._chk_activate_minecraft_on_spawn.setChecked(True)

        self._spin_pest_cooldown_m = QSpinBox(); self._spin_pest_cooldown_m.setRange(0, 10)
        self._spin_pest_cooldown_m.setSuffix(" min")
        self._spin_pest_cooldown_s = QSpinBox(); self._spin_pest_cooldown_s.setRange(0, 59)
        self._spin_pest_cooldown_s.setSuffix(" s")
        cd_row = QHBoxLayout()
        cd_row.addWidget(QLabel("Minuutit:")); cd_row.addWidget(self._spin_pest_cooldown_m)
        cd_row.addSpacing(10)
        cd_row.addWidget(QLabel("Sekunnit:")); cd_row.addWidget(self._spin_pest_cooldown_s)
        cd_row.addStretch(1)
        cd_wrap = QWidget(); cd_wrap.setLayout(cd_row)

        self._spin_pest_timeout = QSpinBox()
        self._spin_pest_timeout.setRange(1000, 15000); self._spin_pest_timeout.setSuffix(" ms")
        self._spin_pest_timeout.setSingleStep(250)

        # Pest tekstit + värit
        self._ed_pest_spawn_text = QLineEdit()
        self._ed_pest_ready_text = QLineEdit()
        self._btn_pest_color_spawn = self._make_color_button("#ff9500")
        self._btn_pest_color_ready = self._make_color_button("#00c7ff")
        self._btn_pest_color_spawn.clicked.connect(lambda: self._pick_color(self._btn_pest_color_spawn, "pest_spawn"))
        self._btn_pest_color_ready.clicked.connect(lambda: self._pick_color(self._btn_pest_color_ready, "pest_ready"))

        spawn_row = QHBoxLayout()
        spawn_row.addWidget(self._ed_pest_spawn_text, 1)
        spawn_row.addWidget(QLabel("Väri:"))
        spawn_row.addWidget(self._btn_pest_color_spawn)
        spawn_wrap = QWidget(); spawn_wrap.setLayout(spawn_row)

        ready_row = QHBoxLayout()
        ready_row.addWidget(self._ed_pest_ready_text, 1)
        ready_row.addWidget(QLabel("Väri:"))
        ready_row.addWidget(self._btn_pest_color_ready)
        ready_wrap = QWidget(); ready_wrap.setLayout(ready_row)

        # Testi-napit
        self._btn_pest_test_spawn = QPushButton("🐞 Testaa: PEST SPAWN (aloita cooldown)")
        self._btn_pest_test_spawn.setStyleSheet(self._btn_style_solid("#ff9500", "#111"))
        self._btn_pest_test_spawn.clicked.connect(self._on_test_pest_spawn)
        self._btn_pest_test_ready = QPushButton("✅ Testaa: COOLDOWN OHI (ready)")
        self._btn_pest_test_ready.setStyleSheet(self._btn_style_solid("#00c7ff", "#111"))
        self._btn_pest_test_ready.clicked.connect(self._on_test_pest_ready)
        self._btn_pest_reset = QPushButton("❌ Nollaa pest-cooldown")
        self._btn_pest_reset.clicked.connect(self._on_pest_reset)

        test_row = QHBoxLayout()
        test_row.addWidget(self._btn_pest_test_spawn, 1)
        test_row.addWidget(self._btn_pest_test_ready, 1)
        test_row.addWidget(self._btn_pest_reset, 1)
        test_wrap = QWidget(); test_wrap.setLayout(test_row)

        # --- Loadout-osio ---
        self._lbl_current_loadout = QLabel("❓ Tuntematon (ei vielä 'You equipped' -viestiä)")
        self._lbl_current_loadout.setStyleSheet(
            "font-size:15px; font-weight:700; padding:8px 12px;"
            "color:#8e8e93; background:#11151b; border:2px solid #8e8e93; border-radius:8px;"
        )

        self._chk_loadout_enabled = QCheckBox("🛡  Loadout-tarkistus päällä")
        self._chk_loadout_enabled.setToolTip(
            "SPAWN → pitää olla 1=Pest kill / READY → pitää olla 2=Pest spawn. "
            "Jos väärä → punainen varoitus eikä varsinaista ilmoitusta näytetä."
        )
        self._chk_loadout_block = QCheckBox(
            "❌ Blokataan alkuperäinen ilmoitus kunnes loadout on oikein"
        )
        self._chk_loadout_block.setChecked(True)

        self._cbo_loadout_on_spawn = QComboBox()
        self._cbo_loadout_on_spawn.addItem("1. Pest kill", LOADOUT_KILL)
        self._cbo_loadout_on_spawn.addItem("2. Pest spawn", LOADOUT_SPAWN)
        self._cbo_loadout_on_ready = QComboBox()
        self._cbo_loadout_on_ready.addItem("1. Pest kill", LOADOUT_KILL)
        self._cbo_loadout_on_ready.addItem("2. Pest spawn", LOADOUT_SPAWN)

        self._btn_loadout_test_kill = QPushButton("💉 Simuloi: 1=Pest kill")
        self._btn_loadout_test_kill.clicked.connect(
            lambda: self.loadout_event.emit(self._detector.loadout_trigger_now(LOADOUT_KILL))
        )
        self._btn_loadout_test_spawn_btn = QPushButton("🌱 Simuloi: 2=Pest spawn")
        self._btn_loadout_test_spawn_btn.clicked.connect(
            lambda: self.loadout_event.emit(self._detector.loadout_trigger_now(LOADOUT_SPAWN))
        )
        lo_test_row = QHBoxLayout()
        lo_test_row.addWidget(self._btn_loadout_test_kill, 1)
        lo_test_row.addWidget(self._btn_loadout_test_spawn_btn, 1)
        lo_test_wrap = QWidget(); lo_test_wrap.setLayout(lo_test_row)

        self._ed_loadout_wrong_text = QLineEdit()
        self._btn_loadout_wrong_color = self._make_color_button("#ff2d55")
        self._btn_loadout_wrong_color.clicked.connect(
            lambda: self._pick_color(self._btn_loadout_wrong_color, "loadout_wrong")
        )
        lo_wrong_row = QHBoxLayout()
        lo_wrong_row.addWidget(self._ed_loadout_wrong_text, 1)
        lo_wrong_row.addWidget(QLabel("Väri:"))
        lo_wrong_row.addWidget(self._btn_loadout_wrong_color)
        lo_wrong_wrap = QWidget(); lo_wrong_wrap.setLayout(lo_wrong_row)

        # Info
        pest_tip = QLabel(
            "💡 <b>SkyHanni</b> kirjoittaa chattiin aina kun pestit spawnaavat: "
            "<code>§eX §aPests Spawned in §bY§a!</code>. "
            "Sovellus lukee tämän viestin logista, näyttää ilmoituksen <b>heti</b>, "
            "ja käynnistää <b>2 min 15 s</b> cooldown-ajastimen. "
            "Kun cooldown loppuu → <b>READY</b>-ilmoitus, että seuraavat pestit voivat millä tahansa hetkellä spawnata.<br><br>"
            "🎯 <b>Loadout-säännöt (oma farmaus):</b><br>"
            "• <b>Cooldownin aikana (SPAWN-tapahtuma)</b> → pitää olla <b>1. Pest kill</b> (tappoloadout).<br>"
            "• <b>Cooldown loppunut (READY)</b> → vaihda <b>2. Pest spawn</b> (spawniloadout), "
            "jotta saat uudet pestit nopeammin kun ne ilmestyvät.<br>"
            "• Jos olet väärässä loadoutissa → näytetään punainen <b>VÄÄRÄ LOADOUT</b> -varoitus eikä varsinaista ilmoitusta lähetetä ennenkuin vaihdat oikeaan."
        )
        pest_tip.setWordWrap(True)
        pest_tip.setStyleSheet("padding:10px; background:#2a1a0c; color:#ffd9a8; border-radius:8px;")
        f.addRow("", self._chk_move_minecraft_on_spawn)
        f.addRow("Ruutuvaihdon tapa:", self._cmb_minecraft_move_mode)
        f.addRow("Tila:", self._lbl_pest_state)
        f.addRow("Cooldown:", self._lbl_pest_remaining)
        f.addRow("Edistyminen:", self._progress_pest)
        sep1 = QFrame(); sep1.setFrameShape(QFrame.HLine); sep1.setStyleSheet("color:#444;")
        f.addRow(sep1)
        f.addRow(QLabel("<b>⚙  Asetukset:</b>"))
        f.addRow("", self._chk_pest_enabled)
        f.addRow("", self._chk_pest_show_spawn)
        f.addRow("", self._chk_pest_show_ready)
        f.addRow("", self._chk_activate_minecraft_on_spawn)
        f.addRow("Cooldown-pituus:", cd_wrap)
        f.addRow("Pest-ilmoituksen näkyvyysaika:", self._spin_pest_timeout)
        f.addRow("SPAWN teksti + väri:", spawn_wrap)
        f.addRow("READY teksti + väri:", ready_wrap)
        sep_lo = QFrame(); sep_lo.setFrameShape(QFrame.HLine); sep_lo.setStyleSheet("color:#444;")
        f.addRow(sep_lo)
        f.addRow(QLabel("<b>🛡  Loadout (1 = kill, 2 = spawn):</b>"))
        f.addRow("Nykyinen loadout:", self._lbl_current_loadout)
        f.addRow("", self._chk_loadout_enabled)
        f.addRow("", self._chk_loadout_block)
        f.addRow("SPAWN (pestit tulossa) vaatii:", self._cbo_loadout_on_spawn)
        f.addRow("READY (cooldown ohi) vaatii:", self._cbo_loadout_on_ready)
        f.addRow("Väärä loadout -varoitus:", lo_wrong_wrap)
        f.addRow("", lo_test_wrap)
        sep2 = QFrame(); sep2.setFrameShape(QFrame.HLine); sep2.setStyleSheet("color:#444;")
        f.addRow(sep2)
        f.addRow(QLabel("<b>🧪 Testi:</b>"))
        f.addRow("", test_wrap)
        f.addRow(pest_tip)
        return w

    def _make_color_button(self, default: str) -> QPushButton:
        b = QPushButton("")
        b.setMinimumHeight(28)
        self._set_button_color(b, default)
        return b

    def _set_button_color(self, btn: QPushButton, color_hex: str) -> None:
        btn.setStyleSheet(self._btn_style_solid(color_hex, text_color="#111"))
        btn.setProperty("color", color_hex)

    @staticmethod
    def _btn_style_solid(bg_color_hex: str, text_color: str = "#fff") -> str:
        return (
            f"QPushButton {{ background-color: {bg_color_hex}; color: {text_color};"
            f"border:2px solid #888; border-radius:6px; min-height:26px; padding:6px 10px;"
            f"font-weight:700; }}"
        )

    # ---------------------------------------------------------------
    # Tab 4: Äänet
    # ---------------------------------------------------------------

    def _build_tab_sound(self) -> QWidget:
        w = QWidget()
        f = QFormLayout(w); f.setContentsMargins(14,14,14,14); f.setSpacing(10)
        self._chk_sound = QCheckBox("Äänet käytössä")
        self._slide_volume = QSlider(Qt.Horizontal); self._slide_volume.setRange(0, 100)
        self._ed_left_sound = QLineEdit(); self._ed_right_sound = QLineEdit()
        bl = QPushButton("Selaa..."); br = QPushButton("Selaa...")
        bl.clicked.connect(lambda: self._browse_sound(self._ed_left_sound))
        br.clicked.connect(lambda: self._browse_sound(self._ed_right_sound))
        r1 = QHBoxLayout(); r1.addWidget(self._ed_left_sound, 1); r1.addWidget(bl)
        r2 = QHBoxLayout(); r2.addWidget(self._ed_right_sound, 1); r2.addWidget(br)
        w1 = QWidget(); w1.setLayout(r1); w2 = QWidget(); w2.setLayout(r2)
        btns = QPushButton("🔊 Testaa vasen / oikea 🔊")
        btns.clicked.connect(self._test_sound)
        f.addRow("", self._chk_sound)
        f.addRow("Äänenvoimakkuus:", self._slide_volume)
        f.addRow("Vasen (custom .wav):", w1)
        f.addRow("Oikea (custom .wav):", w2)
        f.addRow("", btns)
        return w

    # ---------------------------------------------------------------
    # Tab 5: Muuta
    # ---------------------------------------------------------------

    def _build_tab_misc(self) -> QWidget:
        w = QWidget()
        f = QFormLayout(w); f.setContentsMargins(14,14,14,14); f.setSpacing(10)
        self._chk_tray = QCheckBox("Pienennä system tray:hin suljettaessa")
        self._chk_startup = QCheckBox("Käynnistä Windowsin mukana (kokeellinen)")
        info = QLabel(
            "<hr><b>⚠  Tärkeää (Hypixel-säädöt):</b><br><br>"
            "Tämä ohjelma on <b>VAIN ilmoitustyökalu</b>. Se <b>ei koskaan paina "
            "näppäimiä, simuloi syötteitä tai tee mitään automatisoitua "
            "toimintoa</b> Minecraftissa. Se lukee ainoastaan Minecraftin "
            "latest.log-tiedostosta SkyHanni Lane Switch - ja "
            "Pests Spawned -viestejä ja näyttää visuaalisia hälytyksiä overlay-näytölläsi.<br><br>"
            "Sinä olet vastuussa siitä miten sovellusta käytät. "
            "Tarkista Hypixelin säännöt ennen käyttöä. Älä käytä mitään "
            "automaatioskriptejä yhdessä tämän kanssa."
        )
        info.setWordWrap(True); info.setStyleSheet("padding:10px; background:#1e1e1e; border-radius:8px;")
        f.addRow("", self._chk_tray)
        f.addRow("", self._chk_startup)
        f.addRow(info)
        return w

    # ---------------------------------------------------------------
    # Debug
    # ---------------------------------------------------------------

    def _build_tab_debug(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w); lay.setContentsMargins(14,14,14,14); lay.setSpacing(8)
        self._chk_debug = QCheckBox("Debug-tila (tulosta konsoliin + tapahtumaloki)")
        lay.addWidget(self._chk_debug)
        self._pt_debug_log = QPlainTextEdit()
        self._pt_debug_log.setReadOnly(True)
        self._pt_debug_log.setPlaceholderText("Debug-tulosteet näkyvät täällä kun debug on päällä...")
        self._pt_debug_log.setMaximumBlockCount(500)
        lay.addWidget(QLabel("Tapahtumaloki:"))
        lay.addWidget(self._pt_debug_log, 1)
        return w

    # ============================================================
    # Tray
    # ============================================================

    def _init_tray(self) -> None:
        if not QSystemTrayIcon.isSystemTrayAvailable():
            return
        icon = self._make_tray_icon()
        self._tray = QSystemTrayIcon(icon, self)
        self._tray.setToolTip("Skyblock Lane Overlay")
        menu = QMenu()
        a_show = QAction("Avaa asetukset", self); a_show.triggered.connect(self._bring_to_front)
        a_toggle = QAction("▶ Käynnistä / ⏹ Pysäytä", self); a_toggle.triggered.connect(self._toggle_running)
        a_tl = QAction("⬅ Testaa vasen", self); a_tl.triggered.connect(lambda: self._manual_trigger(DIRECTION_LEFT))
        a_tr = QAction("➡ Testaa oikea", self); a_tr.triggered.connect(lambda: self._manual_trigger(DIRECTION_RIGHT))
        a_hide = QAction("Piilota overlay", self); a_hide.triggered.connect(self._overlay.hide_overlay)
        a_quit = QAction("Poistu", self); a_quit.triggered.connect(self._quit_app)
        menu.addAction(a_show); menu.addSeparator()
        menu.addAction(a_toggle); menu.addAction(a_tl); menu.addAction(a_tr); menu.addAction(a_hide)
        menu.addSeparator(); menu.addAction(a_quit)
        self._tray.setContextMenu(menu)
        self._tray.activated.connect(self._on_tray_activated)
        self._tray.show()

    def _make_tray_icon(self) -> QIcon:
        pm = QPixmap(64, 64); pm.fill(Qt.transparent)
        p = QPainter(pm); p.setRenderHint(QPainter.Antialiasing, True)
        p.setBrush(QColor("#34c759")); p.setPen(Qt.NoPen)
        p.drawRoundedRect(2, 2, 60, 60, 14, 14)
        p.setBrush(QColor("#ffffff"));
        poly = QPolygon()
        poly << QPoint(18, 32) << QPoint(42, 16) << QPoint(42, 48)
        p.drawPolygon(poly)
        p.end()
        return QIcon(pm)

    def _on_tray_activated(self, reason: QSystemTrayIcon.ActivationReason) -> None:
        if reason == QSystemTrayIcon.DoubleClick:
            self._bring_to_front()

    def _bring_to_front(self) -> None:
        self.showNormal(); self.raise_(); self.activateWindow()

    def closeEvent(self, event) -> None:
        if self._config.minimize_to_tray and self._tray and self._tray.isVisible():
            event.ignore()
            self.hide()
            self._tray.showMessage(
                "Skyblock Lane Overlay",
                "Sovellus pyörii järjestelmäpalkissa.",
                QSystemTrayIcon.Information, 2200,
            )
            return
        self._quit_app()

    # ============================================================
    # Config <-> UI synkronointi
    # ============================================================

    def _update_monitor_list(self) -> None:
        screens = QApplication.screens()
        monitors = []
        for i, s in enumerate(screens, start=1):
            geo = s.geometry()
            name = s.name() or f"Ruutu {i}"
            primary = (s == QApplication.primaryScreen())
            monitors.append({
                "index": i,
                "name": f"{name} – {geo.width()}x{geo.height()}{' (pää)' if primary else ''}",
            })
        def fill(cmb: QComboBox):
            cmb.blockSignals(True); cmb.clear()
            for m in monitors:
                cmb.addItem(m["name"], m["index"])
            cmb.blockSignals(False)
        fill(self._cmb_overlay_monitor)
        self._set_cmb_index(self._cmb_overlay_monitor, self._config.overlay_monitor_index)

    def _set_cmb_index(self, cmb: QComboBox, data_value: int) -> None:
        for i in range(cmb.count()):
            if cmb.itemData(i) == data_value:
                cmb.setCurrentIndex(i); return
        if cmb.count() > 0:
            cmb.setCurrentIndex(0)

    def _load_ui_from_config(self) -> None:
        c = self._config
        # Logi & Tunnistus
        self._chk_move_minecraft_on_spawn.setChecked(
    bool(getattr(c, "move_minecraft_on_pest_spawn", True))
    )
        mode = getattr(c, "minecraft_move_mode", "swap")
        idx = self._cmb_minecraft_move_mode.findData(mode)
        if idx >= 0:
            self._cmb_minecraft_move_mode.setCurrentIndex(idx)
        self._spin_interval.setValue(c.detection_interval_ms)
        self._chk_detect.setChecked(c.detection_enabled)
        self._chk_log.setChecked(bool(getattr(c, "log_enabled", True)))
        self._ed_logpath.setText(c.log_file_path)
        self._chk_alt_dir.setChecked(bool(getattr(c, "alternate_direction", True)))
        initial = getattr(c, "initial_direction", DIRECTION_LEFT) or DIRECTION_LEFT
        if initial.lower() == DIRECTION_RIGHT:
            self._radio_initial_right.setChecked(True)
        else:
            self._radio_initial_left.setChecked(True)
        self._detector.reset_direction_to(initial)
        self._refresh_current_dir_label()

        # Overlay
        idx = self._cmb_position.findData(c.overlay_position)
        self._cmb_position.setCurrentIndex(max(0, idx))
        self._spin_offset_x.setValue(c.overlay_offset_x)
        self._spin_offset_y.setValue(c.overlay_offset_y)
        self._spin_size.setValue(c.overlay_size)
        self._spin_timeout.setValue(c.overlay_timeout_ms)
        self._spin_delay.setValue(int(getattr(c, "overlay_delay_ms", 0)))
        self._slide_opacity.setValue(int(c.overlay_opacity * 100))
        self._chk_flash.setChecked(c.flash_effect)
        self._chk_show_text.setChecked(c.show_text)
        self._ed_text.setText(c.overlay_text)
        self._set_button_color(self._btn_color_left, c.arrow_color_left)
        self._set_button_color(self._btn_color_right, c.arrow_color_right)
        self._set_button_color(self._btn_color_text, c.text_color)

        # Pest
        self._chk_pest_enabled.setChecked(bool(getattr(c, "pest_enabled", True)))
        self._chk_pest_show_spawn.setChecked(bool(getattr(c, "pest_show_spawn", True)))
        self._chk_pest_show_ready.setChecked(bool(getattr(c, "pest_show_ready", True)))
        self._chk_activate_minecraft_on_spawn.setChecked(
            bool(getattr(c, "activate_minecraft_on_pest_spawn", True))
        )
        pest_cd_ms = int(getattr(c, "pest_cooldown_ms", 135000))
        pest_cd_s = max(0, pest_cd_ms // 1000)
        self._spin_pest_cooldown_m.setValue(pest_cd_s // 60)
        self._spin_pest_cooldown_s.setValue(pest_cd_s % 60)
        self._spin_pest_timeout.setValue(int(getattr(c, "pest_overlay_timeout_ms", 4000)))
        self._ed_pest_spawn_text.setText(getattr(c, "pest_text_spawn", "PESTIT TULED! 4-8kpl"))
        self._ed_pest_ready_text.setText(getattr(c, "pest_text_ready", "PESTI COOLDOWN OHI!"))
        self._set_button_color(self._btn_pest_color_spawn, getattr(c, "pest_color_spawn", "#ff9500"))
        self._set_button_color(self._btn_pest_color_ready, getattr(c, "pest_color_ready", "#00c7ff"))

        # Loadout
        self._chk_loadout_enabled.setChecked(bool(getattr(c, "loadout_enabled", True)))
        self._chk_loadout_block.setChecked(bool(getattr(c, "loadout_block_until_correct", True)))
        lo_spawn = getattr(c, "loadout_on_spawn", LOADOUT_KILL)
        lo_ready = getattr(c, "loadout_on_ready", LOADOUT_SPAWN)
        idx = self._cbo_loadout_on_spawn.findData(lo_spawn)
        if idx >= 0:
            self._cbo_loadout_on_spawn.setCurrentIndex(idx)
        idx2 = self._cbo_loadout_on_ready.findData(lo_ready)
        if idx2 >= 0:
            self._cbo_loadout_on_ready.setCurrentIndex(idx2)
        self._ed_loadout_wrong_text.setText(
            getattr(c, "loadout_wrong_text",
                    "⚠ VÄÄRÄ LOADOUT!\n{hint}\nVaihda ensin → sitten jatka")
        )
        self._set_button_color(
            self._btn_loadout_wrong_color,
            getattr(c, "loadout_wrong_color", "#ff2d55")
        )
        # Päivitä loadout GUI label (jos logi ei ole vielä löytänyt viestiä)
        self._update_loadout_ui_label()

        # Sound
        self._chk_sound.setChecked(c.sound_enabled)
        self._slide_volume.setValue(int(c.sound_volume * 100))
        if hasattr(self, "_ed_left_sound"):
            self._ed_left_sound.setText(getattr(c, "sound_file_left", "") or "")
        if hasattr(self, "_ed_right_sound"):
            self._ed_right_sound.setText(getattr(c, "sound_file_right", "") or "")
        # Misc
        self._chk_tray.setChecked(c.minimize_to_tray)
        self._chk_startup.setChecked(c.run_on_startup)
        self._chk_debug.setChecked(c.debug_mode)

    def _collect_ui_to_config(self) -> None:
        c = self._config
        # Monitors
        over_idx = self._cmb_overlay_monitor.currentData() or 1
        c.overlay_monitor_index = int(over_idx)
        # Logi & Tunnistus
        c.detection_interval_ms = self._spin_interval.value()
        c.detection_enabled = self._chk_detect.isChecked()
        c.log_enabled = self._chk_log.isChecked()
        c.log_file_path = self._ed_logpath.text().strip()
        c.alternate_direction = self._chk_alt_dir.isChecked()
        c.move_minecraft_on_pest_spawn = self._chk_move_minecraft_on_spawn.isChecked()
        c.minecraft_move_mode = self._cmb_minecraft_move_mode.currentData() or "swap"
        c.initial_direction = (
            DIRECTION_RIGHT if self._radio_initial_right.isChecked() else DIRECTION_LEFT
        )
        # Overlay
        c.overlay_position = self._cmb_position.currentData() or "center"
        c.overlay_offset_x = self._spin_offset_x.value()
        c.overlay_offset_y = self._spin_offset_y.value()
        c.overlay_size = self._spin_size.value()
        c.overlay_timeout_ms = self._spin_timeout.value()
        c.overlay_delay_ms = self._spin_delay.value()
        c.overlay_opacity = self._slide_opacity.value() / 100.0
        c.flash_effect = self._chk_flash.isChecked()
        c.show_text = self._chk_show_text.isChecked()
        c.overlay_text = self._ed_text.text() or "VAIHDA NÄPPÄIN"
        c.arrow_color_left = self._btn_color_left.property("color") or "#ff3b30"
        c.arrow_color_right = self._btn_color_right.property("color") or "#34c759"
        c.text_color = self._btn_color_text.property("color") or "#ffffff"
        # Pest
        c.pest_enabled = self._chk_pest_enabled.isChecked()
        c.pest_show_spawn = self._chk_pest_show_spawn.isChecked()
        c.pest_show_ready = self._chk_pest_show_ready.isChecked()
        c.activate_minecraft_on_pest_spawn = self._chk_activate_minecraft_on_spawn.isChecked()
        cd_ms = (
            int(self._spin_pest_cooldown_m.value()) * 60
            + int(self._spin_pest_cooldown_s.value())
        ) * 1000
        c.pest_cooldown_ms = max(1000, cd_ms)
        c.pest_overlay_timeout_ms = self._spin_pest_timeout.value()
        c.pest_text_spawn = self._ed_pest_spawn_text.text().strip() or "PESTIT TULED!"
        c.pest_text_ready = self._ed_pest_ready_text.text().strip() or "PESTI COOLDOWN OHI!"
        c.pest_color_spawn = self._btn_pest_color_spawn.property("color") or "#ff9500"
        c.pest_color_ready = self._btn_pest_color_ready.property("color") or "#00c7ff"

        # Loadout
        c.loadout_enabled = self._chk_loadout_enabled.isChecked()
        c.loadout_block_until_correct = self._chk_loadout_block.isChecked()
        c.loadout_on_spawn = self._cbo_loadout_on_spawn.currentData() or LOADOUT_KILL
        c.loadout_on_ready = self._cbo_loadout_on_ready.currentData() or LOADOUT_SPAWN
        c.loadout_wrong_text = (
            self._ed_loadout_wrong_text.text().strip()
            or "⚠ VÄÄRÄ LOADOUT!\n{hint}\nVaihda ensin → sitten jatka"
        )
        c.loadout_wrong_color = (
            self._btn_loadout_wrong_color.property("color") or "#ff2d55"
        )
        # Sound
        c.sound_enabled = self._chk_sound.isChecked()
        c.sound_volume = self._slide_volume.value() / 100.0
        if hasattr(self, "_ed_left_sound"):
            c.sound_file_left = self._ed_left_sound.text().strip()
        if hasattr(self, "_ed_right_sound"):
            c.sound_file_right = self._ed_right_sound.text().strip()
        # Misc
        c.minimize_to_tray = self._chk_tray.isChecked()
        c.run_on_startup = self._chk_startup.isChecked()
        c.debug_mode = self._chk_debug.isChecked()

    # ============================================================
    # Handlers: napit yms.
    # ============================================================

    def _pick_color(self, btn: QPushButton, which: str) -> None:
        current = btn.property("color") or "#ffffff"
        col = QColorDialog.getColor(QColor(current), self, f"Valitse väri – {which}")
        if col.isValid():
            self._set_button_color(btn, col.name())

    def _browse_log(self) -> None:
        p, _ = QFileDialog.getOpenFileName(self, "Valitse latest.log", "",
                                           "Log files (*.log);;All files (*)")
        if p:
            self._ed_logpath.setText(p)

    def _auto_log_path(self) -> None:
        from .config import default_minecraft_log_path
        auto = default_minecraft_log_path()
        if auto and os.path.exists(auto):
            self._ed_logpath.setText(auto)
            self._append_debug_log(f"[Config] Auto-detect löysi login: {auto}")
        else:
            self._ed_logpath.setText(auto or "")
            self._append_debug_log(
                "[Config] Auto-detect: standardia %APPDATA%\\.minecraft polkua ei löytynyt "
                "(käytätkö Prism/Badlion/Feather/SomeOtherLauncheria?). Aseta polku käsin "
                "'Selaa'-napilla, löydät sen launcherisi 'logs'-kansiosta nimellä latest.log."
            )

    def _set_current_direction(self, direction: str) -> None:
        self._detector.reset_direction_to(direction)
        self._refresh_current_dir_label()
        label_dir = "VASEN (A)" if direction == DIRECTION_LEFT else "OIKEA (D)"
        self._append_debug_log(f"[SUUNTA] Manuaalisesti asetettu => {label_dir}")

    def _refresh_current_dir_label(self) -> None:
        try:
            cur = self._detector.current_direction
        except AttributeError:
            cur = DIRECTION_LEFT
        if cur == DIRECTION_RIGHT:
            self._lbl_current_dir.setText("Seuraava: OIKEA (D)")
            self._lbl_current_dir.setStyleSheet(
                "font-weight:700; font-size:14px; padding:8px 14px;"
                "background:#0d2e14; color:#a0f3b5; border:1px solid #34c759; border-radius:8px;"
            )
        else:
            self._lbl_current_dir.setText("Seuraava: VASEN (A)")
            self._lbl_current_dir.setStyleSheet(
                "font-weight:700; font-size:14px; padding:8px 14px;"
                "background:#2e1010; color:#ffb0a8; border:1px solid #ff3b30; border-radius:8px;"
            )

    def _browse_sound(self, target: QLineEdit) -> None:
        p, _ = QFileDialog.getOpenFileName(self, "Valitse äänitiedosto", "",
                                           "Wave (*.wav);;All files (*)")
        if p:
            target.setText(p)

    def _test_sound(self) -> None:
        self._collect_ui_to_config()
        self._sound.set_enabled(self._config.sound_enabled)
        self._sound.set_volume(self._config.sound_volume)
        if self._ed_left_sound.text() or self._ed_right_sound.text():
            self._sound.set_custom_sound(self._ed_left_sound.text() or None,
                                         self._ed_right_sound.text() or None)
        if self._config.sound_enabled:
            self._sound.play_left()
            QTimer.singleShot(350, self._sound.play_right)

    def _toggle_running(self) -> None:
        self._running = not self._running
        self._update_run_state()

    def _update_run_state(self) -> None:
        if self._running:
            self._btn_run.setText("⏹  Pysäytä logi tarkistus")
            self._lbl_status.setText("● Tarkistaa logia")
            self._lbl_status.setStyleSheet(
                "color:#88ff88; font-weight:600; font-size:14px; padding:4px 10px;"
                "background:#0d2e14; border:1px solid #34c759; border-radius:8px;"
            )
            self._collect_ui_to_config()
            self._scan_timer.start(self._config.detection_interval_ms)
        else:
            self._btn_run.setText("▶  Käynnistä logi tarkistus")
            self._lbl_status.setText("● Seis")
            self._lbl_status.setStyleSheet(
                "color:#aaa; font-weight:600; font-size:14px; padding:4px 10px;"
                "background:#222; border-radius:8px;"
            )
            self._scan_timer.stop()

    def _manual_trigger(self, direction: str) -> None:
        self._collect_ui_to_config()
        self._sound.set_enabled(self._config.sound_enabled)
        self._sound.set_volume(self._config.sound_volume)
        self._overlay.update_config(self._config)
        res = self._detector.mock_trigger(direction)
        if res.direction:
            self.lane_triggered.emit(res.direction)

    # --- Pest testi napit ---
    def _on_test_pest_spawn(self) -> None:
        self._collect_ui_to_config()
        self._overlay.update_config(self._config)
        self._sound.set_enabled(self._config.sound_enabled)
        self._sound.set_volume(self._config.sound_volume)
        ev = self._detector.pest_trigger_spawn_now(count=5, plot="TEST")
        if getattr(self._config, "pest_enabled", True) and getattr(self._config, "pest_show_spawn", True):
            self._on_pest_event(ev)

    def _on_test_pest_ready(self) -> None:
        self._collect_ui_to_config()
        self._overlay.update_config(self._config)
        self._sound.set_enabled(self._config.sound_enabled)
        self._sound.set_volume(self._config.sound_volume)
        ev = self._detector.pest_trigger_ready_now()
        if getattr(self._config, "pest_enabled", True) and getattr(self._config, "pest_show_ready", True):
            self._on_pest_event(ev)

    def _on_pest_reset(self) -> None:
        self._detector.pest_reset_cooldown()
        self._append_debug_log("[PEST] Cooldown nollattu manuaalisesti.")
        self._update_pest_ui_state()

    # --- Signaalien handlers ---

    def _on_lane_triggered(self, direction: str) -> None:
        self._overlay.show_lane_switch(direction)
        self._cfg_mgr.increment_counter()
        self._update_counter_label()
        try:
            self._refresh_current_dir_label()
        except Exception:  # noqa: BLE001
            pass
        if self._config.debug_mode:
            self._append_debug_log(f"[TRIGGER] suunta={direction}")

    def _on_loadout_event(self, ev: LoadoutEvent) -> None:
        if not ev:
            return
        if self._config.debug_mode:
            name = "Pest kill (1)" if ev.loadout == LOADOUT_KILL else (
                "Pest spawn (2)" if ev.loadout == LOADOUT_SPAWN else ev.loadout
            )
            self._append_debug_log(f"[LOADOUT] Vaihdettu → {name}")
        # Päivitä GUI:n label jos sellainen on olemassa
        if hasattr(self, "_lbl_current_loadout"):
            self._update_loadout_ui_label()

        # ONKO ODOITTAVA PEST-EVENTTI (väärä loadout oli blokattu)?
        #   Jos on: tarkista onko uusi loadout nyt oikea odottavalle eventille.
        #   Jos on → poista varoitus-kortti ja näytä alkuperäinen ilmoitus.
        pending = getattr(self, "_pending_pest_event", None)
        if pending is not None:
            if self._detector.is_loadout_correct_for(pending.type):
                # 👍 Oikea loadout vaihdettu!
                ev_name = "SPAWN" if pending.type == PestEventType.SPAWN else (
                    "READY" if pending.type == PestEventType.READY else str(pending.type)
                )
                if self._config.debug_mode:
                    self._append_debug_log(
                        f"[LOADOUT][FIXED] Nyt oikea ({ev_name}-tapahtumalle) "
                        "→ suljetaan varoitus ja näytetään alkuperäinen pest-ilmoitus."
                    )
                # Poista pysyvä varoitus-kortti
                self._overlay.dismiss_by_tag("loadout_warning")
                # Siivoa pending ennenkuin kutsumme _on_pest_event (ettei tule
                # ikuista silmukka jos jokin menee pieleen):
                self._pending_pest_event = None
                # Käynnistä alkuperäinen eventti käsittelyyn – nyt loadout on ok
                # joten se menee normaaliin "show_pest_notification"-haaraan.
                self._on_pest_event(pending)
            else:
                # Loadout vaihtui, mutta ei vielä oikeaksi. Pysyvä kortti jatkaa.
                if self._config.debug_mode:
                    exp = self._detector.expected_loadout_for(pending.type)
                    self._append_debug_log(
                        "[LOADOUT] Vaihdoit mutta ei vieläkään oikea "
                        f"(odottaa: {exp}). Varoitus jatkuu."
                    )

    def _find_minecraft_window(self) -> Optional[int]:
        """Etsi todennäköisin Minecraft-/client-ikkuna Windowsissa."""
        if os.name != "nt" or win32gui is None:
            return None

        matches = []

        def _enum_cb(hwnd: int, _extra) -> None:
            if not win32gui.IsWindowVisible(hwnd):
                return
            title = (win32gui.GetWindowText(hwnd) or "").strip()
            cls = (win32gui.GetClassName(hwnd) or "").strip()
            title_l = title.lower()
            cls_l = cls.lower()
            if (
                any(key in title_l for key in _MINECRAFT_WINDOW_TITLE_KEYWORDS)
                or any(key in cls_l for key in _MINECRAFT_WINDOW_CLASS_KEYWORDS)
            ):
                score = 0
                if "minecraft" in title_l:
                    score += 5
                if "badlion" in title_l or "lunar client" in title_l:
                    score += 4
                if any(key in cls_l for key in _MINECRAFT_WINDOW_CLASS_KEYWORDS):
                    score += 3
                if title:
                    score += 1
                matches.append((score, hwnd))

        try:
            win32gui.EnumWindows(_enum_cb, None)
        except Exception:
            return None

        if not matches:
            return None
        matches.sort(key=lambda item: item[0], reverse=True)
        return matches[0][1]

    def _activate_minecraft_window(self) -> bool:
        """Yritä tuoda Minecraft-ikkuna etualalle."""
        if os.name != "nt" or win32gui is None or win32con is None:
            return False
    

        hwnd = self._find_minecraft_window()
        if not hwnd:
            if self._config.debug_mode:
                self._append_debug_log("[PEST][FOCUS] Minecraft-ikkunaa ei löytynyt.")
            return False

        try:
            if win32gui.IsIconic(hwnd):
                win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
            else:
                win32gui.ShowWindow(hwnd, win32con.SW_SHOW)
            win32gui.BringWindowToTop(hwnd)
            win32gui.SetForegroundWindow(hwnd)
            return True
        except Exception as e:
            if self._config.debug_mode:
                self._append_debug_log(f"[PEST][FOCUS] Minecraftin aktivointi epäonnistui: {e}")
            return False

    def _update_loadout_ui_label(self) -> None:
        """Päivitä GUI:ssa näkyvä 'nykyinen loadout' -label (jos on rakennettu)."""
        if not hasattr(self, "_lbl_current_loadout"):
            return
        lo = self._detector.current_loadout
        if lo == LOADOUT_KILL:
            txt = "✅ Pest kill (1)"
            col = "#34c759"
        elif lo == LOADOUT_SPAWN:
            txt = "✅ Pest spawn (2)"
            col = "#ff9500"
        else:
            txt = "❓ Tuntematon (ei vielä 'You equipped' -viestiä)"
            col = "#8e8e93"
        self._lbl_current_loadout.setText(txt)
        self._lbl_current_loadout.setStyleSheet(
            f"font-size:15px; font-weight:700; padding:8px 12px;"
            f"color:{col}; background:#11151b; border:2px solid {col}; border-radius:8px;"
        )

    def _on_pest_event(self, ev: PestEvent) -> None:
        if not ev:
            return
        if not getattr(self._config, "pest_enabled", True):
            return
        show_spawn = getattr(self._config, "pest_show_spawn", True)
        show_ready = getattr(self._config, "pest_show_ready", True)

        # Loadout-tarkistus: SPAWN ja READY -tapahtumille
        expected = self._detector.expected_loadout_for(ev.type)
        loadout_ok = self._detector.is_loadout_correct_for(ev.type)
        block_until_correct = bool(
            getattr(self._config, "loadout_block_until_correct", True)
            and getattr(self._config, "loadout_enabled", True)
            and expected is not None
        )

        # Mikä teksti näytetään, mikä väri ja mikä timeout
        text = ""
        color = ""
        timeout = int(getattr(self._config, "pest_overlay_timeout_ms", 4000))
        play_sound = True
        will_block_original = False
        wrong_hint = ""

        if ev.type == PestEventType.SPAWN and show_spawn:
            if (
                ev.source == DetectionSource.LOG
                and getattr(self._config, "activate_minecraft_on_pest_spawn", True)
            ):
                activated = self._activate_minecraft_window()
                if self._config.debug_mode:
                    msg = "Minecraft aktivoitiin." if activated else "Minecraftin aktivointi ei onnistunut."
                    self._append_debug_log(f"[PEST][FOCUS] {msg}")
            count = ev.count if ev.count and ev.count > 0 else None
            plot = ev.plot if ev.plot else None
            text = getattr(self._config, "pest_text_spawn", "PESTIT TULED!")
            if count is not None:
                text = text.replace("{count}", str(count))
            else:
                text = text.replace("{count}", "?")
            if plot is not None:
                text = text.replace("{plot}", str(plot))
            else:
                text = text.replace("{plot}", "")
            color = getattr(self._config, "pest_color_spawn", "#ff9500")
            if not loadout_ok and block_until_correct:
                will_block_original = True
                wrong_hint = "VAIHDA → Pest kill (1)"
                if self._config.debug_mode:
                    self._append_debug_log(
                        f"[PEST][SPAWN] count={ev.count} plot={ev.plot!r} "
                        f"→ ⚠ VÄÄRÄ LOADOUT (odottaa: {expected}). Blokattu -> ei overlay."
                    )

        elif ev.type == PestEventType.READY and show_ready:
            text = getattr(self._config, "pest_text_ready", "PESTI COOLDOWN OHI!")
            color = getattr(self._config, "pest_color_ready", "#00c7ff")
            if not loadout_ok and block_until_correct:
                will_block_original = True
                wrong_hint = "VAIHDA → Pest spawn (2)"
                if self._config.debug_mode:
                    self._append_debug_log(
                        f"[PEST][READY] ⚠ VÄÄRÄ LOADOUT (odottaa: {expected}). Blokattu."
                    )

        # COUNTDOWN-tapahtumat eivät näytä overlayta (tai jos halutaan niin voidaan lisätä)
        if not text:
            # Muut tapahtumat (COUNTDOWN) ilmoitetaan vain debug-lokiin
            if ev.type == PestEventType.COUNTDOWN and self._config.debug_mode:
                self._append_debug_log(
                    f"[PEST][COUNTDOWN] {ev.count}s jäljellä ({ev.plot})"
                )
            return

        # Jos väärä loadout ja halutaan blokata -> näytÄ PYSYVÄ VAROITUS overlay eikä
        # alkuperäistä ilmoitusta. Kortti jää näkyviin KUNNES detector tunnistaa että
        # käyttäjä on vaihtanut oikeaan loadoutiin (ks. _on_loadout_event).
        if will_block_original:
            wrong_template = getattr(
                self._config, "loadout_wrong_text",
                "⚠ VÄÄRÄ LOADOUT!\n{hint}\nVaihda ensin → sitten jatka"
            )
            try:
                wrong_text = wrong_template.replace("{hint}", wrong_hint or "")
            except Exception:
                wrong_text = f"⚠ VÄÄRÄ LOADOUT!\n{wrong_hint}"
            wrong_color = getattr(self._config, "loadout_wrong_color", "#ff2d55")
            # Tallenna tämä tapahtuma "jonoon" – näytetään heti kun loadout ok
            self._pending_pest_event = ev
            # Näytä pysyvä (persistent=True) kortti joka EI katoa aikakatkaisulla
            self._overlay.show_pest_notification(
                wrong_text, wrong_color,
                play_sound=play_sound,
                tag="loadout_warning",
                persistent=True,
            )
            if self._config.debug_mode:
                self._append_debug_log(
                    "[LOADOUT][BLOCK] Väärä loadout! Varoitus-kortti jää näkyviin "
                    f"kunnes vaihdettu -> odotettu: {expected}"
                )
            # Älä näytä alkuperäistä spawn/ready-viestiä koska väärä loadout
            return

        # Normaali tapaus: loadout ok tai tarkistus pois päältä
        #   -> Jos oli odottava loadout-varoitus (vanha), poista se nyt kun
        #      saatiin hyväksytty event.
        self._overlay.dismiss_by_tag("loadout_warning")
        # Varmistetaan ettei mikään jää odottamaan turhaan
        self._pending_pest_event = None
        self._overlay.show_pest_notification(text, color, timeout_ms=timeout,
                                             play_sound=play_sound)
        if self._config.debug_mode:
            if ev.type == PestEventType.SPAWN:
                self._append_debug_log(
                    f"[PEST][SPAWN] count={ev.count} plot={ev.plot!r} "
                    f"cooldown={_fmt_ms(self._detector.pest_cooldown_total_ms)}"
                )
            elif ev.type == PestEventType.READY:
                self._append_debug_log(
                    "[PEST][READY] Cooldown ohi! Seuraavat pestit voivat tulla."
                )

    def _update_counter_label(self) -> None:
        self._lbl_counter.setText(f"Lane-vaihdot: {self._config.lane_switch_count}")

    def _reset_counter(self) -> None:
        self._cfg_mgr.reset_counter()
        self._update_counter_label()

    def _save_and_reload(self) -> None:
        self._collect_ui_to_config()
        self._cfg_mgr.config = self._config
        self._cfg_mgr.save()
        self._overlay.update_config(self._config)
        self._detector.update_config(self._config)
        self._sound.set_enabled(self._config.sound_enabled)
        self._sound.set_volume(self._config.sound_volume)
        sl = getattr(self._config, "sound_file_left", "") or None
        sr = getattr(self._config, "sound_file_right", "") or None
        self._sound.set_custom_sound(sl, sr)
        if self._running:
            self._scan_timer.setInterval(self._config.detection_interval_ms)
        self._append_debug_log("[Config] Asetukset tallennettu.")

    def _reload_from_disk(self) -> None:
        self._cfg_mgr.load()
        self._config = self._cfg_mgr.config
        self._update_monitor_list()
        self._load_ui_from_config()
        self._overlay.update_config(self._config)
        self._detector.update_config(self._config)
        self._sound.set_enabled(self._config.sound_enabled)
        self._sound.set_volume(self._config.sound_volume)
        sl = getattr(self._config, "sound_file_left", "") or None
        sr = getattr(self._config, "sound_file_right", "") or None
        self._sound.set_custom_sound(sl, sr)
        self._update_counter_label()
        self._append_debug_log("[Config] Asetukset ladattu levyltä.")

    def _reset_defaults(self) -> None:
        r = QMessageBox.question(self, "Vahvista",
                                 "Haluatko varmasti nollata kaikkien asetusten "
                                 "oletusarvot? Tämä ei nollaa laskuria eikä pest-cooldownia.",
                                 QMessageBox.Yes | QMessageBox.No)
        if r != QMessageBox.Yes:
            return
        count = self._config.lane_switch_count
        self._cfg_mgr.reset()
        self._cfg_mgr.config.lane_switch_count = count
        self._cfg_mgr.save()
        self._reload_from_disk()

    def _quit_app(self) -> None:
        try:
            self._save_and_reload()
        except Exception:
            pass
        try:
            if self._tray:
                self._tray.hide()
        except Exception:
            pass
        QApplication.quit()

    # ============================================================
    # Logi tarkistus + pest-tapahtumat loop
    # ============================================================

    def _on_scan_tick(self) -> None:
        if not self._config.detection_enabled:
            return

        # Kutsu check_all() jotta logi luetaan KERRAN per tick ja sekä
        # lane-, pest- että loadout-tapahtumat saadaan luettua samoista riveistä.
        res, events, loadout_events = self._detector.check_all()

        # 1) Lane switch
        if res.direction:
            self._report_lane_result(res)

        # 2) Loadout-tapahtumat (päivitetään ensin, jotta pest-tapahtumien
        #    aikana tarkastus on ajantasalla)
        for lo_ev in loadout_events:
            self._report_loadout_result(lo_ev)

        # 3) Pestit (SPAWN / COUNTDOWN logista + READY cooldownista)
        if getattr(self._config, "pest_enabled", True):
            for ev in events:
                self._report_pest_result(ev)

    def _report_lane_result(self, res: DetectionResult) -> None:
        if not res.direction:
            return
        if self._config.debug_mode:
            self._append_debug_log(
                f"[DETECT][{res.source.value}] dir={res.direction} "
                f"conf={res.confidence:.2f} text={res.raw_text!r}"
            )
        self.lane_triggered.emit(res.direction)

    def _report_loadout_result(self, ev: LoadoutEvent) -> None:
        if not ev:
            return
        self.loadout_event.emit(ev)

    def _report_pest_result(self, ev: PestEvent) -> None:
        if not ev:
            return
        # Emitoidaan signaali -> _on_pest_event hoitaa overlay + debug logi
        self.pest_event.emit(ev)

    # ============================================================
    # Pest UI state (ajastin näyttää jäljellä olevan ajan + progress)
    # ============================================================

    def _update_pest_ui_state(self) -> None:
        rem = self._detector.pest_cooldown_remaining_ms
        total = max(1, self._detector.pest_cooldown_total_ms)
        # Tila label
        if rem <= 0:
            self._lbl_pest_state.setText("Cooldown: VALMIS / EI KÄYNNISSÄ")
            self._lbl_pest_state.setStyleSheet(
                "font-size:15px; font-weight:800; padding:10px 14px;"
                "background:#0d2e14; color:#88ff88; border:2px solid #34c759; border-radius:8px;"
            )
            self._lbl_pest_remaining.setText("0:00")
            self._lbl_pest_remaining.setStyleSheet(
                "font-size:22px; font-weight:800; padding:10px 14px;"
                "color:#88ff88; background:#0d2e14; border:2px solid #34c759; border-radius:10px;"
            )
            self._progress_pest.setValue(self._progress_pest.maximum())
        else:
            # Cooldown käynnissä: punainen / oranssi alku, sininen loppua kohden
            ratio = 1.0 - (rem / total)  # 0...1
            if ratio < 0.2:
                self._lbl_pest_state.setText("Cooldown: KÄYNNISSÄ (nettiin tulossa...)")
                self._lbl_pest_state.setStyleSheet(
                    "font-size:15px; font-weight:800; padding:10px 14px;"
                    "background:#2a1a0c; color:#ff9500; border:2px solid #ff9500; border-radius:8px;"
                )
            else:
                self._lbl_pest_state.setText("Cooldown: KÄYNNISSÄ")
                self._lbl_pest_state.setStyleSheet(
                    "font-size:15px; font-weight:800; padding:10px 14px;"
                    "background:#0b1e26; color:#00c7ff; border:2px solid #00c7ff; border-radius:8px;"
                )
            self._lbl_pest_remaining.setText(_fmt_ms(rem))
            self._lbl_pest_remaining.setStyleSheet(
                "font-size:22px; font-weight:800; padding:10px 14px;"
                "color:#00c7ff; background:#0b1e26; border:2px solid #00c7ff; border-radius:10px;"
            )
            self._progress_pest.setValue(int(ratio * self._progress_pest.maximum()))

    # ============================================================
    # Debug
    # ============================================================

    def _append_debug_log(self, line: str) -> None:
        import datetime
        ts = datetime.datetime.now().strftime("%H:%M:%S.%f")[:-3]
        self._pt_debug_log.appendPlainText(f"[{ts}] {line}")
