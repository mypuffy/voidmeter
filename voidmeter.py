#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
VOIDMETER — Live-Combat-Telemetrie-Overlay für EVE Online
=========================================================

Liest die EVE-Gamelogs (Dokumente\\EVE\\logs\\Gamelogs) live mit und zeigt
DPS raus/rein, Reps, Cap-Transfer, Neuts, Hit-Profil, Waffen und Ziele an –
als randloses, immer-im-Vordergrund-Overlay.

Nur Python-Standardbibliothek (tkinter). Start:  python voidmeter.py
Optionen:  --demo      simulierter Kampf zum Ausprobieren
           --logdir X  eigenen Gamelogs-Ordner setzen

Bedienung:
  Kopfzeile ziehen = verschieben · Ecke unten rechts ziehen = skalieren
  Rechtsklick = Menü (Charakter, Fenster, Theme, Deckkraft, Lock, Demo …)
  Strg+Shift+F9 = Click-Through-Lock an/aus (Windows)
"""

import os
import sys
import re
import json
import time
import math
import random
import codecs
import csv
import threading
import datetime
import argparse
import socket
import select
from collections import deque, defaultdict, Counter

try:
    import tkinter as tk
    from tkinter import font as tkfont, filedialog
except Exception:  # Relay-Modus auf Servern ohne tkinter
    tk = tkfont = filedialog = None

APP_NAME = "VOIDMETER"
VERSION = "1.0"
IS_WIN = sys.platform.startswith("win")

# ════════════════════════════════════════════════════════════════════
#  Konfiguration
# ════════════════════════════════════════════════════════════════════

def config_dir():
    if IS_WIN:
        base = os.environ.get("APPDATA") or os.path.expanduser("~")
    else:
        base = os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config")
    d = os.path.join(base, APP_NAME)
    try:
        os.makedirs(d, exist_ok=True)
    except OSError:
        pass
    return d


DEFAULTS = {
    "x": 80, "y": 80,
    "scale": 1.0,
    "alpha": 0.94,
    "window": 10,
    "theme": "VOID",
    "compact": False,
    "character": None,       # None = AUTO (folgt dem aktiven Kampf)
    "log_dir": None,
    "right_list": "targets",
    "particles": True,
    "tab": "solo",
    "design": "HUD",
    "layout": "COCKPIT",
    "list_tab": "weapons",
    "fleet_window": False,
    "fleet_x": 500, "fleet_y": 80,
    "net_mode": None, "net_host": "", "net_port": 47820, "net_code": "",
}


def load_config():
    cfg = dict(DEFAULTS)
    try:
        with open(os.path.join(config_dir(), "config.json"), "r", encoding="utf-8") as f:
            cfg.update(json.load(f))
    except Exception:
        pass
    return cfg


def save_config(cfg):
    try:
        with open(os.path.join(config_dir(), "config.json"), "w", encoding="utf-8") as f:
            json.dump(cfg, f, indent=2)
    except Exception:
        pass


# ════════════════════════════════════════════════════════════════════
#  Gamelogs finden
# ════════════════════════════════════════════════════════════════════

def documents_dir():
    if IS_WIN:
        try:
            import ctypes
            buf = ctypes.create_unicode_buffer(1024)
            # CSIDL_PERSONAL = 5 → echter "Dokumente"-Pfad (auch bei OneDrive-Umleitung)
            ctypes.windll.shell32.SHGetFolderPathW(None, 5, None, 0, buf)
            if buf.value:
                return buf.value
        except Exception:
            pass
    return os.path.join(os.path.expanduser("~"), "Documents")


def detect_log_dir():
    home = os.path.expanduser("~")
    cands = [os.path.join(documents_dir(), "EVE", "logs", "Gamelogs")]
    for sub in ("Documents", "Dokumente", os.path.join("OneDrive", "Documents"),
                os.path.join("OneDrive", "Dokumente")):
        cands.append(os.path.join(home, sub, "EVE", "logs", "Gamelogs"))
    for c in cands:
        if os.path.isdir(c):
            return c
    return cands[0]


# ════════════════════════════════════════════════════════════════════
#  Log-Sprachen (Muster kompatibel zu PELD)
# ════════════════════════════════════════════════════════════════════

LANGS = {
    "english": {
        "character": r"(?<=Listener: ).*",
        "damageOut": r"\(combat\) <.*?><b>([0-9]+).*>to<",
        "damageIn": r"\(combat\) <.*?><b>([0-9]+).*>from<",
        "armorRepairedOut": r"\(combat\) <.*?><b>([0-9]+).*> remote armor repaired to <",
        "hullRepairedOut": r"\(combat\) <.*?><b>([0-9]+).*> remote hull repaired to <",
        "shieldBoostedOut": r"\(combat\) <.*?><b>([0-9]+).*> remote shield boosted to <",
        "armorRepairedIn": r"\(combat\) <.*?><b>([0-9]+).*> remote armor repaired by <",
        "hullRepairedIn": r"\(combat\) <.*?><b>([0-9]+).*> remote hull repaired by <",
        "shieldBoostedIn": r"\(combat\) <.*?><b>([0-9]+).*> remote shield boosted by <",
        "capTransferedOut": r"\(combat\) <.*?><b>([0-9]+).*> remote capacitor transmitted to <",
        "capNeutralizedOut": r"\(combat\) <.*?ff7fffff><b>([0-9]+).*> energy neutralized <",
        "nosRecieved": r"\(combat\) <.*?><b>\+([0-9]+).*> energy drained from <",
        "capTransferedIn": r"\(combat\) <.*?><b>([0-9]+).*> remote capacitor transmitted by <",
        "capNeutralizedIn": r"\(combat\) <.*?ffe57f7f><b>([0-9]+).*> energy neutralized <",
        "nosTaken": r"\(combat\) <.*?><b>\-([0-9]+).*> energy drained to <",
        "mined": r"\(mining\) .*? <.*?><.*?>([0-9]+).*> units of <.*?><.*?>(.+?)<",
    },
    "german": {
        "character": r"(?<=Empfänger: ).*",
        "damageOut": r"\(combat\) <.*?><b>([0-9]+).*>nach<",
        "damageIn": r"\(combat\) <.*?><b>([0-9]+).*>von<",
        "armorRepairedOut": r"\(combat\) <.*?><b>([0-9]+).*> Panzerungs-Fernreparatur zu <",
        "hullRepairedOut": r"\(combat\) <.*?><b>([0-9]+).*> Rumpf-Fernreparatur zu <",
        "shieldBoostedOut": r"\(combat\) <.*?><b>([0-9]+).*> Schildfernbooster aktiviert zu <",
        "armorRepairedIn": r"\(combat\) <.*?><b>([0-9]+).*> Panzerungs-Fernreparatur von <",
        "hullRepairedIn": r"\(combat\) <.*?><b>([0-9]+).*> Rumpf-Fernreparatur von <",
        "shieldBoostedIn": r"\(combat\) <.*?><b>([0-9]+).*> Schildfernbooster aktiviert von <",
        "capTransferedOut": r"\(combat\) <.*?><b>([0-9]+).*> Fernenergiespeicher übertragen zu <",
        "capNeutralizedOut": r"\(combat\) <.*?ff7fffff><b>([0-9]+).*> Energie neutralisiert <",
        "nosRecieved": r"\(combat\) <.*?><b>\+([0-9]+).*> Energie transferiert von <",
        "capTransferedIn": r"\(combat\) <.*?><b>([0-9]+).*> Fernenergiespeicher übertragen von <",
        "capNeutralizedIn": r"\(combat\) <.*?ffe57f7f><b>\-?([0-9]+).*> Energie neutralisiert <",
        "nosTaken": r"\(combat\) <.*?><b>\-([0-9]+).*> Energie transferiert zu <",
        "mined": r"\(mining\) .*? <.*?><.*?>([0-9]+).*(?:<localized .*?>)?(.+)\*<",
    },
    "russian": {
        "character": r"(?<=Слушатель: ).*",
        "damageOut": r"\(combat\) <.*?><b>([0-9]+).*>на<",
        "damageIn": r"\(combat\) <.*?><b>([0-9]+).*>из<",
        "armorRepairedOut": r"\(combat\) <.*?><b>([0-9]+).*> единиц запаса прочности брони отремонтировано <",
        "hullRepairedOut": r"\(combat\) <.*?><b>([0-9]+).*> единиц запаса прочности корпуса отремонтировано <",
        "shieldBoostedOut": r"\(combat\) <.*?><b>([0-9]+).*> единиц запаса прочности щитов накачано <",
        "armorRepairedIn": r"\(combat\) <.*?><b>([0-9]+).*> единиц запаса прочности брони получено дистанционным ремонтом от <",
        "hullRepairedIn": r"\(combat\) <.*?><b>([0-9]+).*> единиц запаса прочности корпуса получено дистанционным ремонтом от <",
        "shieldBoostedIn": r"\(combat\) <.*?><b>([0-9]+).*> единиц запаса прочности щитов получено накачкой от <",
        "capTransferedOut": r"\(combat\) <.*?><b>([0-9]+).*> единиц запаса энергии накопителя отправлено в <",
        "capNeutralizedOut": r"\(combat\) <.*?ff7fffff><b>([0-9]+).*> энергии нейтрализовано <",
        "nosRecieved": r"\(combat\) <.*?><b>\+([0-9]+).*> энергии извлечено из <",
        "capTransferedIn": r"\(combat\) <.*?><b>([0-9]+).*> единиц запаса энергии накопителя получено от <",
        "capNeutralizedIn": r"\(combat\) <.*?ffe57f7f><b>([0-9]+).*> энергии нейтрализовано <",
        "nosTaken": r"\(combat\) <.*?><b>\-([0-9]+).*> энергии извлечено и передано <",
        "mined": r"\(mining\) .*? <.*?><.*?>([0-9]+).*(?:<localized .*?>)?(.+)\*<",
    },
    "french": {
        "character": r"(?<=Auditeur: ).*",
        "damageOut": r"\(combat\) <.*?><b>([0-9]+).*>à<",
        "damageIn": r"\(combat\) <.*?><b>([0-9]+).*>de<",
        "armorRepairedOut": r"\(combat\) <.*?><b>([0-9]+).*> points de blindage transférés à distance à <",
        "hullRepairedOut": r"\(combat\) <.*?><b>([0-9]+).*> points de structure transférés à distance à <",
        "shieldBoostedOut": r"\(combat\) <.*?><b>([0-9]+).*> points de boucliers transférés à distance à <",
        "armorRepairedIn": r"\(combat\) <.*?><b>([0-9]+).*> points de blindage réparés à distance par <",
        "hullRepairedIn": r"\(combat\) <.*?><b>([0-9]+).*> points de structure réparés à distance par <",
        "shieldBoostedIn": r"\(combat\) <.*?><b>([0-9]+).*> points de boucliers transférés à distance par <",
        "capTransferedOut": r"\(combat\) <.*?><b>([0-9]+).*> points de capaciteur transférés à distance à <",
        "capNeutralizedOut": r"\(combat\) <.*?ff7fffff><b>([0-9]+).*> d'énergie neutralisée en faveur de <",
        "nosRecieved": r"\(combat\) <.*?><b>([0-9]+).*> d'énergie siphonnée aux dépens de <",
        "capTransferedIn": r"\(combat\) <.*?><b>([0-9]+).*> points de capaciteur transférés à distance par <",
        "capNeutralizedIn": r"\(combat\) <.*?ffe57f7f><b>([0-9]+).*> d'énergie neutralisée aux dépens de <",
        "nosTaken": r"\(combat\) <.*?><b>([0-9]+).*> d'énergie siphonnée en faveur de <",
        "mined": r"\(mining\) .*? <.*?><.*?>([0-9]+).*(?:<localized .*?>)?(.+)\*<",
    },
    "japanese": {
        "character": r"(?<=傍聴者: ).*",
        "damageOut": r"\(combat\) <color=0xff00ffff><b>([0-9]+).*>から<",
        "damageIn": r"\(combat\) <color=0xffcc0000><b>([0-9]+).*>から<",
        "armorRepairedOut": r"\(combat\) <.*?><b>([0-9]+).*>リモートアーマーリペアを<.*に与えました",
        "hullRepairedOut": r"\(combat\) <.*?><b>([0-9]+).*>リモート船体リペアを<.*に与えました",
        "shieldBoostedOut": r"\(combat\) <.*?><b>([0-9]+).*>リモートシールドブーストを<.*に与えました",
        "armorRepairedIn": r"\(combat\) <.*?><b>([0-9]+).*>リモートアーマーリペアを<.*から受けました",
        "hullRepairedIn": r"\(combat\) <.*?><b>([0-9]+).*>リモート船体リペアを<.*で受けました",
        "shieldBoostedIn": r"\(combat\) <.*?><b>([0-9]+).*>リモートシールドブーストを<.*で受けました",
        "capTransferedOut": r"\(combat\) <.*?><b>([0-9]+).*>リモートキャパシタが<.*に転送されました",
        "capNeutralizedOut": r"\(combat\) <.*?ff7fffff><b>([0-9]+).*> エネルギーニュートラライズ 対象:<",
        "nosRecieved": r"\(combat\) <.*?><b>\+([0-9]+).*> エネルギードレイン 対象:<",
        "capTransferedIn": r"\(combat\) <.*?><b>([0-9]+).*>リモートキャパシタを<.*で受けました",
        "capNeutralizedIn": r"\(combat\) <.*?ffe57f7f><b>([0-9]+).*>のエネルギーが解放されました<",
        "nosTaken": r"\(combat\) <.*?><b>\-([0-9]+).*> エネルギードレイン 攻撃者:<",
        "mined": r"\(mining\) .*>([0-9]+).*ユニットの.*>(.+)を採掘しました",
    },
    "chinese": {
        "character": r"(?<=收听者: ).*",
        "damageOut": r"\(combat\) <.*?><b>([0-9]+).*>对<",
        "damageIn": r"\(combat\) <.*?><b>([0-9]+).*>来自<",
        "armorRepairedOut": r"\(combat\) <.*?><b>([0-9]+).*>远程装甲维修量至<",
        "hullRepairedOut": r"\(combat\) <.*?><b>([0-9]+).*>远程结构维修量至<",
        "shieldBoostedOut": r"\(combat\) <.*?><b>([0-9]+).*>远程护盾回充增量至<",
        "armorRepairedIn": r"\(combat\) <.*?><b>([0-9]+).*>远程装甲维修量由<",
        "hullRepairedIn": r"\(combat\) <.*?><b>([0-9]+).*>远程结构维修量由<",
        "shieldBoostedIn": r"\(combat\) <.*?><b>([0-9]+).*>远程护盾回充增量由<",
        "capTransferedOut": r"\(combat\) <.*?><b>([0-9]+).*>远程电容传输至<",
        "capNeutralizedOut": r"\(combat\) <.*?ff7fffff><b>([0-9]+).*>能量中和<",
        "nosRecieved": r"\(combat\) <.*?><b>\+([0-9]+).*>被从<",
        "capTransferedIn": r"\(combat\) <.*?><b>([0-9]+).*>远程电容传输量由<",
        "capNeutralizedIn": r"\(combat\) <.*?ffe57f7f><b>([0-9]+).*>能量中和<",
        "nosTaken": r"\(combat\) <.*?><b>\-([0-9]+).*>被吸取到<",
        "mined": r"\(mining\) .*? <.*?><.*?>([0-9]+).*(?:<localized .*?>)?(.+)\*<",
    },
}

# Reihenfolge: spezifische Muster zuerst, Schaden zuletzt
RULE_ORDER = [
    ("armorRepairedOut", ("logi_out",)), ("hullRepairedOut", ("logi_out",)),
    ("shieldBoostedOut", ("logi_out",)), ("armorRepairedIn", ("logi_in",)),
    ("hullRepairedIn", ("logi_in",)), ("shieldBoostedIn", ("logi_in",)),
    ("capTransferedOut", ("cap_out",)), ("capTransferedIn", ("cap_in",)),
    ("capNeutralizedOut", ("neut_out",)), ("capNeutralizedIn", ("neut_in",)),
    ("nosRecieved", ("cap_in", "neut_out")), ("nosTaken", ("neut_in",)),
    ("damageOut", ("dmg_out",)), ("damageIn", ("dmg_in",)),
]

LINE_RE = re.compile(r"^\[\s*(\d{4})\.(\d{2})\.(\d{2}) (\d{2}):(\d{2}):(\d{2})\s*\]\s*\((\w+)\)\s?(.*)$")
TAG_RE = re.compile(r"<[^>]*>")
NAME_RE = re.compile(r"ffffffff>(.*?)</b>")
MISS_IN_RE = re.compile(r"\bmisses you\b|verfehlt (?:sie|dich|euch)\b|промахивается по вам|vous manque")
MISS_OUT_RE = re.compile(r"\bmisses\b|\bverfehlt\b|промах|manque")

# Trefferqualität: bekannte Begriffe → Rang (0 = bester Treffer)
QUALITY_RANK = {
    "wrecks": 0, "smashes": 1, "penetrates": 2, "hits": 3, "glances off": 4, "grazes": 5,
}
QUALITY_SHORT = {
    "wrecks": "WRK", "smashes": "SMH", "penetrates": "PEN", "hits": "HIT",
    "glances off": "GLC", "grazes": "GRZ",
}


class Event:
    __slots__ = ("kind", "amount", "who", "ship", "weapon", "quality")

    def __init__(self, kind, amount, who="", ship="", weapon="", quality=""):
        self.kind, self.amount, self.who = kind, amount, who
        self.ship, self.weapon, self.quality = ship, weapon, quality


class LineParser:
    _cache = {}

    def __init__(self, lang):
        if lang not in LineParser._cache:
            pats = LANGS[lang]
            rules = [(key, re.compile(pats[key]), kinds) for key, kinds in RULE_ORDER]
            LineParser._cache[lang] = (rules, re.compile(pats["mined"]))
        self.lang = lang
        self.rules, self.mined = LineParser._cache[lang]

    @staticmethod
    def _clean(s):
        return TAG_RE.sub("", s).replace("*", "").strip()

    def _details(self, body):
        who = ship = ""
        m = NAME_RE.search(body)
        if m:
            raw = self._clean(m.group(1))
            sm = re.search(r"\(([^()]*)\)\s*$", raw)
            if sm:
                ship = sm.group(1).strip()
                raw = raw[:sm.start()]
            raw = re.sub(r"\[.*?\]", "", raw)
            who = raw.strip(" -")
            after = body[m.end():]
        else:
            after = body
        segs = [self._clean(s) for s in TAG_RE.sub("", after).split(" - ")]
        segs = [s for s in segs if s]
        if not who and segs:
            who = segs[0]
        return who, ship, segs

    def parse(self, line):
        m = LINE_RE.match(line.strip())
        if not m:
            return []
        cat = m.group(7)
        if cat == "combat":
            body = line[line.find("(combat)"):]
            for key, rx, kinds in self.rules:
                mm = rx.search(body)
                if not mm:
                    continue
                amt = int(mm.group(1) or 0)
                if amt <= 0:
                    return []
                who, ship, segs = self._details(body)
                weapon = quality = ""
                if key in ("damageOut", "damageIn"):
                    if len(segs) >= 2:
                        weapon, quality = segs[-2], segs[-1]
                    elif len(segs) == 1:
                        quality = segs[0]
                else:
                    weapon = segs[0] if segs else ""
                return [Event(k, amt, who, ship, weapon, quality) for k in kinds]
            # Fehlschüsse
            txt = self._clean(body[8:])
            low = txt.lower()
            if MISS_IN_RE.search(low):
                return [Event("miss_in", 0, who=txt.split(" ")[0])]
            if MISS_OUT_RE.search(low):
                parts = txt.split(" - ")
                return [Event("miss_out", 0, weapon=parts[-1].strip() if len(parts) > 1 else "")]
            return []
        if cat == "mining":
            mm = self.mined.search(line)
            if mm:
                return [Event("mined", int(mm.group(1)), weapon=self._clean(mm.group(2)))]
        return []


def read_header(path):
    """→ (charakter, sprache, encoding, bom_len) oder None"""
    try:
        with open(path, "rb") as f:
            raw = f.read(4096)
    except OSError:
        return None
    if raw.startswith(codecs.BOM_UTF16_LE):
        enc, bom = "utf-16-le", 2
    elif raw.startswith(codecs.BOM_UTF8):
        enc, bom = "utf-8", 3
    else:
        enc, bom = "utf-8", 0
    text = raw[bom:].decode(enc, errors="replace")
    for line in text.splitlines()[:8]:
        for lang, pats in LANGS.items():
            m = re.search(pats["character"], line)
            if m and m.group(0).strip():
                return m.group(0).strip(), lang, enc, bom
    return None


class Tail:
    def __init__(self, path, enc, bom, from_end):
        self.path, self.enc = path, enc
        try:
            size = os.path.getsize(path)
        except OSError:
            size = 0
        self.pos = size if from_end else bom
        self.bom = bom
        self.dec = codecs.getincrementaldecoder(enc)(errors="replace")
        self.buf = ""

    def read(self):
        try:
            size = os.path.getsize(self.path)
        except OSError:
            return []
        if size < self.pos:  # Datei neu begonnen
            self.pos = self.bom
            self.dec.reset()
            self.buf = ""
        if size == self.pos:
            return []
        try:
            with open(self.path, "rb") as f:
                f.seek(self.pos)
                data = f.read(size - self.pos)
        except OSError:
            return []
        self.pos += len(data)
        text = self.buf + self.dec.decode(data)
        lines = text.split("\n")
        self.buf = lines.pop()
        return [l.rstrip("\r") for l in lines]


class LogWatcher:
    """Beobachtet den Gamelogs-Ordner und liefert neue Zeilen je Charakter."""

    def __init__(self, log_dir):
        self.dir = log_dir
        self.headers = {}
        self.chars = {}     # name → dict(path, lang, parser, tail)
        self.first = True
        self.ok = os.path.isdir(log_dir)

    def scan(self):
        try:
            names = os.listdir(self.dir)
            self.ok = True
        except OSError:
            self.ok = False
            return
        cutoff = time.time() - 24 * 3600
        for n in sorted(names):
            if not n.lower().endswith(".txt"):
                continue
            p = os.path.join(self.dir, n)
            try:
                st = os.stat(p)
            except OSError:
                continue
            if st.st_mtime < cutoff:
                continue
            if p not in self.headers:
                h = read_header(p)
                if h is None and st.st_size < 600:
                    continue  # Kopf evtl. noch nicht geschrieben → später erneut
                self.headers[p] = h
            h = self.headers[p]
            if not h:
                continue
            char, lang, enc, bom = h
            cur = self.chars.get(char)
            newer = cur is None or (cur["path"] != p and
                                    (n, st.st_mtime) > (os.path.basename(cur["path"]), cur["mtime0"]))
            if newer:
                self.chars[char] = {
                    "path": p, "lang": lang, "mtime0": st.st_mtime,
                    "parser": LineParser(lang),
                    "tail": Tail(p, enc, bom, from_end=self.first),
                }
        self.first = False

    def poll(self):
        out = []
        for char, info in self.chars.items():
            lines = info["tail"].read()
            if lines:
                evs = []
                for l in lines:
                    evs.extend(info["parser"].parse(l))
                if evs:
                    out.append((char, evs))
        return out


# ════════════════════════════════════════════════════════════════════
#  Tracker (Raten, Peaks, Encounter)
# ════════════════════════════════════════════════════════════════════

KINDS = ("dmg_out", "dmg_in", "logi_out", "logi_in", "cap_out", "cap_in",
         "neut_out", "neut_in", "mined")
COMBAT_KINDS = {"dmg_out", "dmg_in", "logi_out", "logi_in", "neut_out", "neut_in",
                "miss_out", "miss_in"}
HIST_STEP = 0.25
HIST_LEN = 240           # 60 s Verlauf
ENC_TIMEOUT = 15.0


class Tracker:
    def __init__(self, window):
        self.window = window
        self.reset()

    def reset(self):
        self.events = deque()
        self.sums = defaultdict(int)
        self.total = defaultdict(int)
        self.peak = defaultdict(float)
        self.rate = {k: 0.0 for k in KINDS}
        self.by_weapon = Counter()
        self.by_target = Counter()
        self.by_source = Counter()
        self.quality = Counter()
        self.hits_out = self.miss_out = self.hits_in = self.miss_in = 0
        self.biggest = None
        self.feed = deque(maxlen=60)
        self.src_weapons = defaultdict(Counter)
        self.src_ship = {}
        self.src_who = {}
        self.tgt_ship = {}
        self.history = deque([(0.0, 0.0, 0.0)] * HIST_LEN, maxlen=HIST_LEN)
        self.last_event = 0.0
        self.enc = None
        self.last_enc = None
        self.fresh = []

    def add(self, ev, now):
        k = ev.kind
        if k in COMBAT_KINDS:
            self.last_event = now
            if self.enc is None:
                self.enc = {"start": now, "dmg_out": 0, "dmg_in": 0, "logi_in": 0,
                            "peak": 0.0, "peak_in": 0.0}
        if k == "miss_out":
            self.miss_out += 1
            return
        if k == "miss_in":
            self.miss_in += 1
            return
        a = ev.amount
        self.events.append((now, k, a))
        self.sums[k] += a
        self.total[k] += a
        if self.enc is not None and k in self.enc:
            self.enc[k] += a
        if k == "dmg_out":
            self.hits_out += 1
            self.by_weapon[ev.weapon or "?"] += a
            self.by_target[ev.who or "?"] += a
            if ev.ship:
                self.tgt_ship[ev.who or "?"] = ev.ship
            if ev.quality:
                self.quality[ev.quality] += 1
            if self.biggest is None or a > self.biggest[0]:
                self.biggest = (a, ev.weapon, ev.who)
            self.fresh.append(ev)
        elif k == "dmg_in":
            self.hits_in += 1
            src = ev.who or "?"
            if ev.ship and ev.ship != ev.who:
                src = f"{ev.who} ({ev.ship})"
            self.by_source[src] += a
            if ev.weapon:
                self.src_weapons[src][ev.weapon] += a
            self.src_ship[src] = ev.ship
            self.src_who[src] = ev.who
            self.fresh.append(ev)
        if k in ("dmg_out", "dmg_in", "neut_in"):
            self.feed.append({"t": now, "kind": k, "amt": a, "who": ev.who, "ship": ev.ship,
                              "weapon": ev.weapon, "q": ev.quality})

    def update(self, now):
        cutoff = now - self.window
        ev = self.events
        while ev and ev[0][0] < cutoff:
            _, k, a = ev.popleft()
            self.sums[k] -= a
        w = float(self.window)
        for k in KINDS:
            r = self.sums[k] / w
            self.rate[k] = r
            if r > self.peak[k]:
                self.peak[k] = r
        closed = None
        if self.enc is not None:
            self.enc["peak"] = max(self.enc["peak"], self.rate["dmg_out"])
            self.enc["peak_in"] = max(self.enc["peak_in"], self.rate["dmg_in"])
            if now - self.last_event > ENC_TIMEOUT:
                e = self.enc
                e["end"] = self.last_event
                e["dur"] = max(1.0, e["end"] - e["start"])
                self.last_enc = e
                self.enc = None
                if e["dmg_out"] or e["dmg_in"]:
                    closed = e
        return closed

    def sample(self):
        r = self.rate
        self.history.append((r["dmg_out"], r["dmg_in"], r["logi_in"]))


# ════════════════════════════════════════════════════════════════════
#  Demo-Kampf (erzeugt echte englische Logzeilen)
# ════════════════════════════════════════════════════════════════════

class Demo:
    TARGETS = ["Dread Guristas Pith Usurper", "Pith Despoiler", "Guristas Destroyer",
               "Dire Pithi Arrogator", "Pith Eradicator"]
    DEFAULT_WEAPONS = [("Heavy Entropic Disintegrator II", 1.2, 380), ("Hammerhead II", 2.6, 95)]
    QUAL = [("Wrecks", 3), ("Smashes", 10), ("Penetrates", 30), ("Hits", 35),
            ("Glances Off", 14), ("Grazes", 8)]
    PLAYERS = [("Vex Morrow", "GRIM", "Loki", "Caldari Navy Scourge Light Missile"),
               ("Ash Kerrin", "GRIM", "Vagabond", "425mm AutoCannon II"),
               ("Ryn Tal", "SNUFF", "Deimos", "Heavy Neutron Blaster II")]
    MULT = {"Wrecks": 3.0, "Smashes": 1.4, "Penetrates": 1.15, "Hits": 1.0,
            "Glances Off": 0.7, "Grazes": 0.45}

    def __init__(self, weapons=None, logi=False, offset=0.0, incoming=1.0):
        self.t0 = time.time() - offset
        self.weapons = weapons if weapons is not None else self.DEFAULT_WEAPONS
        self.logi = logi
        self.incoming = incoming
        self.next = {}
        self.spool = 0.0
        self.target = random.choice(self.TARGETS)

    @staticmethod
    def ts():
        return datetime.datetime.now(datetime.timezone.utc).strftime("%Y.%m.%d %H:%M:%S")

    def lines(self, now):
        out = []
        t = now - self.t0
        phase = (t % 75)
        if phase >= 58:
            self.spool = 0.0
            return out
        self.spool = min(1.0, self.spool + 0.004)
        if random.random() < 0.004:
            self.target = random.choice(self.TARGETS)
        ts = self.ts()
        for name, cyc, base in self.weapons:
            if now >= self.next.get(name, 0):
                self.next[name] = now + cyc * random.uniform(0.9, 1.1)
                if random.random() < 0.08:
                    out.append(f"[ {ts} ] (combat) Your group of {name} misses {self.target} completely - {name}")
                    continue
                q = random.choices([q for q, _ in self.QUAL], [w for _, w in self.QUAL])[0]
                ramp = (1 + 1.4 * self.spool) if "Entropic" in name else 1.0
                amt = int(base * self.MULT[q] * ramp * random.uniform(0.85, 1.1))
                out.append(f"[ {ts} ] (combat) <color=0xff00ffff><b>{amt}</b> <color=0x77ffffff>"
                           f"<font size=10>to</font> <b><color=0xffffffff>{self.target}</b>"
                           f"<font size=10><color=0x77ffffff> - {name} - {q}")
        if self.incoming and now >= self.next.get("in", 0):
            self.next["in"] = now + random.uniform(0.6, 1.6)
            q = random.choice(["Hits", "Smashes", "Glances Off", "Penetrates"])
            amt = int(random.uniform(60, 260) * self.incoming * (1.6 if 20 < phase < 40 else 1))
            if random.random() < 0.45:
                n, corp, ship, wpn = random.choice(self.PLAYERS)
                out.append(f"[ {ts} ] (combat) <color=0xffcc0000><b>{amt}</b> <color=0x77ffffff>"
                           f"<font size=10>from</font> <b><color=0xffffffff>{n}[{corp}]({ship})</b>"
                           f"<font size=10><color=0x77ffffff> - {wpn} - {q}")
            else:
                src = random.choice(self.TARGETS)
                out.append(f"[ {ts} ] (combat) <color=0xffcc0000><b>{amt}</b> <color=0x77ffffff>"
                           f"<font size=10>from</font> <b><color=0xffffffff>{src}</b>"
                           f"<font size=10><color=0x77ffffff> - {q}")
        if self.logi:
            if now >= self.next.get("lo", 0):
                self.next["lo"] = now + random.uniform(1.8, 2.4)
                out.append(f"[ {ts} ] (combat) <color=0xffccff66><b>{random.randint(760, 900)}</b>"
                           f"<color=0x77ffffff><font size=10> remote armor repaired to </font><b>"
                           f"<color=0xffffffff>{random.choice(['Demo Pilot', 'Xine Alpha', 'Kael Varo'])}</b>"
                           f"<font size=10><color=0x77ffffff> - Large Remote Armor Repairer II</font>")
            return out
        if now >= self.next.get("rep", 0) and phase > 12:
            self.next["rep"] = now + 4.0
            out.append(f"[ {ts} ] (combat) <color=0xffccff66><b>{random.randint(780, 860)}</b>"
                       f"<color=0x77ffffff><font size=10> remote armor repaired by </font><b>"
                       f"<color=0xffffffff>Guardian</b><font size=10><color=0x77ffffff>"
                       f" - Large Remote Armor Repairer II</font>")
        if now >= self.next.get("repo", 0) and phase > 25:
            self.next["repo"] = now + 5.0
            out.append(f"[ {ts} ] (combat) <color=0xffccff66><b>{random.randint(400, 460)}</b>"
                       f"<color=0x77ffffff><font size=10> remote shield boosted to </font><b>"
                       f"<color=0xffffffff>Wingmate</b><font size=10><color=0x77ffffff>"
                       f" - Medium Remote Shield Booster II</font>")
        if now >= self.next.get("neut", 0) and 30 < phase < 50:
            self.next["neut"] = now + 6.0
            out.append(f"[ {ts} ] (combat) <color=0xffe57f7f><b>{random.randint(90, 160)} GJ</b>"
                       f"<color=0x77ffffff><font size=10> energy neutralized </font><b>"
                       f"<color=0xffffffff>Pith Usurper</b><color=0x77ffffff><font size=10>"
                       f" - Corpse Neutralizer</font>")
        if now >= self.next.get("nos", 0) and phase > 8:
            self.next["nos"] = now + 5.0
            out.append(f"[ {ts} ] (combat) <color=0xff7fffff><b>+{random.randint(20, 40)} GJ</b>"
                       f"<color=0x77ffffff><font size=10> energy drained from </font><b>"
                       f"<color=0xffffffff>{self.target}</b><color=0x77ffffff><font size=10>"
                       f" - Medium Energy Nosferatu II</font>")
        return out


DEMO_BOTS = {
    "Xine Alpha": dict(weapons=[("Mega Pulse Laser II", 1.1, 470), ("Hammerhead II", 2.6, 95)], offset=6),
    "Kael Varo": dict(weapons=[("Heavy Missile Launcher II", 1.9, 610)], offset=13, incoming=0.7),
    "Nyx Ostara": dict(weapons=[("Small Focused Pulse Laser II", 0.9, 150)], offset=21, incoming=0.4),
    "Logi Lisa": dict(weapons=[], logi=True, offset=3, incoming=0.5),
}


# ════════════════════════════════════════════════════════════════════
#  Waffen-Datenbank (Reichweiten: alle Skills V, ohne Schiffsboni/Module)
#  Quellen: EVE University Wiki (Turret-Tabellen, Munitions-Modifikatoren),
#           EVE Ref (Raketen-Geschwindigkeit/Flugzeit)
# ════════════════════════════════════════════════════════════════════

SKILL_OPT = 1.25    # Sharpshooter V
SKILL_FALL = 1.25   # Trajectory Analysis V
SKILL_MISSILE = 1.5 * 1.5  # Missile Projection V × Missile Bombardment V

# key → (Familie, Größe, Optimal m, Falloff m)  – Basiswerte Tech 1 laut EVE Uni
TURRETS = {
    # Projektil
    "125mm gatling autocannon": ("ac", "S", 800, 4300),
    "150mm light autocannon": ("ac", "S", 900, 4730),
    "200mm autocannon": ("ac", "S", 1000, 5160),
    "250mm light artillery": ("arty", "S", 8050, 8750),
    "280mm howitzer": ("arty", "S", 10000, 8750),
    "dual 180mm autocannon": ("ac", "M", 1600, 9030),
    "220mm vulcan": ("ac", "M", 1800, 9933),
    "425mm autocannon": ("ac", "M", 2000, 10836),
    "650mm artillery": ("arty", "M", 16100, 17500),
    "720mm howitzer": ("arty", "M", 20000, 17500),
    "dual 425mm autocannon": ("ac", "L", 3200, 17200),
    "dual 650mm repeating": ("ac", "L", 3600, 18920),
    "800mm repeating": ("ac", "L", 4000, 20640),
    "1200mm artillery": ("arty", "L", 32200, 35000),
    "1400mm howitzer": ("arty", "L", 40000, 35000),
    # Hybrid
    "light electron blaster": ("blaster", "S", 1000, 1500),
    "light ion blaster": ("blaster", "S", 1250, 2000),
    "light neutron blaster": ("blaster", "S", 1500, 2500),
    "75mm gatling rail": ("rail", "S", 6000, 3000),
    "125mm railgun": ("rail", "S", 9000, 5000),
    "150mm railgun": ("rail", "S", 12000, 6000),
    "heavy electron blaster": ("blaster", "M", 2000, 3000),
    "heavy ion blaster": ("blaster", "M", 2500, 4000),
    "heavy neutron blaster": ("blaster", "M", 3000, 5000),
    "dual 150mm railgun": ("rail", "M", 12000, 6000),
    "200mm railgun": ("rail", "M", 18000, 10000),
    "250mm railgun": ("rail", "M", 24000, 12000),
    "electron blaster cannon": ("blaster", "L", 4000, 6000),
    "ion blaster cannon": ("blaster", "L", 5000, 8000),
    "neutron blaster cannon": ("blaster", "L", 6000, 10000),
    "dual 250mm railgun": ("rail", "L", 24000, 12000),
    "350mm railgun": ("rail", "L", 36000, 20000),
    "425mm railgun": ("rail", "L", 48000, 24000),
    # Laser
    "gatling pulse laser": ("pulse", "S", 4200, 1000),
    "dual light pulse laser": ("pulse", "S", 4725, 2000),
    "small focused pulse laser": ("pulse", "S", 5250, 2500),
    "dual light beam laser": ("beam", "S", 9625, 2000),
    "small focused beam laser": ("beam", "S", 11000, 2500),
    "focused medium pulse laser": ("pulse", "M", 9450, 4000),
    "heavy pulse laser": ("pulse", "M", 10500, 5000),
    "quad light beam laser": ("beam", "M", 8800, 1000),
    "focused medium beam laser": ("beam", "M", 19250, 6000),
    "heavy beam laser": ("beam", "M", 22000, 8000),
    "dual heavy pulse laser": ("pulse", "L", 18900, 8000),
    "mega pulse laser": ("pulse", "L", 21000, 10000),
    "dual heavy beam laser": ("beam", "L", 35000, 12000),
    "mega beam laser": ("beam", "L", 40000, 16000),
    "tachyon beam laser": ("beam", "L", 44000, 20000),
}
_TURRET_KEYS = sorted(TURRETS, key=len, reverse=True)

SIZE_DE = {"S": "Klein (Fregatten/Zerstörer)", "M": "Mittel (Kreuzer/Schlachtkreuzer)",
           "L": "Groß (Schlachtschiffe/Attack-BCs)", "XL": "Capital"}

TURRET_FAMILY = {
    "ac": dict(cls="Autokanone (Projektil)", dmg="Explosiv/Kinetisch (je nach Muni, alle 4 möglich)",
               long=("Barrage", 1.0, 1.5), short=("EMP/Phased Plasma/Fusion", 0.5, 1.0),
               pros=["Hoher Schaden, gute Nachführung (Tracking)", "Kein Capacitor nötig",
                     "Langer Falloff im Verhältnis zur Reichweite"],
               cons=["Sehr kurze optimale Reichweite", "Schaden fällt im Falloff stark ab"],
               tip="Außerhalb von Optimal + 2× Falloff bleiben (kiten). Tracking-Disruptor mit "
                   "Optimal-/Falloff-Skript wirkt sehr gut."),
    "arty": dict(cls="Artillerie (Projektil)", dmg="Explosiv/Kinetisch (je nach Muni)",
                 long=("Tremor", 1.8, 1.0), short=("Quake", 0.25, 1.0),
                 pros=["Lange Reichweite", "Größter Alpha-Schaden aller Langstreckenwaffen",
                       "Kein Capacitor nötig"],
                 cons=["Sehr schlechtes Tracking", "Niedrige DPS, lange Zykluszeit"],
                 tip="Nah rangehen und schnell um ihn herum fliegen (Transversal) – auf kurze "
                     "Distanz trifft Artillerie kaum."),
    "blaster": dict(cls="Blaster (Hybrid)", dmg="Kinetisch/Thermisch",
                    long=("Null", 1.4, 1.4), short=("Void / Antimatter", 0.75, 0.5),
                    pros=["Höchste DPS aller Turrets", "Gute Nachführung auf kurze Distanz"],
                    cons=["Extrem kurze Reichweite", "Braucht Capacitor"],
                    tip="Abstand halten! Schon 10–15 km ohne Schiffsbonus machen Blaster nutzlos. "
                        "Webs/Scrams meiden, Neuts helfen."),
    "rail": dict(cls="Railgun (Hybrid)", dmg="Kinetisch/Thermisch",
                 long=("Spike", 1.8, 1.0), short=("Javelin", 0.25, 1.0),
                 pros=["Sehr lange Reichweite", "Präzise auf Distanz"],
                 cons=["Schwaches Tracking auf kurze Distanz", "Niedrigere DPS als Blaster",
                       "Braucht Capacitor"],
                 tip="Unter die Waffen fliegen: nah ran und hohe Querbewegung. Sensor-Dampener "
                     "verkürzen seine Lock-Reichweite."),
    "pulse": dict(cls="Pulse-Laser (Energie)", dmg="EM/Thermisch (gut gegen Panzerung, schwach gegen Schilde)",
                  long=("Scorch", 1.4, 1.0), short=("Conflagration / Multifrequency", 0.5, 1.0),
                  pros=["Hohe DPS mit der größten Reichweite der Kurzstrecken-Turrets",
                        "Kristall-Wechsel sofort, passt Reichweite schnell an"],
                  cons=["Hoher Capacitor-Verbrauch", "Kleiner Falloff", "Nur EM/Thermisch"],
                  tip="Neutralisieren (Cap leeren) stoppt Laser komplett. Thermal/EM-Resistenzen fitten."),
    "beam": dict(cls="Beam-Laser (Energie)", dmg="EM/Thermisch",
                 long=("Aurora", 1.8, 1.0), short=("Gleam", 0.25, 1.0),
                 pros=["Lange Reichweite", "Kein Nachladen bei T1-Kristallen"],
                 cons=["Sehr kleiner Falloff – außerhalb der Optimal kaum Schaden",
                       "Sehr hoher Capacitor-Verbrauch", "Schwaches Tracking"],
                 tip="Knapp außerhalb der Optimal bleiben oder nah ran – Neuts sind extrem effektiv."),
}

MISSILES = {
    # key → (Name, Größe, Basisreichweite m, Expl.-Radius, Expl.-Geschw., Kurzstrecke?)
    "rocket": ("Rakete (Rocket)", "S", 4500, 20, 150, True),
    "light": ("Light Missile", "S", 18750, 40, 170, False),
    "ham": ("Heavy Assault Missile", "M", 9000, 125, 101, True),
    "heavy": ("Heavy Missile", "M", 27950, 140, 81, False),
    "cruise": ("Cruise Missile", "L", 75000, 300, 69, False),
    "torpedo": ("Torpedo", "L", 9000, 450, 71, True),
    "xltorp": ("XL-Torpedo", "XL", None, None, None, True),
    "xlcruise": ("XL-Cruise Missile", "XL", None, None, None, False),
}
MISSILE_DMG = {"scourge": "Kinetisch", "mjolnir": "EM", "inferno": "Thermisch", "nova": "Explosiv"}


def _km(m):
    return f"{m / 1000:.1f}".replace(".", ",") + " km"


def weapon_info(name):
    """Liefert ein Info-Dict zu einem Waffen-/Munitionsnamen aus dem Log oder None."""
    if not name:
        return None
    low = name.lower()
    note = "Alle Skills auf V, ohne Schiffsboni und Module. Schiffsboni und Tracking-Computer/" \
           "Missile-Guidance können die Reichweite um 25–100 % erhöhen."

    # ── Raketen ──
    mk = None
    if "rocket" in low:
        mk = "rocket"
    elif "heavy assault" in low:
        mk = "ham"
    elif "light missile" in low or "rapid light" in low:
        mk = "light"
    elif "heavy missile" in low or "rapid heavy" in low:
        mk = "heavy"
    elif "cruise" in low:
        mk = "xlcruise" if ("xl" in low or "citadel" in low) else "cruise"
    elif "torpedo" in low:
        mk = "xltorp" if ("xl" in low or "citadel" in low) else "torpedo"
    if mk:
        title, size, base, er, ev, short = MISSILES[mk]
        mult, variant = 1.0, ""
        if "precision" in low:
            mult, variant = 0.5, "Precision: bessere Anwendung auf kleine/schnelle Ziele, −50 % Flugzeit"
        elif "fury" in low:
            mult, variant = 0.75, "Fury: mehr Schaden, schlechtere Anwendung, −25 % Flugzeit"
        elif "javelin" in low:
            variant = "Javelin: schneller, dadurch mehr Reichweite, etwas weniger Schaden"
        elif "rage" in low:
            variant = "Rage: mehr Schaden, deutlich schlechtere Anwendung, etwas weniger Reichweite"
        dmg = next((v for k, v in MISSILE_DMG.items() if k in low), "wählbar (EM/Therm/Kin/Exp je nach Rakete)")
        lines = []
        if base:
            rng = base * SKILL_MISSILE * mult
            short_txt = "≈ " + _km(rng)
            lines.append(f"Max. Reichweite ≈ {_km(rng)}  (Basis {_km(base * mult)})")
            lines.append(f"Explosionsradius {er} m · Explosionsgeschw. {ev} m/s")
        else:
            short_txt = "Capital"
            rng = None
            lines.append("Capital-Rakete – Reichweite stark schiffsabhängig")
        if variant:
            lines.append(variant)
        pros = ["Trifft immer (kein Tracking/Verfehlen)", "Schadensart frei wählbar",
                "Volle Wirkung auf jede Distanz bis Max-Reichweite"]
        cons = ["Verzögerter Schaden (Flugzeit)", "Weniger Schaden gegen kleine, schnelle Ziele"]
        if short:
            pros.append("Kurzstrecken-Typ: höhere DPS")
            cons.append("Kurze Reichweite")
        if "rapid" in low:
            cons.append("Rapid-Launcher: langer Reload (≈ 35–40 s) nach leerem Magazin")
            pros.append("Rapid-Launcher: sehr hoher Burst-Schaden")
        tip = ("Klein und schnell bleiben (MWD aus, Signatur klein), Defender Missiles oder "
               "Smartbombs gegen Salven. " + ("Bei Rapid-Launchern: Reload-Fenster abwarten und dann angreifen."
                                             if "rapid" in low else "Raus aus der Max-Reichweite."))
        cls = title + (" · Rapid-Launcher" if "rapid" in low else "") + (" · Capital" if size == "XL" else "")
        return dict(name=name, cls=cls, dmg=dmg, short=short_txt,
                    lines=lines, pros=pros, cons=cons, tip=tip, note=note, range_m=rng)

    if "defender" in low:
        return dict(name=name, cls="Defender Missile", dmg="—", short="Abwehr",
                    lines=["Schießt anfliegende Raketen ab"], pros=["Kontert Raketen-Salven"],
                    cons=["Macht keinen Schaden an Schiffen"], tip="Mehr Raketen pro Salve überladen ihn.",
                    note="", range_m=None)
    if "auto-targeting" in low or " fof" in low:
        return dict(name=name, cls="Auto-Targeting (FoF) Missile", dmg="wählbar", short="FoF",
                    lines=["Sucht sich selbst ein Ziel – auch bei ECM-Jam"],
                    pros=["Funktioniert ohne Lock"], cons=["Wenig Schaden, zufällige Zielwahl"],
                    tip="Harmlos – Hauptschaden kommt woanders her.", note="", range_m=None)

    # ── Turrets ──
    key = next((k for k in _TURRET_KEYS if k in low), None)
    if key:
        fam, size, opt, fall = TURRETS[key]
        f = TURRET_FAMILY[fam]
        o, fo = opt * SKILL_OPT, fall * SKILL_FALL
        lo, lf = o * f["long"][1], fo * f["long"][2]
        so, sf = o * f["short"][1], fo * f["short"][2]
        lines = [
            f"Standard-Muni: Optimal {_km(o)} + Falloff {_km(fo)}",
            f"Langstrecke ({f['long'][0]}): {_km(lo)} + {_km(lf)}",
            f"Kurzstrecke ({f['short'][0]}): {_km(so)} + {_km(sf)}",
            "Im Optimal: volle Trefferchance · bei Optimal+Falloff: 50 % · bei +2× Falloff: ~6 %",
        ]
        return dict(name=name, cls=f"{f['cls']} · {SIZE_DE[size]}", dmg=f["dmg"],
                    short=f"≈ {_km(o + fo)}", lines=lines, pros=f["pros"], cons=f["cons"],
                    tip=f["tip"], note=note, range_m=o + fo)

    generic = [
        (("autocannon",), "ac"), (("artillery", "howitzer"), "arty"), (("blaster",), "blaster"),
        (("railgun", "rail"), "rail"), (("pulse",), "pulse"), (("beam",), "beam"),
    ]
    for words, fam in generic:
        if any(w in low for w in words):
            f = TURRET_FAMILY[fam]
            return dict(name=name, cls=f["cls"], dmg=f["dmg"], short="?", lines=["Modell unbekannt – "
                        "Reichweite hängt von der Größe ab"], pros=f["pros"], cons=f["cons"],
                        tip=f["tip"], note=note, range_m=None)

    # ── Precursor / Vorton (grobe Werte) ──
    if "entropic" in low or "disintegrator" in low:
        size = "S" if "light" in low else ("M" if "heavy" in low else "L")
        approx = {"S": "≈ 6–8 km", "M": "≈ 12–16 km", "L": "≈ 25–30 km"}[size]
        return dict(name=name, cls=f"Entropic Disintegrator (Triglavian) · {SIZE_DE[size]}",
                    dmg="Explosiv/Thermisch", short=approx,
                    lines=[f"Reichweite grob {approx} (Optimal + Falloff, je nach Muni)",
                           "Schaden steigt bis +150 % an, solange er durchgehend auf dasselbe Ziel feuert"],
                    pros=["Extrem hohe Schadens-Spitzen nach dem Hochfahren", "Gutes Tracking"],
                    cons=["Spin-up dauert – Zielwechsel setzt den Bonus zurück", "Braucht Capacitor"],
                    tip="Früh brechen oder Abstand gewinnen, bevor er hochgefahren ist. Zielwechsel "
                        "erzwingen (Drohnen/Flottenkollegen) setzt seinen Bonus zurück.",
                    note="Grobe Richtwerte – stark abhängig von Muni und Schiff.", range_m=None)
    if "vorton" in low:
        size = "S" if "small" in low else ("M" if "medium" in low else "L")
        approx = {"S": "≈ 5–8 km", "M": "≈ 10–15 km", "L": "≈ 20–30 km"}[size]
        return dict(name=name, cls=f"Vorton Projector (EDENCOM) · {SIZE_DE[size]}",
                    dmg="EM/Explosiv (je nach Muni)", short=approx,
                    lines=[f"Reichweite grob {approx}", "Blitz springt auf mehrere nahe Ziele über"],
                    pros=["Trifft Gruppen gleichzeitig (Kettenblitz)", "Stark gegen Drohnen und Schwärme"],
                    cons=["Kurze bis mittlere Reichweite", "Schlecht gegen einzelne kleine Ziele"],
                    tip="Auseinanderziehen – nicht eng beieinander fliegen. Drohnen zurückholen.",
                    note="Grobe Richtwerte.", range_m=None)
    if "smartbomb" in low:
        return dict(name=name, cls="Smartbomb", dmg="je nach Typ eine Schadensart", short="≈ 5–7,5 km Radius",
                    lines=["Flächenschaden rund um sein Schiff (ca. 5–7,5 km je nach Größe)"],
                    pros=["Trifft alles im Radius, killt Drohnen/Raketen"],
                    cons=["Trifft auch Verbündete", "Keine Wirkung außerhalb des Radius"],
                    tip="Mehr als 7,5 km Abstand halten, Drohnen fernhalten.", note="", range_m=7500)

    # ── Drohnen ──
    drones = {
        "light": (("warrior", "hobgoblin", "acolyte", "hornet", "dragonfly"), "Leichte Drohne"),
        "medium": (("valkyrie", "hammerhead", "infiltrator", "vespa"), "Mittlere Drohne"),
        "heavy": (("berserker", "ogre", "praetor", "wasp"), "Schwere Drohne"),
    }
    for kind, (names, label) in drones.items():
        if any(n in low for n in names):
            return dict(name=name, cls=label, dmg="je nach Rasse (Kin/Therm/EM/Exp)",
                        short="Drohnen-Kontrolle ≈ 60–90 km",
                        lines=["Drohnen fliegen zum Ziel – Reichweite = Kontrollreichweite des Piloten",
                               "Typisch 60–90 km mit guten Skills"],
                        pros=["Schaden unabhängig von Waffenreichweite des Schiffs", "Lautlos dauerhaft aktiv"],
                        cons=["Können abgeschossen werden", "Langsam bei großen Distanzen" if kind == "heavy"
                              else "Wenig Lebenspunkte"],
                        tip="Drohnen gezielt abschießen (Leichte Drohnen/Smartbomb) oder den Piloten außer "
                            "Kontrollreichweite bringen.", note="", range_m=None)
    sentries = {"garde": "≈ 15 km", "curator": "≈ 30 km", "bouncer": "≈ 45 km", "warden": "≈ 60 km"}
    for n, approx in sentries.items():
        if n in low:
            return dict(name=name, cls="Sentry-Drohne (stationär)", dmg="je nach Rasse", short=approx,
                        lines=[f"Grobe effektive Reichweite {approx} (Skills V, ohne Boni)",
                               "Bleibt an einer Stelle stehen – feuert wie ein Turret"],
                        pros=["Hoher Schaden auf Distanz"], cons=["Unbeweglich", "Schwaches Tracking bei Nähe"],
                        tip="Nah ran an den Sentry-Träger oder die Sentries abschießen.",
                        note="Grobe Richtwerte.", range_m=None)
    return None


# ════════════════════════════════════════════════════════════════════
#  Schiffs- & NPC-Datenbank → Konter-Schadensart
#  Resistenzen: EVE University "Natural resistances" (T1-Profile; T2 = Restlücke
#  der Primär-Schadensart −75 %, der Sekundär-Schadensart −50 %), NPC-Tabellen:
#  EVE University "NPC damage types".
# ════════════════════════════════════════════════════════════════════

DMG = ("EM", "Thermisch", "Kinetisch", "Explosiv")
DMG_SHORT = ("EM", "TH", "KIN", "EXP")
DMG_COL = ("#7fd8ff", "#ff7a3d", "#c9c9d6", "#ffc23d")

T1_SHIELD = (0, 20, 40, 50)
EDENCOM_SHIELD = (20, 20, 50, 50)
T1_ARMOR = {"A": (50, 35, 25, 20), "C": (50, 45, 25, 10), "G": (50, 35, 35, 10),
            "M": (60, 35, 25, 10), "ORE": (60, 35, 25, 10)}
T2_BONUS = {"A": (3, 2), "C": (1, 2), "G": (2, 1), "M": (0, 1)}  # (primär, sekundär) Index in DMG

RACE = {
    "A": ("Amarr", "#ffc24a"), "C": ("Caldari", "#7ab0ff"), "G": ("Gallente", "#4dffbf"),
    "M": ("Minmatar", "#ff7a3d"), "Angel": ("Angel Cartel", "#ff9f43"), "Guristas": ("Guristas", "#facc15"),
    "Serpentis": ("Serpentis", "#a3e635"), "Blood": ("Blood Raiders", "#f43f5e"),
    "Sansha": ("Sansha's Nation", "#4ade80"), "Mordu": ("Mordu's Legion", "#93c5fd"),
    "SoE": ("Sisters of EVE", "#f5f5f5"), "Trig": ("Triglavian", "#ef4444"), "Eden": ("EDENCOM", "#60a5fa"),
    "Concord": ("CONCORD", "#e5e7eb"), "ORE": ("ORE", "#fbbf24"),
}
# Fraktion → (Rasse für Panzer-Profil, Schild-Profil)
FACTION_PROFILE = {"Angel": "M", "Guristas": "C", "Serpentis": "G", "Blood": "A", "Sansha": "A",
                   "Mordu": "C", "SoE": "G", "Trig": "A", "Eden": "C", "Concord": "G"}

CLASS_DE = {
    "F": "Fregatte", "D": "Zerstörer", "C": "Kreuzer", "BC": "Schlachtkreuzer", "BS": "Schlachtschiff",
    "CAP": "Capital", "IND": "Industrie/Mining",
    "AF": "Assault Frigate", "INT": "Interceptor", "COV": "Covert Ops", "SB": "Stealth Bomber",
    "EAF": "Electronic Attack Frigate", "LF": "Logistik-Fregatte", "DIC": "Interdictor",
    "CD": "Command Destroyer", "T3D": "Tactical Destroyer (T3)", "HAC": "Heavy Assault Cruiser",
    "HIC": "Heavy Interdictor", "REC": "Recon", "LOG": "Logistik-Kreuzer", "T3C": "Strategic Cruiser (T3)",
    "CS": "Command Ship", "MAR": "Marauder", "BLOPS": "Black Ops",
}
CLASS_SIZE = {"F": 1, "AF": 1, "INT": 1, "COV": 1, "SB": 1, "EAF": 1, "LF": 1, "D": 1.4, "DIC": 1.4,
              "CD": 1.4, "T3D": 1.4, "C": 2, "HAC": 2, "HIC": 2, "REC": 2, "LOG": 2, "T3C": 2,
              "BC": 2.6, "CS": 2.6, "BS": 3.2, "MAR": 3.2, "BLOPS": 3.2, "CAP": 3.8, "IND": 2}

# Name|Rasse|Klasse|Tech(1/2/3/N=Navy/P=Pirat)|üblicher Tank (S/A/B=beides)
_SHIP_DATA = """
Executioner|A|F|1|A;Inquisitor|A|F|1|A;Punisher|A|F|1|A;Tormentor|A|F|1|A;Crucifier|A|F|1|A;Magnate|A|F|1|A
Coercer|A|D|1|A;Dragoon|A|D|1|A;Arbitrator|A|C|1|A;Augoror|A|C|1|A;Maller|A|C|1|A;Omen|A|C|1|A
Harbinger|A|BC|1|A;Prophecy|A|BC|1|A;Oracle|A|BC|1|A;Abaddon|A|BS|1|A;Apocalypse|A|BS|1|A;Armageddon|A|BS|1|A
Imperial Navy Slicer|A|F|N|A;Malediction|A|INT|2|A;Crusader|A|INT|2|A;Retribution|A|AF|2|A;Vengeance|A|AF|2|A
Anathema|A|COV|2|A;Purifier|A|SB|2|A;Sentinel|A|EAF|2|A;Deacon|A|LF|2|A;Heretic|A|DIC|2|A;Pontifex|A|CD|2|A
Confessor|A|T3D|3|A;Zealot|A|HAC|2|A;Sacrilege|A|HAC|2|A;Devoter|A|HIC|2|A;Curse|A|REC|2|A;Pilgrim|A|REC|2|A
Guardian|A|LOG|2|A;Legion|A|T3C|3|A;Absolution|A|CS|2|A;Damnation|A|CS|2|A;Paladin|A|MAR|2|A;Redeemer|A|BLOPS|2|A
Archon|A|CAP|1|A;Apostle|A|CAP|1|A;Revelation|A|CAP|1|A;Aeon|A|CAP|1|A;Avatar|A|CAP|1|A;Bane|A|CAP|1|A
Merlin|C|F|1|B;Kestrel|C|F|1|S;Condor|C|F|1|S;Griffin|C|F|1|S;Heron|C|F|1|S;Bantam|C|F|1|S
Cormorant|C|D|1|S;Corax|C|D|1|S;Blackbird|C|C|1|S;Caracal|C|C|1|S;Moa|C|C|1|S;Osprey|C|C|1|S
Drake|C|BC|1|S;Ferox|C|BC|1|S;Naga|C|BC|1|S;Raven|C|BS|1|S;Rokh|C|BS|1|S;Scorpion|C|BS|1|S
Caldari Navy Hookbill|C|F|N|S;Crow|C|INT|2|S;Raptor|C|INT|2|S;Harpy|C|AF|2|S;Hawk|C|AF|2|S;Buzzard|C|COV|2|S
Manticore|C|SB|2|S;Kitsune|C|EAF|2|S;Kirin|C|LF|2|S;Flycatcher|C|DIC|2|S;Stork|C|CD|2|S;Jackdaw|C|T3D|3|S
Cerberus|C|HAC|2|S;Eagle|C|HAC|2|S;Onyx|C|HIC|2|S;Falcon|C|REC|2|S;Rook|C|REC|2|S;Basilisk|C|LOG|2|S
Tengu|C|T3C|3|S;Nighthawk|C|CS|2|S;Vulture|C|CS|2|S;Golem|C|MAR|2|S;Widow|C|BLOPS|2|S
Chimera|C|CAP|1|S;Minokawa|C|CAP|1|S;Phoenix|C|CAP|1|S;Wyvern|C|CAP|1|S;Leviathan|C|CAP|1|S
Atron|G|F|1|A;Incursus|G|F|1|A;Tristan|G|F|1|B;Maulus|G|F|1|A;Imicus|G|F|1|A;Navitas|G|F|1|A
Catalyst|G|D|1|B;Algos|G|D|1|B;Celestis|G|C|1|A;Exequror|G|C|1|A;Thorax|G|C|1|A;Vexor|G|C|1|A
Brutix|G|BC|1|A;Myrmidon|G|BC|1|A;Talos|G|BC|1|A;Dominix|G|BS|1|A;Hyperion|G|BS|1|A;Megathron|G|BS|1|A
Federation Navy Comet|G|F|N|A;Ares|G|INT|2|A;Taranis|G|INT|2|A;Enyo|G|AF|2|A;Ishkur|G|AF|2|A;Helios|G|COV|2|A
Nemesis|G|SB|2|A;Keres|G|EAF|2|A;Thalia|G|LF|2|A;Eris|G|DIC|2|A;Magus|G|CD|2|A;Hecate|G|T3D|3|A
Deimos|G|HAC|2|A;Ishtar|G|HAC|2|S;Phobos|G|HIC|2|A;Arazu|G|REC|2|A;Lachesis|G|REC|2|A;Oneiros|G|LOG|2|A
Proteus|G|T3C|3|A;Astarte|G|CS|2|A;Eos|G|CS|2|A;Kronos|G|MAR|2|A;Sin|G|BLOPS|2|A
Thanatos|G|CAP|1|A;Ninazu|G|CAP|1|A;Moros|G|CAP|1|A;Nyx|G|CAP|1|A;Erebus|G|CAP|1|A
Breacher|M|F|1|S;Burst|M|F|1|S;Probe|M|F|1|S;Rifter|M|F|1|B;Slasher|M|F|1|S;Vigil|M|F|1|S
Thrasher|M|D|1|S;Talwar|M|D|1|S;Bellicose|M|C|1|B;Rupture|M|C|1|B;Scythe|M|C|1|S;Stabber|M|C|1|S
Cyclone|M|BC|1|S;Hurricane|M|BC|1|B;Tornado|M|BC|1|S;Maelstrom|M|BS|1|S;Tempest|M|BS|1|B;Typhoon|M|BS|1|S
Republic Fleet Firetail|M|F|N|B;Vigil Fleet Issue|M|F|N|S;Claw|M|INT|2|S;Stiletto|M|INT|2|S;Jaguar|M|AF|2|S
Wolf|M|AF|2|A;Cheetah|M|COV|2|S;Hound|M|SB|2|S;Hyena|M|EAF|2|S;Scalpel|M|LF|2|S;Sabre|M|DIC|2|S
Bifrost|M|CD|2|S;Svipul|M|T3D|3|B;Muninn|M|HAC|2|A;Vagabond|M|HAC|2|S;Broadsword|M|HIC|2|S
Huginn|M|REC|2|B;Rapier|M|REC|2|A;Scimitar|M|LOG|2|S;Loki|M|T3C|3|B;Claymore|M|CS|2|S;Sleipnir|M|CS|2|S
Vargur|M|MAR|2|B;Panther|M|BLOPS|2|A;Nidhoggur|M|CAP|1|B;Lif|M|CAP|1|B;Naglfar|M|CAP|1|B;Hel|M|CAP|1|B
Dramiel|Angel|F|P|S;Cynabal|Angel|C|P|S;Machariel|Angel|BS|P|B;Worm|Guristas|F|P|S;Gila|Guristas|C|P|S
Rattlesnake|Guristas|BS|P|S;Daredevil|Serpentis|F|P|A;Vigilant|Serpentis|C|P|B;Vindicator|Serpentis|BS|P|A
Cruor|Blood|F|P|A;Ashimmu|Blood|C|P|A;Bhaalgorn|Blood|BS|P|A;Succubus|Sansha|F|P|A;Phantasm|Sansha|C|P|A
Nightmare|Sansha|BS|P|B;Garmur|Mordu|F|P|S;Orthrus|Mordu|C|P|S;Barghest|Mordu|BS|P|S;Astero|SoE|F|P|A
Stratios|SoE|C|P|A;Nestor|SoE|BS|P|A;Damavik|Trig|F|P|A;Kikimora|Trig|D|P|A;Vedmak|Trig|C|P|A
Rodiva|Trig|C|P|A;Ikitursa|Trig|C|P|A;Drekavac|Trig|BC|P|A;Leshak|Trig|BS|P|A;Nergal|Trig|F|P|A
Draugur|Trig|D|P|A;Zarmazd|Trig|LOG|P|A;Skybreaker|Eden|F|P|S;Stormbringer|Eden|C|P|S;Thunderchild|Eden|BS|P|S
Pacifier|Concord|F|P|A;Enforcer|Concord|C|P|A;Marshal|Concord|BS|P|A;Gnosis|Concord|BC|P|B
Venture|ORE|IND|1|S;Procurer|ORE|IND|1|S;Retriever|ORE|IND|1|S;Covetor|ORE|IND|1|S;Skiff|ORE|IND|2|S
Mackinaw|ORE|IND|2|S;Hulk|ORE|IND|2|S;Prospect|ORE|IND|2|S;Endurance|ORE|IND|2|S;Orca|ORE|CAP|1|S
"""


def _parse_ships():
    out = {}
    for chunk in _SHIP_DATA.replace("\n", ";").split(";"):
        chunk = chunk.strip()
        if not chunk:
            continue
        name, race, cls, tech, tank = chunk.split("|")
        out[name.lower()] = dict(name=name, race=race, cls=cls, tech=tech, tank=tank)
    return out


SHIPS = _parse_ships()
_SHIP_KEYS = sorted(SHIPS, key=len, reverse=True)

# NPC-Namen → Fraktion: (deals, weak_to) laut EVE Uni
NPC_FACTIONS = {
    "Angel": (("Explosiv", "Kinetisch"), (3, 2)),
    "Blood": (("EM", "Thermisch"), (0, 1)),
    "Guristas": (("Kinetisch", "Thermisch"), (2, 1)),
    "Sansha": (("EM", "Thermisch"), (0, 1)),
    "Serpentis": (("Thermisch", "Kinetisch"), (2, 1)),
    "Drones": (("je nach Drohne",), (0, 1)),
    "Mordu": (("Kinetisch", "Thermisch"), (2, 0)),
    "Merc": (("Kinetisch", "Thermisch"), (2, 1)),
    "Trig": (("Thermisch", "Explosiv"), (3, 1)),
    "Sleeper": (("alle gleich (Omni)",), None),
    "Concord": (("alle gleich (Omni)",), (2, 1)),
}
NPC_PREFIX = [
    (("pith", "guristas", "dread guristas"), "Guristas"),
    (("gist", "angel", "domination"), "Angel"),
    (("corpus", "corpii", "corpum", "corpior", "blood", "dark blood"), "Blood"),
    (("centus", "centii", "centum", "centior", "centatis", "sansha"), "Sansha"),
    (("core ", "coreli", "corelum", "corelior", "serpentis"), "Serpentis"),
    (("alvi", "alvus", "drone", "infester", "render", "sentient", "swarm", "spider drone"), "Drones"),
    (("mordu",), "Mordu"),
    (("mercenary",), "Merc"),
    (("starving", "renewing", "blinding", "harrowing", "ghosting", "anchoring", "tangling", "raznaborg",
      "triglavian", "liminal", "zorya"), "Trig"),
    (("sleepless", "awakened", "emergent", "sleeper"), "Sleeper"),
    (("concord", "ded "), "Concord"),
]
NPC_RACE_KEY = {"Angel": "Angel", "Blood": "Blood", "Guristas": "Guristas", "Sansha": "Sansha",
                "Serpentis": "Serpentis", "Mordu": "Mordu", "Trig": "Trig", "Concord": "Concord"}

# Munition je eigener Waffenfamilie und Schadensart
AMMO_BY_FAMILY = {
    "missile": ("Mjolnir", "Inferno", "Scourge", "Nova"),
    "ac": ("EMP", "Phased Plasma", "Titanium Sabot / Depleted Uranium", "Fusion"),
    "arty": ("EMP", "Phased Plasma", "Titanium Sabot / Depleted Uranium", "Fusion"),
    "drone": ("Acolyte / Infiltrator / Praetor", "Hobgoblin / Hammerhead / Ogre",
              "Hornet / Vespa / Wasp", "Warrior / Valkyrie / Berserker"),
}


def _resists(race, tech):
    """→ (Schild-Profil, Panzer-Profil) als Tupel EM/TH/KIN/EXP in %"""
    base_race = FACTION_PROFILE.get(race, race)
    shield = EDENCOM_SHIELD if race == "Eden" else T1_SHIELD
    armor = T1_ARMOR.get(base_race, T1_ARMOR["G"])
    if tech in ("2", "3") and race in T2_BONUS:
        p, s2 = T2_BONUS[race]

        def boost(prof):
            prof = list(prof)
            prof[p] = 100 - (100 - prof[p]) * 0.25
            prof[s2] = 100 - (100 - prof[s2]) * 0.5
            return tuple(round(v, 1) for v in prof)
        shield, armor = boost(shield), boost(armor)
    return shield, armor


def _best(profile):
    order = sorted(range(4), key=lambda i: profile[i])
    return order[0], order[1]


def entity_info(ship="", who=""):
    """Infos zu einem Schiff (Spieler) oder NPC → dict oder None"""
    if ship:
        low = ship.lower()
        key = next((k for k in _SHIP_KEYS if k in low), None)
        if key:
            d = SHIPS[key]
            tech = d["tech"]
            if any(w in low for w in ("navy issue", "fleet issue", "navy ", "fleet ")) and tech == "1":
                tech = "N"
            shield, armor = _resists(d["race"], tech)
            rname, rcol = RACE.get(d["race"], (d["race"], "#cccccc"))
            techtxt = {"1": "Tech 1", "2": "Tech 2", "3": "Tech 3", "N": "Navy-Fraktion",
                       "P": "Piraten-/Sonderfraktion"}[tech]
            return dict(kind="ship", name=ship, base=d["name"], race=d["race"], race_name=rname, color=rcol,
                        cls=d["cls"], cls_name=CLASS_DE.get(d["cls"], d["cls"]), tech=tech, techtxt=techtxt,
                        tank=d["tank"], shield=shield, armor=armor, size=CLASS_SIZE.get(d["cls"], 2))
        return dict(kind="ship", name=ship, base=ship, race="?", race_name="unbekannt", color="#9ca3af",
                    cls="?", cls_name="unbekannte Klasse", tech="?", techtxt="", tank="B",
                    shield=T1_SHIELD, armor=T1_ARMOR["G"], size=2, unknown=True)
    if who:
        low = who.lower()
        for prefixes, fac in NPC_PREFIX:
            if any(low.startswith(px) or (" " + px) in low for px in prefixes):
                deals, weak = NPC_FACTIONS[fac]
                rk = NPC_RACE_KEY.get(fac)
                rname, rcol = RACE.get(rk, (fac, "#9ca3af")) if rk else (
                    {"Drones": "Rogue Drones", "Merc": "Mercenaries", "Sleeper": "Sleepers"}[fac], "#9ca3af")
                size = 3.2 if any(w in low for w in ("battleship", "dread", "usurper", "eradicator",
                                                       "overlord", "titan")) else \
                    (2 if any(w in low for w in ("cruiser", "despoiler", "arrogator", "destroyer")) else 1.4)
                return dict(kind="npc", name=who, race_name=rname, color=rcol, faction=fac, deals=deals,
                            weak=weak, size=size)
    return None


def counter_lines(ent, own_family=None):
    """→ Liste (Überschrift, Text) mit Empfehlungen"""
    out = []

    def ammo(idx):
        if own_family in AMMO_BY_FAMILY:
            return f"  ({AMMO_BY_FAMILY[own_family][idx]})"
        return ""

    if ent["kind"] == "npc":
        if ent["weak"]:
            a, b = ent["weak"]
            out.append(("SCHIESS MIT", f"{DMG[a]} › {DMG[b]}"))
            out.append(("MUNITION", (AMMO_BY_FAMILY[own_family][a] if own_family in AMMO_BY_FAMILY
                                     else DMG[a] + "-Munition")))
        else:
            out.append(("SCHIESS MIT", "egal – gleichmäßige Resistenzen"))
        out.append(("ER MACHT", " / ".join(ent["deals"]) + " – diese Resistenzen fitten"))
        return out
    tank = ent["tank"]
    if tank in ("S", "B"):
        a, b = _best(ent["shield"])
        out.append(("GEGEN SCHILD-FIT", f"{DMG[a]} › {DMG[b]}{ammo(a)}"))
    if tank in ("A", "B"):
        a, b = _best(ent["armor"])
        out.append(("GEGEN PANZER-FIT", f"{DMG[a]} › {DMG[b]}{ammo(a)}"))
    return out


def own_family(weapon):
    if not weapon:
        return None
    low = weapon.lower()
    if any(w in low for w in ("missile", "rocket", "torpedo", "launcher")):
        return "missile"
    info = weapon_info(weapon)
    if info and "Autokanone" in info["cls"]:
        return "ac"
    if info and "Artillerie" in info["cls"]:
        return "arty"
    if info and "Drohne" in info["cls"]:
        return "drone"
    return None


# ════════════════════════════════════════════════════════════════════
#  Themes & Zeichen-Helfer
# ════════════════════════════════════════════════════════════════════

THEMES = {
    "VOID":     dict(acc="#3ce8ff", hi="#c8fbff", inc="#ff4563", logi="#4dffa0", cap="#ffd84a",
                     neut="#c48cff", bg="#05090f", panel="#08111b", line="#173444",
                     text="#d6eef7", dim="#5d7f90"),
    "AMARR":    dict(acc="#ffc24a", hi="#fff0c8", inc="#ff4f3d", logi="#7dff8a", cap="#ffe9a0",
                     neut="#d68cff", bg="#0b0805", panel="#130e07", line="#3e2e14",
                     text="#f6ead2", dim="#8f7d5c"),
    "MINMATAR": dict(acc="#ff7a3d", hi="#ffd9c4", inc="#ff3d77", logi="#6dffb0", cap="#ffd84a",
                     neut="#b98cff", bg="#0d0706", panel="#150b09", line="#40201a",
                     text="#f4dfd6", dim="#8e6c60"),
    "GALLENTE": dict(acc="#4dffbf", hi="#d1fff0", inc="#ff5577", logi="#7fd4ff", cap="#ffe066",
                     neut="#c48cff", bg="#040c0a", panel="#07140f", line="#153d30",
                     text="#d4f5ea", dim="#5a8c7b"),
    "CALDARI":  dict(acc="#7ab0ff", hi="#dfeaff", inc="#ff4f5e", logi="#5dffb0", cap="#ffd84a",
                     neut="#d08cff", bg="#05070d", panel="#090e18", line="#1b2a46",
                     text="#dbe6fb", dim="#62728f"),
    "NEON":     dict(acc="#ff4fd8", hi="#ffe3fb", inc="#ffb03b", logi="#4dffa0", cap="#ffe84a",
                     neut="#8f7bff", bg="#0a0614", panel="#120a22", line="#3d1f60",
                     text="#f3e6ff", dim="#8f72ad"),
    "TERMINAL": dict(acc="#39ff7a", hi="#d2ffe0", inc="#ff5a3c", logi="#b4ff3c", cap="#ffd23c",
                     neut="#3cd2ff", bg="#010503", panel="#030a06", line="#0f3a1d",
                     text="#a6ffc4", dim="#2f8a4f"),
    "GRAPHITE": dict(acc="#e8ecef", hi="#ffffff", inc="#ff5f57", logi="#5ad18c", cap="#f0c04a",
                     neut="#a58cff", bg="#121416", panel="#1a1d20", line="#2e3337",
                     text="#e6e8ea", dim="#7c848b"),
}
KEY_COLOR = "#010203"

# Designs = Form & Effekte (Farben kommen vom Theme, jedes Design hat ein passendes Standard-Theme)
DESIGNS = {
    "HUD":      dict(label="HUD · Segment-Ringe, Glow, Scanlines", gauge="segments", glow=1, scan=True,
                     shape="chamfer", mono=False, grid="dots", theme="VOID"),
    "NEON":     dict(label="Neon · Synthwave, starker Glow, Horizont-Gitter", gauge="arc", glow=2, scan=False,
                     shape="chamfer", mono=False, grid="perspective", theme="NEON"),
    "MINIMAL":  dict(label="Minimal · flach, dünne Bögen, kein Glow", gauge="thin", glow=0, scan=False,
                     shape="rect", mono=False, grid="none", theme="GRAPHITE"),
    "TERMINAL": dict(label="Terminal · Phosphor-Grün, Monospace, Blöcke", gauge="blocks", glow=0, scan=True,
                     shape="rect", mono=True, grid="dots", theme="TERMINAL"),
}   # wird unter Windows transparent geschaltet


def _rgb(c):
    c = c.lstrip("#")
    return int(c[0:2], 16), int(c[2:4], 16), int(c[4:6], 16)


_mix_cache = {}


def mix(a, b, t):
    """a → b um Anteil t (0..1)"""
    key = (a, b, round(t, 3))
    r = _mix_cache.get(key)
    if r is None:
        t = max(0.0, min(1.0, t))
        ra, ga, ba = _rgb(a)
        rb, gb, bb = _rgb(b)
        r = "#%02x%02x%02x" % (int(ra + (rb - ra) * t), int(ga + (gb - ga) * t), int(ba + (bb - ba) * t))
        if len(_mix_cache) > 20000:
            _mix_cache.clear()
        _mix_cache[key] = r
    return r


def fmt_int(v):
    return f"{int(round(v)):,}".replace(",", ".")


def fmt_short(v):
    v = float(v)
    if v >= 1e9:
        return f"{v / 1e9:.2f}B"
    if v >= 1e6:
        return f"{v / 1e6:.2f}M"
    if v >= 1e4:
        return f"{v / 1e3:.1f}k"
    return fmt_int(v)


def fmt_time(sec):
    sec = int(sec)
    return f"{sec // 60:02d}:{sec % 60:02d}"


def nice_ceil(v):
    if v <= 0:
        return 100
    e = 10 ** math.floor(math.log10(v))
    for m in (1, 1.5, 2, 2.5, 3, 4, 5, 6, 8, 10):
        if m * e >= v:
            return m * e
    return 10 * e


# ════════════════════════════════════════════════════════════════════
#  Flotten-Netzwerk (Host / Client / Relay) – JSON-Zeilen über TCP
# ════════════════════════════════════════════════════════════════════

NET_PORT = 47820
MAX_LINE = 1 << 20


def _send(sock, obj):
    sock.sendall((json.dumps(obj, separators=(",", ":")) + "\n").encode("utf-8"))


def _clean_pilots(pl):
    out = []
    if not isinstance(pl, list):
        return out
    for p in pl[:24]:
        if not isinstance(p, dict) or not isinstance(p.get("n"), str):
            continue
        q = {"n": p["n"][:40]}
        for k in ("o", "i", "ri", "ro", "to", "ti", "pk"):
            try:
                q[k] = float(p.get(k, 0) or 0)
            except (TypeError, ValueError):
                q[k] = 0.0
        h = p.get("h") if isinstance(p.get("h"), list) else []
        q["h"] = [float(x) for x in h[:60] if isinstance(x, (int, float))]
        q["l"] = bool(p.get("l"))
        q["tg"] = str(p.get("tg", ""))[:48]
        out.append(q)
    return out


class FleetServer:
    """Host- bzw. Relay-Server: sammelt Piloten aller Clients und verteilt die Flotte 1×/s."""

    def __init__(self, port, code, get_local=None, on_update=None, on_status=None):
        self.port, self.code = int(port), str(code or "")
        self.get_local, self.on_update, self.on_status = get_local, on_update, on_status

    def _status(self, txt, ok):
        if self.on_status:
            self.on_status(txt, ok)

    @staticmethod
    def _drop(conns, s):
        conns.pop(s, None)
        try:
            s.close()
        except OSError:
            pass

    def run(self, stop):
        try:
            srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            srv.bind(("", self.port))
            srv.listen(16)
        except OSError:
            self._status(f"PORT {self.port} BELEGT", False)
            return
        conns = {}
        last = 0.0
        self._status(f"HOST :{self.port} · 0 verbunden", True)
        try:
            while not stop.is_set():
                try:
                    r, _, _ = select.select([srv] + list(conns), [], [], 0.25)
                except (OSError, ValueError):
                    r = []
                now = time.time()
                for s in r:
                    if s is srv:
                        try:
                            c, a = srv.accept()
                            c.settimeout(2.0)
                            conns[c] = {"addr": a[0], "buf": b"", "auth": False, "pilots": [], "t": now}
                        except OSError:
                            pass
                        continue
                    st = conns.get(s)
                    if st is None:
                        continue
                    try:
                        data = s.recv(65536)
                    except OSError:
                        data = b""
                    if not data:
                        self._drop(conns, s)
                        continue
                    st["buf"] += data
                    if len(st["buf"]) > MAX_LINE:
                        self._drop(conns, s)
                        continue
                    while b"\n" in st["buf"] and s in conns:
                        line, st["buf"] = st["buf"].split(b"\n", 1)
                        try:
                            msg = json.loads(line.decode("utf-8"))
                        except Exception:
                            continue
                        if isinstance(msg, dict):
                            self._handle(conns, s, st, msg, now)
                for s, st in list(conns.items()):
                    if (not st["auth"] and now - st["t"] > 5) or now - st["t"] > 15:
                        self._drop(conns, s)
                if now - last >= 1.0:
                    last = now
                    remote, seen = [], set()
                    for st in conns.values():
                        if st["auth"]:
                            for p in st["pilots"]:
                                if p["n"] not in seen:
                                    seen.add(p["n"])
                                    remote.append(p)
                    local = list(self.get_local()) if self.get_local else []
                    lnames = {p["n"] for p in local}
                    fleet = local + [p for p in remote if p["n"] not in lnames]
                    n = sum(1 for st in conns.values() if st["auth"])
                    payload = (json.dumps({"t": "fleet", "pilots": fleet, "peers": n},
                                          separators=(",", ":")) + "\n").encode("utf-8")
                    for s, st in list(conns.items()):
                        if st["auth"]:
                            try:
                                s.sendall(payload)
                            except OSError:
                                self._drop(conns, s)
                    if self.on_update:
                        self.on_update(remote, n)
                    self._status(f"HOST :{self.port} · {n} verbunden", True)
        finally:
            for s in list(conns):
                self._drop(conns, s)
            try:
                srv.close()
            except OSError:
                pass

    def _handle(self, conns, s, st, msg, now):
        t = msg.get("t")
        if t == "hello":
            if str(msg.get("code", "")) == self.code:
                st["auth"], st["t"] = True, now
                try:
                    _send(s, {"t": "welcome"})
                except OSError:
                    self._drop(conns, s)
            else:
                try:
                    _send(s, {"t": "error", "msg": "Falscher Flotten-Code"})
                except OSError:
                    pass
                self._drop(conns, s)
        elif t == "pilots" and st["auth"]:
            st["pilots"] = _clean_pilots(msg.get("pilots"))
            st["t"] = now


class FleetNet:
    """Läuft im Hintergrund; die UI setzt lokale Piloten und liest die Flotte."""

    def __init__(self, mode, host, port, code):
        self.mode, self.host, self.port, self.code = mode, host, int(port), str(code or "")
        self.lock = threading.Lock()
        self.local, self.remote = [], []
        self.status, self.ok = "VERBINDE …", False
        self.stop = threading.Event()
        target = self._run_host if mode == "host" else self._run_client
        threading.Thread(target=target, daemon=True).start()

    def set_local(self, pilots):
        with self.lock:
            self.local = pilots

    def snapshot(self):
        with self.lock:
            return list(self.remote), self.status, self.ok

    def _set(self, status=None, ok=None, remote=None):
        with self.lock:
            if status is not None:
                self.status = status
            if ok is not None:
                self.ok = ok
            if remote is not None:
                self.remote = remote

    def close(self):
        self.stop.set()

    def _run_host(self):
        def get_local():
            with self.lock:
                return list(self.local)
        srv = FleetServer(self.port, self.code, get_local=get_local,
                          on_update=lambda r, n: self._set(remote=r),
                          on_status=lambda s, ok: self._set(status=s, ok=ok))
        srv.run(self.stop)

    def _run_client(self):
        while not self.stop.is_set():
            s = None
            try:
                self._set(status=f"VERBINDE MIT {self.host} …", ok=False)
                s = socket.create_connection((self.host, self.port), timeout=5)
                s.settimeout(0.3)
                _send(s, {"t": "hello", "code": self.code, "v": 1})
                buf, last_send, last_rx = b"", 0.0, time.time()
                while not self.stop.is_set():
                    now = time.time()
                    if now - last_send >= 1.0:
                        last_send = now
                        with self.lock:
                            mine = list(self.local)
                        _send(s, {"t": "pilots", "pilots": mine})
                    try:
                        data = s.recv(65536)
                        if not data:
                            raise ConnectionError("getrennt")
                        buf += data
                        if len(buf) > MAX_LINE:
                            raise ConnectionError("zu groß")
                    except socket.timeout:
                        pass
                    while b"\n" in buf:
                        line, buf = buf.split(b"\n", 1)
                        try:
                            msg = json.loads(line.decode("utf-8"))
                        except Exception:
                            continue
                        if not isinstance(msg, dict):
                            continue
                        if msg.get("t") == "error":
                            self._set(status=str(msg.get("msg", "FEHLER")).upper(), ok=False, remote=[])
                            self.stop.wait(8)
                            raise ConnectionError("abgelehnt")
                        if msg.get("t") in ("welcome", "fleet"):
                            last_rx = now
                        if msg.get("t") == "fleet":
                            self._set(status=f"VERBUNDEN · {self.host}", ok=True,
                                      remote=_clean_pilots(msg.get("pilots")))
                    if now - last_rx > 10:
                        raise ConnectionError("Timeout")
            except Exception:
                if not self.status.startswith("FALSCHER"):
                    self._set(status="GETRENNT · neuer Versuch …", ok=False, remote=[])
                else:
                    self._set(ok=False, remote=[])
            finally:
                if s:
                    try:
                        s.close()
                    except OSError:
                        pass
            self.stop.wait(3)


def run_relay(port, code):
    print(f"VOIDMETER Relay {VERSION} · Port {port} · Code {'gesetzt' if code else 'keiner'}")
    last = [None]

    def st(txt, ok):
        if txt != last[0]:
            last[0] = txt
            print(time.strftime("%H:%M:%S"), txt, flush=True)

    stop = threading.Event()
    try:
        FleetServer(port, code, on_status=st).run(stop)
    except KeyboardInterrupt:
        stop.set()


# ════════════════════════════════════════════════════════════════════
#  Windows-Extras: DPI, Click-Through, globaler Hotkey
# ════════════════════════════════════════════════════════════════════

def win_dpi_aware():
    if not IS_WIN:
        return
    try:
        import ctypes
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except Exception:
        try:
            import ctypes
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            pass


def win_set_clickthrough(root, on):
    if not IS_WIN:
        return False
    try:
        import ctypes
        hwnd = int(root.wm_frame(), 16)
        GWL_EXSTYLE, WS_EX_LAYERED, WS_EX_TRANSPARENT = -20, 0x80000, 0x20
        u = ctypes.windll.user32
        style = u.GetWindowLongW(hwnd, GWL_EXSTYLE)
        style = (style | WS_EX_LAYERED | WS_EX_TRANSPARENT) if on else (style & ~WS_EX_TRANSPARENT)
        u.SetWindowLongW(hwnd, GWL_EXSTYLE, style)
        return True
    except Exception:
        return False


class WinHotkey(threading.Thread):
    """Strg+Shift+F9 global → setzt ein Flag, das die UI abholt."""

    def __init__(self):
        super().__init__(daemon=True)
        self.pressed = False

    def run(self):
        try:
            import ctypes
            from ctypes import wintypes
            u = ctypes.windll.user32
            MOD_CONTROL, MOD_SHIFT, MOD_NOREPEAT, VK_F9, WM_HOTKEY = 0x2, 0x4, 0x4000, 0x78, 0x312
            if not u.RegisterHotKey(None, 1, MOD_CONTROL | MOD_SHIFT | MOD_NOREPEAT, VK_F9):
                return
            msg = wintypes.MSG()
            while u.GetMessageW(ctypes.byref(msg), None, 0, 0) != 0:
                if msg.message == WM_HOTKEY:
                    self.pressed = True
        except Exception:
            pass


# ════════════════════════════════════════════════════════════════════
#  Zeichen-Basis (geteilt von Hauptfenster und Flotten-Fenster)
# ════════════════════════════════════════════════════════════════════

BASE_W = 400
FULL_H = 624
COMPACT_H = 138
HEADER_H = 36
LAYOUTS = {
    "COCKPIT": dict(label="Cockpit · alles im Blick (Standard)", size=(400, 624)),
    "STRIP": dict(label="Leiste · flaches Band für den Bildschirmrand", size=(780, 100)),
    "TOWER": dict(label="Turm · schmal, mit großem Kampf-Feed", size=(240, 620)),
    "ORBIT": dict(label="Orbit · konzentrische Ringe im Radar-Look", size=(380, 430)),
}
PILOT_COLORS = ["#3ce8ff", "#ff9f43", "#a78bfa", "#4ade80", "#f472b6", "#facc15", "#38bdf8", "#fb7185"]


class DrawMixin:
    """Erwartet: self.app, self.root, self.c, self.buttons, self.drag"""

    has_grip = False
    pos_keys = ("x", "y")

    @property
    def th(self):
        return THEMES.get(self.app.cfg.get("theme"), THEMES["VOID"])

    @property
    def s(self):
        return float(self.app.cfg.get("scale", 1.0))

    @property
    def style(self):
        return DESIGNS.get(self.app.cfg.get("design"), DESIGNS["HUD"])

    def setup_window(self, win):
        win.overrideredirect(True)
        win.attributes("-topmost", True)
        win.configure(bg=KEY_COLOR if IS_WIN else self.th["bg"])
        if IS_WIN:
            try:
                win.attributes("-transparentcolor", KEY_COLOR)
            except tk.TclError:
                pass
        self.c = tk.Canvas(win, highlightthickness=0, bd=0, bg=KEY_COLOR if IS_WIN else self.th["bg"])
        self.c.pack(fill="both", expand=True)
        self.c.bind("<ButtonPress-1>", self.on_press)
        self.c.bind("<B1-Motion>", self.on_motion)
        self.c.bind("<ButtonRelease-1>", self.on_release)
        self.c.bind("<Button-3>", lambda e: self.app.show_menu(e.x_root, e.y_root))

    def make_fonts(self):
        fams = set(tkfont.families(self.root))

        def pick(*names):
            for n in names:
                if n in fams:
                    return n
            return "Helvetica"

        ui = pick("Bahnschrift", "Segoe UI", "DejaVu Sans", "Helvetica")
        num = pick("Bahnschrift SemiBold SemiConden", "Bahnschrift SemiBold", "Bahnschrift",
                   "DejaVu Sans Condensed", "Segoe UI Semibold", "DejaVu Sans")
        mono = pick("Cascadia Mono", "Consolas", "DejaVu Sans Mono", "Courier")
        if self.style["mono"]:
            ui = num = mono
        s = self.s

        def F(fam, px, bold=False):
            return tkfont.Font(root=self.root, family=fam, size=-max(6, int(round(px * s))),
                               weight="bold" if bold else "normal")

        self.F = {
            "title": F(ui, 12, True), "tiny": F(ui, 9), "tinyb": F(ui, 9, True),
            "small": F(ui, 11), "smallb": F(ui, 11, True), "label": F(ui, 10, True),
            "xl": F(num, 40, True), "l": F(num, 25, True), "m": F(num, 17, True),
            "ms": F(num, 14, True), "sn": F(num, 12, True), "mono": F(mono, 10),
            "monob": F(mono, 10, True),
        }

    # ── Grundformen ────────────────────────────────────────────────
    def X(self, *v):
        s = self.s
        return [a * s for a in v]

    def line(self, pts, col, w=1.0, tag="dyn", **kw):
        return self.c.create_line(*self.X(*pts), fill=col, width=max(1.0, w * self.s), tags=tag, **kw)

    def poly(self, pts, fill, outline="", w=1.0, tag="dyn"):
        return self.c.create_polygon(*self.X(*pts), fill=fill, outline=outline,
                                     width=max(1.0, w * self.s), tags=tag)

    def rect(self, x1, y1, x2, y2, fill, outline="", tag="dyn"):
        return self.c.create_rectangle(*self.X(x1, y1, x2, y2), fill=fill, outline=outline,
                                       width=0 if not outline else max(1.0, self.s), tags=tag)

    def oval(self, x1, y1, x2, y2, fill, outline="", tag="dyn"):
        return self.c.create_oval(*self.X(x1, y1, x2, y2), fill=fill, outline=outline,
                                  width=0 if not outline else max(1.0, self.s), tags=tag)

    def text(self, x, y, t, font, col, anchor="nw", tag="dyn"):
        return self.c.create_text(x * self.s, y * self.s, text=t, font=self.F[font],
                                  fill=col, anchor=anchor, tags=tag)

    def arc(self, cx, cy, r, start, extent, col, w, tag="dyn"):
        s = self.s
        return self.c.create_arc((cx - r) * s, (cy - r) * s, (cx + r) * s, (cy + r) * s,
                                 start=start, extent=extent, style="arc", outline=col,
                                 width=max(1.0, w * s), tags=tag)

    def glow_line(self, pts, col, w=1.5, tag="dyn"):
        bg = self.th["panel"]
        g = self.style["glow"]
        if g >= 2:
            self.line(pts, mix(col, bg, 0.88), w + 11, tag)
            self.line(pts, mix(col, bg, 0.72), w + 6, tag)
            self.line(pts, mix(col, bg, 0.45), w + 2.5, tag)
            self.line(pts, mix(col, "#ffffff", 0.25), w, tag)
        elif g == 1:
            self.line(pts, mix(col, bg, 0.82), w + 5, tag)
            self.line(pts, mix(col, bg, 0.55), w + 2.2, tag)
            self.line(pts, col, w, tag)
        else:
            self.line(pts, col, w, tag)

    def fit(self, t, font, maxw):
        f = self.F[font]
        maxw *= self.s
        if f.measure(t) <= maxw:
            return t
        while t and f.measure(t + "…") > maxw:
            t = t[:-1]
        return t + "…"

    def tw(self, t, font):
        return self.F[font].measure(t) / self.s

    @staticmethod
    def spaced(t):
        return " ".join(t)

    # ── Rahmen, Kopfzeile, Buttons ─────────────────────────────────
    def chrome(self, W, H, tabs=None, active_tab=None, title=None, buttons=(), grip=False):
        th = self.th
        st = self.style
        cut = 18 if st["shape"] == "chamfer" else 0
        acc, panel, line = th["acc"], th["panel"], th["line"]
        if not IS_WIN:
            self.c.configure(bg=th["bg"])
        shape = [cut, 0, W - 1, 0, W - 1, H - cut, W - cut, H - 1, 0, H - 1, 0, cut]
        self.poly(shape, panel, outline=line, w=1, tag="st")
        if st["scan"]:
            sc = mix(panel, th["bg"], 0.35) if not st["mono"] else mix(panel, acc, 0.035)
            for y in range(HEADER_H + 2, H - 4, 3 if st["mono"] else 4):
                self.line((2, y, W - 2, y), sc, 1, "st")
        self.poly([cut, 0, W - 1, 0, W - 1, HEADER_H, 0, HEADER_H, 0, cut], mix(panel, acc, 0.06), tag="st")
        if cut:
            self.glow_line((0, cut, cut, 0), acc, 1.5, "st")
            self.glow_line((W - 1, H - cut, W - cut, H - 1), acc, 1.5, "st")
        else:
            self.line((0, 0, W - 1, 0), mix(acc, panel, 0.3), 1, "st")
        self.line((0, HEADER_H, W, HEADER_H), line, 1, "st")
        for i in range(6):
            x = W - 120 + i * 6
            self.line((x, HEADER_H - 3, x, HEADER_H), mix(acc, panel, 0.4), 1, "st")
        for (x, y, dx, dy) in ((4, H - 4, 1, -1), (W - 4, 4, -1, 1)):
            self.line((x, y, x + 12 * dx, y), mix(acc, panel, 0.3), 1, "st")
            self.line((x, y, x, y + 12 * dy), mix(acc, panel, 0.3), 1, "st")

        x = 46
        if tabs:
            narrow = W < 300
            tf = "smallb" if narrow else "title"
            for label, key, cb in tabs:
                txt = label if narrow else self.spaced(label)
                w = self.tw(txt, tf)
                on = key == active_tab
                self.text(x, 12 if narrow else 11, txt, tf, th["text"] if on else mix(th["dim"], panel, 0.15),
                          tag="st")
                if on:
                    self.line((x - 2, HEADER_H - 1, x + w + 2, HEADER_H - 1), acc, 2.2, "st")
                self.buttons.append(((x - 6, 4, x + w + 6, HEADER_H), cb))
                x += w + (12 if narrow else 18)
        elif title:
            txt = self.spaced(title)
            self.text(x, 11, txt, "title", th["text"], tag="st")
            self.line((x - 2, HEADER_H - 1, x + self.tw(txt, "title") + 2, HEADER_H - 1), acc, 2.2, "st")
        self.header_end = x

        bx = W - 16
        dimc = th["dim"]
        for kind, cb in buttons:
            if kind == "close":
                self.line((bx - 4, 14, bx + 4, 22), dimc, 1.6, "st")
                self.line((bx - 4, 22, bx + 4, 14), dimc, 1.6, "st")
            elif kind == "compact":
                if self.app.cfg["compact"]:
                    self.rect(bx - 5, 13, bx + 5, 23, "", outline=dimc, tag="st")
                else:
                    self.line((bx - 5, 21, bx + 5, 21), dimc, 1.6, "st")
            elif kind == "menu":
                for yy in (14, 18, 22):
                    self.line((bx - 5, yy, bx + 5, yy), dimc, 1.4, "st")
            self.buttons.append(((bx - 10, 6, bx + 10, 30), cb))
            bx -= 24
        self.buttons_left = bx + 12
        if grip:
            for i in range(3):
                o = 5 + i * 4
                self.line((W - 4 - o, H - 4, W - 4, H - 4 - o), mix(th["dim"], panel, 0.3), 1, "st")

    def wave_grid(self, x1, y1, x2, y2, mid):
        th = self.th
        panel, line = th["panel"], th["line"]
        g = self.style["grid"]
        if g == "dots":
            for i in range(1, 6):
                x = x1 + (x2 - x1) * i / 6
                for y in range(int(y1), int(y2), 6):
                    self.line((x, y, x, y + 2), mix(line, panel, 0.2), 1, "st")
        elif g == "perspective":
            col = mix(th["acc"], panel, 0.78)
            cx = (x1 + x2) / 2
            for i in range(-8, 9):
                self.line((cx + i * 9, mid, cx + i * 60, y2), col, 1, "st")
            yy, gap = mid + 3, 3
            while yy < y2:
                self.line((x1, yy, x2, yy), col, 1, "st")
                gap *= 1.45
                yy += gap
            for i in range(1, 6):
                y = mid - (mid - y1) * i / 6
                self.line((x1, y, x2, y), mix(th["acc"], panel, 0.9), 1, "st")
        self.line((x1, mid, x2, mid), line if g != "perspective" else mix(th["acc"], panel, 0.4),
                  1, "st")

    def section(self, y1, y2, label=None, x1=10, x2=BASE_W - 10):
        th = self.th
        col = mix(th["acc"], th["panel"], 0.35)
        self.line((x1, y1, x1 + 8, y1), col, 1, "st")
        self.line((x1, y1, x1, y1 + 8), col, 1, "st")
        self.line((x2, y2, x2 - 8, y2), col, 1, "st")
        self.line((x2, y2, x2, y2 - 8), col, 1, "st")
        if label:
            self.text(x1 + 12, y1 - 5, label, "tinyb", th["dim"], tag="st")

    def emblem(self, now, live):
        th = self.th
        acc, panel = th["acc"], th["panel"]
        cx, cy, r = 25, 18, 9
        ang = (now * (1.6 if live else 0.25)) % (2 * math.pi)
        pts = []
        for i in range(6):
            a = ang + i * math.pi / 3
            pts += [cx + r * math.cos(a), cy + r * math.sin(a)]
        self.poly(pts, mix(acc, panel, 0.85), outline=acc, w=1.4)
        pulse = 0.5 + 0.5 * math.sin(now * 5) if live else 0.2
        self.oval(cx - 3, cy - 3, cx + 3, cy + 3, mix(panel, acc, 0.35 + 0.65 * pulse))

    # ── Flotten-Ansicht ────────────────────────────────────────────
    def fleet_static(self, H):
        self.section(150, 262, "FLOTTE · 60s")
        self.wave_grid(12, 158, 388, 260 if self.style["grid"] == "perspective" else 252, 254)
        self.section(274, 598, "PILOTEN")
        self.line((20, 604, BASE_W - 20, 604), self.th["line"], 1, "st")
        self.buttons.append(((BASE_W - 120, 606, BASE_W - 18, 622), self.app.open_net_dialog))

    def pilot_color(self, name):
        cols = self.app.fleet_colors
        if name not in cols:
            cols[name] = len(cols) % len(PILOT_COLORS)
        idx = cols[name]
        return self.th["acc"] if idx == 0 else PILOT_COLORS[idx]

    def fleet_draw(self, now, compact=False):
        app, th = self.app, self.th
        acc, inc, panel, dim = th["acc"], th["inc"], th["panel"], th["dim"]
        fleet = app.fleet
        fd = app.fdisp

        def v(p, k):
            return fd.get(p["n"], {}).get(k, p.get(k, 0.0))

        pilots = sorted(fleet, key=lambda p: -v(p, "o"))
        tot_o = sum(v(p, "o") for p in pilots)
        tot_i = sum(v(p, "i") for p in pilots)
        tot_r = sum(v(p, "ro") for p in pilots)
        live = sum(1 for p in pilots if p.get("l"))

        if compact:
            y0 = HEADER_H + 6
            mx = max([v(p, "o") for p in pilots] + [1.0])
            if not pilots:
                self.text(200, 86, "Keine Piloten · Rechtsklick › Flotte", "small", dim, anchor="center")
            for i, p in enumerate(pilots[:4]):
                y = y0 + i * 23
                if i == 3 and len(pilots) > 4:
                    self.text(16, y + 3, f"+ {len(pilots) - 3} weitere · FLOTTE {fmt_int(tot_o)} DPS", "tinyb", dim)
                    break
                col = self.pilot_color(p["n"])
                self.oval(14, y + 6, 22, y + 14, col if p.get("l") else mix(col, panel, 0.6))
                self.text(28, y + 2, self.fit(p["n"], "small", 112), "small", th["text"])
                self.rect(146, y + 8, 312, y + 12, mix(col, panel, 0.85))
                self.rect(146, y + 8, 146 + 166 * min(1.0, v(p, "o") / mx), y + 12, col)
                self.text(386, y + 1, fmt_int(v(p, "o")), "ms", col, anchor="ne")
            return

        # Übersicht
        self.text(16, 46, self.spaced("FLOTTEN-DPS"), "label", acc)
        self.text(14, 58, fmt_int(tot_o), "xl", th["hi"])
        self.text(16, 108, f"PEAK {fmt_int(app.fleet_peak)}  ·  TOTAL {fmt_short(sum(p.get('to', 0) for p in pilots))}",
                  "tiny", dim)
        rows = [("DPS IN", fmt_int(tot_i), inc), ("REPS", fmt_int(tot_r), th["logi"]),
                ("PILOTEN", f"{len(pilots)} · {live} live", th["text"])]
        for i, (lab, val, col) in enumerate(rows):
            y = 48 + i * 22
            self.text(232, y + 4, lab, "tinyb", dim)
            self.text(386, y, val, "ms", col, anchor="ne")
            self.line((232, y + 19, 386, y + 19), mix(th["line"], panel, 0.3), 1)
        # Anteils-Balken
        x = 16
        if tot_o > 0.5:
            for p in pilots:
                w = 368 * v(p, "o") / tot_o
                if w >= 1:
                    col = self.pilot_color(p["n"])
                    self.rect(x, 126, x + w - 1, 134, col)
                x += w
        else:
            self.rect(16, 126, 384, 134, mix(acc, panel, 0.88))

        # Gestapelter Verlauf
        x1, x2, base, top = 12, 388, 254, 164
        series = []
        for p in reversed(pilots):  # stärkster Pilot oben
            h = list(p.get("h") or [])[-60:]
            h = [0.0] * (60 - len(h)) + h
            series.append((p, h))
        stacked, cum = [], [0.0] * 60
        for p, h in series:
            cum = [a + b for a, b in zip(cum, h)]
            stacked.append((p, cum))
        hmax = max(cum) if series else 0
        target = max(100.0, nice_ceil(hmax * 1.1))
        app.fleet_wave_max += (target - app.fleet_wave_max) * 0.1
        m = app.fleet_wave_max
        step = (x2 - x1) / 59
        for p, cs in reversed(stacked):
            col = self.pilot_color(p["n"])
            pts = []
            for i, val in enumerate(cs):
                pts += [x1 + i * step, base - min(1.0, val / m) * (base - top)]
            self.poly([x1, base] + pts + [x2, base], mix(col, panel, 0.62))
            self.line(pts, col, 1.3)
        self.text(388, 146, f"SKALA {fmt_short(m)}", "tiny", dim, anchor="se")

        # Piloten-Karten
        if not pilots:
            self.text(200, 420, "Noch keine Piloten.\n\nDeine Chars auf diesem PC erscheinen automatisch.\n"
                                "Flotte: Rechtsklick › Flotte › Verbindung einrichten",
                      "small", dim, anchor="center")
        gmax = max([max(p.get("h") or [0]) for p in pilots] + [1.0])
        for i, p in enumerate(pilots[:6]):
            y = 280 + i * 53
            if i == 5 and len(pilots) > 6:
                self.text(200, y + 18, f"+ {len(pilots) - 5} weitere Piloten", "smallb", dim, anchor="center")
                break
            col = self.pilot_color(p["n"])
            is_me = p["n"] == app.active
            self.rect(12, y, 15, y + 46, col if p.get("l") else mix(col, panel, 0.6))
            if is_me:
                self.rect(16, y, 388, y + 46, mix(panel, col, 0.06))
            name = self.fit(p["n"], "smallb", 130)
            self.text(22, y + 4, name, "smallb", th["text"])
            tag = ("DU" if is_me else "LOKAL") if p.get("src") == "local" else "NETZ"
            tx = 22 + self.tw(name, "smallb") + 8
            self.text(tx, y + 6, tag, "tinyb", col if tag == "DU" else dim)
            self.text(22, y + 21, f"IN {fmt_int(v(p, 'i'))}   REP {fmt_int(v(p, 'ro'))}   TOTAL {fmt_short(p.get('to', 0))}",
                      "tiny", dim)
            if p.get("tg"):
                self.text(22, y + 33, self.fit("› " + p["tg"], "tiny", 170), "tiny", mix(col, panel, 0.25))
            # Sparkline
            h = list(p.get("h") or [])[-60:]
            sx1, sx2, sb, st_ = 206, 306, y + 40, y + 8
            if len(h) > 1:
                stp = (sx2 - sx1) / (len(h) - 1)
                pts = []
                for j, val in enumerate(h):
                    pts += [sx1 + j * stp, sb - min(1.0, val / gmax) * (sb - st_)]
                self.poly([sx1, sb] + pts + [sx2, sb], mix(col, panel, 0.78))
                self.line(pts, col, 1.2)
            self.line((sx1, sb, sx2, sb), mix(th["line"], panel, 0.2), 1)
            self.text(386, y + 4, fmt_int(v(p, "o")), "m", col, anchor="ne")
            share = (v(p, "o") / tot_o * 100) if tot_o > 0.5 else 0
            self.text(386, y + 27, f"DPS · {share:.0f}%", "tiny", dim, anchor="ne")
            if p.get("l"):
                pulse = 0.5 + 0.5 * math.sin(now * 5 + i)
                self.oval(316, y + 8, 322, y + 14, mix(panel, th["logi"], 0.4 + 0.6 * pulse))
            self.line((22, y + 49, 386, y + 49), mix(th["line"], panel, 0.4), 1)

        # Fußzeile: Netzwerkstatus
        if app.net:
            col = th["logi"] if app.net_ok else th["cap"]
            self.text(14, 614, self.fit(app.net_status, "tinyb", 260), "tinyb", col, anchor="w")
        else:
            self.text(14, 614, "NUR LOKAL · keine Flottenverbindung", "tinyb", dim, anchor="w")
        self.text(BASE_W - 22, 614, "VERBINDUNG", "tinyb", acc, anchor="e")

    # ── Maus ───────────────────────────────────────────────────────
    def hit_button(self, x, y):
        ux, uy = x / self.s, y / self.s
        for rect, cb in list(getattr(self, "dyn_buttons", [])) + list(self.buttons):
            x1, y1, x2, y2 = rect
            if x1 <= ux <= x2 and y1 <= uy <= y2:
                return rect, cb
        return None

    def info_btn(self, x, y, weapon, ship=""):
        th = self.th
        r = 6
        self.oval(x - r, y - r, x + r, y + r, mix(th["acc"], th["panel"], 0.78), outline=th["acc"])
        self.text(x, y, "i", "tinyb", th["hi"], anchor="center")
        self.dyn_buttons.append(((x - r - 3, y - r - 3, x + r + 3, y + r + 3),
                                 lambda w=weapon, sh=ship: self.app.show_info(w, sh)))

    def ship_tag(self, x, y, ship, who, font="tiny", maxw=110):
        """Zeichnet [Icon] Schiffsname (klickbar → Konter-Info). Gibt neues x zurück."""
        ent = entity_info(ship, who)
        if not ent:
            return x
        th = self.th
        sz = ent["size"]
        L, w = 3 + sz * 1.6, 2 + sz * 0.9
        cy = y + (6 if font == "tiny" else 7)
        col = ent["color"]
        pts = [x, cy - w, x + 2 * L, cy, x, cy + w, x + L * 0.55, cy]
        self.poly(pts, col, outline=mix(col, "#ffffff", 0.4), w=1)
        label = ent["base"] if ent["kind"] == "ship" else ent["race_name"]
        label = self.fit(label, font, maxw)
        tx = x + 2 * L + 4
        self.text(tx, y, label, font, col)
        tw = self.tw(label, font)
        self.line((tx, cy + 6, tx + tw, cy + 6), mix(col, th["panel"], 0.55), 1)
        self.dyn_buttons.append(((x - 3, y - 3, tx + tw + 3, y + 15),
                                 lambda sh=ship, wh=who: self.app.show_ship(sh, wh)))
        return tx + tw + 5

    def win_w(self):
        return BASE_W

    def on_press(self, e):
        hit = self.hit_button(e.x, e.y)
        if hit:
            self.drag = ("btn", hit[0], hit[1])
            return
        W, H = self.win_w() * self.s, self.win_h() * self.s
        if self.has_grip and e.x > W - 22 * self.s and e.y > H - 22 * self.s:
            self.drag = ("resize", e.x_root, self.s)
        else:
            self.drag = ("move", e.x_root - self.root.winfo_x(), e.y_root - self.root.winfo_y())

    def on_motion(self, e):
        if not self.drag:
            return
        if self.drag[0] == "move":
            x, y = e.x_root - self.drag[1], e.y_root - self.drag[2]
            self.root.geometry(f"+{x}+{y}")
            self.app.cfg[self.pos_keys[0]], self.app.cfg[self.pos_keys[1]] = x, y
        elif self.drag[0] == "resize":
            ns = max(0.6, min(2.6, self.drag[2] + (e.x_root - self.drag[1]) / BASE_W))
            if abs(ns - self.s) > 0.02:
                self.app.cfg["scale"] = round(ns, 2)
                self.app.apply_layout()

    def on_release(self, e):
        if self.drag and self.drag[0] == "btn":
            x1, y1, x2, y2 = self.drag[1]
            ux, uy = e.x / self.s, e.y / self.s
            if x1 <= ux <= x2 and y1 <= uy <= y2:
                self.drag[2]()
        elif self.drag:
            self.app.store_pos()
        self.drag = None


class FleetWindow(DrawMixin):
    """Flotten-Ansicht als eigenes Overlay-Fenster."""

    pos_keys = ("fleet_x", "fleet_y")

    def __init__(self, app):
        self.app = app
        self.buttons, self.drag = [], None
        self.root = tk.Toplevel(app.root)
        self.root.title("VOIDMETER · Flotte")
        self.setup_window(self.root)
        self.root.bind("<Escape>", lambda e: self.close())
        self.relayout()

    def win_h(self):
        return FULL_H

    def relayout(self):
        s = self.s
        cfg = self.app.cfg
        self.root.geometry(f"{int(BASE_W * s)}x{int(FULL_H * s)}+{int(cfg['fleet_x'])}+{int(cfg['fleet_y'])}")
        try:
            self.root.attributes("-alpha", float(cfg["alpha"]))
        except tk.TclError:
            pass
        self.make_fonts()
        self.draw_static()

    def draw_static(self):
        self.c.delete("all")
        self.buttons = []
        if not IS_WIN:
            self.c.configure(bg=self.th["bg"])
        self.chrome(BASE_W, FULL_H, title="FLOTTE", buttons=[("close", self.close)])
        self.fleet_static(FULL_H)

    def draw(self, now):
        self.c.delete("dyn")
        self.emblem(now, any(p.get("l") for p in self.app.fleet))
        app = self.app
        n = len(app.fleet)
        self.text(BASE_W - 44, 18, f"{n} PILOT{'EN' if n != 1 else ''}", "tinyb", self.th["dim"], anchor="e")
        self.fleet_draw(now, compact=False)

    def close(self):
        self.app.cfg["fleet_window"] = False
        self.app.store_pos()
        try:
            self.root.destroy()
        except tk.TclError:
            pass
        self.app.fleet_win = None


# ════════════════════════════════════════════════════════════════════
#  Hauptfenster
# ════════════════════════════════════════════════════════════════════

class InfoPopup(DrawMixin):
    """Kleines Overlay mit Stärken/Schwächen/Reichweite einer Waffe."""

    pos_keys = ("info_x", "info_y")
    W = 340

    def __init__(self, app, weapon, ship="", who="", mode="weapon"):
        self.app, self.weapon, self.ship, self.who, self.mode = app, weapon, ship, who, mode
        self.buttons, self.dyn_buttons, self.drag = [], [], None
        self.root = tk.Toplevel(app.root)
        self.root.title("Waffen-Info")
        self.setup_window(self.root)
        self.root.bind("<Escape>", lambda e: self.close())
        self.make_fonts()
        self.h = 200
        self.render()
        s = self.s
        px, py = app.root.winfo_pointerx() + 14, app.root.winfo_pointery() - 40
        sw, sh = self.root.winfo_screenwidth(), self.root.winfo_screenheight()
        w, h = int(self.W * s), int(self.h * s)
        px = max(0, min(px, sw - w))
        py = max(0, min(py, sh - h))
        self.root.geometry(f"{w}x{h}+{px}+{py}")
        try:
            self.root.attributes("-alpha", max(0.9, float(app.cfg["alpha"])))
        except tk.TclError:
            pass

    def win_w(self):
        return self.W

    def win_h(self):
        return self.h

    def wrap(self, x, y, txt, font, col, width=None):
        s = self.s
        width = width or (self.W - x - 16)
        iid = self.c.create_text(x * s, y * s, text=txt, font=self.F[font], fill=col, anchor="nw",
                                 width=width * s, tags=("content",))
        b = self.c.bbox(iid)
        return (b[3] / s) + 3 if b else y + 14

    def resist_row(self, y, label, prof):
        th = self.th
        self.text(16, y + 1, label, "tinyb", th["dim"], tag="content")
        x = 68
        for i in range(4):
            col = DMG_COL[i]
            self.text(x, y + 1, DMG_SHORT[i], "tinyb", col, tag="content")
            bx = x + 22
            self.rect(bx, y + 4, bx + 22, y + 10, mix(col, th["panel"], 0.82), tag="content")
            self.rect(bx, y + 4, bx + 22 * prof[i] / 100, y + 10, col, tag="content")
            self.text(bx + 25, y + 1, f"{prof[i]:.0f}", "tiny", th["text"], tag="content")
            x += 66
        return y + 17

    def render_ship(self):
        th = self.th
        acc, panel, dim = th["acc"], th["panel"], th["dim"]
        ent = entity_info(self.ship, self.who)
        y = 46
        if not ent:
            y = self.wrap(16, y, self.who or self.ship or "?", "smallb", th["text"])
            y = self.wrap(16, y, "Unbekanntes Ziel – keine Daten.", "small", dim)
            return y
        title = ent["base"] if ent["kind"] == "ship" else ent["name"]
        y = self.wrap(16, y, title, "m", ent["color"])
        if ent["kind"] == "ship":
            sub = f"{ent['cls_name']} · {ent['race_name']}" + (f" · {ent['techtxt']}" if ent["techtxt"] else "")
            if self.who and self.who != title:
                sub = f"Pilot: {self.who}\n" + sub
        else:
            sub = f"NPC · {ent['race_name']}"
        y = self.wrap(16, y, sub, "tiny", dim)
        y += 6
        fam = own_family(self.app.own_weapon())
        for lab, val in counter_lines(ent, fam):
            y = self.wrap(16, y, lab, "tinyb", dim)
            y = self.wrap(16, y, val, "ms", acc)
            y += 2
        if ent["kind"] == "ship":
            tank = {"S": "Schild", "A": "Panzerung", "B": "Schild ODER Panzerung (beides üblich)"}[ent["tank"]]
            y += 4
            y = self.wrap(16, y, f"Üblicher Tank: {tank}", "small", th["text"])
            if ent["tank"] == "B":
                y = self.wrap(16, y, "Schau, welcher Balken bei ihm zuerst fällt.", "tiny", dim)
            y += 6
            y = self.wrap(16, y, "RESISTENZEN (ohne Module)", "tinyb", dim)
            y += 2
            y = self.resist_row(y, "SCHILD", ent["shield"])
            y = self.resist_row(y, "PANZER", ent["armor"])
            y = self.resist_row(y, "RUMPF", (33, 33, 33, 33))
            y += 6
            note = ("Basis-Resistenzen des Rumpfs. Hardener und Rigs im Fit verschieben die Werte – "
                    "Löcher bleiben aber meist dort, wo der Rumpf schwach ist.")
            if ent.get("unknown"):
                note = "Schiff nicht in der Datenbank – Standard-T1-Profil angenommen."
            y = self.wrap(16, y, note, "tiny", mix(dim, panel, 0.2))
        else:
            y += 4
            y = self.wrap(16, y, "Tipp: Gegen NPCs lohnt sich Munition mit seiner Schwäche fast immer.",
                          "tiny", mix(dim, panel, 0.2))
        return y

    def render(self):
        th = self.th
        acc, panel, dim = th["acc"], th["panel"], th["dim"]
        self.c.delete("all")
        self.buttons = []
        if self.mode == "ship":
            y = self.render_ship()
            self.h = int(y + 14)
            self.chrome(self.W, self.h, title="KONTER-INFO", buttons=[("close", self.close)])
            self.c.tag_raise("content")
            return
        info = weapon_info(self.weapon)
        y = 46
        y = self.wrap(16, y, self.weapon, "smallb", th["text"])
        if info:
            y = self.wrap(16, y, info["cls"], "tiny", dim)
            if self.ship:
                y = self.wrap(16, y, f"Gesehen auf: {self.ship}", "tiny", dim)
            y += 4
            y = self.wrap(16, y, info["short"], "l", acc)
            y = self.wrap(16, y, "Schaden: " + info["dmg"], "tiny", th["text"])
            y += 4
            for ln in info["lines"]:
                y = self.wrap(16, y, "· " + ln, "tiny", th["text"])
            for title, items, col, pre in (("STÄRKEN", info["pros"], th["logi"], "+ "),
                                           ("SCHWÄCHEN", info["cons"], th["inc"], "- ")):
                y += 6
                y = self.wrap(16, y, title, "tinyb", col)
                for it in items:
                    y = self.wrap(16, y, pre + it, "small", th["text"])
            y += 6
            y = self.wrap(16, y, "KONTER", "tinyb", th["cap"])
            y = self.wrap(16, y, info["tip"], "small", th["text"])
            if info.get("note"):
                y += 6
                y = self.wrap(16, y, info["note"], "tiny", mix(dim, panel, 0.2))
        else:
            y += 6
            y = self.wrap(16, y, "Keine Daten zu diesem Modul (NPC-Waffe oder unbekannter Name).", "small", dim)
        self.h = int(y + 14)
        self.chrome(self.W, self.h, title="WAFFEN-INFO", buttons=[("close", self.close)])
        self.c.tag_raise("content")

    def close(self):
        try:
            self.root.destroy()
        except tk.TclError:
            pass
        if self.app.info_win is self:
            self.app.info_win = None


class App(DrawMixin):
    has_grip = True

    def __init__(self, args):
        self.app = self
        self.cfg = load_config()
        if args.logdir:
            self.cfg["log_dir"] = args.logdir
        self.demo = None
        self.demo_bots = {}
        self.snap = args.snap
        self.log_dir = self.cfg.get("log_dir") or detect_log_dir()
        self.watcher = LogWatcher(self.log_dir)
        self.trackers = {}
        self.active = None
        self.locked = False
        self.particles = []
        self.sparks = []
        self.info_win = None
        self.flash = None
        self.disp = defaultdict(float)
        self.wave_max = 100.0
        self.last_poll = self.last_scan = self.last_sample = self.last_fleet = 0.0
        self.last_save = time.time()
        self.drag = None
        self.buttons = []
        self.fleet = []
        self.fdisp = {}
        self.fleet_colors = {}
        self.fleet_peak = 0.0
        self.fleet_wave_max = 100.0
        self.fleet_win = None
        self.net = None
        self.net_status, self.net_ok = "", False
        self._dlg = None
        self._menu_vars = []

        win_dpi_aware()
        self.root = tk.Tk()
        self.root.title(APP_NAME)
        self.setup_window(self.root)
        self.c.bind("<Double-Button-1>", self.on_double)
        self.root.bind("<Escape>", lambda e: self.quit())

        self.hotkey = None
        if IS_WIN:
            self.hotkey = WinHotkey()
            self.hotkey.start()

        if args.demo:
            self.start_demo()
        if self.cfg.get("net_mode") in ("host", "client"):
            self.start_net(self.cfg["net_mode"], self.cfg.get("net_host", ""),
                           self.cfg.get("net_port", NET_PORT), self.cfg.get("net_code", ""), quiet=True)
        self.apply_layout()
        if self.cfg.get("fleet_window"):
            self.open_fleet_window()
        self.root.after(30, self.frame)

    @property
    def layout(self):
        if self.cfg.get("tab") == "fleet":
            return "COCKPIT"
        lay = self.cfg.get("layout", "COCKPIT")
        return lay if lay in LAYOUTS else "COCKPIT"

    def win_size(self):
        if self.layout == "COCKPIT":
            return BASE_W, (COMPACT_H if self.cfg.get("compact") else FULL_H)
        return LAYOUTS[self.layout]["size"]

    @property
    def H(self):
        return self.win_size()[1]

    def win_w(self):
        return self.win_size()[0]

    def win_h(self):
        return self.win_size()[1]

    def tracker(self, char):
        t = self.trackers.get(char)
        if t is None:
            t = self.trackers[char] = Tracker(self.cfg["window"])
        return t

    def apply_layout(self):
        s = self.s
        W, H = self.win_size()
        w, h = int(W * s), int(H * s)
        self.root.geometry(f"{w}x{h}+{int(self.cfg['x'])}+{int(self.cfg['y'])}")
        try:
            self.root.attributes("-alpha", float(self.cfg["alpha"]))
        except tk.TclError:
            pass
        self.make_fonts()
        self.draw_static()
        if self.fleet_win:
            self.fleet_win.relayout()

    # ── Statischer Layer ───────────────────────────────────────────
    def draw_static(self):
        th = self.th
        self.c.delete("all")
        self.buttons = []
        W, H = self.win_size()
        lay = self.layout
        tabs = [("SOLO", "solo", lambda: self.set_tab("solo")),
                ("FLOTTE", "fleet", lambda: self.set_tab("fleet"))]
        btns = [("close", self.quit)]
        if lay == "COCKPIT":
            btns.append(("compact", self.toggle_compact))
        btns.append(("menu", self.menu_at_button))
        self.chrome(W, H, tabs=tabs, active_tab=self.cfg.get("tab", "solo"), buttons=btns, grip=True)
        if lay == "STRIP":
            return self.static_strip()
        if lay == "TOWER":
            return self.static_tower()
        if lay == "ORBIT":
            return self.static_orbit()
        if self.cfg["compact"]:
            return
        if self.cfg.get("tab") == "fleet":
            self.fleet_static(H)
            return
        acc, panel, line = th["acc"], th["panel"], th["line"]
        self.section(226, 344, "SIGNAL · 60s")
        self.section(356, 428)
        self.section(438, 484, "HIT PROFILE")
        self.section(496, 596)
        self.wave_grid(12, 232, 388, 342, 285)
        for i, lab in enumerate(("-60s", "-40s", "-20s", "jetzt")):
            x = 14 + i * (372 / 3)
            self.text(x, 333, lab, "tiny", mix(th["dim"], panel, 0.3),
                      anchor="w" if i == 0 else ("e" if i == 3 else "center"), tag="st")
        self.line((20, 604, W - 20, 604), line, 1, "st")
        self.buttons.append(((10, 606, 90, 622), self.cycle_window))
        self.buttons.append(((W - 90, 606, W - 18, 622), self.reset_active))

    # ── Dynamischer Layer ──────────────────────────────────────────
    def draw(self, now):
        th = self.th
        self.c.delete("dyn")
        self.dyn_buttons = []
        t = self.trackers.get(self.active)
        live = t is not None and (now - t.last_event) < 6
        panel = th["panel"]
        fleet_tab = self.cfg.get("tab") == "fleet"
        self.emblem(now, live or (fleet_tab and any(p.get("l") for p in self.fleet)))

        if self.demo:
            badge, bcol = "DEMO", th["cap"]
        elif not self.watcher.ok:
            badge, bcol = "KEIN LOG-ORDNER", th["inc"]
        elif not self.watcher.chars:
            badge, bcol = "WARTE AUF EVE", th["dim"]
        elif live:
            badge, bcol = "LIVE", th["logi"]
        else:
            badge, bcol = "IDLE", th["dim"]
        if self.locked:
            badge += " · LOCK"
        if self.net and fleet_tab:
            badge += " · NETZ" if self.net_ok else " · NETZ …"
        right = self.buttons_left - 6
        maxw = right - self.header_end - 4
        name = self.active or "—"
        mode = "AUTO · " if self.cfg.get("character") is None and not self.demo else ""
        if maxw >= 40:
            self.text(right, 12, self.fit(mode + name, "small", maxw), "small", th["text"], anchor="e")
            self.text(right, 26, self.fit(badge, "tinyb", maxw), "tinyb", bcol, anchor="e")
        else:
            self.oval(right - 6, 15, right, 21, bcol)

        if t is None:
            t = Tracker(self.cfg["window"])
        for k in KINDS:
            target = t.rate[k]
            d = self.disp[k]
            self.disp[k] = d + (target - d) * 0.18 if abs(target - d) > 0.05 else target

        lay = self.layout
        if fleet_tab:
            self.fleet_draw(now, compact=self.cfg["compact"])
        elif lay == "STRIP":
            self.draw_strip(t, now)
        elif lay == "TOWER":
            self.draw_tower(t, now)
        elif lay == "ORBIT":
            self.draw_orbit(t, now)
        elif self.cfg["compact"]:
            self.draw_compact(t, now)
        else:
            self.draw_full(t, now)

        if self.flash and now < self.flash[1]:
            a = min(1.0, (self.flash[1] - now) / 0.6)
            W = self.win_w()
            if lay == "COCKPIT":
                y = 48 if self.cfg["compact"] else 256
            else:
                y = {"STRIP": 30, "TOWER": 232, "ORBIT": 420}[lay]
            self.text(W / 2, y, self.flash[0], "tinyb", mix(panel, th["hi"], a), anchor="center")

    # Radial-Gauge aus Segmenten
    def gauge(self, cx, cy, r, frac, col, segs, width, peak_frac=None):
        th, panel = self.th, self.th["panel"]
        kind = self.style["gauge"]
        if kind in ("arc", "thin"):
            return self.gauge_arc(cx, cy, r, frac, col, width, peak_frac, kind == "thin")
        if kind == "blocks":
            segs = max(12, segs // 2)
        span, start = 270.0, 225.0
        step = span / segs
        gap = min(1.8, step * 0.3)
        if kind == "blocks":
            gap = step * 0.42
        lit = int(round(max(0.0, min(1.0, frac)) * segs))
        off = mix(col, panel, 0.86)
        glow = mix(col, panel, 0.72)
        for i in range(lit if kind == "segments" else 0):
            a = start - i * step
            self.arc(cx, cy, r, a - gap / 2, -(step - gap), glow, width + 6)
        for i in range(segs):
            a = start - i * step
            if i < lit:
                if kind == "blocks":
                    c = col
                else:
                    c = mix(col, "#ffffff", 0.55) if i == lit - 1 else mix(col, panel, 0.25 * (1 - i / max(1, lit)))
            else:
                c = off
            self.arc(cx, cy, r, a - gap / 2, -(step - gap), c, width)
        if peak_frac is not None and peak_frac > 0:
            i = min(segs - 1, int(peak_frac * segs))
            a = start - i * step
            self.arc(cx, cy, r + width * 0.9, a - gap / 2, -(step - gap), th["hi"], 2)
        self.arc(cx, cy, r - width - 3, 225, -270, mix(col, panel, 0.8), 1)
        for f in (0, 0.25, 0.5, 0.75, 1.0):
            a = math.radians(225 - 270 * f)
            r1, r2 = r - width - 3, r - width - 8
            self.line((cx + r1 * math.cos(a), cy - r1 * math.sin(a),
                       cx + r2 * math.cos(a), cy - r2 * math.sin(a)), mix(col, panel, 0.45), 1)

    def gauge_arc(self, cx, cy, r, frac, col, width, peak_frac, thin):
        th, panel = self.th, self.th["panel"]
        f = max(0.0, min(1.0, frac))
        if thin:
            self.arc(cx, cy, r, 225, -270, mix(col, panel, 0.85), 2)
            if f > 0.002:
                self.arc(cx, cy, r, 225, -270 * f, col, 4)
            for k in range(11):
                a = math.radians(225 - 27 * k)
                r1, r2 = r - 6, r - (11 if k % 5 == 0 else 8)
                self.line((cx + r1 * math.cos(a), cy - r1 * math.sin(a),
                           cx + r2 * math.cos(a), cy - r2 * math.sin(a)), mix(th["dim"], panel, 0.2), 1)
        else:
            self.arc(cx, cy, r, 225, -270, mix(col, panel, 0.88), width + 2)
            if f > 0.002:
                for w, t in ((width + 14, 0.9), (width + 8, 0.78), (width + 3, 0.5)):
                    self.arc(cx, cy, r, 225, -270 * f, mix(col, panel, t), w)
                self.arc(cx, cy, r, 225, -270 * f, mix(col, "#ffffff", 0.3), width - 2)
            a = math.radians(225 - 270 * f)
            px, py = cx + r * math.cos(a), cy - r * math.sin(a)
            self.oval(px - 4, py - 4, px + 4, py + 4, "#ffffff", outline=col)
            self.arc(cx, cy, r - width - 6, 225, -270, mix(col, panel, 0.75), 1)
        if peak_frac:
            a = math.radians(225 - 270 * min(1.0, peak_frac))
            r1, r2 = r + width / 2 + 2, r + width / 2 + 8
            self.line((cx + r1 * math.cos(a), cy - r1 * math.sin(a),
                       cx + r2 * math.cos(a), cy - r2 * math.sin(a)), th["hi"], 2)

    # ── Gemeinsame Bausteine ───────────────────────────────────────
    def draw_wave(self, t, x1, x2, mid, amp, last=None, tips=True):
        th = self.th
        acc, inc, panel = th["acc"], th["inc"], th["panel"]
        hist = list(t.history)
        if last:
            hist = hist[-last:]
        hmax = max(max(h[0], h[1]) for h in hist) if hist else 0
        target = max(60.0, nice_ceil(hmax * 1.15))
        self.wave_max += (target - self.wave_max) * 0.1
        step = (x2 - x1) / (len(hist) - 1)
        top, bot = [], []
        for i, h in enumerate(hist):
            x = x1 + i * step
            top += [x, mid - min(1.0, h[0] / self.wave_max) * amp]
            bot += [x, mid + min(1.0, h[1] / self.wave_max) * amp]
        self.poly([x1, mid] + top + [x2, mid], mix(acc, panel, 0.80))
        self.poly([x1, mid] + bot + [x2, mid], mix(inc, panel, 0.80))
        self.glow_line(top, acc, 1.4)
        self.glow_line(bot, inc, 1.2)
        if tips:
            for (yy, col) in ((top[-1], acc), (bot[-1], inc)):
                self.oval(x2 - 3, yy - 3, x2 + 3, yy + 3, th["hi"], outline=col)

    def weapon_label(self, wpn):
        info = weapon_info(wpn)
        if info and info["short"] not in ("?", ""):
            return f"{wpn}  ({info['short']})"
        return wpn

    def feed_row(self, ev, x1, x2, y, now, narrow=False):
        th = self.th
        out = ev["kind"] == "dmg_out"
        neut = ev["kind"] == "neut_in"
        col = th["acc"] if out else (th["neut"] if neut else th["inc"])
        amt = fmt_int(ev["amt"]) + (" GJ" if neut else "")
        self.text(x1, y, amt, "sn", col)
        aw = self.tw(amt, "sn") + 6
        who = ev["who"] or "?"
        if ev.get("ship") and ev["ship"] != who:
            who = f"{who} ({ev['ship']})"
        arrow = "» " if out else "« "
        self.text(x1 + aw, y + 1, self.fit(arrow + who, "small", x2 - x1 - aw - 34), "small", th["text"])
        age = max(0, int(now - ev["t"]))
        self.text(x2, y + 2, f"{age}s", "tiny", th["dim"], anchor="ne")
        wpn = ev.get("weapon") or ""
        sx = self.ship_tag(x1, y + 14, ev.get("ship", ""), ev.get("who", ""), maxw=70 if narrow else 100)
        sub = self.weapon_label(wpn) if wpn else ""
        if ev.get("q"):
            sub = (sub + " · " if sub else "") + ev["q"]
        if sx > x1 and sub:
            sub = "· " + sub
        lim = x2 - sx - (18 if wpn else 0)
        if lim > 20:
            self.text(sx, y + 14, self.fit(sub, "tiny", lim), "tiny", mix(col, th["panel"], 0.25))
        if wpn:
            self.info_btn(x2 - 6, y + 20, wpn, ev.get("ship", ""))

    def last_attacker(self, t, now, within=90):
        for ev in reversed(t.feed):
            if ev["kind"] == "dmg_in" and now - ev["t"] < within:
                return ev
        return None

    def set_list_tab(self, key):
        self.cfg["list_tab"] = key
        save_config(self.cfg)

    def bottom_panel(self, t, now):
        th = self.th
        acc, inc, panel, dim = th["acc"], th["inc"], th["panel"], th["dim"]
        tab = self.cfg.get("list_tab", "weapons")
        x = 16
        for lab, key in (("WAFFEN", "weapons"), ("ZIELE", "targets"), ("ANGREIFER", "sources"), ("FEED", "feed")):
            on = key == tab
            w = self.tw(lab, "tinyb")
            self.text(x, 499, lab, "tinyb", acc if on else dim)
            if on:
                self.line((x, 512, x + w, 512), acc, 1.5)
            self.dyn_buttons.append(((x - 5, 493, x + w + 5, 515), lambda k=key: self.set_list_tab(k)))
            x += w + 16
        if tab in ("weapons", "targets"):
            counter = t.by_weapon if tab == "weapons" else t.by_target
            tot = sum(counter.values()) or 1
            for i, (name, val) in enumerate(counter.most_common(4)):
                y = 519 + i * 19
                if tab == "weapons":
                    self.text(16, y, self.fit(self.weapon_label(name), "small", 280), "small", th["text"])
                    self.info_btn(316, y + 7, name)
                else:
                    sx = self.ship_tag(16, y + 1, t.tgt_ship.get(name, ""), name, maxw=90)
                    nm = name if t.tgt_ship.get(name) or sx == 16 else name
                    self.text(sx + (4 if sx > 16 else 0), y, self.fit(nm, "small", 330 - sx), "small", th["text"])
                self.text(384, y, fmt_short(val), "sn", acc, anchor="ne")
                self.rect(16, y + 15, 384, y + 16, mix(acc, panel, 0.86))
                self.rect(16, y + 15, 16 + 368 * val / tot, y + 16, acc)
            if not counter:
                self.text(16, 522, "—", "small", dim)
        elif tab == "sources":
            for i, (src, val) in enumerate(t.by_source.most_common(3)):
                y = 519 + i * 26
                self.text(16, y, self.fit(src, "small", 300), "small", th["text"])
                self.text(384, y, fmt_short(val), "sn", inc, anchor="ne")
                wc = t.src_weapons.get(src)
                sx = self.ship_tag(16, y + 13, t.src_ship.get(src, ""), t.src_who.get(src, src))
                if wc:
                    wpn = wc.most_common(1)[0][0]
                    self.text(sx, y + 13, self.fit(("· " if sx > 16 else "") + self.weapon_label(wpn), "tiny",
                                                   362 - sx), "tiny", mix(inc, "#ffffff", 0.35))
                    self.info_btn(378, y + 19, wpn, t.src_ship.get(src, ""))
                elif sx == 16:
                    self.text(16, y + 13, "NPC / keine Waffeninfo", "tiny", dim)
            if not t.by_source:
                self.text(16, 522, "Noch nichts abbekommen", "small", dim)
        else:
            evs = list(t.feed)[-3:][::-1]
            for i, ev in enumerate(evs):
                self.feed_row(ev, 16, 384, 519 + i * 26, now)
            if not evs:
                self.text(16, 522, "Feed leer – wartet auf Kampf", "small", dim)

    # ── Layout: Leiste ─────────────────────────────────────────────
    def static_strip(self):
        th = self.th
        for x in (150, 262, 362, 572):
            self.line((x, 44, x, 92), mix(th["line"], th["panel"], 0.0), 1, "st")
        self.wave_grid(372, 42, 562, 96, 68)

    def draw_strip(self, t, now):
        th = self.th
        acc, inc, panel, dim = th["acc"], th["inc"], th["panel"], th["dim"]
        d = self.disp
        y0 = HEADER_H
        out_max = nice_ceil(max(150.0, t.peak["dmg_out"] * 1.08))
        self.text(14, y0 + 6, self.spaced("DPS OUT"), "tinyb", acc)
        self.text(14, y0 + 16, fmt_int(d["dmg_out"]), "l", th["hi"])
        lit = int(round(min(1.0, d["dmg_out"] / out_max) * 20))
        for i in range(20):
            x = 14 + i * 6.5
            self.rect(x, y0 + 50, x + 4.5, y0 + 56, acc if i < lit else mix(acc, panel, 0.85))
        in_max = nice_ceil(max(100.0, t.peak["dmg_in"] * 1.08))
        self.text(160, y0 + 6, self.spaced("DPS IN"), "tinyb", inc)
        self.text(160, y0 + 18, fmt_int(d["dmg_in"]), "m", mix(inc, "#ffffff", 0.5))
        lit = int(round(min(1.0, d["dmg_in"] / in_max) * 14))
        for i in range(14):
            x = 160 + i * 6.5
            self.rect(x, y0 + 50, x + 4.5, y0 + 56, inc if i < lit else mix(inc, panel, 0.85))
        net = d["logi_in"] - d["dmg_in"]
        self.text(272, y0 + 6, self.spaced("NET HP/S"), "tinyb", dim)
        self.text(272, y0 + 18, ("+" if net >= 0 else "-") + fmt_int(abs(net)), "m",
                  th["logi"] if net >= 0 else inc)
        self.text(272, y0 + 44, f"REP IN {fmt_int(d['logi_in'])}", "tiny", th["logi"] if d["logi_in"] > 0.5 else dim)
        self.draw_wave(t, 372, 562, y0 + 32, 24, last=160)
        ev = self.last_attacker(t, now)
        if ev:
            who = ev["who"] + (f" ({ev['ship']})" if ev.get("ship") and ev["ship"] != ev["who"] else "")
            self.text(582, y0 + 4, "LETZTER TREFFER AUF DICH", "tinyb", inc)
            self.text(582, y0 + 18, self.fit(who, "small", 186), "small", th["text"])
            wpn = ev.get("weapon") or ""
            sx = self.ship_tag(582, y0 + 36, ev.get("ship", ""), ev.get("who", ""), maxw=60)
            self.text(sx, y0 + 36, self.fit(("· " if sx > 582 else "") + (self.weapon_label(wpn) if wpn else ""),
                                            "tiny", 748 - sx), "tiny", mix(inc, "#ffffff", 0.35))
            if wpn:
                self.info_btn(762, y0 + 42, wpn, ev.get("ship", ""))
        else:
            shots = t.hits_out + t.miss_out
            hr = (t.hits_out / shots * 100) if shots else 0
            self.text(582, y0 + 4, "TREFFER-PROFIL", "tinyb", dim)
            self.text(582, y0 + 18, f"{hr:.0f}% Treffer · MAX {fmt_int(t.biggest[0]) if t.biggest else 0}",
                      "small", th["text"])
            if t.by_weapon:
                wpn = t.by_weapon.most_common(1)[0][0]
                self.text(582, y0 + 36, self.fit(self.weapon_label(wpn), "tiny", 166), "tiny", acc)
                self.info_btn(762, y0 + 42, wpn)

    # ── Layout: Turm ───────────────────────────────────────────────
    def static_tower(self):
        self.section(240, 306, "SIGNAL", x2=230)
        self.wave_grid(12, 246, 228, 304, 274)
        self.section(318, 610, "KAMPF-FEED", x2=230)

    def draw_tower(self, t, now):
        th = self.th
        acc, inc, panel, dim = th["acc"], th["inc"], th["panel"], th["dim"]
        d = self.disp
        out_max = nice_ceil(max(150.0, t.peak["dmg_out"] * 1.08))
        self.text(14, 44, self.spaced("DPS OUT"), "label", acc)
        self.text(12, 56, fmt_int(d["dmg_out"]), "xl", th["hi"])
        lit = int(round(min(1.0, d["dmg_out"] / out_max) * 26))
        for i in range(26):
            x = 14 + i * 8.2
            self.rect(x, 106, x + 6, 113, acc if i < lit else mix(acc, panel, 0.85))
        self.text(14, 119, f"PEAK {fmt_int(t.peak['dmg_out'])} · TOTAL {fmt_short(t.total['dmg_out'])}", "tiny", dim)
        self.text(14, 142, "DPS IN", "tinyb", inc)
        self.text(14, 154, fmt_int(d["dmg_in"]), "m", mix(inc, "#ffffff", 0.5))
        net = d["logi_in"] - d["dmg_in"]
        self.text(126, 142, "NET HP/S", "tinyb", dim)
        self.text(126, 154, ("+" if net >= 0 else "-") + fmt_int(abs(net)), "m", th["logi"] if net >= 0 else inc)
        rows = [("REP IN", "logi_in", th["logi"]), ("REP OUT", "logi_out", th["logi"]),
                ("NEUT IN", "neut_in", th["neut"])]
        for i, (lab, k, col) in enumerate(rows):
            y = 186 + i * 16
            v = d[k]
            self.text(14, y, lab, "tinyb", col if v > 0.5 else dim)
            self.text(226, y - 1, fmt_int(v), "sn", th["text"] if v > 0.5 else dim, anchor="ne")
        self.draw_wave(t, 12, 228, 274, 26, last=160)
        evs = list(t.feed)[-8:][::-1]
        for i, ev in enumerate(evs):
            self.feed_row(ev, 16, 226, 328 + i * 34, now, narrow=True)
            self.line((16, 328 + i * 34 + 30, 226, 328 + i * 34 + 30), mix(th["line"], panel, 0.4), 1)
        if not evs:
            self.text(120, 460, "Wartet auf Kampf …", "small", dim, anchor="center")

    # ── Layout: Orbit ──────────────────────────────────────────────
    ORBIT_C = (190, 228)

    def static_orbit(self):
        th = self.th
        cx, cy = self.ORBIT_C
        col = mix(th["line"], th["panel"], 0.1)
        self.arc(cx, cy, 172, 0, 359.9, col, 1, "st")
        for k in range(24):
            a = math.radians(k * 15)
            r1, r2 = 172, 176 if k % 2 else 181
            self.line((cx + r1 * math.cos(a), cy - r1 * math.sin(a),
                       cx + r2 * math.cos(a), cy - r2 * math.sin(a)), col, 1, "st")

    def draw_orbit(self, t, now):
        th = self.th
        acc, inc, panel, dim = th["acc"], th["inc"], th["panel"], th["dim"]
        d = self.disp
        cx, cy = self.ORBIT_C
        out_max = nice_ceil(max(150.0, t.peak["dmg_out"] * 1.08))
        in_max = nice_ceil(max(100.0, t.peak["dmg_in"] * 1.08))
        rep_max = nice_ceil(max(100.0, t.peak["logi_in"] * 1.08))
        self._orbit_fr = (d["dmg_out"] / out_max, d["dmg_in"] / in_max)
        self.gauge(cx, cy, 152, d["dmg_out"] / out_max, acc, 56, 10, t.peak["dmg_out"] / out_max)
        self.gauge(cx, cy, 116, d["dmg_in"] / in_max, inc, 42, 7, t.peak["dmg_in"] / in_max)
        self.gauge(cx, cy, 86, d["logi_in"] / rep_max, th["logi"], 30, 5, None)
        self.text(cx, cy - 12, fmt_int(d["dmg_out"]), "xl", th["hi"], anchor="center")
        self.text(cx, cy + 18, self.spaced("DPS OUT"), "label", acc, anchor="center")
        net = d["logi_in"] - d["dmg_in"]
        self.text(cx, cy + 36, f"NET {'+' if net >= 0 else '-'}{fmt_int(abs(net))}", "tinyb",
                  th["logi"] if net >= 0 else inc, anchor="center")
        # Funken bei Treffern
        alive = []
        for sp in self.sparks:
            age = now - sp["t"]
            if age > 0.9:
                continue
            alive.append(sp)
            rr = sp["r"] + 8 + age * 42 * sp["dir"]
            a = math.radians(sp["a"])
            x, y = cx + rr * math.cos(a), cy - rr * math.sin(a)
            sz = 3.2 * (1 - age)
            self.oval(x - sz, y - sz, x + sz, y + sz, mix(sp["col"], panel, age / 0.9))
        self.sparks = alive
        # Legende
        items = [("OUT", d["dmg_out"], acc), ("IN", d["dmg_in"], inc), ("REP", d["logi_in"], th["logi"])]
        x = 60
        for lab, v, col in items:
            self.oval(x, 372, x + 8, 380, col)
            self.text(x + 13, 369, f"{lab} {fmt_int(v)}", "smallb", col)
            x += 98
        ev = self.last_attacker(t, now)
        if ev:
            who = ev["who"] + (f" ({ev['ship']})" if ev.get("ship") and ev["ship"] != ev["who"] else "")
            wpn = ev.get("weapon") or ""
            self.text(20, 388, self.fit(f"« {who}", "tiny", 320), "tiny", th["text"])
            sx = self.ship_tag(20, 402, ev.get("ship", ""), ev.get("who", ""), maxw=80)
            self.text(sx, 402, self.fit(("· " if sx > 20 else "") + (self.weapon_label(wpn) if wpn else ""),
                                        "tiny", 336 - sx), "tiny", mix(inc, "#ffffff", 0.35))
            if wpn:
                self.info_btn(352, 408, wpn, ev.get("ship", ""))
        else:
            shots = t.hits_out + t.miss_out
            hr = (t.hits_out / shots * 100) if shots else 0
            self.text(cx, 400, f"TREFFER {hr:.0f}% · TOTAL {fmt_short(t.total['dmg_out'])}", "tiny", dim,
                      anchor="center")

    def spawn_sparks(self, evs, now):
        th = self.th
        fo, fi = getattr(self, "_orbit_fr", (0, 0))
        for ev in evs[:6]:
            out = ev.kind == "dmg_out"
            frac = min(1.0, fo if out else fi)
            self.sparks.append({"t": now, "a": 225 - 270 * frac + random.uniform(-8, 8),
                                "r": 152 if out else 116, "dir": 1 if out else -0.6,
                                "col": th["hi"] if out else th["inc"]})
        self.sparks = self.sparks[-40:]

    def show_info(self, weapon, ship=""):
        if self.info_win:
            self.info_win.close()
        self.info_win = InfoPopup(self, weapon, ship)

    def own_weapon(self):
        t = self.trackers.get(self.active)
        if t and t.by_weapon:
            return t.by_weapon.most_common(1)[0][0]
        return None

    def show_ship(self, ship, who=""):
        if self.info_win:
            self.info_win.close()
        self.info_win = InfoPopup(self, None, ship, who=who, mode="ship")

    def set_layout(self, name):
        self.cfg["layout"] = name
        self.cfg["tab"] = "solo"
        self.particles, self.sparks = [], []
        save_config(self.cfg)
        self.apply_layout()

    def draw_full(self, t, now):
        th = self.th
        acc, inc, panel = th["acc"], th["inc"], th["panel"]
        d = self.disp

        # ── DPS OUT ────────────────────────────────────────────
        out_max = nice_ceil(max(150.0, t.peak["dmg_out"] * 1.08))
        cx, cy = 112, 134
        self.gauge(cx, cy, 76, d["dmg_out"] / out_max, acc, 46, 9, t.peak["dmg_out"] / out_max)
        self.text(cx, cy - 6, fmt_int(d["dmg_out"]), "xl", th["hi"], anchor="center")
        self.text(cx, cy + 24, self.spaced("DPS OUT"), "label", acc, anchor="center")
        self.text(cx, cy + 40, f"PEAK {fmt_int(t.peak['dmg_out'])}", "tiny", th["dim"], anchor="center")
        self.text(cx, cy + 70, f"TOTAL {fmt_short(t.total['dmg_out'])}", "smallb", th["text"], anchor="center")
        self.text(cx - 58, cy + 62, "0", "tiny", th["dim"], anchor="center")
        self.text(cx + 58, cy + 62, fmt_short(out_max), "tiny", th["dim"], anchor="center")

        # ── DPS IN ─────────────────────────────────────────────
        in_max = nice_ceil(max(100.0, t.peak["dmg_in"] * 1.08))
        ix, iy = 304, 108
        self.gauge(ix, iy, 50, d["dmg_in"] / in_max, inc, 30, 7, t.peak["dmg_in"] / in_max)
        self.text(ix, iy - 4, fmt_int(d["dmg_in"]), "l", mix(inc, "#ffffff", 0.55), anchor="center")
        self.text(ix, iy + 18, self.spaced("DPS IN"), "tinyb", inc, anchor="center")
        self.text(ix, iy + 46, f"TOTAL {fmt_short(t.total['dmg_in'])}", "tiny", th["dim"], anchor="center")
        # Netto-HP-Fluss
        net = d["logi_in"] - d["dmg_in"]
        ncol = th["logi"] if net >= 0 else inc
        self.text(ix, 186, self.spaced("NET HP/S"), "tinyb", th["dim"], anchor="center")
        self.text(ix, 204, ("+" if net >= 0 else "-") + fmt_int(abs(net)), "m", ncol, anchor="center")

        # ── Signal / Waveform ──────────────────────────────────
        self.draw_wave(t, 12, 388, 285, 50)
        self.text(388, 222, f"SKALA {fmt_short(self.wave_max)}", "tiny", th["dim"], anchor="se")
        if not self.demo and (not self.watcher.ok or not self.watcher.chars):
            msg = ("Gamelogs-Ordner nicht gefunden\nRechtsklick › Log-Ordner wählen"
                   if not self.watcher.ok else "Warte auf EVE-Login …\n(Logs der letzten 24h werden erkannt)")
            self.text(200, 285, msg, "small", th["dim"], anchor="center")

        # Partikel (Schadenszahlen)
        alive = []
        for p in self.particles:
            age = now - p["t"]
            if age > p["life"]:
                continue
            alive.append(p)
            y = p["y"] - age * p["vy"]
            if y < 234 or y > 330:
                continue
            fade = age / p["life"]
            self.text(p["x"], y, p["txt"], p["font"], mix(p["col"], panel, fade ** 1.6), anchor="center")
        self.particles = alive

        # ── Stat-Kacheln ───────────────────────────────────────
        cells = [
            ("REP OUT", "logi_out", th["logi"], "HP/s"), ("CAP OUT", "cap_out", th["cap"], "GJ/s"),
            ("NEUT OUT", "neut_out", th["neut"], "GJ/s"),
            ("REP IN", "logi_in", th["logi"], "HP/s"), ("CAP IN", "cap_in", th["cap"], "GJ/s"),
            ("NEUT IN", "neut_in", th["neut"], "GJ/s"),
        ]
        cw = 376 / 3
        for i, (lab, k, col, unit) in enumerate(cells):
            row, colm = divmod(i, 3)
            x = 12 + colm * cw
            y = 360 + row * 34
            v = d[k]
            active = v > 0.5
            lc = col if active else th["dim"]
            self.text(x + 6, y + 3, lab, "tinyb", lc)
            self.text(x + cw - 8, y + 1, fmt_int(v), "ms", th["text"] if active else mix(th["dim"], panel, 0.3), anchor="ne")
            self.text(x + 6, y + 15, unit, "tiny", mix(th["dim"], panel, 0.35))
            pk = max(1.0, t.peak[k])
            bw = (cw - 14) * min(1.0, v / pk)
            self.rect(x + 6, y + 27, x + cw - 8, y + 29, mix(col, panel, 0.85))
            if bw > 0.5:
                self.rect(x + 6, y + 27, x + 6 + bw, y + 29, col)

        # ── Hit-Profil ─────────────────────────────────────────
        shots = t.hits_out + t.miss_out
        hr = (t.hits_out / shots * 100) if shots else 0
        mx = f"  ·  MAX HIT {fmt_int(t.biggest[0])}" if t.biggest else ""
        self.text(388 - 4, 433, f"TREFFER {hr:.0f}%  ·  {t.hits_out}/{shots}{mx}", "tinyb",
                  th["text"] if shots else th["dim"], anchor="ne")
        q = t.quality
        qtotal = sum(q.values())
        bx1, bx2, by = 16, 384, 452
        if qtotal:
            items = sorted(q.items(), key=lambda kv: (QUALITY_RANK.get(kv[0].lower(), 10), -kv[1]))
            x = bx1
            legend = []
            for idx, (name, cnt) in enumerate(items):
                rank = QUALITY_RANK.get(name.lower(), min(5, idx))
                col = mix(th["hi"], mix(acc, panel, 0.6), rank / 5.0)
                w = (bx2 - bx1) * cnt / qtotal
                if w >= 1:
                    self.rect(x, by, x + w - 1, by + 10, col)
                x += w
                short = QUALITY_SHORT.get(name.lower(), name[:3].upper())
                legend.append((short, cnt / qtotal * 100, col))
            lx = bx1
            for short, pct, col in legend[:6]:
                self.text(lx, by + 15, f"{short} {pct:.0f}%", "tiny", col)
                lx += 61
        else:
            self.rect(bx1, by, bx2, by + 10, mix(acc, panel, 0.88))
            self.text(bx1, by + 15, "noch keine Treffer", "tiny", th["dim"])

        self.bottom_panel(t, now)

        # ── Fußzeile ───────────────────────────────────────────
        self.text(14, 614, f"FENSTER {self.cfg['window']}s", "tinyb", th["dim"], anchor="w")
        if t.enc:
            e = t.enc
            dur = max(1.0, now - e["start"])
            s = f"KAMPF {fmt_time(dur)} · {fmt_short(e['dmg_out'])} · Ø {fmt_int(e['dmg_out'] / dur)}"
            self.text(200, 614, s, "tinyb", acc, anchor="center")
        elif t.last_enc:
            e = t.last_enc
            s = f"LETZTER {fmt_time(e['dur'])} · {fmt_short(e['dmg_out'])} · Ø {fmt_int(e['dmg_out'] / e['dur'])}"
            self.text(200, 614, s, "tiny", th["text"], anchor="center")
        self.text(BASE_W - 22, 614, "RESET", "tinyb", th["dim"], anchor="e")

    def draw_compact(self, t, now):
        th = self.th
        acc, inc, panel = th["acc"], th["inc"], th["panel"]
        d = self.disp
        y0 = HEADER_H
        # OUT
        out_max = nice_ceil(max(150.0, t.peak["dmg_out"] * 1.08))
        self.text(16, y0 + 8, self.spaced("DPS OUT"), "tinyb", acc)
        self.text(16, y0 + 20, fmt_int(d["dmg_out"]), "l", th["hi"])
        segs = 22
        lit = int(round(min(1.0, d["dmg_out"] / out_max) * segs))
        for i in range(segs):
            x = 16 + i * 6
            self.rect(x, y0 + 56, x + 4, y0 + 62, acc if i < lit else mix(acc, panel, 0.85))
        self.text(16, y0 + 70, f"PEAK {fmt_int(t.peak['dmg_out'])} · TOTAL {fmt_short(t.total['dmg_out'])}",
                  "tiny", th["dim"])
        # IN
        self.text(168, y0 + 8, self.spaced("DPS IN"), "tinyb", inc)
        self.text(168, y0 + 20, fmt_int(d["dmg_in"]), "l", mix(inc, "#ffffff", 0.5))
        self.text(168, y0 + 56, f"REP IN  {fmt_int(d['logi_in'])}", "tinyb", th["logi"] if d["logi_in"] > 0.5 else th["dim"])
        self.text(168, y0 + 70, f"REP OUT {fmt_int(d['logi_out'])}", "tinyb", th["logi"] if d["logi_out"] > 0.5 else th["dim"])
        # Mini-Signal
        x1, x2, mid, amp = 262, 386, y0 + 46, 30
        hist = list(t.history)[-120:]
        hmax = max(60.0, max(max(h[0], h[1]) for h in hist) * 1.15)
        step = (x2 - x1) / (len(hist) - 1)
        top = []
        bot = []
        for i, h in enumerate(hist):
            x = x1 + i * step
            top += [x, mid - min(1.0, h[0] / hmax) * amp]
            bot += [x, mid + min(1.0, h[1] / hmax) * amp]
        self.line((x1, mid, x2, mid), th["line"], 1)
        self.poly([x1, mid] + top + [x2, mid], mix(acc, panel, 0.8))
        self.poly([x1, mid] + bot + [x2, mid], mix(inc, panel, 0.8))
        self.glow_line(top, acc, 1.2)
        self.glow_line(bot, inc, 1.0)

    # ── Hauptschleife ──────────────────────────────────────────────
    def frame(self):
        now = time.time()
        try:
            self.step(now)
            self.draw(now)
            if self.fleet_win:
                self.fleet_win.draw(now)
        except Exception as e:  # Overlay darf nie abstürzen
            sys.stderr.write(f"[voidmeter] {e!r}\n")
        self.root.after(33, self.frame)

    def step(self, now):
        if self.hotkey and self.hotkey.pressed:
            self.hotkey.pressed = False
            self.toggle_lock()

        if self.demo:
            if now - self.last_poll > 0.1:
                self.last_poll = now
                p = LineParser("english")
                self.active = "Demo Pilot"
                for name, gen in [("Demo Pilot", self.demo)] + list(self.demo_bots.items()):
                    t = self.tracker(name)
                    for l in gen.lines(now):
                        for ev in p.parse(l):
                            t.add(ev, now)
        else:
            if now - self.last_scan > 2.5:
                self.last_scan = now
                self.watcher.scan()
            if now - self.last_poll > 0.15:
                self.last_poll = now
                for char, evs in self.watcher.poll():
                    t = self.tracker(char)
                    for ev in evs:
                        t.add(ev, now)
            self.pick_active(now)

        for char, t in self.trackers.items():
            closed = t.update(now)
            if closed:
                self.log_encounter(char, closed)
        if now - self.last_sample >= HIST_STEP:
            self.last_sample = now
            for t in self.trackers.values():
                t.sample()

        if now - self.last_fleet >= 1.0:
            self.last_fleet = now
            self.update_fleet(now)
        for p in self.fleet:
            d = self.fdisp.setdefault(p["n"], {})
            for k in ("o", "i", "ri", "ro"):
                cur = d.get(k, 0.0)
                tgt = p.get(k, 0.0)
                d[k] = cur + (tgt - cur) * 0.15 if abs(tgt - cur) > 0.05 else tgt

        t = self.trackers.get(self.active)
        if t and t.fresh:
            if self.cfg.get("particles", True) and self.cfg.get("tab") != "fleet":
                if self.layout == "COCKPIT" and not self.cfg["compact"]:
                    self.spawn_particles(t.fresh, now)
                elif self.layout == "ORBIT":
                    self.spawn_sparks(t.fresh, now)
        for other in self.trackers.values():
            other.fresh = []

        if now - self.last_save > 10:
            self.last_save = now
            self.store_pos()

    # ── Flotte ─────────────────────────────────────────────────────
    def local_pilots(self, now):
        out = []
        for name, t in self.trackers.items():
            if not self.demo and name not in self.watcher.chars:
                continue
            r = t.rate
            h = list(t.history)[3::4]
            out.append({
                "n": name, "o": round(r["dmg_out"], 1), "i": round(r["dmg_in"], 1),
                "ri": round(r["logi_in"], 1), "ro": round(r["logi_out"], 1),
                "to": int(t.total["dmg_out"]), "ti": int(t.total["dmg_in"]),
                "pk": round(t.peak["dmg_out"], 1), "h": [round(x[0], 1) for x in h],
                "l": (now - t.last_event) < 6,
                "tg": t.by_target.most_common(1)[0][0] if t.by_target else "",
            })
        # Chars aus Logs ohne Tracker (noch nie gekämpft) trotzdem zeigen
        if not self.demo:
            for name in self.watcher.chars:
                if name not in self.trackers:
                    out.append({"n": name, "o": 0, "i": 0, "ri": 0, "ro": 0, "to": 0, "ti": 0,
                                "pk": 0, "h": [], "l": False, "tg": ""})
        return out

    def update_fleet(self, now):
        local = self.local_pilots(now)
        share = [p for p in local if p["n"] not in self.demo_bots]
        remote = []
        if self.net:
            self.net.set_local(share)
            remote, self.net_status, self.net_ok = self.net.snapshot()
        fleet = []
        names = set()
        for p in local:
            q = dict(p)
            q["src"] = "net" if p["n"] in self.demo_bots else "local"
            fleet.append(q)
            names.add(p["n"])
        for p in remote:
            if p["n"] not in names:
                q = dict(p)
                q["src"] = "net"
                fleet.append(q)
                names.add(p["n"])
        self.fleet = fleet
        for n in list(self.fdisp):
            if n not in names:
                del self.fdisp[n]
        if self.active and self.active in names and self.active not in self.fleet_colors:
            # eigener aktiver Char bekommt immer die Theme-Farbe
            self.fleet_colors = {self.active: 0, **{k: v for k, v in self.fleet_colors.items()}}
        self.fleet_peak = max(self.fleet_peak, sum(p.get("o", 0) for p in fleet))

    def start_net(self, mode, host, port, code, quiet=False):
        self.stop_net(save=False)
        try:
            port = int(port)
        except (TypeError, ValueError):
            port = NET_PORT
        self.net = FleetNet(mode, host, port, code)
        self.net_status, self.net_ok = "VERBINDE …", False
        self.cfg.update(net_mode=mode, net_host=host, net_port=port, net_code=code)
        save_config(self.cfg)
        if not quiet:
            self.flash = ("FLOTTE GEHOSTET" if mode == "host" else "VERBINDE MIT FLOTTE …", time.time() + 3)

    def stop_net(self, save=True):
        if self.net:
            self.net.close()
        self.net = None
        self.net_status, self.net_ok = "", False
        if save:
            self.cfg["net_mode"] = None
            save_config(self.cfg)

    def open_fleet_window(self):
        if self.fleet_win:
            try:
                self.fleet_win.root.lift()
            except tk.TclError:
                pass
            return
        self.fleet_win = FleetWindow(self)
        self.cfg["fleet_window"] = True
        save_config(self.cfg)

    def toggle_fleet_window(self):
        if self.fleet_win:
            self.fleet_win.close()
        else:
            self.open_fleet_window()

    def open_net_dialog(self):
        if self._dlg is not None:
            try:
                self._dlg.lift()
                return
            except tk.TclError:
                self._dlg = None
        th = self.th
        bg, fg, ent = th["panel"], th["text"], mix(th["panel"], "#ffffff", 0.07)
        d = tk.Toplevel(self.root)
        self._dlg = d
        d.title("VOIDMETER · Flotte")
        d.configure(bg=bg, padx=18, pady=14)
        d.attributes("-topmost", True)
        d.resizable(False, False)
        f_ui = ("Bahnschrift" if "Bahnschrift" in tkfont.families(self.root) else "Segoe UI", 10)
        f_h = (f_ui[0], 13, "bold")

        mode = tk.StringVar(master=d, value=self.cfg.get("net_mode") or "client")
        host = tk.StringVar(master=d, value=self.cfg.get("net_host", ""))
        port = tk.StringVar(master=d, value=str(self.cfg.get("net_port", NET_PORT)))
        code = tk.StringVar(master=d, value=self.cfg.get("net_code", ""))

        def lab(t, **kw):
            return tk.Label(d, text=t, bg=bg, fg=kw.pop("fg", fg), font=kw.pop("font", f_ui), anchor="w",
                            justify="left", **kw)

        def entry(var, **kw):
            return tk.Entry(d, textvariable=var, bg=ent, fg=fg, insertbackground=fg, relief="flat",
                            font=f_ui, highlightthickness=1, highlightbackground=th["line"],
                            highlightcolor=th["acc"], **kw)

        lab("FLOTTEN-VERBINDUNG", font=f_h, fg=th["acc"]).grid(row=0, column=0, columnspan=2, sticky="w")
        lab("Alle in der Flotte nutzen denselben Code. Einer hostet (oder ihr nutzt\n"
            "ein Relay auf einem Server), die anderen verbinden sich dorthin.",
            fg=th["dim"]).grid(row=1, column=0, columnspan=2, sticky="w", pady=(2, 10))
        for i, (val, txt) in enumerate((("client", "Mit Host / Relay verbinden"),
                                        ("host", "Ich hoste die Flotte auf diesem PC"))):
            tk.Radiobutton(d, text=txt, variable=mode, value=val, bg=bg, fg=fg, selectcolor=ent,
                           activebackground=bg, activeforeground=th["acc"], font=f_ui, anchor="w",
                           highlightthickness=0).grid(row=2 + i, column=0, columnspan=2, sticky="w")
        lab("Adresse (IP / Domain)").grid(row=4, column=0, sticky="w", pady=(10, 0))
        e_host = entry(host, width=30)
        e_host.grid(row=5, column=0, columnspan=2, sticky="we", ipady=3)
        lab("Port").grid(row=6, column=0, sticky="w", pady=(8, 0))
        lab("Flotten-Code").grid(row=6, column=1, sticky="w", pady=(8, 0), padx=(10, 0))
        entry(port, width=8).grid(row=7, column=0, sticky="we", ipady=3)
        entry(code, width=18).grid(row=7, column=1, sticky="we", ipady=3, padx=(10, 0))
        status = lab("", fg=th["dim"])
        status.grid(row=8, column=0, columnspan=2, sticky="w", pady=(10, 0))

        def refresh_mode(*_):
            e_host.configure(state="disabled" if mode.get() == "host" else "normal")
            if mode.get() == "host":
                status.configure(text=f"Andere verbinden sich zu deiner IP, Port {port.get()}.\n"
                                      "Port im Router weiterleiten + Windows-Firewall erlauben.")
            else:
                status.configure(text="Adresse vom Host oder deinem Relay-Server eintragen.")
        mode.trace_add("write", refresh_mode)
        refresh_mode()

        def start():
            if mode.get() == "client" and not host.get().strip():
                status.configure(text="Bitte eine Adresse eintragen.", fg=th["inc"])
                return
            self.start_net(mode.get(), host.get().strip(), port.get().strip(), code.get().strip())
            self.set_tab("fleet")
            close()

        def disconnect():
            self.stop_net()
            self.flash = ("FLOTTE GETRENNT", time.time() + 2)
            close()

        def close():
            self._dlg = None
            d.destroy()

        bar = tk.Frame(d, bg=bg)
        bar.grid(row=9, column=0, columnspan=2, sticky="we", pady=(14, 0))
        bstyle = dict(relief="flat", font=(f_ui[0], 10, "bold"), padx=12, pady=4, bd=0, cursor="hand2")
        tk.Button(bar, text="STARTEN", command=start, bg=th["acc"], fg=th["bg"],
                  activebackground=th["hi"], **bstyle).pack(side="left")
        if self.net:
            tk.Button(bar, text="TRENNEN", command=disconnect, bg=mix(th["inc"], bg, 0.5), fg="#ffffff",
                      activebackground=th["inc"], **bstyle).pack(side="left", padx=8)
        tk.Button(bar, text="SCHLIESSEN", command=close, bg=ent, fg=fg, activebackground=th["line"],
                  **bstyle).pack(side="right")
        d.protocol("WM_DELETE_WINDOW", close)
        d.update_idletasks()
        x = self.root.winfo_rootx() + 20
        y = self.root.winfo_rooty() + int(60 * self.s)
        d.geometry(f"+{x}+{y}")

    def pick_active(self, now):
        want = self.cfg.get("character")
        chars = self.watcher.chars
        if want and want in chars:
            self.active = want
            return
        cur = self.trackers.get(self.active)
        if self.active in chars and cur and now - cur.last_event < 8:
            return
        best, bt = self.active, -1
        for name in chars:
            tr = self.trackers.get(name)
            le = tr.last_event if tr else 0
            if le > bt:
                best, bt = name, le
        if bt <= 0 and chars and self.active not in chars:
            best = max(chars, key=lambda n: chars[n]["mtime0"])
        self.active = best

    def spawn_particles(self, evs, now):
        th = self.th
        evs = sorted(evs, key=lambda e: -e.amount)[:4]
        for ev in evs:
            if len(self.particles) > 16:
                break
            out = ev.kind == "dmg_out"
            big = ev.quality.lower() == "wrecks" if ev.quality else False
            frac = min(1.0, ev.amount / max(1.0, self.wave_max * 2))
            self.particles.append({
                "t": now, "life": 1.6 if not big else 2.2,
                "x": 388 - random.uniform(26, 120),
                "y": (285 - 10 - frac * 34) if out else (285 + 18 + frac * 26),
                "vy": random.uniform(14, 22) if out else -random.uniform(8, 14),
                "txt": fmt_int(ev.amount) + ("!" if big else ""),
                "col": (th["hi"] if big else th["acc"]) if out else mix(th["inc"], "#ffffff", 0.3),
                "font": "monob" if not big else "smallb",
            })

    def log_encounter(self, char, e):
        dur = e["dur"]
        if char == self.active:
            self.flash = (f"KAMPF GESPEICHERT · {fmt_time(dur)} · Ø {fmt_int(e['dmg_out'] / dur)} DPS",
                          time.time() + 4)
        if self.demo:
            return
        try:
            path = os.path.join(config_dir(), "encounters.csv")
            new = not os.path.exists(path)
            with open(path, "a", newline="", encoding="utf-8") as f:
                w = csv.writer(f, delimiter=";")
                if new:
                    w.writerow(["datum", "charakter", "dauer_s", "schaden_raus", "dps_avg", "dps_peak",
                                "schaden_rein", "dps_rein_avg", "dps_rein_peak", "reps_erhalten"])
                w.writerow([datetime.datetime.fromtimestamp(e["start"]).strftime("%Y-%m-%d %H:%M:%S"),
                            char, int(dur), e["dmg_out"], round(e["dmg_out"] / dur, 1), round(e["peak"], 1),
                            e["dmg_in"], round(e["dmg_in"] / dur, 1), round(e["peak_in"], 1), e["logi_in"]])
        except Exception:
            pass

    # ── Interaktion ────────────────────────────────────────────────
    def on_double(self, e):
        if e.y < HEADER_H * self.s and not self.hit_button(e.x, e.y):
            self.toggle_compact()

    def menu_at_button(self):
        self.show_menu(self.root.winfo_rootx() + int((BASE_W - 60) * self.s),
                       self.root.winfo_rooty() + int(HEADER_H * self.s))

    def _v(self, val):
        v = tk.IntVar(master=self.root, value=val)
        self._menu_vars.append(v)
        return v

    def show_menu(self, x, y):
        self._menu_vars = []
        th = self.th
        opts = dict(tearoff=0, bg=th["panel"], fg=th["text"], activebackground=mix(th["acc"], th["panel"], 0.6),
                    activeforeground="#ffffff", bd=0)
        m = tk.Menu(self.root, **opts)

        cm = tk.Menu(m, **opts)
        cur = self.cfg.get("character")
        cm.add_radiobutton(label="AUTO (folgt dem Kampf)", command=lambda: self.set_char(None),
                           value=1, variable=self._v(1 if cur is None else 0))
        for name in sorted(self.watcher.chars):
            cm.add_radiobutton(label=name, command=lambda n=name: self.set_char(n),
                               value=1, variable=self._v(1 if cur == name else 0))
        if not self.watcher.chars:
            cm.add_command(label="(keine Logs der letzten 24h)", state="disabled")
        m.add_cascade(label="Charakter", menu=cm)

        fm = tk.Menu(m, **opts)
        fm.add_command(label=("Solo-Tab zeigen" if self.cfg.get("tab") == "fleet" else "Flotten-Tab zeigen"),
                       command=lambda: self.set_tab("solo" if self.cfg.get("tab") == "fleet" else "fleet"))
        fm.add_command(label=("Flotten-Fenster schließen" if self.fleet_win else "Flotte als eigenes Fenster"),
                       command=self.toggle_fleet_window)
        fm.add_separator()
        fm.add_command(label="Verbindung einrichten …", command=self.open_net_dialog)
        if self.net:
            fm.add_command(label="Flotte trennen", command=self.stop_net)
            fm.add_command(label=self.net_status or "…", state="disabled")
        m.add_cascade(label="Flotte", menu=fm)

        wm = tk.Menu(m, **opts)
        for w in (5, 10, 15, 20, 30, 60):
            wm.add_radiobutton(label=f"{w} Sekunden", value=1, variable=self._v(int(w == self.cfg["window"])),
                               command=lambda w=w: self.set_window(w))
        m.add_cascade(label="DPS-Fenster", menu=wm)

        lm = tk.Menu(m, **opts)
        for name, d in LAYOUTS.items():
            lm.add_radiobutton(label=d["label"], value=1, variable=self._v(int(name == self.cfg.get("layout", "COCKPIT"))),
                               command=lambda n=name: self.set_layout(n))
        m.add_cascade(label="Layout (Anordnung)", menu=lm)

        dm = tk.Menu(m, **opts)
        for name, d in DESIGNS.items():
            dm.add_radiobutton(label=d["label"], value=1, variable=self._v(int(name == self.cfg.get("design", "HUD"))),
                               command=lambda n=name: self.set_design(n))
        m.add_cascade(label="Stil (Effekte)", menu=dm)

        tm = tk.Menu(m, **opts)
        for name in THEMES:
            tm.add_radiobutton(label=name.title(), value=1, variable=self._v(int(name == self.cfg["theme"])),
                               command=lambda n=name: self.set_theme(n))
        m.add_cascade(label="Farben", menu=tm)

        om = tk.Menu(m, **opts)
        for a in (1.0, 0.94, 0.85, 0.75, 0.6, 0.45):
            om.add_radiobutton(label=f"{int(a * 100)} %", value=1,
                               variable=self._v(int(abs(a - self.cfg["alpha"]) < 0.01)),
                               command=lambda a=a: self.set_alpha(a))
        m.add_cascade(label="Deckkraft", menu=om)

        sm = tk.Menu(m, **opts)
        for sc in (0.75, 0.9, 1.0, 1.15, 1.3, 1.5, 1.75, 2.0):
            sm.add_radiobutton(label=f"{int(sc * 100)} %", value=1,
                               variable=self._v(int(abs(sc - self.s) < 0.01)),
                               command=lambda sc=sc: self.set_scale(sc))
        m.add_cascade(label="Größe", menu=sm)

        m.add_separator()
        m.add_command(label=("Kompakt aus" if self.cfg["compact"] else "Kompakt-Modus"), command=self.toggle_compact)
        m.add_command(label=("Schadenszahlen aus" if self.cfg.get("particles", True) else "Schadenszahlen an"),
                      command=self.toggle_particles)
        m.add_command(label="Click-Through-Lock  (Strg+Shift+F9)", command=self.toggle_lock,
                      state="normal" if IS_WIN else "disabled")
        m.add_separator()
        m.add_command(label="Session zurücksetzen", command=self.reset_active)
        m.add_command(label=("Demo beenden" if self.demo else "Demo-Kampf starten"), command=self.toggle_demo)
        m.add_command(label="Log-Ordner wählen …", command=self.choose_dir)
        m.add_command(label="Kampf-Log (CSV) öffnen", command=self.open_csv)
        m.add_separator()
        m.add_command(label="Beenden", command=self.quit)
        try:
            m.tk_popup(x, y)
        finally:
            m.grab_release()

    # ── Aktionen ───────────────────────────────────────────────────
    def set_tab(self, tab):
        self.cfg["tab"] = tab
        self.particles = []
        save_config(self.cfg)
        self.apply_layout()

    def set_char(self, n):
        self.cfg["character"] = n
        if n:
            self.active = n
        save_config(self.cfg)

    def set_window(self, w):
        self.cfg["window"] = w
        for t in self.trackers.values():
            t.window = w
        save_config(self.cfg)

    def cycle_window(self):
        opts = [5, 10, 15, 20, 30, 60]
        i = opts.index(self.cfg["window"]) if self.cfg["window"] in opts else 1
        self.set_window(opts[(i + 1) % len(opts)])

    def set_theme(self, n):
        self.cfg["theme"] = n
        save_config(self.cfg)
        self.draw_static()
        if self.fleet_win:
            self.fleet_win.draw_static()

    def set_design(self, n):
        self.cfg["design"] = n
        self.cfg["theme"] = DESIGNS[n]["theme"]
        save_config(self.cfg)
        self.apply_layout()

    def set_alpha(self, a):
        self.cfg["alpha"] = a
        save_config(self.cfg)
        self.apply_layout()

    def set_scale(self, sc):
        self.cfg["scale"] = sc
        save_config(self.cfg)
        self.apply_layout()

    def toggle_compact(self):
        self.cfg["compact"] = not self.cfg["compact"]
        save_config(self.cfg)
        self.apply_layout()

    def toggle_particles(self):
        self.cfg["particles"] = not self.cfg.get("particles", True)
        self.particles = []
        save_config(self.cfg)

    def toggle_right_list(self):
        self.cfg["right_list"] = "targets" if self.cfg.get("right_list") == "sources" else "sources"
        save_config(self.cfg)

    def toggle_lock(self):
        if not IS_WIN:
            return
        self.locked = not self.locked
        win_set_clickthrough(self.root, self.locked)
        if self.fleet_win:
            win_set_clickthrough(self.fleet_win.root, self.locked)
        self.flash = ("LOCK AN · Strg+Shift+F9 zum Lösen" if self.locked else "LOCK AUS", time.time() + 3)

    def start_demo(self):
        self.demo = Demo()
        self.demo_bots = {n: Demo(**kw) for n, kw in DEMO_BOTS.items()}

    def toggle_demo(self):
        if self.demo:
            self.demo, self.demo_bots = None, {}
        else:
            self.start_demo()
        self.trackers.clear()
        self.particles = []
        self.active = None
        self.fleet, self.fdisp, self.fleet_colors, self.fleet_peak = [], {}, {}, 0.0

    def reset_active(self):
        t = self.trackers.get(self.active)
        if t:
            t.reset()
        self.particles = []
        self.fleet_peak = 0.0
        self.flash = ("SESSION ZURÜCKGESETZT", time.time() + 2)

    def choose_dir(self):
        d = filedialog.askdirectory(title="EVE Gamelogs-Ordner wählen", initialdir=self.log_dir)
        if d:
            self.cfg["log_dir"] = d
            self.log_dir = d
            self.watcher = LogWatcher(d)
            self.trackers.clear()
            self.active = None
            save_config(self.cfg)

    def open_csv(self):
        path = os.path.join(config_dir(), "encounters.csv")
        target = path if os.path.exists(path) else config_dir()
        try:
            if IS_WIN:
                os.startfile(target)
            elif sys.platform == "darwin":
                os.system(f'open "{target}"')
            else:
                os.system(f'xdg-open "{target}" >/dev/null 2>&1 &')
        except Exception:
            pass

    def store_pos(self):
        try:
            self.cfg["x"], self.cfg["y"] = self.root.winfo_x(), self.root.winfo_y()
            if self.fleet_win:
                self.cfg["fleet_x"], self.cfg["fleet_y"] = self.fleet_win.root.winfo_x(), self.fleet_win.root.winfo_y()
        except tk.TclError:
            pass
        save_config(self.cfg)

    def quit(self):
        self.store_pos()
        if self.net:
            self.net.close()
        self.root.destroy()

    def run(self):
        if self.snap:
            path, secs = self.snap
            self.root.after(int(float(secs) * 1000), lambda: self._snap(path))
        self.root.mainloop()

    def _snap(self, path):
        import subprocess
        self.root.update()
        subprocess.run(["import", "-window", "root", path])
        self.quit()


def main():
    ap = argparse.ArgumentParser(description="VOIDMETER – Live-DPS-Overlay für EVE Online")
    ap.add_argument("--demo", action="store_true", help="simulierten Kampf anzeigen")
    ap.add_argument("--logdir", help="Pfad zum EVE-Gamelogs-Ordner")
    ap.add_argument("--relay", action="store_true",
                    help="nur Flotten-Relay-Server starten (ohne Fenster, z. B. auf einem VPS)")
    ap.add_argument("--port", type=int, default=NET_PORT, help=f"Relay-Port (Standard {NET_PORT})")
    ap.add_argument("--code", default="", help="Flotten-Code für das Relay")
    ap.add_argument("--snap", nargs=2, metavar=("PNG", "SEK"), help=argparse.SUPPRESS)
    args = ap.parse_args()
    if args.relay:
        port = int(os.environ.get("SERVER_PORT", args.port))
        run_relay(port, os.environ.get("VOIDMETER_CODE", args.code))
        return
    if tk is None:
        print("tkinter fehlt – bitte Python von python.org installieren.")
        sys.exit(1)
    App(args).run()


if __name__ == "__main__":
    main()
