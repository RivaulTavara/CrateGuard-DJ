# Converter Auto Export

Project for converting media files and exporting them through a local workflow, with a Python backend and a Vite-based frontend UI.

## Structure

- `converter.py` and related scripts: backend conversion logic
- `GUI/`: frontend application
- `exports/`: generated export output
- `logs/`: runtime logs
- `config.json`: local configuration

## Requirements

- Python 3.x
- Node.js and npm for the GUI

## Run locally

### Backend

```bash
python converter.py --server
```

### Frontend

```bash
cd GUI
npm install
npm run dev
```

## Notes

This repository is configured to ignore local virtual environments, generated outputs, and editor metadata so it stays clean for GitHub.
