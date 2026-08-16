function createEventStream({ url, onEvent, onError }) {
  const source = new EventSource(url)
  let finished = false
  const listener = (event) => {
    try {
      const data = JSON.parse(event.data)
      onEvent?.(event.type, data)
      if (event.type === "done") {
        finished = true
        source.close()
      }
    } catch (err) {
      onError?.(err)
    }
  }

  source.addEventListener("progress", listener)
  source.addEventListener("row", listener)
  source.addEventListener("item", listener)
  source.addEventListener("summary", listener)
  source.addEventListener("done", listener)
  source.addEventListener("error", (event) => {
    if (finished || source.readyState === EventSource.CLOSED) {
      return
    }

    const raw = typeof event?.data === "string" ? event.data.trim() : ""
    if (raw) {
      try {
        const data = JSON.parse(raw)
        onEvent?.("error", data)
        source.close()
        return
      } catch {
        // ignore and fall back to transport error handling
      }
    }

    onError?.(event)
    source.close()
  })

  return {
    cancel: () => source.close(),
  }
}

export function createApiClient(baseUrl) {
  const waveformCache = new Map()
  const coverCache = new Map()
  const lufsCache = new Map()
  const audioBlobCache = new Map()
  const audioBlobInFlight = new Map()
  const audioPrewarmCache = new Map()
  const audioPrewarmInFlight = new Map()
  const ramAudioCache = new Map()
  const ramAudioInFlight = new Map()
  const MAX_WAVEFORM_CACHE_ITEMS = 40
  const MAX_COVER_CACHE_ITEMS = 300
  const MAX_LUFS_CACHE_ITEMS = 300
  const MAX_AUDIO_BLOB_CACHE_ITEMS = 8
  const MAX_AUDIO_PREWARM_CACHE_ITEMS = 32
  const MAX_RAM_AUDIO_CACHE_BYTES = 160 * 1024 * 1024
  const MAX_RAM_AUDIO_TRACK_BYTES = 64 * 1024 * 1024
  const waveformVersion = "6"
  const getWaveformCacheKey = (trackPath) => `${trackPath}::wv${waveformVersion}`
  const BLOB_PREVIEW_EXTS = new Set([])
  const COMPAT_PREWARM_EXTS = new Set(['aif', 'aiff'])
  const PARTIAL_PREFETCH_BYTES = 320 * 1024
  const EXTENDED_PREFETCH_BYTES = 2 * 1024 * 1024

  const getTrackExt = (trackPath) => {
    const raw = String(trackPath || '').trim()
    if (!raw) return ''
    const normalized = raw.replace(/\\/g, '/')
    const fileName = normalized.split('/').pop() || ''
    const dotIndex = fileName.lastIndexOf('.')
    if (dotIndex < 0) return ''
    return fileName.slice(dotIndex + 1).toLowerCase()
  }

  const shouldUseBlobPreview = (trackPath) => {
    const ext = getTrackExt(trackPath)
    return BLOB_PREVIEW_EXTS.has(ext)
  }

  const shouldUseCompatPreview = (trackPath) => {
    const ext = getTrackExt(trackPath)
    return COMPAT_PREWARM_EXTS.has(ext)
  }

  const putCacheWithLimit = (cacheMap, key, value, maxItems, onEvict) => {
    if (!key) return
    if (cacheMap.has(key)) {
      cacheMap.delete(key)
    }
    cacheMap.set(key, value)
    while (cacheMap.size > maxItems) {
      const oldestKey = cacheMap.keys().next().value
      if (oldestKey === undefined) break
      const oldestValue = cacheMap.get(oldestKey)
      cacheMap.delete(oldestKey)
      if (onEvict) {
        try {
          onEvict(oldestValue)
        } catch {
          void 0
        }
      }
    }
  }

  const getRamCacheEntry = (trackPath) => {
    if (!trackPath || !ramAudioCache.has(trackPath)) return null
    const entry = ramAudioCache.get(trackPath)
    ramAudioCache.delete(trackPath)
    ramAudioCache.set(trackPath, entry)
    return entry
  }

  const getRamCacheTotalBytes = () => {
    let total = 0
    for (const entry of ramAudioCache.values()) {
      total += Number(entry?.sizeBytes || 0)
    }
    return total
  }

  const evictRamCacheUntilFits = (requiredBytes = 0) => {
    let total = getRamCacheTotalBytes()
    while (ramAudioCache.size && total + requiredBytes > MAX_RAM_AUDIO_CACHE_BYTES) {
      const oldestKey = ramAudioCache.keys().next().value
      if (!oldestKey) break
      const oldest = ramAudioCache.get(oldestKey)
      ramAudioCache.delete(oldestKey)
      try {
        if (oldest?.blobUrl) {
          URL.revokeObjectURL(oldest.blobUrl)
        }
      } catch {
        // ignore
      }
      total -= Number(oldest?.sizeBytes || 0)
    }
  }

  const putRamCachedAudio = (trackPath, blobUrl, sizeBytes, mode) => {
    if (!trackPath || !blobUrl || !Number.isFinite(sizeBytes) || sizeBytes <= 0) return ''

    const existing = ramAudioCache.get(trackPath)
    if (existing?.blobUrl && existing.blobUrl !== blobUrl) {
      try {
        URL.revokeObjectURL(existing.blobUrl)
      } catch {
        // ignore
      }
    }
    if (existing) {
      ramAudioCache.delete(trackPath)
    }

    evictRamCacheUntilFits(sizeBytes)
    ramAudioCache.set(trackPath, { blobUrl, sizeBytes, mode })

    return blobUrl
  }

  const scan = ({ playlistPath, onRow, onSummary, onStatus, onError, useMock, noLufs }) => {
    const url = new URL("/api/scan", baseUrl)
    if (useMock) {
      url.searchParams.set("demo", "1")
      url.searchParams.set("folder", "C:/Users/rivit/Music/Temitas")
    } else {
      url.searchParams.set("path", playlistPath)
      if (noLufs) {
        url.searchParams.set("noLufs", "1")
      }
    }

    return createEventStream({
      url: url.toString(),
      onEvent: (type, data) => {
        if (type === "progress") {
          onStatus?.({ state: "progress", ...data })
        }
        if (type === "row") {
          onRow?.(data)
        }
        if (type === "summary") {
          onSummary?.(data)
        }
        if (type === "done") {
          onStatus?.({ state: "done" })
        }
        if (type === "error") {
          onError?.(data)
        }
      },
      onError,
    })
  }

  const createTempPlaylist = async ({ tracks, name, coverOverrides }) => {
    const response = await fetch(new URL("/api/create-temp-playlist", baseUrl), {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ tracks, name, coverOverrides })
    })
    return response.json()
  }

  const safeSet = ({ playlistPath, output, artworkDir, embedArt, overwrite, normalize, resample, losslessFormat, lossyFormat, onEvent, onError }) => {
    const url = new URL("/api/safe-set", baseUrl)
    url.searchParams.set("path", playlistPath)
    if (output) url.searchParams.set("output", output)
    if (artworkDir) url.searchParams.set("artworkDir", artworkDir)
    if (embedArt) url.searchParams.set("embedArt", "1")
    if (overwrite) url.searchParams.set("overwrite", "1")
    if (normalize !== undefined) url.searchParams.set("normalize", normalize ? "1" : "0")
    if (resample !== undefined) url.searchParams.set("resample", resample ? "1" : "0")
    if (losslessFormat) url.searchParams.set("losslessFormat", losslessFormat)
    if (lossyFormat) url.searchParams.set("lossyFormat", lossyFormat)

    return createEventStream({
      url: url.toString(),
      onEvent,
      onError,
    })
  }

  const convertSingle = ({ sourcePath, output, artwork, forceAiff, smart, universalTarget, normalize, resample, losslessFormat, lossyFormat, onEvent, onError }) => {
    const url = new URL("/api/convert-single", baseUrl)
    url.searchParams.set("path", sourcePath)
    if (output) url.searchParams.set("output", output)
    if (artwork) url.searchParams.set("artwork", artwork)
    if (forceAiff) url.searchParams.set("forceAiff", "1")
    if (smart !== undefined) url.searchParams.set("smart", smart ? "1" : "0")
    if (universalTarget !== undefined) url.searchParams.set("universalTarget", universalTarget ? "1" : "0")
    if (normalize !== undefined) url.searchParams.set("normalize", normalize ? "1" : "0")
    if (resample !== undefined) url.searchParams.set("resample", resample ? "1" : "0")
    if (losslessFormat) url.searchParams.set("losslessFormat", losslessFormat)
    if (lossyFormat) url.searchParams.set("lossyFormat", lossyFormat)

    return createEventStream({
      url: url.toString(),
      onEvent,
      onError,
    })
  }

  const integrityScan = ({ folder, onEvent, onError }) => {
    const url = new URL("/api/integrity-scan", baseUrl)
    url.searchParams.set("folder", folder)

    return createEventStream({
      url: url.toString(),
      onEvent,
      onError,
    })
  }

  const coversMissing = async (folder) => {
    const url = new URL("/api/covers-missing", baseUrl)
    url.searchParams.set("folder", folder)
    const response = await fetch(url)
    return response.json()
  }

  const coversProcess = ({ source, output, size, onEvent, onError }) => {
    const url = new URL("/api/covers-process", baseUrl)
    url.searchParams.set("source", source)
    url.searchParams.set("output", output)
    if (size) url.searchParams.set("size", String(size))

    return createEventStream({
      url: url.toString(),
      onEvent,
      onError,
    })
  }

  const seratoDrives = async () => {
    const url = new URL("/api/serato-drives", baseUrl)
    const response = await fetch(url)
    return response.json()
  }

  const seratoClone = ({ source, dest, onEvent, onError }) => {
    const url = new URL("/api/serato-clone", baseUrl)
    url.searchParams.set("source", source)
    url.searchParams.set("dest", dest)

    return createEventStream({
      url: url.toString(),
      onEvent,
      onError,
    })
  }

  const waveform = async (trackPath) => {
    const cacheKey = trackPath ? getWaveformCacheKey(trackPath) : ''
    if (cacheKey && waveformCache.has(cacheKey)) {
      return { image: waveformCache.get(cacheKey) }
    }
    const url = new URL("/api/waveform", baseUrl)
    url.searchParams.set("path", trackPath)
    url.searchParams.set("wv", waveformVersion)
    const response = await fetch(url)
    const data = await response.json()
    if (cacheKey && data?.image) {
      putCacheWithLimit(waveformCache, cacheKey, data.image, MAX_WAVEFORM_CACHE_ITEMS)
    }
    return data
  }

  const cover = async (trackPath) => {
    if (trackPath && coverCache.has(trackPath)) {
      return { image: coverCache.get(trackPath) }
    }
    const url = new URL("/api/cover", baseUrl)
    url.searchParams.set("path", trackPath)
    const response = await fetch(url)
    const data = await response.json()
    if (trackPath && data?.image) {
      putCacheWithLimit(coverCache, trackPath, data.image, MAX_COVER_CACHE_ITEMS)
    }
    return data
  }

  const lufs = async (trackPath) => {
    if (!trackPath) return { lufs: '-' }
    if (lufsCache.has(trackPath)) {
      return { lufs: lufsCache.get(trackPath) }
    }
    const url = new URL('/api/lufs', baseUrl)
    url.searchParams.set('path', trackPath)
    const response = await fetch(url)
    const data = await response.json()
    const value = data?.lufs ?? '-'
    if (value !== '-' && value !== null && value !== undefined) {
      putCacheWithLimit(lufsCache, trackPath, value, MAX_LUFS_CACHE_ITEMS)
    }
    return { lufs: value }
  }

  const updateTrackMetadata = async ({ trackPath, artist, title, bpm, key, coverDataUrl, cloneOutputDir, playlistPath }) => {
    const payload = {
      path: trackPath,
      artist: artist || "",
      title: title || "",
    }
    if (bpm !== undefined) {
      payload.bpm = bpm
    }
    if (key !== undefined) {
      payload.key = key
    }
    if (coverDataUrl !== undefined) {
      payload.coverDataUrl = coverDataUrl || ""
    }

    if (cloneOutputDir !== undefined) {
      payload.cloneOutputDir = cloneOutputDir || ""
    }

    if (playlistPath !== undefined) {
      payload.playlistPath = playlistPath || ""
    }

    const response = await fetch(new URL("/api/update-track-metadata", baseUrl), {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    })
    return response.json()
  }

  const getAppConfig = async () => {
    const response = await fetch(new URL('/api/app-config', baseUrl))
    return response.json()
  }

  const pickFolder = async (initialPath) => {
    const url = new URL('/api/pick-folder', baseUrl)
    if (initialPath) {
      url.searchParams.set('initial', String(initialPath))
    }
    const response = await fetch(url)
    return response.json()
  }

  const pickFile = async (initialPath) => {
    const url = new URL('/api/pick-file', baseUrl)
    if (initialPath) {
      url.searchParams.set('initial', String(initialPath))
    }
    const response = await fetch(url)
    return response.json()
  }

  const saveAppConfig = async ({ cloneOutputDir }) => {
    const response = await fetch(new URL('/api/app-config', baseUrl), {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ cloneOutputDir: cloneOutputDir || '' }),
    })
    return response.json()
  }

  const extractCoverFromAudioFile = async (file) => {
    if (!file) return { image: null }

    const fileToBase64 = () => new Promise((resolve, reject) => {
      const reader = new FileReader()
      reader.onload = () => {
        const dataUrl = String(reader.result || "")
        const comma = dataUrl.indexOf(",")
        if (!dataUrl.startsWith("data:") || comma < 0) {
          reject(new Error("No se pudo serializar el archivo"))
          return
        }
        resolve(dataUrl.slice(comma + 1))
      }
      reader.onerror = () => reject(new Error("Error leyendo archivo"))
      reader.readAsDataURL(file)
    })

    const audioBase64 = await fileToBase64()
    const response = await fetch(new URL("/api/extract-cover-from-upload", baseUrl), {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        fileName: file.name || "audio.bin",
        audioBase64,
      }),
    })
    return response.json()
  }

  const clearSessionCache = () => {
    for (const blobUrl of audioBlobCache.values()) {
      try {
        URL.revokeObjectURL(blobUrl)
      } catch {
        // ignore
      }
    }
    audioBlobCache.clear()
    audioBlobInFlight.clear()
    audioPrewarmCache.clear()
    audioPrewarmInFlight.clear()
    for (const entry of ramAudioCache.values()) {
      try {
        if (entry?.blobUrl) {
          URL.revokeObjectURL(entry.blobUrl)
        }
      } catch {
        // ignore
      }
    }
    ramAudioCache.clear()
    ramAudioInFlight.clear()
    waveformCache.clear()
    coverCache.clear()
    lufsCache.clear()
  }

  const getRamCachedAudioSrc = (trackPath) => {
    const key = String(trackPath || '').trim()
    if (!key) return ''
    const entry = getRamCacheEntry(key)
    return entry?.blobUrl || ''
  }

  const warmRamPlaybackCache = async (trackPath, options = {}) => {
    const key = String(trackPath || '').trim()
    if (!key) return ''

    const cached = getRamCacheEntry(key)
    if (cached?.blobUrl) {
      return cached.blobUrl
    }

    if (ramAudioInFlight.has(key)) {
      return await ramAudioInFlight.get(key)
    }

    const forceCompat = options?.forceCompat === true || shouldUseCompatPreview(key)
    const maxTrackBytesRaw = Number(options?.maxTrackBytes)
    const maxTrackBytes = Number.isFinite(maxTrackBytesRaw) && maxTrackBytesRaw > 0
      ? Math.min(MAX_RAM_AUDIO_CACHE_BYTES, Math.max(1, Math.floor(maxTrackBytesRaw)))
      : MAX_RAM_AUDIO_TRACK_BYTES

    const loadPromise = (async () => {
      const url = new URL('/api/audio-file', baseUrl)
      url.searchParams.set('path', key)
      if (forceCompat) {
        url.searchParams.set('preview', '1')
      }

      const response = await fetch(url)
      if (!response.ok) {
        throw new Error(`No se pudo cargar audio en RAM (${response.status})`)
      }

      const contentLength = Number(response.headers.get('content-length') || 0)
      if (Number.isFinite(contentLength) && contentLength > 0 && contentLength > maxTrackBytes) {
        return ''
      }

      const audioBlob = await response.blob()
      const sizeBytes = Number(audioBlob?.size || 0)
      if (!Number.isFinite(sizeBytes) || sizeBytes <= 0 || sizeBytes > maxTrackBytes) {
        return ''
      }

      const blobUrl = URL.createObjectURL(audioBlob)
      return putRamCachedAudio(key, blobUrl, sizeBytes, forceCompat ? 'compat' : 'direct')
    })()

    ramAudioInFlight.set(key, loadPromise)
    try {
      return await loadPromise
    } catch {
      return ''
    } finally {
      ramAudioInFlight.delete(key)
    }
  }

  const audioPreviewUrl = (trackPath) => {
    if (!trackPath) return ''
    const url = new URL('/api/audio-file', baseUrl)
    url.searchParams.set('path', trackPath)
    return url.toString()
  }

  const audioPreviewCompatUrl = (trackPath) => {
    if (!trackPath) return ''
    const url = new URL('/api/audio-file', baseUrl)
    url.searchParams.set('path', trackPath)
    url.searchParams.set('preview', '1')
    return url.toString()
  }

  const audioPreviewBlobUrl = async (trackPath) => {
    if (!trackPath) return ''
    if (!shouldUseBlobPreview(trackPath)) return ''
    if (audioBlobCache.has(trackPath)) {
      return audioBlobCache.get(trackPath)
    }
    if (audioBlobInFlight.has(trackPath)) {
      return await audioBlobInFlight.get(trackPath)
    }
    const loadPromise = (async () => {
      const url = new URL('/api/audio-file', baseUrl)
      url.searchParams.set('path', trackPath)
      const response = await fetch(url)
      if (!response.ok) {
        throw new Error(`No se pudo cargar audio (${response.status})`)
      }
      const audioBlob = await response.blob()
      const blobUrl = URL.createObjectURL(audioBlob)
      putCacheWithLimit(audioBlobCache, trackPath, blobUrl, MAX_AUDIO_BLOB_CACHE_ITEMS, (urlToRevoke) => {
        if (urlToRevoke) {
          URL.revokeObjectURL(urlToRevoke)
        }
      })
      return blobUrl
    })()

    audioBlobInFlight.set(trackPath, loadPromise)
    try {
      return await loadPromise
    } finally {
      audioBlobInFlight.delete(trackPath)
    }
  }

  const prewarmAudioPreview = async (trackPath, options = {}) => {
    if (!trackPath) return

    const stage = String(options?.stage || 'partial').toLowerCase()
    const clipSecondsRaw = Number(options?.clipSeconds)
    const clipSeconds = Number.isFinite(clipSecondsRaw) && clipSecondsRaw > 0
      ? Math.max(1, Math.min(60, Math.round(clipSecondsRaw)))
      : 0

    const ext = getTrackExt(trackPath)
    const needsCompat = COMPAT_PREWARM_EXTS.has(ext)
    const cacheKey = `${trackPath}::${stage}::${clipSeconds}::${needsCompat ? 'compat' : 'direct'}`

    if (audioPrewarmCache.has(cacheKey)) return
    if (audioPrewarmInFlight.has(cacheKey)) {
      await audioPrewarmInFlight.get(cacheKey)
      return
    }

    const prewarmPromise = (async () => {
      const url = new URL('/api/audio-file', baseUrl)
      url.searchParams.set('path', trackPath)

      if (needsCompat) {
        url.searchParams.set('preview', '1')
        if (clipSeconds > 0) {
          url.searchParams.set('clipSeconds', String(clipSeconds))
        }
      }

      const headers = {}
      if (stage === 'partial' && !needsCompat) {
        headers.Range = `bytes=0-${PARTIAL_PREFETCH_BYTES - 1}`
      } else if (stage !== 'partial') {
        headers.Range = `bytes=0-${EXTENDED_PREFETCH_BYTES - 1}`
      }

      const response = await fetch(url, {
        headers,
      })
      if (!response.ok) {
        throw new Error(`Prewarm failed (${response.status})`)
      }
      await response.arrayBuffer()

      putCacheWithLimit(audioPrewarmCache, cacheKey, true, MAX_AUDIO_PREWARM_CACHE_ITEMS)
    })()

    audioPrewarmInFlight.set(cacheKey, prewarmPromise)
    try {
      await prewarmPromise
    } catch {
      // ignore prewarm failures
    } finally {
      audioPrewarmInFlight.delete(cacheKey)
    }
  }

  return {
    scan,
    createTempPlaylist,
    safeSet,
    convertSingle,
    integrityScan,
    coversMissing,
    coversProcess,
    seratoDrives,
    seratoClone,
    waveform,
    cover,
    lufs,
    updateTrackMetadata,
    getAppConfig,
    pickFile,
    pickFolder,
    saveAppConfig,
    extractCoverFromAudioFile,
    clearSessionCache,
    audioPreviewUrl,
    audioPreviewCompatUrl,
    audioPreviewBlobUrl,
    prewarmAudioPreview,
    getRamCachedAudioSrc,
    warmRamPlaybackCache,
  }
}
