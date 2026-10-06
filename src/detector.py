"""
Lane-switch + Pest-Cooldown signaalin detektio.

AINOA lähde (luotettavin 100%): Minecraftin latest.log.
   SkyHanni kirjoittaa chattiin täsmälleen:
     [Render thread/INFO]: [System] [CHAT] §e[SkyHanni] Lane Switch!
   §-koodit poistetaan → etsitään "[skyhanni] lane switch" (case-insensitive).

   PEST COOLDOWN (SkyHanni):
     [23:25:34] ... [CHAT] [SkyHanni] §cPest spawn cooldown expires in 5s
     §e[CLICK to disable this feature]
   §-koodit poistettu → "[skyhanni] pest spawn cooldown expires in 5s"
   - Parsitaan aika (5s tai 2m 30s)
   - Emitataan COUNTDOWN-eventti jossa seconds_left = luettu aika
   - Samalla käynnistetään sisäinen ajastin; kun aika päättyy → READY-eventti

Koska viestissä ei ole mainittua LEFT/RIGHT, käytetään vaihtoehtoista
statemachinea:
   initial_direction (oletus LEFT) → joka kerta kun Lane Switch! tulee,
   suunta käännetään (LEFT ↔ RIGHT). Tämä sopii täydellisesti 2-näppäimiseen
   A ↔ D -farmingiin (Hypixel Garden, 2 lined farm).

TÄRKEÄ: Tämä moduuli EI koskaan lähetä näppäinpainalluksia / inputteja
Minecraftiin. Se LUOKEE VAIN tiedostoja (latest.log).
"""

import os
import re
import time
from dataclasses import dataclass
from typing import Optional, List, Tuple
from enum import Enum

from .config import (
    AppConfig,
    DIRECTION_LEFT, DIRECTION_RIGHT,
    SKYHANNI_LANESWITCH_PATTERNS,
    SKYHANNI_PEST_COOLDOWN_REGEXES,
    SKYHANNI_PEST_SPAWN_REGEXES,
    COUNTDOWN_SECONDS_RE,
    PLOT_NAME_RE,
    LOADOUT_PATTERN_KILL, LOADOUT_PATTERN_SPAWN,
    LOADOUT_KILL, LOADOUT_SPAWN,
    default_minecraft_log_path,
)


class DetectionSource(Enum):
    LOG = "log"             # Pääasiallinen (luotettavin)
    MOCK = "mock"           # Testi-napit / -hotkeyt


@dataclass
class DetectionResult:
    direction: Optional[str]       # DIRECTION_LEFT / RIGHT / None
    source: DetectionSource
    raw_text: str = ""
    confidence: float = 0.0        # 0.0 - 1.0


# ---------- Pest-cooldown ----------

class PestEventType(Enum):
    SPAWN = "spawn"        # Pestit spawnaavat → aloita cooldown (SPAWN-tapahtuma)
    COUNTDOWN = "countdown"  # SkyHanni: "cooldown expires in 5s" → seconds_left = X
    READY = "ready"          # Cooldown todella päättynyt → seuraavat pestit voivat tulla


@dataclass
class PestEvent:
    """Yksittäinen pest-ilmoitus (SPAWN, COUNTDOWN tai READY)."""
    type: PestEventType
    count: int = 0               # SPAWN: pestien lukumäärä (tai 0 jos ei tiedossa)
                                 # COUNTDOWN: sekunnit jäljellä (tai 0 jos ei tiedossa)
    plot: str = ""               # SPAWN: plotin nimi (tai plotit / "2m 9s")
                                 # COUNTDOWN: "5s" / "2m 30s" (lähde-tekstin lyhyt versio)
    source: DetectionSource = DetectionSource.LOG
    raw_text: str = ""


# ---------- Loadout tunnistus ----------

class LoadoutEventType(Enum):
    SWITCH = "switch"  # Loadout vaihdettu (kill ↔ spawn)


@dataclass
class LoadoutEvent:
    """Loadout-tilan muutos (Pest kill / Pest spawn)."""
    type: LoadoutEventType
    loadout: str                 # LOADOUT_KILL tai LOADOUT_SPAWN
    source: DetectionSource = DetectionSource.LOG
    raw_text: str = ""


# Minecraft chatissä esiintyvät väri/tyylikoodit: § + yksi heksa/kirjain
_SECTION_CODE_RE = re.compile(r"§[0-9a-fk-orxA-FK-OR]")


