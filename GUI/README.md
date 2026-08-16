# CrateGuard UI

Interfaz web minimalista para CrateGuard.

## Ejecutar

```bash
npm install
npm run dev
```

## Backend

Desde la raíz del proyecto:

```bash
python converter.py --server
```

Luego en la UI usa la URL del backend:

```text
http://127.0.0.1:8765
```

## Uso

- Pega la ruta completa del archivo `.m3u8`
- La UI muestra el análisis en tiempo real
- Puedes usar el modo demo para probar la interfaz sin backend

## Nota

Los navegadores no pueden leer rutas locales completas desde un input de archivo, por eso conviene pegar la ruta completa manualmente.
