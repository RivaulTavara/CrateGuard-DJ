# CrateGuard UI

Dark-mode React dashboard for CrateGuard, focused on real-time playlist analysis.

## Modules

- Deep Scan
- Safe Export (-12 LUFS)
- Single Convert
- Integrity Check
- Cover Tools
- USB Serato Clone

## Run the UI

```bash
npm install
npm run dev
```

## Start the Python scan server

From the project root (one level up from this GUI folder):

```bash
python crateguard_server.py
```

Then set the Backend URL in the UI to `http://127.0.0.1:8765` and provide a `.m3u8` path.

Note: browsers cannot read full local file paths from file inputs. Paste full paths when needed.

## Notes

- Demo Mode can be used without the backend to preview the interface.
- The backend streams SSE events to populate the table in real time.
