"""
Always-on-top overlay-ikkuna, joka näyttää rinnakkain useita ilmoituksia:
  - Lane-switch nuoli (vasen / oikea)
  - Pest-spawn / pest-ready ilmoitus (laatikko + teksti + väri)
  - Loadout-varoitus

Ikkuna on läpinäkyvä, frameton ja jää kaiken päälle. Useat ilmoitukset näkyvät
samanaikaisesti allekkain korttina.

TÄRKEÄ TURVAROHTO: Tämä moduuli EI koskaan luo, simuloi tai lähetä
näppäinpainalluksia tai hiiritoimintoja. Se VAIN näyttää visuaalisen
ilmoituksen ja toistaa äänen. Tämä on notification-työkalu, ei automaatio.
"""

from collections import OrderedDict
from typing import Optional, Dict, Any, List, Tuple

from PySide6.QtCore import (
    Qt, QTimer, QPoint, QRect, Signal,
)
try:
    from PySide6.QtCore import QPolygon
except ImportError:
    from PySide6.QtGui import QPolygon  # type: ignore[attr-defined, no-redef]

from PySide6.QtGui import QPainter, QColor, QPen, QFont, QBrush, QPainterPath
from PySide6.QtWidgets import QWidget, QApplication

from .config import AppConfig
from .sound_manager import SoundManager


DIRECTION_LEFT = "left"
DIRECTION_RIGHT = "right"

# Ilmoitustyypit
KIND_LANE = "lane"
KIND_PEST = "pest"

# Väli korttien välissä (px)
_CARD_GAP = 18


