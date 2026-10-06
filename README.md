# Skyblock Lane Overlay 🔰

Kevyt Windows-ilmoitussovellus Hypixel Skyblock Garden-farmingiin.

> **TÄRKEÄÄ:** Tämä on **VAIN ILMOITUSTYÖKALU**. Se **ei koskaan paina näppäimiä**, simuloi syötteitä tai tee mitään automatisoitua toimintoa Minecraftissa.
>
> - ✅ Lukee ruudun pikseleitä (mss)
> - ✅ Lukee (vapaaehtoisesti) Minecraftin `latest.log`-tiedoston
> - ✅ Näyttää oman always-on-top overlay-näytön pääruudulle
> - ✅ Toistaa äänimerkkejä (PySide6 QSoundEffect)
> - ✅ Vastaa omassa GUI:ssa oleviin nappeihin
> - ❌ **EI** koskaan lähetä näppäinpainalluksia, klikkauksia tai muuta syötettä
>
> Käyttäjä on **vastuussa omasta käytöstään** Hypixel-sääntöjen näkökulmasta. Tarkista säännöt aina ennen käyttöä.

---

## Ominaisuudet

| Ominaisuus | Kuvaus |
|---|---|
| **Always-on-top overlay** | Suuri nuoli (vasen ⬅ / oikea ➡) + teksti + flash-efekti. Läpinäkyvä, ei häiritse YouTubeta. |
| **Kaksinayttö-tuki** | Minecraft kakkosruudulla → skannataan sinne, overlay näytetään YouTube-pääruudulle (tai valittuun ruutuun). |
| **Monipuolinen detektio** 1) **Testi-napit / hotkey** (manuaalinen), 2) **OCR** (SkyHannin HUD-tekstit, tarv. Tesseract), 3) **Väri-tunnistus** (kirkkaat tekstialueet), 4) **Minecraft-loki** (valinnainen). |
| **Skannausalueen valinta** | Interaktiivinen alueen valinta (vetäytyminen ruudulta). |
| **System tray** | Pienennä taustalle; kaksoisklikkaa palkkia palauttaaksesi. |
| **Laskuri** | Laskee montako lane-vaihtoa on tullut session aikana. |
| **Debug-tila** | Näyttää ruutukaappauksen mitä sovellus "näkee" + tapahtumalokin. |
| **Tallenna/lataa asetukset** | JSON-tiedostoon käyttäjän kotihakemistoon `.skyblock_lane_overlay/config.json`. |

---

## Vaatimukset

