#!/usr/bin/env python3
"""DateShift engine — diagnose and repair photo date/time metadata.

Reads, for each photo, three independent date sources: EXIF timestamps
(DateTimeOriginal / DateTimeDigitized / DateTime, plus OffsetTime tags),
the file system modification time, and any date embedded in the file
name. Repairs are COPY-ONLY: fixed copies are written to an output
folder and the original files are never modified.

Scope (honest, by design):
- JPEG and TIFF: full EXIF read and write via piexif.
- HEIC: diagnosis plus file-time / filename handling only. EXIF write
  is reported as unsupported, never silently faked.
- RAW formats (CR2, NEF, ARW, DNG, ORF, RW2, RAF, PEF, SRW, ...):
  reported as unsupported and never processed.

Everything runs locally. No API, no upload, no account.
"""
import argparse
import hashlib
import os
import re
import shutil
import sys
import time
from datetime import datetime, timedelta

try:
    import piexif
except ImportError:  # pragma: no cover
    piexif = None

EXIF_FMT = "%Y:%m:%d %H:%M:%S"

JPEG_EXTS = {".jpg", ".jpeg"}
TIFF_EXTS = {".tif", ".tiff"}
HEIC_EXTS = {".heic", ".heif"}
RAW_EXTS = {".cr2", ".nef", ".arw", ".dng", ".orf", ".rw2", ".raf",
            ".pef", ".srw", ".cr3", ".crw", ".nrw", ".x3f"}
IMAGE_EXTS = JPEG_EXTS | TIFF_EXTS | HEIC_EXTS | RAW_EXTS

# ---------------------------------------------------------------- dates


def fmt(dt):
    return dt.strftime(EXIF_FMT) if dt else "—"


def parse_exif_dt(s):
    if not s:
        return None
    if isinstance(s, bytes):
        s = s.decode("utf-8", "replace")
    s = s.strip()
    try:
        return datetime.strptime(s, EXIF_FMT)
    except ValueError:
        return None


def encode_exif_dt(dt):
    return dt.strftime(EXIF_FMT).encode("utf-8")


# Filename date patterns, most specific first.
_RE_14DIGIT = re.compile(r"(?<!\d)(\d{4})(\d{2})(\d{2})(\d{2})(\d{2})(\d{2})(?!\d)")
_RE_DATETIME = re.compile(
    r"(?<!\d)(\d{4})[-_]?(\d{2})[-_]?(\d{2})[ _\-T]"
    r"(\d{2})[-_:]?(\d{2})(?:[-_:]?(\d{2}))?(?!\d)")
_RE_DATE_DASH = re.compile(r"(?<!\d)(\d{4})-(\d{2})-(\d{2})(?!\d)")
_RE_DATE_PLAIN = re.compile(r"(?<!\d)(\d{4})(\d{2})(\d{2})(?!\d)")


def _mk(y, mo, d, h=0, mi=0, s=0):
    try:
        return datetime(int(y), int(mo), int(d), int(h), int(mi), int(s))
    except ValueError:
        return None


def parse_filename_date(name):
    """Return a datetime parsed from the file name, or None."""
    m = _RE_14DIGIT.search(name)
    if m:
        dt = _mk(*m.groups())
        if dt:
            return dt
    m = _RE_DATETIME.search(name)
    if m:
        g = m.groups()
        dt = _mk(g[0], g[1], g[2], g[3], g[4], g[5] or 0)
        if dt:
            return dt
    m = _RE_DATE_DASH.search(name)
    if m:
        dt = _mk(*m.groups())
        if dt:
            return dt
    m = _RE_DATE_PLAIN.search(name)
    if m:
        dt = _mk(*m.groups())
        if dt:
            return dt
    return None


# ---------------------------------------------------------------- EXIF


def read_exif(path):
    """Return (exif_dict_or_None, error_or_None). None dict = no EXIF."""
    if piexif is None:
        return None, "piexif not installed"
    try:
        return piexif.load(path), None
    except Exception as e:  # no EXIF segment, or unreadable
        return None, str(e)


def exif_times(exif):
    """Extract the three EXIF timestamps + offset tags from a dict."""
    if not exif:
        return {}
    out = {}
    zeroth = exif.get("0th", {})
    ex = exif.get("Exif", {})
    out["datetime"] = parse_exif_dt(zeroth.get(piexif.ImageIFD.DateTime))
    out["original"] = parse_exif_dt(ex.get(piexif.ExifIFD.DateTimeOriginal))
    out["digitized"] = parse_exif_dt(ex.get(piexif.ExifIFD.DateTimeDigitized))
    for key, tag in (("offset", piexif.ExifIFD.OffsetTime),
                     ("offset_original", piexif.ExifIFD.OffsetTimeOriginal),
                     ("offset_digitized", piexif.ExifIFD.OffsetTimeDigitized)):
        v = ex.get(tag)
        if isinstance(v, bytes):
            v = v.decode("utf-8", "replace")
        out[key] = v or None
    return out


