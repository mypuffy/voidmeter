# VOIDMETER

Live-DPS-Overlay für EVE Online (im Stil von PELD). Liest die EVE-Gamelogs live mit und zeigt DPS raus/rein, Reps, Cap-Transfer, Neuts, Hit-Profil, Waffen, Ziele und Angreifer als randloses Overlay über dem Spiel.

## Features

- **Solo und Flotte:** Tab „FLOTTE“ für eigene Multibox-Chars und Flottenkollegen (Host oder Relay)
- **Layouts:** Cockpit, Leiste, Turm, Orbit
- **Stile:** HUD, Neon, Minimal, Terminal, dazu frei kombinierbare Farbschemata
- **Waffen- und Konter-Info:** Reichweiten, Stärken, Schwächen, passende Schadensart gegen Schild- oder Panzer-Fits
- **Click-Through-Lock** (Strg+Shift+F9), Kampf-Log in `encounters.csv`
- Unterstützt den deutschen, englischen, russischen, französischen, japanischen und chinesischen Client

## Start

1. Python 3 installieren (python.org, „Add python.exe to PATH“ anhaken). Weitere Pakete braucht es nicht, nur tkinter aus der Standardbibliothek.
2. `VOIDMETER starten.bat` doppelklicken. Ohne laufendes EVE zum Ausprobieren: `VOIDMETER Demo.bat`.
3. Optional: `Als EXE bauen.bat` erzeugt mit PyInstaller `dist\VOIDMETER.exe`.

Oder direkt: `python voidmeter.py [--demo] [--logdir PFAD]`

Relay-Server für die Flotte: `python voidmeter.py --relay --port 47820 --code MEINCODE`

Ausführliche Anleitung: [LIESMICH.txt](LIESMICH.txt)
