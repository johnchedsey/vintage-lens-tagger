#!/usr/bin/env python3
"""
CR3 / DNG Lens Tagger
----------------------
A small GUI tool for batch-writing lens metadata (Focal Length, Lens Maker,
Lens Model) into Canon CR3 and DNG RAW files shot with non-electronic
(manual/vintage) lenses.

Requires: exiftool (https://exiftool.org) available on PATH, or point the
tool at exiftool.exe manually (this location is remembered between runs).

Usage:
    python cr3_lens_tagger.py

Logs (including full tracebacks for any crash) are written to:
    %APPDATA%\\CR3LensTagger\\logs\\cr3_lens_tagger.log   (Windows)
    ~/CR3LensTagger/logs/cr3_lens_tagger.log               (other platforms)
and echoed to the terminal the app was launched from.
"""

import json
import logging
import os
import re
import subprocess
import sys
import shutil
import traceback
from datetime import datetime
from pathlib import Path
import tkinter as tk
import tkinter.simpledialog
from tkinter import ttk, filedialog, messagebox

APP_NAME = "CR3 / DNG Lens Tagger"
CONFIG_DIR = Path(os.environ.get("APPDATA", Path.home())) / "CR3LensTagger"
PRESETS_FILE = CONFIG_DIR / "presets.json"
CONFIG_FILE = CONFIG_DIR / "config.json"
LOG_DIR = CONFIG_DIR / "logs"
LOG_FILE = LOG_DIR / "cr3_lens_tagger.log"

RAW_EXTENSIONS = ("*.CR3", "*.cr3", "*.DNG", "*.dng")

# Shown in a field when the selected files have different existing values
# for that tag. Applying with this value left in place skips writing that
# tag entirely, so each file keeps whatever it already had.
KEEP_PLACEHOLDER = "<keep>"

# Offered in the dropdown alongside <keep> when some of the selected files
# have a value for a tag and others have none at all. Unlike <keep>, actively
# selecting this clears the tag (writes it blank) on every selected file.
BLANK_PLACEHOLDER = "<blank>"

# Not hardcoded to any specific user — resolves to whoever is running the
# script. Used as the starting folder for file/folder pickers.
DEFAULT_PICTURES_DIR = str(Path.home() / "Pictures")


# --------------------------------------------------------------------------
# Logging setup — file + terminal, so errors are never silently lost
# --------------------------------------------------------------------------

def setup_logging() -> logging.Logger:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger("cr3_lens_tagger")
    logger.setLevel(logging.DEBUG)

    fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s")

    file_handler = logging.FileHandler(LOG_FILE, encoding="utf-8")
    file_handler.setFormatter(fmt)
    file_handler.setLevel(logging.DEBUG)

    console_handler = logging.StreamHandler(sys.stderr)
    console_handler.setFormatter(fmt)
    console_handler.setLevel(logging.DEBUG)

    logger.addHandler(file_handler)
    logger.addHandler(console_handler)
    logger.info("=== %s starting (log file: %s) ===", APP_NAME, LOG_FILE)
    return logger


log = setup_logging()


# --------------------------------------------------------------------------
# Config / preset storage
# --------------------------------------------------------------------------

def load_json(path: Path) -> dict:
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as exc:
            log.warning("Could not read %s: %s", path, exc)
            return {}
    return {}


def save_json(path: Path, data: dict) -> None:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")


def load_presets() -> dict:
    return load_json(PRESETS_FILE)


def save_presets(presets: dict) -> None:
    save_json(PRESETS_FILE, presets)


def load_config() -> dict:
    return load_json(CONFIG_FILE)


def save_config(config: dict) -> None:
    save_json(CONFIG_FILE, config)


# --------------------------------------------------------------------------
# exiftool helpers
# --------------------------------------------------------------------------

def find_exiftool() -> str | None:
    """Try to find exiftool on PATH under common Windows names."""
    for name in ("exiftool", "exiftool.exe", "exiftool(-k).exe"):
        path = shutil.which(name)
        if path:
            return path
    return None


