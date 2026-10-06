"""
Konfiguraatiohallinta Skyblock Lane Overlay -sovellukselle.
Tallentaa ja lataa asetukset JSON-tiedostona.
"""

import json
import os
import re
from dataclasses import dataclass, asdict, field
from typing import Optional, Tuple

# Hyödynnetään näitä detectorissa ja GUI:ssä
DIRECTION_LEFT = "left"
DIRECTION_RIGHT = "right"

# SkyHanni-logiikan tunnistusmerkit (chat/loki). §-koodit poistetaan ennen tarkistusta.
SKYHANNI_LANESWITCH_PATTERNS: Tuple[str, ...] = (
    # Tarkka format käyttäjän antamasta:
    #   [Render thread/INFO]: [System] [CHAT] §e[SkyHanni] Lane Switch!
    "[skyhanni] lane switch",
    "skyhanni lane switch",
    # Lisää muita yleisiä SkyHanni Garden -muotoja ilman §-koodia:
    "switch lanes",
)

# SkyHanni Pest-Cooldown tunnistus (ainoa lähde logista).
#
# Etsi SkyHannin ilmoitus joka tulee juuri ennen cooldownin päättymistä:
#   "[23:25:34] ... [CHAT] [SkyHanni] §cPest spawn cooldown expires in 5s"
#   "§e[CLICK to disable this feature]"
#
# §-koodit poistettu → "[skyhanni] pest spawn cooldown expires in 5s"
#
# Regexissä luetaan joko "expires in Xs" tai pidempi "in Xm Ys":
#   (?P<sec>\d+)\s*s            → 5s, 10s, 30s
#   (?P<min>\d+)\s*m\s*(?P<sec>\d+)\s*s → 2m 30s
SKYHANNI_PEST_COOLDOWN_REGEXES: Tuple[str, ...] = (
    # 1) SkyHanni prefix + cooldown expires in ...
    r"\[skyhanni\][^\n]*\bcooldown\s+expires\s+in\b",
    # 2) Fallback: mikä vain "cooldown expires in" jotta ei jää kiinni prefixiin
    r"\bcooldown\s+expires\s+in\b",
)

# SkyHanni/Hypixel Pest Spawn -tunnistus (logista).
#
# Muodot (§-koodit poistettu lowercase:ksi):
#   1) Yksi pest:    "1 pest spawned in r1!"
#   2) Useampi pest: "5 pests spawned in b2!"
#   3) Moni plotti:  "pests spawned in r1, b2, c3!"  (lasketaan plottien määrä = count)
#   4) SkyHanni summary (ilman countia): "pests spawned in 2m 9s"  → count = None, plot = "2m 9s"
SKYHANNI_PEST_SPAWN_REGEXES: Tuple[str, ...] = (
    # 1) Numeerinen count + (pest/pests) spawned in PLOT
    r"\b(?P<count>\d+)\s+pests?\s+spawned\s+in\b",
    # 2) Pelkkä "pests spawned in ..." (joko monta plottia tai summary-aika)
    r"\bpests?\s+spawned\s+in\b",
)

# Erikoisregex jolla luetaan sekunnit tai min+sec ilmoituksesta (ajastimen viiveelle).
# Tämä ajetaan erikseen NÄYTETYN regexin matchauksen jälkeen (löydetty rivi).
COUNTDOWN_SECONDS_RE = re.compile(
    r"in\s*(?:(?P<min>\d+)\s*m\s*)?(?:(?P<sec>\d+)\s*s)",
    re.IGNORECASE,
)

# Plot-nimien tunnistus (The Barn, Garden jne.).
# Esim. R1, B2, C10, PLOT - 12 jne. Käytetään kun "pests spawned in ..."
# -viestissä ei ole numero-countia (offline-tapauksessa listataan monta plottia).
PLOT_NAME_RE = re.compile(
    r"\b(?:plot\s*[-–:]\s*)?([a-z]{1,3}\d{1,3})\b",
    re.IGNORECASE,
)

# Loadout-tunnistus (Hypixel: "You equipped X!").
# 1 = "Pest kill"   → pestien tappoloadout (käytössä farmatessa / cooldownin aikana)
# 2 = "Pest spawn"  → pestien spawnausloadout (kun cooldown loppuu)
LOADOUT_PATTERN_KILL: Tuple[str, ...] = (
    "you equipped pest kill!",
    "equipped pest kill",
)
LOADOUT_PATTERN_SPAWN: Tuple[str, ...] = (
    "you equipped pest spawn!",
    "equipped pest spawn",
)

# Loadout-identifiointi: tarvitaan detectorissa
LOADOUT_KILL = "kill"    # 1. loadout = pestien tappo
LOADOUT_SPAWN = "spawn"  # 2. loadout = pestien spawn


# Kansio polun apufunktio – auttaa auto-tunnistuksessa Minecraftin lokia
def default_minecraft_log_path() -> str:
    """Yritä löytää Minecraftin latest.log Windowsilla.

    Palauttaa tyhjän merkkijonon jos AppData-polkua ei löydy.
    """
    appdata = os.environ.get("APPDATA") or ""
    if not appdata:
        return ""
    return os.path.join(appdata, ".minecraft", "logs", "latest.log")