- **Windows 10 / 11** (64-bit)
- **Python 3.10** tai uudempi (asenna [python.org](https://www.python.org/downloads/))
  - 💡 Asennuksessa **ruksaa** *"Add Python to PATH"*.
- **Vapaaehtoiset:**
  - [Tesseract OCR](https://github.com/UB-Mannheim/tesseract/wiki) (jos haluat käyttää OCR-tunnistusta) – asenna ja lisää kansio `PATH`-muuttujaan.
  - Custom-äänet: omat `.wav`-tiedostot (16-bit PCM toimii parhaiten).

---

## Asennus

1. **Kloonaa / lataa** tämä projekti kansioon.
2. **Avaa PowerShell tai CMD** kansiossa.
3. Suorita joko:

    ```cmd
    run_overlay.bat
    ```

    (ensimmäisenä käynnistyskerran asentaa riippuvuudet automaattisesti)

    **TAI** käsin:

    ```bash
    python -m venv .venv
    .venv\Scripts\activate
    pip install -r requirements.txt
    python main.py
    ```

---

## Käyttö (Hypixel Skyblock Garden)

1. Käynnistä sovellus (`python main.py` tai `run_overlay.bat`).
2. **Välilehti 1 – Skannaus:**
   - Valitse *Skannausruutu* → se ruutu jossa Minecraft pyörii (yleensä ruutu 2).
   - Paina **Valitse alue ruudulta…**
   - Vedä suorakulmio ympäröimään SkyHannin HUD-alue jossa lane-switch -teksti ilmestyy. Vähintään 200x50 pikseliä, mutta ei liian suuri (hitaampaa). 💡 Vinkki: kokeile ensin koko SkyHanni-sidepanelin lähellä olevaa ilmoitusaluetta.
3. **Välilehti 2 – Overlay:**
   - Valitse *Overlay-ruutu* → se ruutu jossa YouTube pyörii (yleensä ruutu 1).
   - Säädä sijainti (Keskelle on hyvä alku), koko, peittävyys, näkyvyysaika, värit.
4. **Välilehti 3 – Äänet:**
   - Kokeile ääniä. Voit asettaa custom `.wav`-tiedostot.
5. **Testaa:**
   - Paina ylärivin **⬅ Testaa vasen** / **Testaa oikea ➡**.
   - Jos overlay ilmestyy ja ääni kuuluu ✅.
6. **Käynnistä skannaus** – paina **▶ Käynnistä skannaus**.
7. Halutessasi pienenna ikkunan → **X** (pienenee system tray:hin jos asetus päällä).

---

## Detektio – Parhaat käytännöt

SkyHanni **ei yleensä kirjoita** chat-viesteinä lane-vaihdoista. Sen vuoksi meillä on useita lähteitä järjestyksessä nopeimmasta luotettavimpaan:

| Lähteet | Luotettavuus | Nopeus | Tarvitsee |
|---|---|---|---|
| Manuaalinen testi-nappi | ⭐⭐⭐⭐⭐ | ~0 ms | ei mitään (hyvä testaukseen) |
| Minecraft-loki (ChatTriggers) | ⭐⭐⭐⭐⭐ | < 50 ms | .minecraft/logs/latest.log + CT-skripti |
| Väripohjainen tunnistus | ⭐⭐⭐ | ~100 ms | ei mitään |
| OCR (Tesseract) | ⭐⭐⭐⭐ | ~200–400 ms | Tesseract asennettuna |

### Vinkki 1: Käytä ChatTriggers + Loki (luotettavin!)

Jos osaat käyttää [ChatTriggers](https://www.chattriggers.com/)-modia, tee yksinkertainen skripti joka **kirjoittaa chattiin** tai printtaa konsoliin aina kun SkyHanni ilmoittaa lane-vaihdosta. Tämän jälkeen aseta sovellus seuraamaan `latest.log`:ia. Esimerkiksi:

```js
// ChatTriggers (js - esimerkki, sovita SkyHannin API/in tapahtumiin):
register("tick", () => {
   // SkyHanni sijoittaa usein tietoja World#displayName tai scoreboard:een
   // Alla esimerkki – tarkista SkyHannin dokumentaatio oikeasta eventistä
});
```

Kun signaali on logissa/lokituksessa, aseta **Avainsanat**-välilehdellä oikeat matchit:
- Vasemmalle: `left`, `switch left`, `←`
- Oikealle: `right`, `switch right`, `→`

### Vinkki 2: OCR-asetukset

- Skannausalue **pieneksi** mutta kattamaan täsmälleen se kohta jossa teksti ilmestyy.
- Aseta skannausväli ~80–150 ms.
- Muuta tekstin väri/avainsanat vastaamaan SkyHannin todellista ilmoitusta.

---

## Tiedostot

```
modi/
├── main.py                     # ← Käynnistä täältä (python main.py)
├── run_overlay.bat             # Windowsin kaksoisklikkaus-käynnistys
├── requirements.txt
├── README.md
└── src/
    ├── __init__.py
    ├── app.py                  # QApplication + komponenttien kasaaminen
    ├── config.py               # JSON-asetukset + ConfigManager
    ├── sound_manager.py        # Äänet (QSoundEffect, oletusbiepit generoidaan .wav:ksi)
    ├── overlay.py              # Always-on-top overlay-ikkuna (nuoli+teksti)
    ├── screen_capture.py       # mss-ruudunkaappaus + monitorit
    ├── detector.py             # Lane-switch tunnistus (OCR/väri/logi/mock)
    ├── region_selector.py      # Alueen interaktiivinen valinta
    └── main_window.py          # Pääikkuna, asetukset, system tray, debug
```

Asetukset tallentuvat:
```
C:\Users\<sinä>\.skyblock_lane_overlay\config.json
```

---

## Turvallisuus / Hypixel

🔴 **Mikään tämän ohjelman osa EI SAA:**

- kutsua `SendInput`, `mouse_event`, `keybd_event` tai vastaavia Windows-API:ita
- lähettää `PostMessage`/`SendMessage` Minecraft-ikkunaan (tai mihinkään muuhun)
- muokata toisen prosessin muistia (writeProcessMemory yms.)
- tehdä muuta kuin **lukea** ruutua / lokitiedostoja / käyttäjän syötteitä omassa ikkunassaan

Koodissa on kommentoitu selvästi joka moduulin kohdalla nämä rajoitukset. Jos epäilet, lue lähdekoodi ennen käyttöä.

> Käytä vastuullisesti. Tämä työkalu **ei säädä** Hypixelin sääntöjen ulkopuolella, koska se ei tee mitään muuta kuin näyttää käyttäjälle ilmoituksen – *sama asia kuin jos joku huutaa Discordissa "vaihda nyt vasemmalle!"*.
>
> **Sinä olet vastuussa** siitä, että et yhdistä tähän työkaluun mitään ulkoista automaattiohjelmistoa (macro, AHK-skripti yms.), joka käyttäisi tätä signaalia syötteiden generoimiseen.

---

## Ongelmienratkaisu

- **Overlay ei näy?** Aseta overlay-monitori uudelleen ja tallenna.
- **OCR ei tunnista mitään?** Käynnistä debug-tila (Debug-välilehti) ja katso miltä ruutukaappaus näyttää. Säädä skannausalue ja/tai Tesseractin asennuspolku PATH:iin.
- **Minecraft FPS laskee?**
  - Kasvata skannausväliä esim. 150 → 250 ms.
  - Pienennä skannausalueen kokoa.
  - Poista OCR käytöstä jos ei tarvita.
- **Sovellus kaatuu käynnistyksessä?** Avaa CMD, aja `python main.py` ja katso virheilmoitus. Usein puuttuva kirjasto → aja `pip install -r requirements.txt`.

---

## Kehittäjälle

Run syntax check:
```bash
python -m compileall main.py src
```

Tyyliohjeet:
- Käytä type-hinttejä.
- Kommentoi suomeksi tai englanniksi.
- Älä lisää mitään input-simulaatioon liittyvää koodia.

---

## Lisenssi

MIT.
