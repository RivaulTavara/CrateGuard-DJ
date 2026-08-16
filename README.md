# CrateGuard

Proyecto para analizar, convertir y exportar archivos de audio con una interfaz web y un backend local.

## Requisitos

- Python
- Node.js + npm
- VS Code

## Arranque con los tasks del workspace

En VS Code, ejecuta estos dos tasks:

1. CrateGuard Backend: Server
2. CrateGuard UI: Dev Server

Esto levanta:

- Backend: http://127.0.0.1:8765
- Frontend: http://localhost:5173/

## Arranque manual

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

## Uso

- Abre la UI en http://localhost:5173/
- Usa la URL del backend: http://127.0.0.1:8765
- Si necesitas pasar rutas locales, copia la ruta completa del archivo o playlist

## Nota

La UI es una app Vite y el backend es un servidor local Python. Los archivos generados, temporales y dependencias locales quedan ignorados para que el repositorio de GitHub quede limpio.
