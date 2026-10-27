#!/usr/bin/env python3
"""DateShift desktop shell (tkinter). Pick a folder of photos -> scan
for EXIF / file-time / filename date conflicts, or fix: shift all dates
by a signed offset (Date Maths), optionally sync all three date sources,
write COPIES to an output folder (originals are never modified) and a
re-check report. Dual-camera sync: pick one same-moment photo from each
camera and DateShift computes the offset for the whole second-camera
folder."""
import os
import sys
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, scrolledtext

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from dateshift import run_scan, run_shift, compute_dual_offset, fmt


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("DateShift — Photo Date Fixer "
                   "(Originals Never Modified)")
        self.geometry("780x620")
        self.folder = tk.StringVar()
        self.seconds = tk.StringVar(value="-20220")
        self.sync = tk.BooleanVar(value=True)
        self.pair_a = tk.StringVar()
        self.pair_b = tk.StringVar()
        tk.Label(self, text="Folder of photos (JPEG / TIFF; HEIC is "
                            "file-time only; RAW is reported, not "
                            "processed):").pack(anchor="w", padx=12, pady=(12, 2))
        row = tk.Frame(self)
        row.pack(fill="x", padx=12)
        tk.Entry(row, textvariable=self.folder).pack(side="left", fill="x",
                                                     expand=True)
        tk.Button(row, text="Browse…", command=self.pick_folder).pack(
            side="left", padx=6)
        row2 = tk.Frame(self)
        row2.pack(fill="x", padx=12, pady=(8, 0))
        tk.Label(row2, text="Shift (seconds, negative = clock was fast):"
                 ).pack(side="left")
        tk.Entry(row2, textvariable=self.seconds, width=10).pack(
            side="left", padx=6)
        tk.Checkbutton(row2, text="Sync EXIF + file time + filename date",
                       variable=self.sync).pack(side="left", padx=6)
        tk.Label(self, text="Dual-camera sync — one same-moment photo "
                            "from each camera:").pack(anchor="w", padx=12,
                                                      pady=(10, 2))
        for label, var, cmd in (("Camera A (correct clock):", self.pair_a,
                                 lambda: self.pick_file(self.pair_a)),
                                ("Camera B (wrong clock):", self.pair_b,
                                 lambda: self.pick_file(self.pair_b))):
            r = tk.Frame(self)
            r.pack(fill="x", padx=12)
            tk.Label(r, text=label, width=24, anchor="w").pack(side="left")
            tk.Entry(r, textvariable=var).pack(side="left", fill="x",
                                               expand=True)
            tk.Button(r, text="Browse…", command=cmd).pack(side="left",
                                                           padx=6)
        tk.Button(self, text="Compute dual offset → fill shift field",
                  command=self.compute).pack(pady=6)
        btns = tk.Frame(self)
        btns.pack(pady=8)
        self.btn_scan = tk.Button(btns, text="Scan Only — Report, "
                                             "Write Nothing",
                                  command=lambda: self.run(False), height=2)
        self.btn_scan.pack(side="left", padx=6)
        self.btn_fix = tk.Button(btns, text="Fix — Shift, Sync & Re-Check",
                                 command=lambda: self.run(True), height=2)
        self.btn_fix.pack(side="left", padx=6)
        self.log = scrolledtext.ScrolledText(self, wrap="word")
        self.log.pack(fill="both", expand=True, padx=12, pady=(0, 12))
        self.write("DateShift fixes wrong photo dates: a camera clock set "
                   "wrong, a forgotten time-zone change on a trip, or two "
                   "cameras whose clocks never agreed. It diagnoses three "
                   "date sources per photo — EXIF time, file time, and "
                   "any date in the file name — then shifts them by a "
                   "relative offset (Date Maths: every photo keeps its "
                   "relative spacing) and re-checks the copies.\n"
                   "SAFETY PROMISE: your originals are never modified — "
                   "DateShift only ever writes copies to a new output "
                   "folder, and the report proves each copy's EXIF and "
                   "file time agree.\n"
                   "Everything runs locally — your photos never leave "
                   "this computer.\n")

    def write(self, s):
        self.log.insert("end", s)
        self.log.see("end")

    def pick_folder(self):
        p = filedialog.askdirectory()
        if p:
            self.folder.set(p)

    def pick_file(self, var):
        p = filedialog.askopenfilename(
            filetypes=[("Photos", "*.jpg *.jpeg *.tif *.tiff *.heic *.heif"),
                       ("All files", "*.*")])
        if p:
            var.set(p)

    def compute(self):
        a, b = self.pair_a.get().strip(), self.pair_b.get().strip()
        if not a or not b or not os.path.isfile(a) or not os.path.isfile(b):
            messagebox.showerror("DateShift", "Pick both pair photos first.")
            return
        try:
            off, _, _ = compute_dual_offset(a, b)
            self.seconds.set(str(int(round(off))))
            self.write("\nDual offset: camera B is %.1f s behind camera A "
                       "— shift field set to %d s.\n" % (off, int(round(off))))
        except Exception as e:
            self.write("\nError computing offset: %s\n" % e)

    def run(self, fix):
        folder = self.folder.get().strip()
        if not folder or not os.path.isdir(folder):
            messagebox.showerror("DateShift", "Choose a photo folder first.")
            return
        try:
            seconds = int(self.seconds.get().strip())
        except ValueError:
            messagebox.showerror("DateShift",
                                 "Shift must be whole seconds, e.g. -20220.")
            return
        self.btn_scan.config(state="disabled")
        self.btn_fix.config(state="disabled")
        threading.Thread(target=self._work, args=(folder, seconds, fix),
                         daemon=True).start()

    def _work(self, folder, seconds, fix):
        try:
            if fix:
                outdir = os.path.join(folder, "dateshift_output")
                r = run_shift(folder, seconds, outdir, sync=self.sync.get())
                green = sum(1 for x in r["results"] if x["recheck"] == "green")
                self.write("\nDone. Files: %d · re-check green: %d · "
                           "report: %s\nOutput folder: %s\n"
                           "Your originals were not touched.\n" % (
                               len(r["results"]), green, r["report_path"],
                               outdir))
            else:
                s = run_scan(folder)
                for f in s["files"]:
                    self.write("%s | EXIF %s | file %s | filename %s | "
                               "spread %.0f s | %s\n" % (
                                   f["name"], fmt(f["exif"].get("original")),
                                   fmt(f["file_mtime"]),
                                   fmt(f["filename_date"]),
                                   f["spread_seconds"], f["status"]))
                self.write("\nScan done. Files: %d · conflicts: %d · "
                           "unsupported RAW: %d · HEIC (limited): %d\n"
                           "Scan only — nothing was written.\n" % (
                               s["total"], s["conflicts"], s["unsupported"],
                               s["heic_limited"]))
        except Exception as e:
            self.write("\nError: %s\n" % e)
        finally:
            self.btn_scan.config(state="normal")
            self.btn_fix.config(state="normal")


if __name__ == "__main__":
    App().mainloop()