def primary_time(info):
    """Best available timestamp for a scanned file, in priority order."""
    for key in ("original", "digitized", "datetime"):
        if info["exif"].get(key):
            return info["exif"][key]
    if info["filename_date"]:
        return info["filename_date"]
    return info["file_mtime"]


def write_exif_times(path, new_dt, sync, existing_keys):
    """Write EXIF timestamps into a JPEG/TIFF copy in place.

    When sync is on, all three tags are set to new_dt. Otherwise only
    the tags that already existed are shifted (the caller passes the
    per-tag values via existing_keys mapping tag->datetime).
    Returns (ok, error).
    """
    if piexif is None:
        return False, "piexif not installed"
    try:
        exif = piexif.load(path)
    except Exception:
        exif = {"0th": {}, "Exif": {}, "GPS": {}, "1st": {},
                "thumbnail": None}
    if sync:
        exif.setdefault("0th", {})[piexif.ImageIFD.DateTime] = encode_exif_dt(new_dt)
        exif.setdefault("Exif", {})[piexif.ExifIFD.DateTimeOriginal] = encode_exif_dt(new_dt)
        exif["Exif"][piexif.ExifIFD.DateTimeDigitized] = encode_exif_dt(new_dt)
    else:
        for tag, dt in existing_keys.items():
            if tag == "datetime":
                exif.setdefault("0th", {})[piexif.ImageIFD.DateTime] = encode_exif_dt(dt)
            elif tag == "original":
                exif.setdefault("Exif", {})[piexif.ExifIFD.DateTimeOriginal] = encode_exif_dt(dt)
            elif tag == "digitized":
                exif.setdefault("Exif", {})[piexif.ExifIFD.DateTimeDigitized] = encode_exif_dt(dt)
    try:
        piexif.insert(piexif.dump(exif), path)
        return True, None
    except Exception as e:
        return False, str(e)


# ---------------------------------------------------------------- scan


def kind_of(path):
    ext = os.path.splitext(path)[1].lower()
    if ext in JPEG_EXTS:
        return "jpeg"
    if ext in TIFF_EXTS:
        return "tiff"
    if ext in HEIC_EXTS:
        return "heic"
    if ext in RAW_EXTS:
        return "raw"
    return "other"


def scan_file(path):
    info = {"path": path, "name": os.path.basename(path),
            "kind": kind_of(path)}
    try:
        st = os.stat(path)
        info["file_mtime"] = datetime.fromtimestamp(st.st_mtime)
        info["size"] = st.st_size
    except OSError as e:
        info["error"] = str(e)
        return info
    info["filename_date"] = parse_filename_date(info["name"])
    exif, err = read_exif(path)
    info["exif"] = exif_times(exif) if exif else {}
    info["exif_present"] = bool(exif) and any(
        info["exif"].get(k) for k in ("original", "digitized", "datetime"))
    if err and info["kind"] in ("jpeg", "tiff") and not info["exif_present"]:
        info["exif_error"] = err
    sources = [v for v in (info["exif"].get("original"),
                           info["exif"].get("digitized"),
                           info["exif"].get("datetime"),
                           info["filename_date"],
                           info["file_mtime"]) if v]
    if len(sources) >= 2:
        info["spread_seconds"] = (max(sources) - min(sources)).total_seconds()
    else:
        info["spread_seconds"] = 0.0
    info["conflict"] = info["spread_seconds"] > 1.0
    if info["kind"] == "raw":
        info["status"] = "unsupported"
    elif info["kind"] == "heic":
        info["status"] = "heic_limited"
    elif info["kind"] == "other":
        info["status"] = "skipped"
    else:
        info["status"] = "ok"
    return info


def scan_folder(folder):
    files = []
    for name in sorted(os.listdir(folder)):
        p = os.path.join(folder, name)
        if os.path.isfile(p):
            files.append(scan_file(p))
    return {"folder": folder, "files": files,
            "total": len(files),
            "conflicts": sum(1 for f in files if f.get("conflict")),
            "unsupported": sum(1 for f in files if f.get("status") == "unsupported"),
            "heic_limited": sum(1 for f in files if f.get("status") == "heic_limited")}