class OverlayWindow(QWidget):
    """Frameton, always-on-top overlay joka näyttää useita ilmoituksia rinnakkain.

    Ilmoitukset säilytetään aktiivisena sanakirjassa (OrderedDict) yksittäisten
    aikakatkaisujensa ajan. Kun uusi ilmoitus saapuu, se lisätään listaan ja
    se näytetään heti – vanhat eivät häviä ennen aikakatkaisuaan.
    """

    acknowledged = Signal()

    def __init__(self, config: AppConfig, sound_manager: SoundManager,
                 parent: Optional[QWidget] = None):
        super().__init__(parent)
        self._config = config
        self._sound = sound_manager

        # ---------- Aktiiviset ilmoitukset ----------
        # Avain = yksilöllinen tagi (esim. "lane:left" tai "pest:PESTIT TULED!")
        # Arvo = sanakirja, kentät:
        #   kind: "lane" / "pest"
        #   start_ms: aikaleima (ms)
        #   timeout_ms: näyttöaika (ms)  -->  None = EI AIKAKATKAISUA (pysyvä, poistetaan manuaalisesti)
        #   ... muut (direction / text / color yms.)
        self._active: "OrderedDict[str, Dict[str, Any]]" = OrderedDict()

        # Flash-animaatio kaikille korteille yhteinen (0–3 kierrosta)
        self._flash_state = 0

        # Ikkuna-attribuutit
        self.setWindowFlags(
            Qt.FramelessWindowHint
            | Qt.WindowStaysOnTopHint
            | Qt.Tool
            | Qt.WindowDoesNotAcceptFocus
            | Qt.WindowTransparentForInput
        )
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WA_ShowWithoutActivating, True)
        self.setAttribute(Qt.WA_AlwaysStackOnTop, True)

        # Yhteinen "tick" ajastin – 50ms välein tarkistaa vanhentuneet kortit
        # ja sulkee ne. Yksittäisille korteille ei ole erillisiä QTimereitä
        # (helppo hallita kun kortteja tulee paljon rinnakkain).
        self._tick_timer = QTimer(self)
        self._tick_timer.setInterval(50)
        self._tick_timer.timeout.connect(self._on_tick)
        self._tick_timer.start()

        # Flash-animaatio timer – yhdistetään kaikille korteille (tämä on
        # visuaalinen tehoste, ei vaikuta näyttöaikaan).
        self._flash_timer = QTimer(self)
        self._flash_timer.setInterval(120)
        self._flash_timer.timeout.connect(self._on_flash_tick)

        # Viive-ajastimet per ilmoitus: Dict[tag, QTimer]
        self._delay_timers: Dict[str, QTimer] = {}

        # Oletuskoko – päivittyy heti kun kortteja tulee
        size = self._config.overlay_size + 220
        self.resize(size, size)
        self.hide()

        self._reposition_from_config()

    # ================================================================
    # Julkiset API:t – lisää ilmoituksia
    # ================================================================

    def show_lane_switch(self, direction: str) -> None:
        """Lisää lane-switch ilmoitus (nuoli) näyttöön.

        Ilmoitus näytetään heti rinnakkain muiden mahdollisten korttien
        kanssa, eikä se poista muita (esim. jo näkyvää pest-ilmoitusta).
        """
        if direction not in (DIRECTION_LEFT, DIRECTION_RIGHT):
            return
        delay = max(0, int(getattr(self._config, "overlay_delay_ms", 0)))
        timeout_ms = max(100, int(self._config.overlay_timeout_ms))
        tag = f"lane:{direction}"

        def do_show():
            # Jos uusi ilmoitus on sama tagi → ylikirjoita vanha (ajastus alkaa
            # alusta, mutta näyttö pysyy).
            self._active[tag] = {
                "kind": KIND_LANE,
                "direction": direction,
                "timeout_ms": timeout_ms,
                "start_ms": self._now_ms(),
                "play_sound_done": False,
            }
            # Ääni soitetaan heti (ilman viivettä – SkyHanni-sync viive on vain
            # visuaalinen)
            if self._config.sound_enabled:
                if direction == DIRECTION_LEFT:
                    self._sound.play_left()
                else:
                    self._sound.play_right()
            self._trigger_flash()
            self._refresh_geometry_and_show()

        self._schedule_with_delay(tag, delay, do_show)

    def show_pest_notification(self, text: str, color_hex: str,
                               timeout_ms: Optional[int] = None,
                               play_sound: bool = True,
                               tag: Optional[str] = None,
                               persistent: bool = False) -> None:
        """Lisää pest/loadout -tyylinen tekstilmoitus kortti.

        Args:
            text: Näytettävä teksti.
            color_hex: Väri (esim. '#ff2d55').
            timeout_ms: Näkyvyysaika (None = oletus; persistent=True → ohitetaan).
            play_sound: Soitetaanko ääni.
            tag: Tunniste (tarvitaan jos halutaan myöhemmin poistaa manuaalisesti).
            persistent: True → EI AIKAKATKAISUA, kortti näkyy kunnes kutsutaan
                dismiss_by_tag(tag) tai hide_overlay(). Käytä esim.
                "väärä loadout" -varoitukseen.
        """
        if persistent:
            t_out: Optional[int] = None
        else:
            t_out = timeout_ms if timeout_ms is not None else int(
                getattr(self._config, "pest_overlay_timeout_ms", 4000)
            )
        final_tag = tag or f"pest:{(text or '')[:40]}"
        final_text = text or "PESTIT!"
        final_color = color_hex or "#ff9500"

        def do_show():
            self._active[final_tag] = {
                "kind": KIND_PEST,
                "text": final_text,
                "color": final_color,
                "timeout_ms": t_out,
                "start_ms": self._now_ms(),
                "persistent": persistent,
            }
            if play_sound and self._config.sound_enabled:
                self._sound.play_right()
            self._trigger_flash()
            self._refresh_geometry_and_show()

        self._schedule_with_delay(final_tag, 0, do_show)

    def dismiss_by_tag(self, tag: str) -> None:
        """Poista tietty kortti tunnisteen perusteella (esim. 'loadout_warning').

        Ei tee mitään jos tagia ei löydy. Käytetään kun esim. loadout-virhe
        on korjattu (käyttäjä vaihdo oikeaan).
        """
        if tag in self._delay_timers:
            self._delay_timers[tag].stop()
            del self._delay_timers[tag]
        if tag in self._active:
            del self._active[tag]
            if not self._active:
                self.hide()
            else:
                self._refresh_geometry_and_show()

    # ================================================================
    # Yleiset (hide, config päivitys)
    # ================================================================

    def hide_overlay(self) -> None:
        """Piilota kaikki kortit heti ja tyhjennä tila."""
        # Peruuta mahdolliset viiveajastimet
        for t in self._delay_timers.values():
            t.stop()
        self._delay_timers.clear()
        self._active.clear()
        self._flash_timer.stop()
        self._flash_state = 0
        self.hide()

    def update_config(self, config: AppConfig) -> None:
        """Kutsu kun asetukset muuttuvat."""
        self._config = config
        self._sound.set_volume(config.sound_volume)
        if not self.isVisible():
            size = self._config.overlay_size + 220
            self.resize(size, size)
            self.setWindowOpacity(self._config.overlay_opacity)
            self._reposition_from_config()
        else:
            # Jos kortteja on näkyvissä, päivitä niiden geometria heti
            self._refresh_geometry_and_show()

    def test_draw(self, direction: str) -> None:
        """API-yhteensopivuus vanhoille testeille."""
        self.show_lane_switch(direction)

    # ================================================================
    # Sisäiset: aikaleimat, viiveet, ajastimet
    # ================================================================

    @staticmethod
    def _now_ms() -> int:
        """Palauta nykyhetki millisekunteina (QTimer/monotoniin perustuen)."""
        from PySide6.QtCore import QDateTime
        return QDateTime.currentMSecsSinceEpoch()

    def _schedule_with_delay(self, tag: str, delay_ms: int, callback) -> None:
        """Suorita callback heti tai delay_ms kuluttua. Peruu saman tagin vanhan."""
        # Jos on jo olemassa saman taginen odottava ajastin, se perutaan
        # (esim. peräkkäiset kutsut lyhyellä välillä -> uusin astuu voimaan).
        if tag in self._delay_timers:
            self._delay_timers[tag].stop()
            del self._delay_timers[tag]

        if delay_ms <= 0:
            callback()
            return

        t = QTimer(self)
        t.setSingleShot(True)

        def done():
            self._delay_timers.pop(tag, None)
            callback()

        t.timeout.connect(done)
        self._delay_timers[tag] = t
        t.start(delay_ms)

    def _trigger_flash(self) -> None:
        """Käynnistä välkky-animaatio (uusi kortti saapunut)."""
        self._flash_state = 1
        self._flash_timer.start()

    def _on_flash_tick(self) -> None:
        self._flash_state += 1
        # 4 tickiä (~480ms) ja sitten sammuu
        if self._flash_state >= 4:
            self._flash_timer.stop()
            self._flash_state = 0
        self.update()

    def _on_tick(self) -> None:
        """Säännöllinen tarkistus (~50ms välein): poista vanhentuneet kortit.

        Ohita kortit joiden timeout_ms = None (pysyvät, esim. loadout-varoitus
        joka odottaa että käyttäjä korjaa asiansa).
        """
        now = self._now_ms()
        expired: List[str] = []
        for tag, item in self._active.items():
            t_out = item.get("timeout_ms")
            if t_out is None:
                # Pysyvä kortti – ei koskaan vanhennu täällä
                continue
            age = now - int(item.get("start_ms", now))
            if age >= int(t_out):
                expired.append(tag)

        if not expired:
            return

        for tag in expired:
            del self._active[tag]

        if not self._active:
            self.hide()
        else:
            self._refresh_geometry_and_show()

    # ================================================================
    # Geometria: laske widgetin koko + sijainti
    # ================================================================

    def _card_geometry(self, item: Dict[str, Any]) -> Tuple[int, int]:
        """Palauta (leveys, korkeus) yksittäiselle kortille."""
        size = self._config.overlay_size
        kind = item.get("kind")
        if kind == KIND_LANE:
            w = max(320, size + 120)
            h = max(240, size + 120)
        else:  # KIND_PEST
            w = max(520, size + 420)
            h = max(240, size + 80)
        return w, h

    def _total_geometry(self) -> Tuple[int, int]:
        """Laske kaikkien korttien yhteenlaskettu (leveys, korkeus)."""
        if not self._active:
            return 1, 1
        widths = []
        heights = []
        for item in self._active.values():
            w, h = self._card_geometry(item)
            widths.append(w)
            heights.append(h)
        total_w = max(widths)
        total_h = sum(heights) + _CARD_GAP * (len(heights) - 1)
        return total_w, total_h

    def _refresh_geometry_and_show(self) -> None:
        """Lasketaan uusi koko, asetetaan se, sijoitetaan ruutuun ja näytetään."""
        if not self._active:
            self.hide()
            return
        w, h = self._total_geometry()
        # Varmista että vähintään 1x1 (Qt ei tykkää nollakokoisista)
        w = max(1, w)
        h = max(1, h)
        # Käytä resize_tai_oletus – ja välitä koko _reposition_from_config:ille
        # JOTEN se EI luota self.width()/height() jotka eivät ehkä vielä ole
        # päivittyneet Qt:n event loopissa.
        self.resize(w, h)
        self.setWindowOpacity(self._config.overlay_opacity)
        self._reposition_from_config(force_size=(w, h))
        self.show()
        self.raise_()
        self.update()

    def _reposition_from_config(self, force_size: Optional[Tuple[int, int]] = None) -> None:
        """Sijoita overlay konfiguraation mukaan oikeaan ruutuun.

        force_size: jos annettu (w, h), käytä näitä arvoja self.width()/height()
                    sijaan – Qt:n resize() ei välttämättä ole vielä päivittänyt
                    attribuutteja heti kutsun jälkeen.
        """
        screen = self._get_target_screen()
        if screen is None:
            screen = QApplication.primaryScreen()
        geo = screen.availableGeometry()
        if force_size is not None:
            ow, oh = force_size
        else:
            ow, oh = self.width(), self.height()
        pos = self._config.overlay_position
        ox, oy = self._config.overlay_offset_x, self._config.overlay_offset_y

        if pos == "center":
            x = geo.x() + (geo.width() - ow) // 2 + ox
            y = geo.y() + (geo.height() - oh) // 2 + oy
        elif pos == "top":
            x = geo.x() + (geo.width() - ow) // 2 + ox
            y = geo.y() + 40 + oy
        elif pos == "bottom":
            x = geo.x() + (geo.width() - ow) // 2 + ox
            y = geo.y() + geo.height() - oh - 40 + oy
        elif pos == "left":
            x = geo.x() + 40 + ox
            y = geo.y() + (geo.height() - oh) // 2 + oy
        elif pos == "right":
            x = geo.x() + geo.width() - ow - 40 + ox
            y = geo.y() + (geo.height() - oh) // 2 + oy
        else:  # custom
            x = geo.x() + ox
            y = geo.y() + oy
        self.move(x, y)

    def _get_target_screen(self):
        screens = QApplication.screens()
        idx = self._config.overlay_monitor_index
        if idx <= 1:
            return QApplication.primaryScreen()
        if idx - 1 < len(screens):
            return screens[idx - 1]
        return screens[-1] if screens else None

    # ================================================================
    # Paint – piirrä kaikki kortit allekkain
    # ================================================================

    def paintEvent(self, event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)

        if not self._active:
            p.end()
            return

        # Piirretään jokainen kortti järjestyksessä ylhäältä alas.
        cur_y = 0
        widget_w = self.width()
        for item in self._active.values():
            w, h = self._card_geometry(item)
            # Centrataan kortti vaakasuunnassa jos se on kapeampi kuin widget
            x = (widget_w - w) // 2
            if item.get("kind") == KIND_LANE:
                self._paint_lane_card(p, QRect(x, cur_y, w, h), item)
            else:
                self._paint_pest_card(p, QRect(x, cur_y, w, h), item)
            cur_y += h + _CARD_GAP

        p.end()

    # ---------- Lane-nuoli kortti ----------

    def _paint_lane_card(self, p: QPainter, rect: QRect, item: Dict[str, Any]) -> None:
        cx = rect.center().x()
        cy = rect.center().y() - 20
        direction = item.get("direction") or DIRECTION_LEFT
        is_left = direction == DIRECTION_LEFT
        color_str = (self._config.arrow_color_left if is_left
                     else self._config.arrow_color_right)
        color = QColor(color_str)

        if self._config.flash_effect and self._flash_state > 0:
            if self._flash_state % 2 == 0:
                color = color.lighter(160)
            else:
                color = color.darker(110)

        # Tausta: hieman läpinäkyvä tumma laatikko koko kortille
        bg = QColor(0, 0, 0, int(255 * 0.50))
        p.setBrush(QBrush(bg))
        p.setPen(Qt.NoPen)
        p.drawRoundedRect(rect, 20, 20)

        # Nuoli
        arrow_size = self._config.overlay_size
        self._draw_arrow(p, cx, cy, arrow_size, color, is_left)

        # Teksti
        if self._config.show_text:
            p.setPen(QColor(self._config.text_color))
            font = QFont("Segoe UI", 24, QFont.Bold)
            p.setFont(font)
            text = self._config.overlay_text
            text_h = 60
            rect_y = rect.y() + rect.height() // 2 + arrow_size // 2 - 10
            p.drawText(rect.x() + 20, rect_y,
                       rect.width() - 40, text_h + 10,
                       Qt.AlignCenter, text)

    def _draw_arrow(self, p: QPainter, cx: int, cy: int, size: int,
                    color: QColor, is_left: bool) -> None:
        half = size // 2
        shaft_w = int(size * 0.32)
        head_len = int(size * 0.55)

        shaft_rect_x: int
        shaft_rect_w: int
        if is_left:
            shaft_rect_x = cx - half + head_len
            shaft_rect_w = size - head_len
        else:
            shaft_rect_x = cx - half
            shaft_rect_w = size - head_len

        shaft = QPolygon([
            QPoint(shaft_rect_x, cy - shaft_w // 2),
            QPoint(shaft_rect_x + shaft_rect_w, cy - shaft_w // 2),
            QPoint(shaft_rect_x + shaft_rect_w, cy + shaft_w // 2),
            QPoint(shaft_rect_x, cy + shaft_w // 2),
        ])

        if is_left:
            tip_x = cx - half
            base_x = cx - half + head_len
        else:
            tip_x = cx + half
            base_x = cx + half - head_len

        head = QPolygon([
            QPoint(tip_x, cy),
            QPoint(base_x, cy - size // 2),
            QPoint(base_x, cy + size // 2),
        ])

        pen = QPen(QColor(255, 255, 255, 200), 3)
        p.setPen(pen)
        brush = QBrush(color)
        p.setBrush(brush)

        p.drawPolygon(shaft)
        p.drawPolygon(head)

    # ---------- Pest / tekstikortti ----------

    def _paint_pest_card(self, p: QPainter, rect: QRect, item: Dict[str, Any]) -> None:
        color = QColor(item.get("color", "#ff9500"))

        if self._config.flash_effect and self._flash_state > 0:
            if self._flash_state % 2 == 0:
                color = color.lighter(150)
            else:
                color = color.darker(115)

        # Ulkoinen tumma laatikko
        pad = 24
        outer_rect = rect.adjusted(pad, pad, -pad, -pad)
        p.setPen(Qt.NoPen)
        bg = QColor(0, 0, 0, int(255 * 0.72))
        p.setBrush(QBrush(bg))
        p.drawRoundedRect(outer_rect, 28, 28)

        # Yläreunassa paksu värinen raita (accent)
        accent_rect = QRect(outer_rect.x(), outer_rect.y(),
                            outer_rect.width(), 14)
        accent_path = QPainterPath()
        accent_path.addRoundedRect(accent_rect, 10, 10)
        p.fillPath(accent_path, QBrush(color))

        # Reunus colorilla
        pen = QPen(color, 5)
        p.setPen(pen)
        p.setBrush(Qt.NoBrush)
        p.drawRoundedRect(outer_rect, 28, 28)

        # Teksti – suuri, lihavoitu, keskellä
        text = item.get("text", "") or ""
        lines = text.split("\n") if text else [""]
        base_size = max(22, min(54, outer_rect.height() // 7))
        first_size = int(base_size * 1.25)
        rest_size = base_size

        inner = outer_rect.adjusted(28, 40, -28, -28)

        y = inner.y()
        for i, line in enumerate(lines):
            if i == 0 and len(lines) > 1:
                font = QFont("Segoe UI", first_size, QFont.Black)
            else:
                font = QFont("Segoe UI", rest_size, QFont.Bold)
            p.setFont(font)
            fm = p.fontMetrics()
            line_h = fm.height()
            while fm.horizontalAdvance(line) > inner.width() and font.pointSize() > 10:
                new_size = font.pointSize() - 2
                font.setPointSize(new_size)
                fm = p.fontMetrics()
                line_h = fm.height()
            p.setPen(QColor(self._config.text_color))
            r = QRect(inner.x(), y, inner.width(), line_h + 8)
            p.drawText(r, Qt.AlignCenter, line)
            y += line_h + 8

    # ================================================================
    # Hiiri (WindowTransparentForInput päällä → normaalisti ei aktivoidu)
    # ================================================================

    def mousePressEvent(self, event) -> None:
        self.acknowledged.emit()
        self.hide_overlay()