def strip_minecraft_codes(text: str) -> str:
    """Poista §X -koodit ja pienennä lowercaseksi."""
    return _SECTION_CODE_RE.sub("", text).lower().strip()


def parse_cooldown_seconds(line_lower: str) -> Optional[int]:
    """Parsitaan "cooldown expires in ..." -rivistä jäljellä oleva aika sekunteina.
    Palauttaa None jos ei löydy aikaa.

    Tuetut muodot:
        in 5s → 5
        in 30s → 30
        in 2m 9s → 129
        in 1m 30s → 90
    """
    if not line_lower:
        return None
    m = COUNTDOWN_SECONDS_RE.search(line_lower)
    if not m:
        return None
    mins = 0
    secs = 0
    try:
        mins = int(m.group("min") or 0)
    except (IndexError, TypeError, ValueError):
        mins = 0
    try:
        secs = int(m.group("sec") or 0)
    except (IndexError, TypeError, ValueError):
        secs = 0
    total = mins * 60 + secs
    return total if total > 0 else None


def parse_spawn_count_and_plot(line_lower: str) -> Tuple[int, str]:
    """Parsitaan "pests spawned in ..." -rivistä count ja plot.

    Palauttaa: (count: int, plot: str)
    - count: 0 jos ei lukumäärää löydy (fallback)
    - plot: plotin nimi tai "?" / "2m 9s" / listaus plottien nimistä
    """
    count = 0
    plot = ""

    if not line_lower:
        return count, plot

    # 1) Yritä lukea numeerinen count (esim. "5 pests spawned...")
    m_count = re.search(r"\b(?P<n>\d+)\s+pests?\s+spawned\b", line_lower, re.IGNORECASE)
    if m_count:
        try:
            count = int(m_count.group("n"))
        except (ValueError, TypeError):
            count = 0

    # 2) Etsi "spawned in XXX" -osa ja erota XXX pelkäksi tekstiksi
    #    (?<![a-z]) = varmista että sana ei jatku ennen spawned:ään (word boundary)
    m_in = re.search(r"(?:(?<![a-z])spawned|(?<=\s)spawned)\s+in\s+(.+?)(?:\s*[\.!\?]|$)", line_lower, re.IGNORECASE)
    if m_in:
        rest = m_in.group(1).strip().rstrip("!.,;: ")
    else:
        # Fallback: etsi koko rivistä "in XXX" jos löytyy
        m_in2 = re.search(r"\bin\s+(.+?)(?:\s*[\.!\?]|$)", line_lower, re.IGNORECASE)
        if m_in2:
            rest = m_in2.group(1).strip().rstrip("!.,;: ")
        else:
            rest = line_lower.strip()

    # 3) Onko "rest" aikamääre? (esim. "2m 9s") → onko min+sec rakenteinen
    is_time_format = bool(COUNTDOWN_SECONDS_RE.search("in " + rest))
    if is_time_format:
        # SkyHanni summary-viesti: count puuttuu, plot = aika
        return count, rest

    # 4) Etsi kaikki plot-nimet restistä (PLOT_NAME_RE)
    plots_found = PLOT_NAME_RE.findall(rest)
    if plots_found:
        # Jos count = 0, käytetään löydettyjen plottien määrää countina
        # (offline-spawn tapaus jossa lista plottien nimet)
        if count == 0:
            count = len(plots_found)
        plot = ", ".join(plots_found)
    else:
        # Jos ei löydy mitään plot-nimeä, käytä raw-rest osaa
        plot = rest if rest else "?"

    return count, plot


def _is_time_only_string(s: str) -> bool:
    """Palauttaa True jos merkkijono on pelkkä aikamääre (esim. "2m 9s").
    Käytetään jotta plot-tunnistus ei sotke aikamääreitä ploteiksi."""
    if not s:
        return False
    return bool(re.fullmatch(r"\s*(?:\d+\s*m\s*)?\d+\s*s\s*", s, re.IGNORECASE))


