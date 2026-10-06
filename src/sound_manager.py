"""
Äänienhallinta. Käyttää PySide6:n QSoundEffectiä (ei ulkoisia äänikirjastoja
tarvita, joten asennus on helpompi eikä tarvitse kääntää SDL/DLL-tiedostoja).

QSoundEffect tukee .wav-tiedostoja (sopiva meidän tarpeeseemme).
Oletus-äänit generoidaan runtime-ssa .wav-tiedostoiksi käyttäjän %TEMP%:iin.
"""

import os
import math
import struct
import wave
import tempfile
from typing import Optional

from PySide6.QtCore import QUrl
from PySide6.QtMultimedia import QSoundEffect


class SoundManager:
    """Hallitsee ilmoitusäänien toistoa (QSoundEffect)."""

    def __init__(self):
        self._volume: float = 0.7
        self._enabled: bool = True

        # Äänet: vasen (korkeampi) ja oikea (alempi)
        self._left_sfx: Optional[QSoundEffect] = None
        self._right_sfx: Optional[QSoundEffect] = None

        # Cache:ssa temp-tiedostopolut, jotta tiedostot eivät häviä kesken soiton
        self._tmp_files: list[str] = []

        self._generate_default_sounds()

    # ---------------------------------------------------------------
    # Yksityiset: oletus-äänien generointi
    # ---------------------------------------------------------------

    def _generate_beep_wav(self, frequency: int, duration_ms: int = 200,
                           amplitude: float = 0.5) -> str:
        """Luo yksinkertaisen bieppiäänen wav-tiedostona ja palauttaa polun."""
        sample_rate = 44100
        n_samples = int(sample_rate * duration_ms / 1000)
        frames: list[bytes] = []
        for i in range(n_samples):
            t = i / sample_rate
            attack_n = int(n_samples * 0.05)
            release_start_n = int(n_samples * 0.7)
            if i < attack_n:
                env = i / max(1, attack_n)
            elif i > release_start_n:
                release_n = n_samples - release_start_n
                env = max(0.0, 1.0 - (i - release_start_n) / max(1, release_n))
            else:
                env = 1.0
            value = int(32767 * amplitude * env * math.sin(2 * math.pi * frequency * t))
            # Stereo
            sample = struct.pack("<h", value)
            frames.append(sample)
            frames.append(sample)

        tmp_fp = tempfile.NamedTemporaryFile(
            prefix="slo_beep_", suffix=".wav", delete=False
        )
        tmp_fp.close()
        with wave.open(tmp_fp.name, "w") as wf:
            wf.setnchannels(2)
            wf.setsampwidth(2)
            wf.setframerate(sample_rate)
            wf.writeframes(b"".join(frames))
        self._tmp_files.append(tmp_fp.name)
        return tmp_fp.name

    def _generate_default_sounds(self) -> None:
        """Generoi oletusbiepit (vasen=korkeampi A5, oikea=alempi E5)."""
        try:
            left_path = self._generate_beep_wav(880, 180, 0.4)
            right_path = self._generate_beep_wav(660, 180, 0.4)
            self._left_sfx = QSoundEffect()
            self._left_sfx.setSource(QUrl.fromLocalFile(left_path))
            self._left_sfx.setVolume(self._volume)
            self._right_sfx = QSoundEffect()
            self._right_sfx.setSource(QUrl.fromLocalFile(right_path))
            self._right_sfx.setVolume(self._volume)
        except Exception as e:
            print(f"[Sound] Oletus-äänien generointi epäonnistui: {e}")
            self._left_sfx = None
            self._right_sfx = None

    # ---------------------------------------------------------------
    # Julkinen API
    # ---------------------------------------------------------------

    def set_enabled(self, enabled: bool) -> None:
        self._enabled = bool(enabled)

    def set_volume(self, volume: float) -> None:
        self._volume = max(0.0, min(1.0, volume))
        if self._left_sfx is not None:
            self._left_sfx.setVolume(self._volume)
        if self._right_sfx is not None:
            self._right_sfx.setVolume(self._volume)

    def set_custom_sound(self, left_path: Optional[str],
                         right_path: Optional[str]) -> None:
        """Aseta custom äänitiedostot (.wav). None = käytä oletusta."""
        try:
            if left_path and os.path.exists(left_path):
                sfx = QSoundEffect()
                sfx.setSource(QUrl.fromLocalFile(left_path))
                sfx.setVolume(self._volume)
                self._left_sfx = sfx
            if right_path and os.path.exists(right_path):
                sfx = QSoundEffect()
                sfx.setSource(QUrl.fromLocalFile(right_path))
                sfx.setVolume(self._volume)
                self._right_sfx = sfx
        except Exception as e:
            print(f"[Sound] Custom-äänien asetus epäonnistui: {e}")

    def play_left(self) -> None:
        if not self._enabled:
            return
        try:
            if self._left_sfx is not None:
                # QSoundEffectissa play() soittaa äänen kerran (loopCount=1 oletus)
                self._left_sfx.play()
        except Exception as e:
            print(f"[Sound] Vasemman äänen toisto epäonnistui: {e}")

    def play_right(self) -> None:
        if not self._enabled:
            return
        try:
            if self._right_sfx is not None:
                self._right_sfx.play()
        except Exception as e:
            print(f"[Sound] Oikean äänen toisto epäonnistui: {e}")