@dataclass
class AppConfig:
    """Sovelluksen asetukset."""

    # Monitori-asetukset
    overlay_monitor_index: int = 1  # Mihin ruutuun overlay laitetaan (1 = pää = YouTube)

    # Overlay-näyttöasetukset
    overlay_position: str = "center"  # center, top, bottom, left, right, custom
    overlay_offset_x: int = 0
    overlay_offset_y: int = 0
    overlay_size: int = 200  # Nuolen/merkin koko pikseleinä
    overlay_opacity: float = 0.92  # Läpinäkyvyys (0.0 - 1.0)
    overlay_timeout_ms: int = 3000  # Kuinka kauan overlay näkyy (ms)
    overlay_delay_ms: int = 0  # Kuinka pitkän viiveen jälkeen overlay näytetään (ms)
    flash_effect: bool = True
    arrow_color_left: str = "#ff3b30"  # Punainen (vasen)
    arrow_color_right: str = "#34c759"  # Vihreä (oikea)
    text_color: str = "#ffffff"
    show_text: bool = True
    overlay_text: str = "VAIHDA NÄPPÄIN"

    # Pest-cooldown overlay-asetukset (SkyHanni logista tulee "expires in Xs")
    pest_enabled: bool = True
    pest_show_spawn: bool = True
    pest_show_ready: bool = True
    pest_cooldown_ms: int = 135000  # 2 min 15 s = 135 * 1000
    pest_overlay_timeout_ms: int = 4000
    pest_color_spawn: str = "#ff9500"  # Oranssi (pestit tulossa)
    pest_color_ready: str = "#00c7ff"  # Cyan (cooldown ohi)
    pest_text_spawn: str = "PESTIT TULED! {count}kpl\nPlot: {plot}"
    pest_text_ready: str = "PESTI COOLDOWN OHI!\nSeuraavat pestit = MILLO TAHANSA"
    activate_minecraft_on_pest_spawn: bool = True  # Tuo Minecraft eteen kun oikea SPAWN havaitaan logista

    # Loadout-tarkistus (1=Pest kill, 2=Pest spawn)
    # - Oikea loadout ennen kuin overlay laukeaa (estää vahingot)
    loadout_enabled: bool = True
    # Mikä loadout on OIKEA kussakin pest-tilassa:
    #   SPAWN (pestit tulossa) → "kill"   = vaihda takaisin 1.
    #   READY (cooldown ohi)  → "spawn"  = vaihda 2.
    loadout_on_spawn: str = "kill"
    loadout_on_ready: str = "spawn"
    # Väärä loadout -varoitus:
    loadout_wrong_color: str = "#ff2d55"  # Punainen (vaara)
    loadout_wrong_text: str = "⚠ VÄÄRÄ LOADOUT!\n{hint}\nVaihda ensin → sitten jatka"
    loadout_block_until_correct: bool = True  # True = älä näytä alkuperäistä ilmoitusta ennenkuin loadout oikein

    # Ääniasetukset
    sound_enabled: bool = True
    sound_volume: float = 0.7  # 0.0 - 1.0
    sound_file: str = ""  # Tyhjä = oletus-ääni
    sound_file_left: str = ""   # Custom .wav vasemmalle
    sound_file_right: str = ""  # Custom .wav oikealle

    # Detektioasetukset (AINOA = Minecraftin logi)
    detection_enabled: bool = True
    detection_interval_ms: int = 120  # Millä tahdilla luetaan uusia rivejä logista

    # ---- Minecraft-logi (luotettavin, ainoa lähde) ----
    log_enabled: bool = True
    # Tyhjä -> sovellus yrittää itse löytää %APPDATA%/.minecraft/logs/latest.log
    # Prism/Badlion/Feather yms. launcherit -> käyttäjä valitsee oikean polun.
    log_file_path: str = ""
    # SkyHanni ei chattiviestissä kerro suuntaa (vasen/oikea), joten käytetään
    # vaihtoehtoista mallia: ensimmäinen vaihto = initial_direction, jokainen
    # seuraava kääntää suunnan. Sopii 2-näppäin (A ↔ D) farmingiin täydellisesti.
    alternate_direction: bool = True
    initial_direction: str = DIRECTION_LEFT  # Vaihto #1, jonka jälkeen vaihdetaan oikealle

    # Hotkey-asetukset (tekstinä, esim. 'ctrl+shift+o')
    hotkey_toggle: str = ""
    hotkey_acknowledge: str = ""
    hotkey_test_left: str = ""
    hotkey_test_right: str = ""

    # Muut asetukset
    run_on_startup: bool = False
    minimize_to_tray: bool = True
    debug_mode: bool = False

    # Statistiikka
    lane_switch_count: int = 0


class ConfigManager:
    """Hoitaa asetusten tallentamisen ja lataamisen."""

    DEFAULT_CONFIG_PATH = os.path.join(
        os.path.expanduser("~"), ".skyblock_lane_overlay", "config.json"
    )

    def __init__(self, config_path: Optional[str] = None):
        self.config_path = config_path or self.DEFAULT_CONFIG_PATH
        self.config = AppConfig()
        self._ensure_dir()

    def _ensure_dir(self) -> None:
        os.makedirs(os.path.dirname(self.config_path), exist_ok=True)

    def load(self) -> AppConfig:
        """Lataa asetukset tiedostosta tai palauta oletukset."""
        if os.path.exists(self.config_path):
            try:
                with open(self.config_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                self.config = AppConfig(**{k: v for k, v in data.items()
                                           if k in AppConfig.__dataclass_fields__})
            except (json.JSONDecodeError, TypeError, ValueError) as e:
                print(f"[Config] Virhe ladattaessa asetuksia: {e}, käytetään oletuksia.")
                self.config = AppConfig()
        return self.config

    def save(self) -> None:
        """Tallenna asetukset tiedostoon."""
        try:
            data = asdict(self.config)
            with open(self.config_path, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
        except OSError as e:
            print(f"[Config] Virhe tallennettaessa asetuksia: {e}")

    def reset(self) -> None:
        """Nollaa asetukset oletuksiksi."""
        self.config = AppConfig()
        self.save()

    def increment_counter(self) -> None:
        self.config.lane_switch_count += 1
        self.save()

    def reset_counter(self) -> None:
        self.config.lane_switch_count = 0
        self.save()
