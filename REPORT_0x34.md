# DualSense BT Report 0x34 — Haptic Audio

Reverse-engineered aus DSX Captures via Frida (2026-03-20/21).

## Übersicht

547 Bytes, gesendet via `WriteFile` auf das BT HID Device.
Windows BT HID Stack fragmentiert/übersetzt automatisch.
Report ID 0x34 im BT HID Descriptor: 269 Bytes Payload + 1 Byte ID = 270.
Die 547 Bytes entsprechen Report 0x39 (546 Payload + 1 ID) — unklar ob
Windows intern auf 0x39 mappt oder ob die 547 Bytes direkt als 0x34+Padding gehen.

## Byte Map

```
Offset   Bytes  Inhalt                              Status
──────────────────────────────────────────────────────────────
  0        1    0x34                                 Verifiziert (Report ID)
  1        1    Sequenz (+0x20 pro Paket, wraps)     Verifiziert
  2-4      3    91 07 fe                             UNKLAR — Flags? Modus?
                                                     Was passiert bei anderen Werten?
  5-9      5    30 30 30 30 30                       UNKLAR — könnte zu 2-4 gehören
                                                     (zusammen 8 Bytes Flags/Config?)
                                                     ASCII "00000" — Zufall?
 10        1    Timestamp (inkrementiert +2, nur     Verifiziert (variabel)
                ungerade Werte in Captures)
 11-12     2    d2 40                                UNKLAR — immer konstant.
                                                     Teil des Timestamps? Wenn ja,
                                                     warum fix? Oder Config-Bytes?
 13-138  126    AUDIO: signed int8, stereo           Verifiziert
                [L, R, L, R, ...] = 63 Frames
139-150   12    Control-Flags                        Verifiziert (= 0x32 Bytes 2-13)
                90 3f fd f7 00 00 7e 7f ff 09 00 0f
151-177   27    Nullen                               Verifiziert
178-184    7    Control-Data                         Verifiziert (= 0x32 Bytes 41-47)
                0a 07 00 00 02 00 05
185-187    3    Variabel (ändert sich pro Paket)     UNKLAR — Adaptive Triggers?
                                                     Checksum? Zähler?
188-265   78    Nullen                               Verifiziert
266-269    4    CRC32 (seed 0xA2, über Bytes 0-265,  Verifiziert (mathematisch)
                Little Endian)
270-546  277    Nullen                               Verifiziert
```

## Report 0x32 (Control-only) zum Vergleich

```
Offset   Bytes  Inhalt                              Entspricht in 0x34
──────────────────────────────────────────────────────────────
  0        1    0x32 (Report ID)                     —
  1        1    Sequenz (+0x10 pro Paket)            Byte 1 (aber +0x20)
  2-13    12    Control-Flags                        Bytes 139-150
 14-40    27    Nullen                               Bytes 151-177
 41-47     7    Control-Data                         Bytes 178-184
 48-50     3    Variabel                             Bytes 185-187
 51-137   87    Nullen                               Bytes 188-265 (teilweise)
138-141    4    CRC32 (seed 0xA2, Bytes 0-137, LE)   Bytes 266-269 (eigener CRC)
142-546  405    Nullen                               Bytes 270-546
```

## Offene Fragen

1. **Bytes 2-9 (Flags/Config):** Was bedeuten `91 07 fe 30 30 30 30 30`?
   Sind das 8 Bytes Config/Modus? Was passiert bei anderen Werten?
   `30` = ASCII `0` — ist das ein String-Parameter?

2. **Bytes 11-12 (d2 40):** Immer konstant über alle Captures hinweg.
   Wenn Byte 10 ein Timestamp ist, warum sind 11-12 fix?
   Oder ist 10-12 gar kein Timestamp, sondern Byte 10 alleine eine
   Art Frame-Counter und 11-12 sind Config?

3. **Bytes 185-187:** Ändern sich pro Paket, aber nicht Audio.
   Könnten Adaptive Trigger Werte sein, eine fortlaufende Checksumme,
   oder einfach Noise von DSX's Controller-State.

4. **Sequenz-Inkrement:** 0x34 nutzt +0x20, 0x32 nutzt +0x10.
   DSX alterniert 0x32 und 0x34 — teilen sie sich einen gemeinsamen
   Sequenz-Counter? (Würde erklären warum 0x34 +0x20 springt wenn
   dazwischen ein 0x32 mit +0x10 kommt.)

## Audio-Format

- **126 Bytes** pro Paket = 63 Stereo-Frames
- **Signed int8** (Wertebereich -128..+127, zentriert um 0)
- **Stereo interleaved:** [Left, Right, Left, Right, ...]
- **Effektive Sample Rate:** 63 Frames × ~33 Pakete/Sek ≈ **2100 Hz**
- **Senderate:** ~30ms Intervall (33 Hz)

## CRC32

- Algorithmus: Standard CRC32 mit Seed-Byte `0xA2` prepended
- `crc32(bytes([0xA2]) + report[0:266])` → Little Endian in Bytes 266-269
- Identischer Algorithmus wie BT Output Report 0x31

## Getestet & Verifiziert

- [x] Replay von captured DSX-Reports funktioniert
- [x] Eigene Audio-Daten (Sine, WAV) in Template injiziert → funktioniert
- [x] CRC mathematisch verifiziert über 274 Pakete aus 4 Captures
- [x] Audio-Region durch DC-Pattern (7f/40/00) eindeutig lokalisiert
- [x] Stereo-Zuordnung: Even=Left, Odd=Right (verifiziert mit L/R-only WAVs)
- [x] Bytes 1-11 müssen korrekte Werte haben (Binary Search Test)
- [x] Byte 12 kann genullt werden ohne Funktionsverlust
