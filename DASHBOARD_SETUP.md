# SOHO Comet Hunter Dashboard — Easy Setup

This folder is designed to be copied into the ROOT of your existing comet-hunter repository.

You do not need React, npm, Node.js, a database, or a web server.

## What gets added

- `docs/` — the actual GitHub Pages website
- `tools/update_dashboard.py` — scans your comet output and updates the website data

The dashboard starts with clearly marked DEMO candidates. They disappear automatically once the updater finds your real candidate videos.

---

# Part 1 — Copy these files into your comet project

Your project should look roughly like this:

```text
comet_hunter_native_cuda_v7_archive/
├── docs/
│   ├── index.html
│   ├── styles.css
│   ├── app.js
│   ├── data/
│   └── media/
├── tools/
│   └── update_dashboard.py
├── results/
│   └── visual/
│       ├── G0002_1024/
│       │   └── annotated.mp4
│       └── G0010_1024/
│           └── annotated.mp4
└── ...your existing comet hunter files...
```

---

# Part 2 — Test the dashboard updater

From the ROOT of your comet project, run:

```bash
python3 tools/update_dashboard.py
```

The script looks in:

```text
results/visual/
```

It finds candidate folders containing `annotated*.mp4`, builds `docs/data/candidates.json`, and copies media smaller than 50 MB into `docs/media/`.

If your results folder is somewhere else, use:

```bash
python3 tools/update_dashboard.py --source /full/path/to/results/visual
```

For the project path you were using on Bourbaki, if you are already inside:

```bash
cd ~/comethunting/comet_hunter_native_cuda_v7_archive
python3 tools/update_dashboard.py
```

A successful run should end with output similar to:

```text
[OK] Found 2 candidate folder(s).
[OK] Wrote .../docs/data/candidates.json
[OK] Wrote .../docs/data/status.json
```

---

# Part 3 — Optional: add better metadata

The dashboard works without this.

If you want confidence, family, first-seen time, etc. to appear accurately, create a file named:

```text
dashboard.json
```

inside a candidate folder.

Example:

```json
{
  "title": "G0002 Kreutz candidate",
  "confidence": 92.4,
  "family": "Kreutz",
  "status": "candidate",
  "source": "SOHO C2",
  "instrument": "LASCO C2",
  "first_seen": "2026-09-28T05:42:11Z",
  "frames": 11,
  "notes": "Faint coherent inbound track. Needs human verification."
}
```

Allowed status values are:

```text
candidate
review
known
confirmed
rejected
```

Do NOT mark an object `confirmed` merely because the detector likes it. Use that only after external confirmation.

---

# Part 4 — Push it to GitHub

Still inside the repository:

```bash
git add docs tools
git commit -m "Add comet hunter dashboard"
git push
```

If Git tells you there is nothing to commit, check:

```bash
git status
```

---

# Part 5 — Turn on GitHub Pages

On GitHub:

1. Open your comet-hunter repository.
2. Click **Settings**.
3. In the left menu, click **Pages**.
4. Under **Build and deployment**, set **Source** to:
   `Deploy from a branch`
5. Set the branch to:
   `main`
6. Set the folder to:
   `/docs`
7. Click **Save**.

GitHub will publish the site at a URL similar to:

```text
https://YOUR-USERNAME.github.io/YOUR-REPOSITORY/
```

---

# Part 6 — Update the dashboard after new comet runs

Whenever your detector has produced new candidate videos:

```bash
python3 tools/update_dashboard.py
git add docs
git commit -m "Update comet candidates"
git push
```

That is the normal workflow.

---

# Very important

The website is public when published with GitHub Pages. Do not put secrets, passwords, API tokens, private keys, unpublished credentials, or sensitive data anywhere in `docs/`.

The word `candidate` is deliberate. An automated detection is not automatically a new comet or an official discovery.

---

# If a video does not appear

The updater deliberately skips media larger than 50 MB to keep the Git repository manageable.

You can check video sizes with:

```bash
du -h results/visual/*/*.mp4
```

If necessary, make a smaller web copy with ffmpeg:

```bash
ffmpeg -i annotated.mp4 -c:v libx264 -crf 28 -preset medium -an annotated_web.mp4
```

Then either rename that smaller file to `annotated.mp4`, or point `video` to a hosted file through `dashboard.json`.

---

# Local preview

Opening `docs/index.html` by double-clicking it may block JSON loading in some browsers.

Instead, from the repository root run:

```bash
python3 -m http.server 8000 --directory docs
```

Then open:

```text
http://localhost:8000
```

Press `Ctrl+C` to stop the local preview server.
