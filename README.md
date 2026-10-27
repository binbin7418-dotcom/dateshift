# DateShift — Photo Date Fixer: Repair Wrong EXIF Dates, File Dates & Dual-Camera Sync

Your camera clock was wrong, you forgot to change time zones on a trip,
or two cameras at the same wedding never agreed on the time. DateShift
diagnoses every photo's three date sources — EXIF time, file time, and
any date in the file name — then repairs them with a relative offset
(Date Maths: every photo keeps its relative spacing), optionally syncs
all three sources, and proves the result with a re-check report. It only
ever writes copies: **your originals are never modified**.

## What it does
- **Diagnosis report** — per photo: EXIF DateTimeOriginal / Digitized /
  DateTime, OffsetTime tags, file modification time, filename date, and
  the spread between sources (a conflict is flagged when sources differ
  by more than 1 second).
- **Date Maths shift** — enter a signed offset in seconds (e.g. −20220
  for a clock that ran 5 h 37 m fast). EXIF times and file times move by
  exactly that amount, so the sequence stays in order.
- **Sync all three sources** — one checkbox writes the same corrected
  time into EXIF, file time, and (via the report) the filename-date
  reading, so photo apps stop showing three different dates.
- **Filename fallback** — a photo with no EXIF but a dated filename
  (IMG_20230610_091530, 2023-06-10_09-15-30, and similar patterns) gets
  its date from the filename, written into EXIF and labelled `filename`
  in the report.
- **Scanned old photos** — no EXIF and no dated filename? The file time
  is still shifted and the report labels the source `file_only`; nothing
  is invented.
- **Dual-camera sync** — pick one same-moment photo from each camera.
  DateShift computes the offset (camera A time minus camera B time) and
  applies it to the whole second-camera folder, so two shooters' photos
  interleave correctly.
- **Re-check report** — every output copy is re-read: EXIF vs file time
  must agree within 1 second (sync mode) or the file is flagged, not
  passed. Pixel data is never touched.

## Verified behaviour (seeded acceptance, measured 2026-10-27)
Run: `python dev/test_acceptance.py` — **41/41 checks passed**.
- Clock-fast group (5 JPEGs, clock fast by 5 h 37 m = 20220 s): after
  shifting by −20220 s, EXIF error 0.000 s and file-time error 0.000 s
  against ground truth on all 5 files; re-check 5/5 green.
- Filename fallback: JPEG with no EXIF and filename IMG_20230610_091530
  got EXIF 2023-06-10 09:15:30 written from the filename, source
  labelled `filename`.
- Scanned photo: no EXIF, no dated filename — file time shifted from
  1998-05-02 12:00:00 to 13:00:00 exactly (error 0.000 s), source
  labelled `file_only`.
- Dual-camera: A correct, B slow by 83 s — computed offset 83.000 s;
  all 3 B-folder photos aligned to A times with error 0.000 s.
- Pixel safety: decoded-pixel hash unchanged on all 10 processed files;
  all 12 input files byte-identical (SHA-256) before and after.
- Speed: the 5-file clock-fast shift ran in 0.01 s on the test machine.
Full log: TEST-RESULTS.txt. Example report: sample_report.md.

## Run from source
```
python dateshift.py scan  photos_folder
python dateshift.py shift photos_folder --seconds -20220 --out fixed_folder
python dateshift.py dual --pair-a A.jpg --pair-b B.jpg --folder B_folder --out fixed_B
```
Dependencies: piexif, Pillow (`pip install -r requirements.txt`). The
Windows app (`gui.py`, packaged with PyInstaller) wraps the same engine.

## Scope & honest limits
- JPEG and TIFF: full EXIF read and write via piexif.
- HEIC: diagnosis plus file-time / filename handling only. EXIF write
  is reported as unsupported in the report — DateShift never claims a
  HEIC EXIF write it did not perform.
- RAW formats (CR2, NEF, ARW, DNG, ORF, RW2, RAF, PEF, SRW and others):
  reported as unsupported and skipped, never silently processed. A
  future version may add XMP sidecars; v1 does not promise it.
- Filename dates are parsed only from recognised patterns (IMG_YYYYMMDD
  _HHMMSS, YYYY-MM-DD_HH-MM-SS, YYYYMMDD_HHMMSS, YYYY-MM-DD, YYYYMMDD);
  an unrecognised name is treated as undated, not guessed.
- Shifting moves timestamps; it cannot recover a date nobody recorded
  anywhere (no EXIF, no filename date, and a wrong file time). In that
  case the report says so and the file time is still shifted, labelled
  `file_only`.
- DateShift writes copies to a new output folder. If you want the
  corrected dates in your library, replace the originals yourself after
  reading the re-check report — the copies are the verified result.