# ---------------------------------------------------------------- fix


def fix_file(info, seconds, outdir, sync=True):
    """Write one fixed copy. Returns a result dict; never touches input."""
    os.makedirs(outdir, exist_ok=True)
    out_path = os.path.join(outdir, info["name"])
    result = {"name": info["name"], "kind": info["kind"],
              "before_exif": info["exif"].get("original"),
              "before_mtime": info["file_mtime"],
              "before_filename_date": info["filename_date"],
              "source": None, "after_exif": None, "after_mtime": None,
              "recheck": None, "notes": []}
    if info["kind"] == "raw":
        result["notes"].append("unsupported RAW format — skipped, no copy written")
        result["recheck"] = "skipped_unsupported"
        return result
    if info["kind"] == "other":
        result["notes"].append("not a supported photo file — skipped")
        result["recheck"] = "skipped_unsupported"
        return result
    shutil.copyfile(info["path"], out_path)
    delta = timedelta(seconds=seconds)
    if info["exif_present"]:
        result["source"] = "exif"
        if sync:
            new_dt = info["exif"].get("original") or info["exif"].get("digitized") or info["exif"].get("datetime")
            new_dt = new_dt + delta
            if info["kind"] == "heic":
                result["notes"].append("HEIC EXIF write unsupported — file time only")
                ok = False
            else:
                ok, err = write_exif_times(out_path, new_dt, True, {})
                if not ok:
                    result["notes"].append("EXIF write failed: %s" % err)
            after_mtime = info["file_mtime"] + delta
            os.utime(out_path, (after_mtime.timestamp(), after_mtime.timestamp()))
        else:
            shifted = {}
            for key in ("original", "digitized", "datetime"):
                if info["exif"].get(key):
                    shifted[key] = info["exif"][key] + delta
            if info["kind"] == "heic":
                result["notes"].append("HEIC EXIF write unsupported — file time only")
            else:
                ok, err = write_exif_times(out_path, None, False, shifted)
                if not ok:
                    result["notes"].append("EXIF write failed: %s" % err)
            after_mtime = info["file_mtime"] + delta
            os.utime(out_path, (after_mtime.timestamp(), after_mtime.timestamp()))
    elif info["filename_date"]:
        result["source"] = "filename"
        new_dt = info["filename_date"] + delta
        if info["kind"] == "heic":
            result["notes"].append("HEIC EXIF write unsupported — file time only")
        else:
            ok, err = write_exif_times(out_path, new_dt, True, {})
            if not ok:
                result["notes"].append("EXIF write failed: %s" % err)
        os.utime(out_path, (new_dt.timestamp(), new_dt.timestamp()))
    else:
        result["source"] = "file_only"
        after_mtime = info["file_mtime"] + delta
        os.utime(out_path, (after_mtime.timestamp(), after_mtime.timestamp()))
        result["notes"].append("no EXIF and no dated filename — file time only")
    # Recheck: re-read the copy.
    after = scan_file(out_path)
    result["after_exif"] = after["exif"].get("original")
    result["after_mtime"] = after["file_mtime"]
    if result["source"] == "file_only":
        expected = info["file_mtime"] + delta
        diff = abs((after["file_mtime"] - expected).total_seconds())
        result["recheck"] = "green" if diff <= 1.0 else "FAIL"
        result["recheck_detail"] = "file mtime error %.3f s" % diff
    elif info["kind"] == "heic":
        expected = (info["file_mtime"] + delta)
        diff = abs((after["file_mtime"] - expected).total_seconds())
        result["recheck"] = "green" if diff <= 1.0 else "FAIL"
        result["recheck_detail"] = "HEIC file mtime error %.3f s; EXIF write unsupported" % diff
    else:
        ex = after["exif"].get("original")
        if ex is None:
            result["recheck"] = "FAIL"
            result["recheck_detail"] = "no EXIF in output copy"
        else:
            diff = abs((ex - after["file_mtime"]).total_seconds())
            result["recheck"] = "green" if (diff <= 1.0 and sync) else (
                "green" if diff <= 1.0 else "FAIL")
            result["recheck_detail"] = "EXIF vs file time diff %.3f s" % diff
    result["output_path"] = out_path
    return result


