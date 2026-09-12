# CR3 Lens Tagger

A small Windows GUI for batch-writing **Focal Length**, **Lens Maker**, and **Lens Model**
into Canon CR3 files shot with manual/vintage lenses that don't report this data
electronically.

## Setup (Windows)

1. **Install exiftool**
   - Go to https://exiftool.org and download the Windows Executable.
   - It downloads as `exiftool(-k).exe`. Rename it to `exiftool.exe`.
   - Move `exiftool.exe` somewhere permanent, e.g. `C:\Tools\exiftool\`, and
     add that folder to your PATH (Settings → search "environment variables"
     → Edit the `Path` variable → New → paste the folder path).
   - Alternatively, skip the PATH step entirely — the app has a
     **"Locate exiftool..."** button to point directly at the `.exe`.

2. **Install Python 3.10+** if you don't already have it (python.org), making
   sure "Add python.exe to PATH" is checked during install. Tkinter ships
   with the standard Windows installer, so no extra packages are needed.

3. **Run the app**
   ```
   python cr3_lens_tagger.py
   ```

## Using it

1. **Add Files... / Add Folder...** — select the CR3s from one shoot (Add
   Folder grabs every `.CR3` in that folder, non-recursive).
2. Optionally pick a **saved lens preset** to auto-fill the fields, or type
   in Focal Length / Lens Maker / Lens Model directly.
3. **Save current as preset** stores whatever's in the fields for reuse next
   time you shoot with that lens (presets persist between sessions, stored in
   `%APPDATA%\CR3LensTagger\presets.json`).
4. **Apply to all selected files** runs exiftool once against the whole
   batch and writes the standard EXIF tags `FocalLength`, `LensMake`, and
   `LensModel`.
5. By default exiftool keeps a `filename.CR3_original` backup of every file
   it touches. Uncheck **"Keep original backup files"** if you don't want
   those (they're a safety net — recommended to leave this on until you
   trust the workflow).

## Notes

- These are the same standard EXIF tags Canon writes automatically for
  electronic RF/EF lenses, so the values will show up normally in Lightroom,
  Capture One, DigiKam, etc.
- exiftool batches all files in a single process call rather than looping,
  which is both much faster and the officially recommended approach for
  large batches.
- If a shoot mixes multiple vintage lenses, just run the tool once per lens
  — select only the files shot with that lens each time.