class LaneDetector:
    """Lane-vaihtojen tunnistus + pest-cooldown tunnistus (vain logi)."""

    def __init__(self, config: AppConfig):
        self.update_config(config)

        # --- Suunta-statemachine (A ↔ D farming) ---
        self._current_direction: str = DIRECTION_LEFT
        self.reset_direction_to(config.initial_direction)

        # --- Debounce (vähentää kaksoistriggerit lane-switchille) ---
        self._event_debounce_ms: int = 1600
        self._last_event_trigger_time: float = 0.0

        # --- Pest-spawn debounce (10s, koska samasta spawnista usea lokirivi) ---
        self._pest_spawn_debounce_s: float = 10.0
        self._last_pest_spawn_emitted_at: Optional[float] = None

        # --- Login seuraaminen ---
        self._log_last_path: Optional[str] = None
        self._log_last_position: Optional[int] = None
        self._log_last_inode: Optional[int] = None

        # --- Pest-cooldown state ---
        # Kun saadaan SPAWN -> aloita koko cooldown (pest_cooldown_ms)
        # Kun saadaan COUNTDOWN-viesti (expires in Xs): päivitä loppuaika
        self._cooldown_start_at: Optional[float] = None   # Milloin cooldown alkoi (spawn tai ensimmäinen countdown)
        self._cooldown_ready_at: Optional[float] = None   # Milloin cooldown päättyy (epoch s)
        # Onko READY-ilmotus jo lauennut tälle cooldownille (estetään monta kertaa)
        self._ready_fired: bool = False
        # Pienet 5s-viestit saattavat tulla useaan kertaan; emitoi COUNTDOWN vain
        # jos on yli 2s eroa edelliseen tai ensimmäinen kerta.
        self._last_countdown_emitted_at: Optional[float] = None
        self._countdown_emit_gap_s: float = 2.0

        # --- Loadout-state (1 = Pest kill, 2 = Pest spawn) ---
        # None = ei vielä tiedossa (ei vielä nähty You equipped -viestiä)
        self._current_loadout: Optional[str] = None
        # Debounce loadout-riveille (useiden rivien esto):
        self._last_loadout_emitted_at: Optional[float] = None
        self._loadout_debounce_s: float = 1.0

    # ---------------------------------------------------------------
    # Config / state management
    # ---------------------------------------------------------------

    def update_config(self, config: AppConfig) -> None:
        """Päivitä asetukset (kutsutaan kun tallennetaan GUI:sta)."""
        self._config = config
        if config.log_enabled and not config.log_file_path:
            auto = default_minecraft_log_path()
            if auto and os.path.exists(auto):
                pass

    def reset_direction_to(self, direction: str) -> None:
        d = direction.strip().lower() if direction else ""
        if d == DIRECTION_RIGHT:
            self._current_direction = DIRECTION_RIGHT
        else:
            self._current_direction = DIRECTION_LEFT
        self._config.initial_direction = self._current_direction

    @property
    def current_direction(self) -> str:
        return self._current_direction

    def flip_direction(self) -> str:
        if self._current_direction == DIRECTION_LEFT:
            self._current_direction = DIRECTION_RIGHT
        else:
            self._current_direction = DIRECTION_LEFT
        return self._current_direction

    def _pass_event_debounce(self) -> bool:
        now = time.time()
        if now - self._last_event_trigger_time < (self._event_debounce_ms / 1000.0):
            return False
        self._last_event_trigger_time = now
        return True

    # ---------------------------------------------------------------
    # Pää-API: kutsutaan pääsilmukasta (tick)
    # ---------------------------------------------------------------

    def check_all(self) -> Tuple[DetectionResult, List[PestEvent], List[LoadoutEvent]]:
        """Suorita YKSI kierros tunnistusta (luo logit KERRAN ja parsitse
        lane-, pest- ja loadout-tapahtumat samalta lukukerralta).
        Tämä on oikea tapa kutsua detektoria tick-silmukassa.

        Palauttaa (lane_result, pest_events_list, loadout_events_list).
        """
        lane_res = DetectionResult(None, DetectionSource.LOG)
        pest_events: List[PestEvent] = []
        loadout_events: List[LoadoutEvent] = []
        now = time.time()

        if self._config.log_enabled:
            # Lue ja parsitaan KAIKKI samalla kerralla (jotta login sijainti kasvaa vain kerran)
            lane_res_from_log, pest_events_from_log, loadout_events_from_log = self._read_log_and_parse(
                emit_lane=True, emit_pest=True
            )
            if lane_res_from_log is not None:
                lane_res = lane_res_from_log
            pest_events.extend(pest_events_from_log)
            loadout_events.extend(loadout_events_from_log)

        # Lisää READY-tapahtuma jos cooldown on päättynyt
        if (
            getattr(self._config, "pest_enabled", True)
            and self._cooldown_ready_at is not None
            and not self._ready_fired
            and now >= self._cooldown_ready_at
        ):
            self._ready_fired = True
            if getattr(self._config, "pest_show_ready", True):
                pest_events.append(PestEvent(
                    type=PestEventType.READY,
                    count=0,
                    plot="",
                    source=DetectionSource.LOG,
                    raw_text="cooldown-ready",
                ))

        return lane_res, pest_events, loadout_events

    def check_once(self) -> DetectionResult:
        """Suorita yksi lane-switch tunnistuskierros (Minecraft-loki).
        HUOM: jos samalla tickillä haluat myös pest- / loadout-tapahtumat, käytä
        check_all() -metodia jotta logi luetaan vain kerran.
        """
        if self._config.log_enabled:
            lane_res, _p, _l = self._read_log_and_parse(emit_lane=True, emit_pest=True)
            if lane_res is not None:
                return lane_res
        return DetectionResult(None, DetectionSource.LOG)

    def check_pest_events(self) -> List[PestEvent]:
        """Suorita yksi kierros pest-cooldown-tarkistuksia:
        1. Lue uusia rivejä logista → löydetyt SPAWN / COUNTDOWN -eventit
        2. Tarkista onko _cooldown_ready_at saavutettu → jos on, emitoi READY kerran.

        HUOM: jos samalla tickillä kutsutaan myös check_once(), logit luetaan
        kaksi kertaa. Käytä mieluummin check_all().

        Palauttaa 0..N PestEventtiä (tyypillisesti 0 tai 1).
        """
        events: List[PestEvent] = []
        now = time.time()

        if self._config.log_enabled:
            # 1) Uudet rivit logista
            _lane_res, pest_events_from_log, _loadout_events = self._read_log_and_parse(
                emit_lane=True, emit_pest=True
            )
            events.extend(pest_events_from_log)

        # 2) Ready-ilmotus? (jos aikaleima on saavutettu eikä READY:ä vielä lähetetty)
        if (
            getattr(self._config, "pest_enabled", True)
            and self._cooldown_ready_at is not None
            and not self._ready_fired
            and now >= self._cooldown_ready_at
        ):
            self._ready_fired = True
            if getattr(self._config, "pest_show_ready", True):
                events.append(PestEvent(
                    type=PestEventType.READY,
                    count=0,
                    plot="",
                    source=DetectionSource.LOG,
                    raw_text="cooldown-ready",
                ))

        return events

    # ---------- Pest julkiset APIt ----------

    @property
    def pest_cooldown_remaining_ms(self) -> int:
        """Palauttaa jäljellä olevan cooldown-ajan ms tai 0 jos ei ole käynnissä / ohi."""
        if self._cooldown_ready_at is None:
            return 0
        rem = self._cooldown_ready_at - time.time()
        return max(0, int(rem * 1000))

    @property
    def pest_cooldown_total_ms(self) -> int:
        """Palauttaa koko cooldownin pituuden ms (progress baria varten).
        Lasketaan viimeisimmän SPAWN / COUNTDOWN-viestin perusteella."""
        if self._cooldown_start_at is None or self._cooldown_ready_at is None:
            return 0
        return max(1, int((self._cooldown_ready_at - self._cooldown_start_at) * 1000))

    def pest_trigger_spawn_now(self, count: int = 1, plot: str = "?") -> PestEvent:
        """Manuaalinen testi tai oikea tunnistus: pestit spawnaavat -> aloita cooldown.

        Käynnistää config.pest_cooldown_ms cooldownin (oletus 2 min 15 s).
        """
        count = max(0, int(count))
        now = time.time()
        cd_ms = max(1000, int(getattr(self._config, "pest_cooldown_ms", 135000)))
        self._cooldown_start_at = now
        self._cooldown_ready_at = now + (cd_ms / 1000.0)
        self._ready_fired = False
        self._last_countdown_emitted_at = None
        self._last_pest_spawn_emitted_at = now
        return PestEvent(
            type=PestEventType.SPAWN,
            count=count,
            plot=plot or "?",
            source=DetectionSource.MOCK,
            raw_text=f"mock-pest-spawn:{count}@{plot}",
        )

    def pest_trigger_countdown_now(self, seconds: int = 5) -> PestEvent:
        """Manuaalinen testi: countdown X sekunnista (GUI-testaus)."""
        seconds = max(1, int(seconds))
        now = time.time()
        self._cooldown_start_at = now
        self._cooldown_ready_at = now + seconds
        self._ready_fired = False
        self._last_countdown_emitted_at = now
        return PestEvent(
            type=PestEventType.COUNTDOWN,
            count=seconds,
            plot=f"{seconds}s",
            source=DetectionSource.MOCK,
            raw_text=f"mock-countdown:{seconds}s",
        )

    def pest_trigger_ready_now(self) -> PestEvent:
        """Manuaalinen testi: laita cooldown heti päättyneeksi ja palauta READY-event."""
        self._cooldown_ready_at = time.time()
        self._ready_fired = False
        return PestEvent(
            type=PestEventType.READY,
            count=0,
            plot="",
            source=DetectionSource.MOCK,
            raw_text="mock-pest-ready",
        )

    def pest_reset_cooldown(self) -> None:
        """Nollaa pest-cooldown tila kokonaan."""
        self._cooldown_start_at = None
        self._cooldown_ready_at = None
        self._ready_fired = False
        self._last_countdown_emitted_at = None
        self._last_pest_spawn_emitted_at = None

    # ---------- Loadout julkiset APIt ----------

    @property
    def current_loadout(self) -> Optional[str]:
        """Nykyinen loadout: None (tuntematon), 'kill' tai 'spawn'."""
        return self._current_loadout

    def expected_loadout_for(self, event_type: PestEventType) -> Optional[str]:
        """Mikä loadout pitäisi olla kun pest-eventti laukeaa?

        - SPAWN (pestit tulossa) → config.loadout_on_spawn (oletus 'kill' = 1.)
        - READY (cooldown ohi)  → config.loadout_on_ready (oletus 'spawn' = 2.)
        - Muuten → None (ei vaatimuksia)
        """
        if event_type == PestEventType.SPAWN:
            return getattr(self._config, "loadout_on_spawn", LOADOUT_KILL)
        if event_type == PestEventType.READY:
            return getattr(self._config, "loadout_on_ready", LOADOUT_SPAWN)
        return None

    def is_loadout_correct_for(self, event_type: PestEventType) -> bool:
        """Onko nykyinen loadout oikea annetulle pest-eventille?

        Palauttaa True jos:
        - loadout-tarkistus on poistettu käytöstä (config)
        - nykyistä loadoutia ei tunneta (aloitus, varaudu: salli alkuun kaiken)
        - loadout vastaa expected_loadout_for(...)
        """
        if not getattr(self._config, "loadout_enabled", True):
            return True
        expected = self.expected_loadout_for(event_type)
        if expected is None:
            return True
        # Jos nykyistä loadoutia ei vielä tunne (aloitus), sallitaan alkuun event
        # (ohjataan muuten varoituksella jos tarpeen)
        if self._current_loadout is None:
            return True
        return self._current_loadout == expected

    def loadout_trigger_now(self, loadout: str) -> LoadoutEvent:
        """Manuaalinen testi: aseta loadout ja palauta SWITCH-event."""
        loadout = (loadout or "").strip().lower()
        if loadout not in (LOADOUT_KILL, LOADOUT_SPAWN):
            loadout = LOADOUT_KILL
        self._current_loadout = loadout
        self._last_loadout_emitted_at = time.time()
        return LoadoutEvent(
            type=LoadoutEventType.SWITCH,
            loadout=loadout,
            source=DetectionSource.MOCK,
            raw_text=f"mock-loadout:{loadout}",
        )

    # ---------------------------------------------------------------
    # 1. Minecraft-loki
    # ---------------------------------------------------------------

    def _resolve_log_path(self) -> Optional[str]:
        p = self._config.log_file_path or default_minecraft_log_path()
        return p or None

    def _read_log_new_lines(self) -> List[str]:
        """Luetaan kaikki uudet rivit logista (tai tyhjä lista)."""
        path = self._resolve_log_path()
        if not path or not os.path.exists(path):
            return []
        try:
            st = os.stat(path)
            size = st.st_size
        except OSError:
            return []
        try:
            inode = st.st_ino
        except AttributeError:
            inode = None
        changed = (
            self._log_last_path != path
            or (inode is not None and self._log_last_inode != inode)
            or self._log_last_position is None
            or size < (self._log_last_position or 0)
        )
        if changed:
            self._log_last_path = path
            self._log_last_inode = inode
            self._log_last_position = max(0, size - 8192)
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as f:
                f.seek(self._log_last_position)
                data = f.read()
                self._log_last_position = f.tell()
        except OSError:
            return []
        if not data:
            return []
        return data.splitlines()

    def _read_log_and_parse(
        self, emit_lane: bool, emit_pest: bool
    ) -> Tuple[Optional[DetectionResult], List[PestEvent], List[LoadoutEvent]]:
        """Luetaan uusia rivejä JA parsitaan lane, pestit ja loadout samalla kertaa
        (jotta _log_last_position kasvaa vain kerran / tick).

        Palauttaa:
          (lane_result, list[PestEvent], list[LoadoutEvent])
        """
        lines = self._read_log_new_lines()
        lane_res: Optional[DetectionResult] = None
        pest_events: List[PestEvent] = []
        loadout_events: List[LoadoutEvent] = []

        lane_pats = SKYHANNI_LANESWITCH_PATTERNS
        cooldown_rx: List[re.Pattern] = [
            re.compile(p, re.IGNORECASE) for p in SKYHANNI_PEST_COOLDOWN_REGEXES
        ]
        spawn_rx: List[re.Pattern] = [
            re.compile(p, re.IGNORECASE) for p in SKYHANNI_PEST_SPAWN_REGEXES
        ]
        loadout_kill_pats = LOADOUT_PATTERN_KILL
        loadout_spawn_pats = LOADOUT_PATTERN_SPAWN

        now = time.time()

        for raw in lines:
            line = strip_minecraft_codes(raw)
            if not line:
                continue

            # Loadout (tarkistetaan ensin, sillä helpot matchaukset ja tilan päivitys)
            if getattr(self._config, "loadout_enabled", True):
                matched_lo = False
                new_loadout: Optional[str] = None
                for pat in loadout_kill_pats:
                    if pat in line:
                        new_loadout = LOADOUT_KILL
                        matched_lo = True
                        break
                if not matched_lo:
                    for pat in loadout_spawn_pats:
                        if pat in line:
                            new_loadout = LOADOUT_SPAWN
                            matched_lo = True
                            break
                if matched_lo and new_loadout is not None:
                    # Onko riittävän pitkä aika viimeisestä (debounce)?
                    emit = True
                    if (self._last_loadout_emitted_at is not None
                            and (now - self._last_loadout_emitted_at) < self._loadout_debounce_s):
                        emit = False
                    # Jos sama loadout kuin jo aiemmin, ei aina tarvitse emitoitua
                    # (mutta jos debounce ei estä, päivitetään silti tila)
                    if self._current_loadout != new_loadout:
                        self._current_loadout = new_loadout
                        self._last_loadout_emitted_at = now
                        loadout_events.append(LoadoutEvent(
                            type=LoadoutEventType.SWITCH,
                            loadout=new_loadout,
                            source=DetectionSource.LOG,
                            raw_text=raw,
                        ))
                    elif emit:
                        # Varmistetaan että tila on tallessa vaikka emitointia ei tapahdukaan
                        self._current_loadout = new_loadout
                        self._last_loadout_emitted_at = now

            # Lane (ensimmäinen match -> palautetaan)
            if emit_lane and lane_res is None:
                for pat in lane_pats:
                    if pat in line:
                        if self._pass_event_debounce():
                            if self._config.alternate_direction:
                                next_dir = self._current_direction
                                self.flip_direction()
                                lane_res = DetectionResult(
                                    direction=next_dir,
                                    source=DetectionSource.LOG,
                                    raw_text=raw,
                                    confidence=0.99,
                                )
                            else:
                                lane_res = DetectionResult(
                                    direction=self._current_direction,
                                    source=DetectionSource.LOG,
                                    raw_text=raw,
                                    confidence=0.95,
                                )
                        break

            # Pest-tapahtumat (SPAWN tai COUNTDOWN)
            if emit_pest and getattr(self._config, "pest_enabled", True):
                matched = False

                # 1) Ensin kokeillaan SPAWN-tapahtumaa (pests spawned in...)
                for rx in spawn_rx:
                    if rx.search(line):
                        count, plot = parse_spawn_count_and_plot(line)

                        # 10 sekunnin debounce: samasta spawnista usea lokirivi
                        if (self._last_pest_spawn_emitted_at is not None
                                and (now - self._last_pest_spawn_emitted_at) < self._pest_spawn_debounce_s):
                            matched = True
                            break

                        self._last_pest_spawn_emitted_at = now
                        # Aloita koko cooldown (pest_cooldown_ms)
                        cd_ms = max(1000, int(getattr(self._config, "pest_cooldown_ms", 135000)))
                        self._cooldown_start_at = now
                        self._cooldown_ready_at = now + (cd_ms / 1000.0)
                        self._ready_fired = False
                        self._last_countdown_emitted_at = None

                        pest_events.append(PestEvent(
                            type=PestEventType.SPAWN,
                            count=int(count),
                            plot=plot,
                            source=DetectionSource.LOG,
                            raw_text=raw,
                        ))
                        matched = True
                        break
                if matched:
                    continue  # seuraava rivi

                # 2) Sitten kokeillaan COUNTDOWN-tapahtumaa (expires in Xs)
                for rx in cooldown_rx:
                    if rx.search(line):
                        seconds_left = parse_cooldown_seconds(line)
                        if seconds_left is None:
                            seconds_left = 5  # oletus jos regex ei jostain syystä löytänyt

                        # Estä saman viestin (tai 1s välein tulevien 5s) päällekkäisyys
                        if (self._last_countdown_emitted_at is not None
                                and (now - self._last_countdown_emitted_at) < self._countdown_emit_gap_s):
                            break

                        self._last_countdown_emitted_at = now
                        # Lasketaan uusi ehdotettu ready-aika
                        proposed_ready_at = now + seconds_left
                        # Päivitä loppuaika JOS:
                        #   - ei vielä ole mitään cooldownia, TAI
                        #   - uusi aika on aiemmin kuin nykyinen ready_at (esim. oletamme
                        #     että "expires in 5s" on tarkempi kuin vanha spawn-arvio,
                        #     mutta **älä** koskaan pidennä cooldownia tällä viestillä).
                        if self._cooldown_start_at is None:
                            self._cooldown_start_at = now
                            self._cooldown_ready_at = proposed_ready_at
                        elif (self._cooldown_ready_at is None
                              or proposed_ready_at <= self._cooldown_ready_at):
                            # Säilytä alkuperäinen _cooldown_start_at (jotta progressbar pysyy oikeana)
                            self._cooldown_ready_at = proposed_ready_at
                        self._ready_fired = False

                        # Plot-kentässä lyhyt teksti "Xs" tai "2m 9s" esittelyä varten
                        secs = seconds_left % 60
                        mins = seconds_left // 60
                        if mins > 0:
                            short = f"{mins}m {secs}s"
                        else:
                            short = f"{seconds_left}s"

                        pest_events.append(PestEvent(
                            type=PestEventType.COUNTDOWN,
                            count=int(seconds_left),
                            plot=short,
                            source=DetectionSource.LOG,
                            raw_text=raw,
                        ))
                        break  # älä tarkista toista regex-sääntöä samasta rivistä

        return lane_res, pest_events, loadout_events

    def check_minecraft_log(self) -> DetectionResult:
        """Backwards compatibility / wrapper: kutsuu check_once()."""
        return self.check_once()

    # ---------------------------------------------------------------
    # Yhteensopivuus + GUI:n tarvitsemat julkiset APIt
    # ---------------------------------------------------------------

    def detect_from_frame(self) -> DetectionResult:
        """Edellisen version API-yhteensopivuus: kutsuu check_once()."""
        return self.check_once()

    def mock_trigger(self, direction: str) -> DetectionResult:
        """Manuaalinen käynnistys (testi-nappi / -hotkey)."""
        if direction not in (DIRECTION_LEFT, DIRECTION_RIGHT):
            return DetectionResult(None, DetectionSource.MOCK)
        if not self._pass_event_debounce():
            return DetectionResult(None, DetectionSource.MOCK,
                                   raw_text=f"mock-debounce:{direction}")
        return DetectionResult(
            direction=direction,
            source=DetectionSource.MOCK,
            raw_text=f"mock:{direction}",
            confidence=1.0,
        )

    def reset_debounce(self) -> None:
        self._last_event_trigger_time = 0.0