def run_exiftool(exiftool_path: str, args: list[str]) -> subprocess.CompletedProcess:
    creationflags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    log.debug("Running exiftool: %s %s", exiftool_path, args)
    return subprocess.run(
        [exiftool_path, *args],
        capture_output=True,
        text=True,
        creationflags=creationflags,
    )


def _extract_leading_number(value: str) -> str:
    """'28.0 mm' -> '28.0'. Falls back to the original string if no number found."""
    match = re.match(r"\s*([\d.]+)", value)
    return match.group(1) if match else value


# --------------------------------------------------------------------------
# Main application
# --------------------------------------------------------------------------

class CR3LensTagger(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title(APP_NAME)
        self.geometry("740x660")
        self.minsize(660, 580)

        # Global safety net: any exception raised inside a Tk callback
        # (button click, etc.) lands here instead of vanishing.
        self.report_callback_exception = self._handle_callback_exception

        self.config_data = load_config()
        self.exiftool_path = self.config_data.get("exiftool_path") or find_exiftool()
        self.presets = load_presets()
        self.selected_files: list[Path] = []

        # Start at the last folder used, falling back to this user's
        # Pictures folder (resolved dynamically, never hardcoded).
        saved_dir = self.config_data.get("last_dir")
        self.last_dir = saved_dir if saved_dir and Path(saved_dir).is_dir() else DEFAULT_PICTURES_DIR

        self._build_ui()
        self._refresh_preset_list()
        self._update_exiftool_status()

        if self.exiftool_path:
            log.info("Using exiftool at: %s", self.exiftool_path)
        else:
            log.warning("exiftool not found on PATH and no saved location in config.")

    # ---- global error handling -----------------------------------------

    def _handle_callback_exception(self, exc_type, exc_value, exc_tb):
        message = "".join(traceback.format_exception(exc_type, exc_value, exc_tb))
        log.error("Unhandled error in UI callback:\n%s", message)
        self._log(f"ERROR: {exc_value}\n(See log file for full details: {LOG_FILE})")
        messagebox.showerror(
            APP_NAME,
            f"Something went wrong:\n\n{exc_value}\n\n"
            f"Full details were written to:\n{LOG_FILE}",
        )

    # ---- UI construction -------------------------------------------------

    def _build_ui(self):
        pad = {"padx": 10, "pady": 6}

        # exiftool status bar
        status_frame = ttk.Frame(self)
        status_frame.pack(fill="x", **pad)
        self.exiftool_status_var = tk.StringVar()
        ttk.Label(status_frame, textvariable=self.exiftool_status_var).pack(side="left")
        ttk.Button(status_frame, text="Locate exiftool...", command=self._browse_exiftool).pack(side="right")

        # File selection
        file_frame = ttk.LabelFrame(self, text="1. Select CR3 / DNG files")
        file_frame.pack(fill="both", expand=True, **pad)

        btn_row = ttk.Frame(file_frame)
        btn_row.pack(fill="x", padx=8, pady=(8, 4))
        ttk.Button(btn_row, text="Add Files...", command=self._add_files).pack(side="left")
        ttk.Button(btn_row, text="Add Folder...", command=self._add_folder).pack(side="left", padx=6)
        ttk.Button(btn_row, text="Clear List", command=self._clear_files).pack(side="left")
        self.file_count_var = tk.StringVar(value="0 files selected")
        ttk.Label(btn_row, textvariable=self.file_count_var).pack(side="right")

        list_frame = ttk.Frame(file_frame)
        list_frame.pack(fill="both", expand=True, padx=8, pady=(0, 8))
        scrollbar = ttk.Scrollbar(list_frame)
        scrollbar.pack(side="right", fill="y")
        self.file_listbox = tk.Listbox(
            list_frame, yscrollcommand=scrollbar.set, selectmode="extended", height=8,
            exportselection=False,
        )
        self.file_listbox.pack(side="left", fill="both", expand=True)
        scrollbar.config(command=self.file_listbox.yview)
        self.file_listbox.bind("<<ListboxSelect>>", self._on_file_selection_change)

        self.selection_status_var = tk.StringVar(value="0 out of 0 files will be updated")
        ttk.Label(file_frame, textvariable=self.selection_status_var).pack(
            anchor="w", padx=8, pady=(0, 8)
        )

        # Preset row
        preset_frame = ttk.LabelFrame(self, text="2. Lens preset (optional)")
        preset_frame.pack(fill="x", **pad)
        preset_row = ttk.Frame(preset_frame)
        preset_row.pack(fill="x", padx=8, pady=8)
        ttk.Label(preset_row, text="Saved lens:").pack(side="left")
        self.preset_var = tk.StringVar()
        self.preset_combo = ttk.Combobox(
            preset_row, textvariable=self.preset_var, state="readonly", width=35
        )
        self.preset_combo.pack(side="left", padx=6)
        self.preset_combo.bind("<<ComboboxSelected>>", self._on_preset_selected)
        ttk.Button(preset_row, text="Save current as preset", command=self._save_preset).pack(side="left", padx=6)
        ttk.Button(preset_row, text="Update selected preset", command=self._update_preset).pack(side="left", padx=6)
        ttk.Button(preset_row, text="Delete preset", command=self._delete_preset).pack(side="left")

        # Metadata fields
        fields_frame = ttk.LabelFrame(self, text="3. Metadata to write")
        fields_frame.pack(fill="x", **pad)
        grid = ttk.Frame(fields_frame)
        grid.pack(fill="x", padx=8, pady=8)

        ttk.Label(grid, text="Focal Length (mm):").grid(row=0, column=0, sticky="w", pady=4)
        self.focal_length_var = tk.StringVar()
        self.focal_length_combo = ttk.Combobox(grid, textvariable=self.focal_length_var, width=18)
        self.focal_length_combo.grid(row=0, column=1, sticky="w", padx=6)

        ttk.Label(grid, text="Lens Maker:").grid(row=1, column=0, sticky="w", pady=4)
        self.lens_maker_var = tk.StringVar()
        self.lens_maker_combo = ttk.Combobox(grid, textvariable=self.lens_maker_var, width=28)
        self.lens_maker_combo.grid(row=1, column=1, sticky="w", padx=6)

        ttk.Label(grid, text="Lens Model:").grid(row=2, column=0, sticky="w", pady=4)
        self.lens_model_var = tk.StringVar()
        self.lens_model_combo = ttk.Combobox(grid, textvariable=self.lens_model_var, width=38)
        self.lens_model_combo.grid(row=2, column=1, sticky="w", padx=6)

        # Tracks what we last set programmatically per field, so a later
        # autofill only overwrites a field if the user hasn't typed
        # something of their own into it since.
        self._autofilled_values = {"focal_length": None, "lens_maker": None, "lens_model": None}

        self.keep_backup_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(
            fields_frame,
            text="Keep original backup files (adds _original copies)",
            variable=self.keep_backup_var,
        ).pack(anchor="w", padx=8, pady=(0, 8))

        # Apply button
        apply_row = ttk.Frame(self)
        apply_row.pack(fill="x", **pad)
        self.apply_btn = ttk.Button(apply_row, text="Apply to all selected files", command=self._apply)
        self.apply_btn.pack(side="left")

        # Log output
        log_frame = ttk.LabelFrame(self, text=f"Log  (also saved to {LOG_FILE.name})")
        log_frame.pack(fill="both", expand=True, **pad)
        self.log_text = tk.Text(log_frame, height=8, wrap="word", state="disabled")
        self.log_text.pack(fill="both", expand=True, padx=8, pady=8)

    # ---- exiftool detection -----------------------------------------------

    def _update_exiftool_status(self):
        if self.exiftool_path:
            self.exiftool_status_var.set(f"exiftool: {self.exiftool_path}")
        else:
            self.exiftool_status_var.set(
                "exiftool NOT found — click 'Locate exiftool...' or install it from exiftool.org"
            )

    def _browse_exiftool(self):
        path = filedialog.askopenfilename(
            title="Locate exiftool.exe",
            filetypes=[("exiftool executable", "*.exe"), ("All files", "*.*")],
        )
        if path:
            self.exiftool_path = path
            self.config_data["exiftool_path"] = path
            save_config(self.config_data)
            log.info("exiftool path set and saved: %s", path)
            self._log(f"exiftool location saved: {path}")
            self._update_exiftool_status()

    # ---- file selection -----------------------------------------------

    def _remember_dir(self, folder: str):
        self.last_dir = folder
        self.config_data["last_dir"] = folder
        save_config(self.config_data)

    def _add_files(self):
        paths = filedialog.askopenfilenames(
            title="Select CR3 / DNG files",
            initialdir=self.last_dir,
            filetypes=[
                ("RAW files (CR3, DNG)", "*.cr3 *.CR3 *.dng *.DNG"),
                ("All files", "*.*"),
            ],
        )
        for p in paths:
            path = Path(p)
            if path not in self.selected_files:
                self.selected_files.append(path)
        if paths:
            self._remember_dir(str(Path(paths[0]).parent))
        self._refresh_file_list()
        self._autofill_from_existing()

    def _add_folder(self):
        folder = filedialog.askdirectory(
            title="Select folder containing CR3/DNG files", initialdir=self.last_dir
        )
        if not folder:
            return
        self._remember_dir(folder)
        found = []
        for pattern in RAW_EXTENSIONS:
            found.extend(sorted(Path(folder).glob(pattern)))
        added = 0
        for path in found:
            if path not in self.selected_files:
                self.selected_files.append(path)
                added += 1
        self._refresh_file_list()
        log.info("Added %d file(s) from folder %s", added, folder)
        self._log(f"Added {added} file(s) from {folder}")
        self._autofill_from_existing()

    def _clear_files(self):
        self.selected_files.clear()
        self._refresh_file_list()

    def _refresh_file_list(self):
        self.file_listbox.delete(0, "end")
        for path in self.selected_files:
            self.file_listbox.insert("end", str(path))
        self.file_count_var.set(f"{len(self.selected_files)} files selected")
        self._update_selection_status()

    def _on_file_selection_change(self, _event=None):
        self._update_selection_status()
        self._autofill_from_existing()

    def _update_selection_status(self):
        total = len(self.selected_files)
        indices = self.file_listbox.curselection()
        count = len(indices) if indices else total
        self.selection_status_var.set(f"{count} out of {total} files will be updated")

    def _get_active_files(self) -> list[Path]:
        """Files that will actually be written to: the highlighted subset
        of the list, or every file if nothing is highlighted."""
        indices = self.file_listbox.curselection()
        if not indices:
            return list(self.selected_files)
        return [self.selected_files[i] for i in indices]

    @staticmethod
    def _extract_tags_from_record(rec: dict) -> dict:
        """Pull FocalLength/LensMake/LensModel out of one exiftool -j -G1
        record, preferring EXIF over XMP, and normalize focal length to a
        plain number."""
        focal = maker = model = None
        for key, value in rec.items():
            if key in ("SourceFile", "ExifTool") or not value:
                continue
            if "FocalLength" in key and focal is None:
                focal = value
            elif "LensMake" in key and maker is None:
                maker = value
            elif "LensModel" in key and model is None:
                model = value

        found = {}
        if focal:
            found["focal_length"] = _extract_leading_number(str(focal))
        if maker:
            found["lens_maker"] = str(maker)
        if model:
            found["lens_model"] = str(model)
        return found

    def _read_existing_tags_batch(self, file_paths: list[Path]) -> list[dict]:
        """Read existing Focal Length / Lens Make / Lens Model (EXIF and
        XMP) from every given file in a single exiftool call."""
        if not self.exiftool_path or not file_paths:
            return []
        args = [
            "-j", "-G1",
            "-EXIF:FocalLength", "-EXIF:LensMake", "-EXIF:LensModel",
            "-XMP:LensMake", "-XMP:LensModel",
        ]
        args.extend(str(p) for p in file_paths)
        try:
            result = run_exiftool(self.exiftool_path, args)
            records = json.loads(result.stdout) if result.stdout else []
        except (OSError, subprocess.SubprocessError, json.JSONDecodeError) as exc:
            log.warning("Could not read existing metadata: %s", exc)
            return []
        return [self._extract_tags_from_record(rec) for rec in records]

    def _set_field(self, var: tk.StringVar, combo: ttk.Combobox, key: str,
                    display_value: str, dropdown_values: list[str]):
        """Update one field's dropdown list, and its displayed text — but
        only if the current text is blank or matches what we last set
        programmatically (so a value the user actually typed is left alone)."""
        combo["values"] = dropdown_values
        current = var.get()
        if not current.strip() or current == self._autofilled_values.get(key):
            var.set(display_value)
            self._autofilled_values[key] = display_value

    def _autofill_from_existing(self):
        """Inspect existing metadata across the active file selection (the
        highlighted subset, or all files if none are highlighted). If every
        file agrees (or is blank), pre-fill that shared value. If files
        disagree, show '<keep>' plus a dropdown of the differing values so
        applying won't clobber files that already have good data."""
        active_files = self._get_active_files()

        # A preset chosen for the previous batch/selection may not describe
        # this one — clear it rather than let it look still-applied.
        self.preset_var.set("")

        if not self.exiftool_path or not active_files:
            return
        records = self._read_existing_tags_batch(active_files)
        if not records:
            return

        summary = []
        for key, label, var, combo in (
            ("focal_length", "Focal Length", self.focal_length_var, self.focal_length_combo),
            ("lens_maker", "Lens Maker", self.lens_maker_var, self.lens_maker_combo),
            ("lens_model", "Lens Model", self.lens_model_var, self.lens_model_combo),
        ):
            distinct = sorted({rec[key] for rec in records if rec.get(key)})
            has_blank = any(not rec.get(key) for rec in records)

            if not distinct:
                # Nothing found in this selection. If the field is still
                # showing a value we set on behalf of a previous selection,
                # clear it — but leave alone anything the user actually
                # typed themselves.
                combo["values"] = []
                current = var.get()
                if current.strip() and current == self._autofilled_values.get(key):
                    var.set("")
                    self._autofilled_values[key] = None
                continue

            if len(distinct) == 1 and not has_blank:
                # Every file agrees and none are missing the tag — just fill it in.
                self._set_field(var, combo, key, distinct[0], distinct)
                summary.append(f"{label}={distinct[0]}")
            else:
                # Either multiple different values, or some files have a
                # value and others don't — either way this needs a choice.
                dropdown_values = [KEEP_PLACEHOLDER]
                if has_blank:
                    dropdown_values.append(BLANK_PLACEHOLDER)
                dropdown_values.extend(distinct)
                self._set_field(var, combo, key, KEEP_PLACEHOLDER, dropdown_values)
                note = f"{len(distinct)} different value(s)" + (" + some blank" if has_blank else "")
                summary.append(f"{label}=<keep> ({note})")

        if summary:
            log.info("Autofill across %d file(s): %s", len(active_files), summary)
            self._log(f"Existing metadata across selected files — {', '.join(summary)}")

    # ---- presets -----------------------------------------------

    def _refresh_preset_list(self):
        names = sorted(self.presets.keys())
        self.preset_combo["values"] = names

    def _on_preset_selected(self, _event=None):
        name = self.preset_var.get()
        preset = self.presets.get(name)
        if not preset:
            return
        self.focal_length_var.set(preset.get("focal_length", ""))
        self.lens_maker_var.set(preset.get("lens_maker", ""))
        self.lens_model_var.set(preset.get("lens_model", ""))

    def _save_preset(self):
        model = self.lens_model_var.get().strip()
        maker = self.lens_maker_var.get().strip()
        focal = self.focal_length_var.get().strip()
        if not model:
            messagebox.showwarning(APP_NAME, "Enter a Lens Model before saving a preset.")
            return
        default_name = f"{maker} {model}".strip()
        name = tkinter.simpledialog.askstring(
            "Save preset", "Preset name:", initialvalue=default_name, parent=self
        )
        if not name:
            return
        self.presets[name] = {"focal_length": focal, "lens_maker": maker, "lens_model": model}
        save_presets(self.presets)
        self._refresh_preset_list()
        self.preset_var.set(name)
        log.info("Saved preset '%s': %s", name, self.presets[name])
        self._log(f"Saved preset '{name}'")

    def _update_preset(self):
        name = self.preset_var.get()
        if not name or name not in self.presets:
            messagebox.showwarning(APP_NAME, "Select a preset from the dropdown to update.")
            return
        model = self.lens_model_var.get().strip()
        maker = self.lens_maker_var.get().strip()
        focal = self.focal_length_var.get().strip()
        if not model:
            messagebox.showwarning(APP_NAME, "Enter a Lens Model before updating a preset.")
            return
        if not messagebox.askyesno(APP_NAME, f"Overwrite preset '{name}' with the current field values?"):
            return
        self.presets[name] = {"focal_length": focal, "lens_maker": maker, "lens_model": model}
        save_presets(self.presets)
        log.info("Updated preset '%s': %s", name, self.presets[name])
        self._log(f"Updated preset '{name}'")

    def _delete_preset(self):
        name = self.preset_var.get()
        if not name or name not in self.presets:
            return
        if not messagebox.askyesno(APP_NAME, f"Delete preset '{name}'?"):
            return
        del self.presets[name]
        save_presets(self.presets)
        self.preset_var.set("")
        self._refresh_preset_list()
        log.info("Deleted preset '%s'", name)
        self._log(f"Deleted preset '{name}'")

    # ---- apply -----------------------------------------------

    def _log(self, message: str):
        self.log_text.config(state="normal")
        timestamp = datetime.now().strftime("%H:%M:%S")
        self.log_text.insert("end", f"[{timestamp}] {message}\n")
        self.log_text.see("end")
        self.log_text.config(state="disabled")

    def _apply(self):
        if not self.exiftool_path:
            messagebox.showerror(APP_NAME, "exiftool was not found. Click 'Locate exiftool...' first.")
            return
        if not self.selected_files:
            messagebox.showwarning(APP_NAME, "Select at least one CR3/DNG file first.")
            return

        active_files = self._get_active_files()
        if not active_files:
            messagebox.showwarning(APP_NAME, "No files are selected to update.")
            return

        def resolve_field(raw_value: str):
            """Returns ('skip', None) / ('clear', '') / ('write', value)."""
            value = raw_value.strip()
            if value == KEEP_PLACEHOLDER:
                return "skip", None
            if value == BLANK_PLACEHOLDER:
                return "clear", ""
            if not value:
                return "skip", None
            return "write", value

        focal_action, focal_value = resolve_field(self.focal_length_var.get())
        maker_action, maker_value = resolve_field(self.lens_maker_var.get())
        model_action, model_value = resolve_field(self.lens_model_var.get())

        if focal_action == "skip" and maker_action == "skip" and model_action == "skip":
            messagebox.showwarning(
                APP_NAME,
                "Fill in at least one of Focal Length, Lens Maker, or Lens Model "
                "(or pick a value from the dropdown instead of leaving it on <keep>).",
            )
            return

        if focal_action == "write":
            try:
                float(focal_value)
            except ValueError:
                messagebox.showerror(APP_NAME, "Focal Length must be a number (e.g. 50 or 58.0).")
                return

        skipped = [label for label, action in (
            ("Focal Length", focal_action), ("Lens Maker", maker_action), ("Lens Model", model_action)
        ) if action == "skip"]
        cleared = [label for label, action in (
            ("Focal Length", focal_action), ("Lens Maker", maker_action), ("Lens Model", model_action)
        ) if action == "clear"]
        if skipped:
            self._log(f"Leaving unchanged (selection had differing values): {', '.join(skipped)}")
        if cleared:
            self._log(f"Clearing (blanking) on all selected files: {', '.join(cleared)}")

        # Group-qualified tags: CR3 files have BOTH a standard EXIF
        # LensMake/LensModel tag and a separate (non-writable) Canon
        # MakerNotes copy of the same name. Leaving the group unspecified
        # lets exiftool pick either one — which is why LensMake/LensModel
        # writes can silently no-op while FocalLength (only one location)
        # succeeds. Targeting "EXIF:" explicitly, plus mirroring into XMP
        # for apps that read lens info from there instead, fixes it.
        args = ["-m"]  # ignore minor warnings that would otherwise block the write
        if focal_action != "skip":
            args.append(f"-EXIF:FocalLength={focal_value}")
        if maker_action != "skip":
            args.append(f"-EXIF:LensMake={maker_value}")
            args.append(f"-XMP-exifEX:LensMake={maker_value}")
        if model_action != "skip":
            args.append(f"-EXIF:LensModel={model_value}")
            args.append(f"-XMP-exifEX:LensModel={model_value}")
            args.append(f"-XMP-aux:Lens={model_value}")

        if not self.keep_backup_var.get():
            args.append("-overwrite_original")

        args.extend(str(p) for p in active_files)

        self.apply_btn.config(state="disabled")
        log.info("Applying metadata to %d file(s): FocalLength=%r(%s) LensMake=%r(%s) LensModel=%r(%s)",
                  len(active_files),
                  focal_value, focal_action, maker_value, maker_action, model_value, model_action)
        self._log(f"Running exiftool on {len(active_files)} file(s)...")
        self.update_idletasks()

        try:
            result = run_exiftool(self.exiftool_path, args)
        except (OSError, subprocess.SubprocessError) as exc:
            log.error("Failed to launch exiftool: %s", exc, exc_info=True)
            self._log(f"ERROR: could not launch exiftool ({exc}). Full details in log file.")
            messagebox.showerror(APP_NAME, f"Could not launch exiftool:\n{exc}")
            self.apply_btn.config(state="normal")
            return

        if result.stdout:
            log.info("exiftool stdout: %s", result.stdout.strip())
            self._log(result.stdout.strip())
        if result.stderr:
            log.warning("exiftool stderr: %s", result.stderr.strip())
            self._log(result.stderr.strip())

        self.apply_btn.config(state="normal")

        if result.returncode == 0:
            log.info("exiftool completed successfully.")
            self._log("Done.")
            self._verify_written_tags(active_files)
            messagebox.showinfo(APP_NAME, "Metadata written successfully. See log for a per-file verification readout.")
        else:
            log.error("exiftool exited with code %d", result.returncode)
            self._log(f"exiftool exited with code {result.returncode}")
            messagebox.showerror(APP_NAME, "exiftool reported an error. See the log for details.")

    def _verify_written_tags(self, files: list[Path]):
        """Read back the tags we just wrote, per file and per group, so we
        can see exactly where the data actually landed (EXIF vs XMP vs
        nowhere) instead of trusting exiftool's generic success message."""
        read_args = [
            "-j", "-G1",
            "-EXIF:FocalLength", "-EXIF:LensMake", "-EXIF:LensModel",
            "-XMP:LensMake", "-XMP:LensModel",
        ]
        read_args.extend(str(p) for p in files)
        try:
            result = run_exiftool(self.exiftool_path, read_args)
        except (OSError, subprocess.SubprocessError) as exc:
            log.error("Verification read failed: %s", exc, exc_info=True)
            self._log(f"(verification read failed: {exc})")
            return

        try:
            records = json.loads(result.stdout)
        except json.JSONDecodeError:
            log.error("Could not parse verification JSON: %s", result.stdout)
            self._log("(could not parse verification output — see log file)")
            return

        self._log("--- Verification (what's actually in each file now) ---")
        for rec in records:
            filename = Path(rec.get("SourceFile", "?")).name
            fields = {k: v for k, v in rec.items() if k not in ("SourceFile", "ExifTool")}
            if not fields:
                self._log(f"{filename}: no lens/focal tags found in EXIF or XMP")
            else:
                parts = ", ".join(f"{k}={v}" for k, v in fields.items())
                self._log(f"{filename}: {parts}")
            log.info("Verify %s -> %s", filename, fields)
        self._log("--- end verification ---")


if __name__ == "__main__":
    if sys.version_info < (3, 10):
        print("This tool requires Python 3.10 or newer (uses 'str | None' type hints).")
        sys.exit(1)

    try:
        app = CR3LensTagger()
        app.mainloop()
    except Exception:
        log.critical("Fatal error during startup:\n%s", traceback.format_exc())
        raise