def render_report(title, results, seconds, outdir):
    lines = ["# DateShift Report — %s" % title, "",
             "- Shift applied: %s seconds (%s)" % (
                 seconds, "forward" if seconds >= 0 else "backward"),
             "- Files processed: %d" % len(results),
             "- Recheck green: %d" % sum(1 for r in results if r["recheck"] == "green"),
             "- Skipped (unsupported): %d" % sum(
                 1 for r in results if r["recheck"] == "skipped_unsupported"),
             ""]
    for r in results:
        lines.append("## %s" % r["name"])
        lines.append("- Kind: %s · date source used: %s" % (r["kind"], r["source"]))
        lines.append("- Before: EXIF %s · file time %s · filename date %s" % (
            fmt(r["before_exif"]), fmt(r["before_mtime"]), fmt(r["before_filename_date"])))
        lines.append("- After: EXIF %s · file time %s" % (
            fmt(r["after_exif"]), fmt(r["after_mtime"])))
        lines.append("- Recheck: %s%s" % (r["recheck"],
                       (" — " + r["recheck_detail"]) if r.get("recheck_detail") else ""))
        for n in r["notes"]:
            lines.append("- Note: %s" % n)
        lines.append("")
    lines.append("Originals untouched: DateShift only ever writes copies to "
                 "%s. 100%% local, nothing uploaded." % outdir)
    return "\n".join(lines)


def run_scan(folder):
    return scan_folder(folder)


def run_shift(folder, seconds, outdir, sync=True):
    t0 = time.time()
    scan = scan_folder(folder)
    results = []
    for info in scan["files"]:
        results.append(fix_file(info, seconds, outdir, sync=sync))
    report = render_report(os.path.basename(folder.rstrip("/")) or folder,
                           results, seconds, outdir)
    report_path = os.path.join(outdir, "dateshift-report.md")
    with open(report_path, "w", encoding="utf-8") as f:
        f.write(report)
    return {"results": results, "report": report, "report_path": report_path,
            "output_dir": outdir, "elapsed": time.time() - t0,
            "green": sum(1 for r in results if r["recheck"] == "green")}


def compute_dual_offset(path_a, path_b):
    """Seconds to ADD to camera B times so they match camera A.

    pair A = reference (correct clock), pair B = second camera.
    offset = timeA - timeB. A camera slow by 83 s gives +83.
    """
    info_a = scan_file(path_a)
    info_b = scan_file(path_b)
    ta, tb = primary_time(info_a), primary_time(info_b)
    return (ta - tb).total_seconds(), info_a, info_b


def run_dual(pair_a, pair_b, folder, outdir, sync=True):
    offset, _, _ = compute_dual_offset(pair_a, pair_b)
    seconds = int(round(offset))
    res = run_shift(folder, seconds, outdir, sync=sync)
    res["dual_offset_seconds"] = offset
    res["dual_offset_applied"] = seconds
    return res


# ---------------------------------------------------------------- CLI


def main(argv=None):
    ap = argparse.ArgumentParser(prog="dateshift")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("scan"); p.add_argument("folder")
    p = sub.add_parser("shift"); p.add_argument("folder")
    p.add_argument("--seconds", type=int, required=True)
    p.add_argument("--out", required=True)
    p.add_argument("--no-sync", action="store_true")
    p = sub.add_parser("dual"); p.add_argument("--pair-a", required=True)
    p.add_argument("--pair-b", required=True); p.add_argument("--folder", required=True)
    p.add_argument("--out", required=True)
    p.add_argument("--no-sync", action="store_true")
    args = ap.parse_args(argv)
    if args.cmd == "scan":
        s = run_scan(args.folder)
        for f in s["files"]:
            print("%s | %s | EXIF %s | file %s | filename %s | spread %.0f s | conflict=%s | %s" % (
                f["name"], f["kind"], fmt(f["exif"].get("original")),
                fmt(f["file_mtime"]), fmt(f["filename_date"]),
                f["spread_seconds"], f["conflict"], f["status"]))
        print("total=%d conflicts=%d unsupported=%d heic_limited=%d" % (
            s["total"], s["conflicts"], s["unsupported"], s["heic_limited"]))
    elif args.cmd == "shift":
        r = run_shift(args.folder, args.seconds, args.out, sync=not args.no_sync)
        print(r["report"])
        print("elapsed %.2f s · report: %s" % (r["elapsed"], r["report_path"]))
    elif args.cmd == "dual":
        off, _, _ = compute_dual_offset(args.pair_a, args.pair_b)
        print("dual offset: %.1f s (applied %d s)" % (off, int(round(off))))
        r = run_dual(args.pair_a, args.pair_b, args.folder, args.out,
                     sync=not args.no_sync)
        print(r["report"])
        print("elapsed %.2f s · report: %s" % (r["elapsed"], r["report_path"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
