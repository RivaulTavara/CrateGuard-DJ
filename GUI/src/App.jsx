import { useMemo, useRef, useState, useEffect } from 'react'
import { AudioLines, ShieldCheck, Wand2, Scan, Image, Usb, AlertCircle, AlertTriangle, CheckCircle, Wrench, Pencil, Play, Pause, Volume2, SkipBack, SkipForward } from 'lucide-react'
import './App.css'
import { createApiClient } from './services/apiClient'

const VIEWS = [
  { id: 'scan', label: 'Analizar', icon: Scan },
  { id: 'safe', label: 'Exportacion Segura', icon: ShieldCheck },
  { id: 'single', label: 'Conversion Individual', icon: Wand2 },
  { id: 'integrity', label: 'Chequeo de Integridad', icon: AudioLines },
  { id: 'covers', label: 'Herramientas de Portadas', icon: Image },
  { id: 'usb', label: 'USB Export', icon: Usb },
]

const MOCK_WAVEFORM_IMAGE =
  'data:image/svg+xml;utf8,' +
  encodeURIComponent(`<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1200 320" preserveAspectRatio="none">
  <defs>
    <linearGradient id="bg" x1="0" y1="0" x2="0" y2="1">
      <stop offset="0%" stop-color="#0f1726"/>
      <stop offset="100%" stop-color="#090d16"/>
    </linearGradient>
    <linearGradient id="wave" x1="0" y1="0" x2="1" y2="0">
      <stop offset="0%" stop-color="#7b4dff"/>
      <stop offset="100%" stop-color="#00e0ff"/>
    </linearGradient>
  </defs>
  <rect width="1200" height="320" fill="url(#bg)"/>
  <path d="M0 160 L24 146 L48 174 L72 120 L96 198 L120 146 L144 168 L168 116 L192 202 L216 140 L240 172 L264 126 L288 194 L312 152 L336 166 L360 132 L384 188 L408 154 L432 170 L456 124 L480 196 L504 150 L528 172 L552 118 L576 202 L600 146 L624 170 L648 122 L672 198 L696 148 L720 168 L744 130 L768 192 L792 152 L816 170 L840 124 L864 196 L888 146 L912 174 L936 120 L960 202 L984 150 L1008 170 L1032 126 L1056 194 L1080 148 L1104 172 L1128 132 L1152 188 L1176 154 L1200 166" fill="none" stroke="url(#wave)" stroke-width="2.4" stroke-linecap="round"/>
</svg>`)

const LufsMeter = ({ lufs }) => {
  const lufsStr = String(lufs || '');
  const lufsMatch = lufsStr.match(/-?\d+(\.\d+)?/);
  const lufsVal = lufsMatch ? parseFloat(lufsMatch[0]) : NaN;
  
  if (isNaN(lufsVal)) return <div className="lufs-readout lufs-readout-na">N/A</div>;
  
  let tone = 'optimized'
  let bandLabel = 'PUNCHY / OPTIMIZED'
  if (lufsVal > -5.0) {
    tone = 'squashed'
    bandLabel = 'SQUASHED'
  } else if (lufsVal < -12.1) {
    tone = 'dynamic'
    bandLabel = 'DYNAMIC'
  }

  return (
    <div className={`lufs-readout lufs-readout-${tone}`}>
      <span className="lufs-readout-value">{lufsVal.toFixed(1)}</span>
      <span className="lufs-readout-band">{bandLabel}</span>
      <span className="lufs-readout-unit">LUFS</span>
    </div>
  );
};

const scanColumns = ['#', 'Cover', 'Track', 'Formato', 'Hz', 'Bits', 'LUFS', 'Estado']
const defaultScanColumnWidths = [42, 76, 418, 96, 88, 88, 82, 178]
const minScanColumnWidths = [40, 58, 260, 86, 80, 80, 76, 154]
const scanColumnWidthsStorageKey = 'crateguard.scanColumnWidths.v1'
const previewVolumeStorageKey = 'crateguard.previewVolume.v1'
const waveformRenderVersion = '6'
const MAX_SCAN_ROWS_IN_MEMORY = 10000
const IMAGE_EXTENSIONS = new Set(['jpg', 'jpeg', 'png', 'webp', 'gif', 'bmp'])
const AUDIO_EXTENSIONS = new Set(['wav', 'wave', 'aif', 'aiff', 'flac', 'mp3', 'm4a', 'aac', 'alac', 'mp4'])

const extractFileNameFromPath = (value) => {
  const raw = String(value || '').trim()
  if (!raw) return ''
  const normalized = raw.replace(/\\/g, '/')
  const parts = normalized.split('/')
  return parts[parts.length - 1] || ''
}

const getFileExtension = (value) => {
  const fileName = extractFileNameFromPath(value)
  const dotIndex = fileName.lastIndexOf('.')
  if (dotIndex < 0) return ''
  return fileName.slice(dotIndex + 1).toLowerCase()
}

const getFileStem = (value) => {
  const fileName = extractFileNameFromPath(value)
  const dotIndex = fileName.lastIndexOf('.')
  return (dotIndex < 0 ? fileName : fileName.slice(0, dotIndex)).trim().toLowerCase()
}

const normalizePathKey = (value) => String(value || '').replace(/\\/g, '/').trim().toLowerCase()

const getWaveformCacheKey = (path) => `${String(path || '')}::wv${waveformRenderVersion}`

const CoverThumbnail = ({ trackPath, useMock, customSrc, initialSrc, onPick, getCoverForTrack, autoFetch = true, editable = true }) => {
  const [src, setSrc] = useState(initialSrc || null);
  const inputRef = useRef(null);

  useEffect(() => {
    if (!autoFetch || !trackPath || useMock || !getCoverForTrack) return;
    let isMounted = true;
    const fetchCover = async () => {
      try {
        const data = await getCoverForTrack(trackPath)
        if (isMounted && data?.image) {
          setSrc(data.image)
        }
      } catch {
        // ignore
      }
    }
    fetchCover()
    return () => { isMounted = false }
  }, [autoFetch, trackPath, useMock, getCoverForTrack]);

  const previewSrc = customSrc || src;
  const canEdit = editable && Boolean(trackPath) && !useMock;

  const openPicker = (event) => {
    event?.stopPropagation?.();
    if (!canEdit) return;
    inputRef.current?.click();
  };

  const onInputChange = (event) => {
    event?.stopPropagation?.();
    const file = event.target?.files?.[0];
    if (!file) return;
    onPick?.(file);
    event.target.value = '';
  };

  if (!previewSrc) {
    return (
      <>
        <button
          type="button"
          className={`cover-thumb cover-thumb-empty ${canEdit ? 'cover-thumb-editable' : ''}`}
          onClick={openPicker}
          title={canEdit ? 'Click para cargar portada' : 'Portada no disponible'}
        >
          <AlertCircle size={18} color="#ff5f6d" />
        </button>
        {canEdit ? (
          <input
            ref={inputRef}
            type="file"
            accept="image/*"
            className="file-input-hidden"
            onClick={(event) => event.stopPropagation()}
            onChange={onInputChange}
          />
        ) : null}
      </>
    );
  }

  return (
    <>
      <button
        type="button"
        className={`cover-thumb ${canEdit ? 'cover-thumb-editable' : ''}`}
        onClick={openPicker}
        title={canEdit ? 'Cambiar portada' : 'Portada'}
      >
        <img src={previewSrc} className="cover-thumb-img" alt="Cover" />
      </button>
      {canEdit ? (
        <input
          ref={inputRef}
          type="file"
          accept="image/*"
          className="file-input-hidden"
          onClick={(event) => event.stopPropagation()}
          onChange={onInputChange}
        />
      ) : null}
    </>
  );
};


const integrityColumns = ['Archivo', 'Estado', 'Hz', 'Bits', 'Bitrate', 'Detalles']

const DEVICE_ORDER = [
  { key: 'Pro', label: 'Pro', models: 'CDJ-3000X / CDJ-3000 / CDJ-2000NXS2 / XDJ-AZ / RX3 / OPUS-QUAD' },
  { key: 'Club', label: 'Club', models: 'XDJ-XZ / RX2' },
  { key: 'Legacy', label: 'Legacy', models: 'CDJ-850 / CDJ-900 / CDJ-350 / RX1' },
  { key: 'Software', label: 'Software', models: 'Serato / Rekordbox / Traktor / VirtualDJ' },
]

const STATUS_EMOJI = {
  compatible: '🟢',
  warning: '🟠',
  incompatible: '🔴',
  software: '💻',
}

const DEVICE_GROUP_LABELS = {
  Pro: 'Pro',
  Club: 'Club',
  Legacy: 'Legacy',
  Software: 'Software',
}

function normalizeReasonList(reasons = []) {
  const seen = new Set()
  const cleaned = []
  reasons.forEach((item) => {
    const text = String(item || '').replace(/^(riesgo|razón)\s*:\s*/i, '').trim()
    if (!text) return
    const key = text.toLowerCase()
    if (seen.has(key)) return
    seen.add(key)
    cleaned.push(text)
  })
  return cleaned
}

function getDeviceReasonsForDisplay(row, deviceKey) {
  return normalizeReasonList(row?.deviceReasons?.[deviceKey] || [])
}

function splitTrackLabel(trackLabel) {
  const value = String(trackLabel || '').trim()
  if (!value) return { artist: '-', title: '-' }
  if (value.includes(' - ')) {
    const [artist, ...rest] = value.split(' - ')
    return { artist: artist || '-', title: rest.join(' - ') || '-' }
  }
  return { artist: '-', title: value }
}

function hasExplicitErrorStatus(value) {
  const text = String(value || '').toLowerCase().trim()
  if (!text) return false
  if (/(^|\s)(sin|no)\s+errores?($|\s)/i.test(text)) return false
  if (/(^|\s)(sin|no)\s+error($|\s)/i.test(text)) return false
  if (/(^|\s)without\s+errors?($|\s)/i.test(text)) return false
  if (/(^|\s)ok($|\s)/i.test(text)) return false
  return /(^|\s)(error|critical|critico|crítico|roto|corrupto|corrupted)($|\s|:|-)/i.test(text)
}

function classifyRisk(statusText) {
  const row = statusText && typeof statusText === 'object' ? statusText : null
  const value = String(row?.status ?? statusText ?? '').toLowerCase()
  const explicitScore = Number(row?.compatibilityScore)

  if (Number.isFinite(explicitScore)) {
    if (explicitScore <= 3) return 'critical'
    if (explicitScore <= 8) return 'warning'
    return 'ok'
  }

  if (
    hasExplicitErrorStatus(value) ||
    value.includes('roto') ||
    value.includes('incompatible') ||
    value.includes('convertir obligatorio') ||
    value.includes('conversión obligatoria')
  ) {
    return 'critical'
  }
  if (value.includes('warning') || value.includes('advertencia')) {
    return 'warning'
  }
  return 'ok'
}

const scanStateLabels = {
  idle: 'LISTO',
  starting: 'INICIANDO',
  progress: 'ANALIZANDO',
  done: 'FINALIZADO',
  error: 'ERROR',
}

function formatStatus(statusText) {
  const value = String(statusText || '')
  if (!value) return '-'
  const lower = value.toLowerCase()
  let label = value
  if (lower.includes('roto')) return 'ROTO'
  if (lower.includes('limitado')) return 'LIMITADO'
  if (lower.includes('warning')) label = label.replace(/warning/gi, 'ADVERTENCIA')
  if (lower.includes('incompatible')) label = label.replace(/incompatible/gi, 'INCOMPATIBLE')
  if (lower.includes('compatible')) label = 'COMPATIBLE'
  if (lower === 'ok') label = 'COMPATIBLE'
  return label
}

function formatCodec(codec, bits, path) {
  const raw = String(codec || '')
  const lower = raw.toLowerCase()
  const ext = String(path || '').split('.').pop()?.toLowerCase() || ''
  if (ext === 'flac') return 'FLAC'
  if (ext === 'm4a') {
    if (lower.includes('aac')) return 'AAC'
    if (lower.includes('alac')) return 'ALAC'
    return 'M4A'
  }
  if (ext === 'aac') return 'AAC'
  if (ext === 'mp3') return 'MP3'
  if (ext === 'alac') return 'ALAC'
  if (ext === 'wav' || ext === 'wave') return 'WAV'
  if (ext === 'aif' || ext === 'aiff') return 'AIFF'
  if (lower.includes('wav')) return 'WAV'
  if (lower.includes('aiff') || lower.includes('aif')) return 'AIFF'
  if (lower.includes('flac')) return 'FLAC'
  if (lower.includes('mp3')) return 'MP3'
  if (lower.includes('m4a')) return 'M4A'
  if (lower.includes('aac')) return 'AAC'
  if (lower.includes('alac')) return 'ALAC'
  if (bits && lower.includes('pcm')) return 'PCM'
  if (lower.includes('mjpeg') || lower.includes('jpeg')) {
    if (ext === 'flac') return 'FLAC'
    if (ext === 'm4a') return 'M4A'
    if (ext === 'aac') return 'AAC'
    if (ext === 'mp3') return 'MP3'
    if (ext === 'alac') return 'ALAC'
    if (ext === 'wav' || ext === 'wave') return 'WAV'
    if (ext === 'aif' || ext === 'aiff') return 'AIFF'
  }
  return raw ? raw.toUpperCase() : '-'
}

function formatBits(bits, codec, bitrate, path) {
  const codecLower = String(codec || '').toLowerCase();
  const ext = String(path || '').split('.').pop()?.toLowerCase() || ''
  const isLossy = codecLower.includes('mp3') || codecLower.includes('mpeg') || codecLower.includes('aac') || codecLower.includes('m4a') || ['mp3', 'aac', 'm4a'].includes(ext)

  if (isLossy) {
    const bitrateNum = Number.parseFloat(String(bitrate || '').replace(/[^\d.]/g, ''))
    if (Number.isFinite(bitrateNum) && bitrateNum > 0) {
      return `${Math.round(bitrateNum)}k`
    }
    const bitsText = String(bits || '').toLowerCase()
    const fromBits = bitsText.match(/(\d{2,4})\s*k/)
    if (fromBits?.[1]) {
      return `${fromBits[1]}k`
    }

    const numericBits = Number.parseInt(String(bits || '').replace(/[^\d]/g, ''), 10)
    if (Number.isFinite(numericBits) && numericBits >= 96) {
      return `${numericBits}k`
    }

    return '-'
  }

  const value = String(bits || '').trim()
  if (!value || value === '-') return '-'
  if (value.includes('float')) return '32-bit float'
  if (value.toLowerCase().includes('bit')) return value
  
  return `${value} bits`
}

function getDeviceStatus(row, deviceKey) {
  const explicitStatus = String(row?.deviceGroups?.[deviceKey]?.status || '').toLowerCase()
  if (explicitStatus === 'incompatible') return 'bad'
  if (explicitStatus === 'warning') return 'warn'
  if (explicitStatus === 'compatible') return 'ok'

  if (!row?.deviceSupport) return 'ok'
  if (row.deviceSupport.incompatible?.includes(deviceKey)) return 'bad'
  if (row.deviceSupport.warning?.includes(deviceKey)) return 'warn'
  return 'ok'
}

function getDeviceEmoji(deviceKey, status) {
  if (deviceKey === 'Software') return status === 'bad' ? STATUS_EMOJI.incompatible : STATUS_EMOJI.software
  if (status === 'bad') return STATUS_EMOJI.incompatible
  if (status === 'warn') return STATUS_EMOJI.warning
  return STATUS_EMOJI.compatible
}

function parseNumeric(value) {
  const numeric = Number.parseFloat(String(value ?? '').replace(/[^\d.]/g, ''))
  return Number.isFinite(numeric) ? numeric : null
}

function isFestivalSafeTrack(row) {
  const path = String(row?.path || '')
  const ext = getFileExtension(path)
  const codec = String(row?.codec || '').toLowerCase()
  const sampleRate = parseNumeric(row?.sampleRate)
  const bitsText = String(row?.bits || '').toLowerCase()
  const bitDepth = parseNumeric(row?.bits)
  const bitrate = parseNumeric(row?.bitrate) ?? parseNumeric(row?.bits)

  // Only WAV/AIFF 44.1kHz 16-bit or MP3 320kbps are Festival Safe
  const isWavLike = ext === 'wav' || ext === 'wave' || codec.includes('wav') || codec.includes('pcm')
  const isAiffLike = ext === 'aif' || ext === 'aiff' || codec.includes('aiff') || codec.includes('aif')
  const isMp3 = ext === 'mp3' || codec.includes('mp3') || codec.includes('mpeg')

  if (isWavLike || isAiffLike) {
    if (sampleRate === 44100 && bitDepth === 16 && !bitsText.includes('float')) {
      return true;
    }
    return false;
  }
  if (isMp3) {
    if (bitrate >= 320) {
      return true;
    }
    return false;
  }
  return false;
}

function formatDeviceReason(reasons = [], status) {
  const cleanedReasons = normalizeReasonList(reasons)
  if (!cleanedReasons.length) return status === 'ok' ? 'OK' : 'Compatibilidad limitada'
  return cleanedReasons.join(', ')
}

function inferQualityTier(track) {
  const explicit = String(track?.qualityTier || '').toUpperCase()
  if (explicit === 'HQ') return 'HQ'
  if (explicit === 'LOSSY' || explicit === 'STD' || explicit === 'SD') return 'SD'

  const path = String(track?.path || '').toLowerCase()
  if (path.endsWith('.mp3') || path.endsWith('.aac') || path.endsWith('.m4a')) return 'SD'
  if (path.endsWith('.flac') || path.endsWith('.wav') || path.endsWith('.wave') || path.endsWith('.aif') || path.endsWith('.aiff') || path.endsWith('.alac')) return 'HQ'
  return 'SD'
}

function buildHumanExplanation(detailsText) {
  const raw = String(detailsText || '').trim()
  if (!raw) return 'Compatibilidad OK en hardware estandar.'
  const value = raw.toLowerCase()
  if (
    raw.includes('Compatibilidad Total. El estándar de oro para cualquier equipo Pioneer.') ||
    raw.includes('Perfecto para cabinas modernas. Riesgo menor en equipos antiguos.') ||
    raw.includes('Formato de alta fidelidad. Requiere hardware moderno (NXS2/3000). Se recomienda conversión.') ||
    raw.includes("Incompatible. Riesgo de 'Format Error' o glitch digital. Conversión obligatoria.") ||
    raw.includes('Consejo:')
  ) {
    return raw
  }
  if (value.includes('archivo roto')) {
    return 'Archivo roto o ilegible. Necesita reemplazo o reconversion.'
  }
  if (value.includes('32-bit')) {
    return 'Este archivo necesita conversion porque los 32-bit pueden congelar la pantalla de una CDJ-2000.'
  }
  if (value.includes('flac')) {
    return 'FLAC requiere hardware moderno. Convierte a AIFF/MP3 para compatibilidad total.'
  }
  if (value.includes('sample rate alto')) {
    return 'Sample rate alto. Los perfiles con límites de formato/SR/bit depth pueden fallar en la carga.'
  }
  if (value.includes('lossy')) {
    return 'Archivo lossy. Es usable pero con limitaciones de calidad.'
  }
  return 'Compatibilidad OK en hardware estandar.'
}

function inferScore(statusText) {
  const value = String(statusText || '').toLowerCase()
  if (hasExplicitErrorStatus(value)) return 1
  if (value.includes('roto')) return 1
  if (value.includes('incompatible') || value.includes('conversión obligatoria') || value.includes('convertir obligatorio')) return 3
  if (value.includes('limitado')) return 5
  if (value.includes('warning') || value.includes('advertencia')) return 6
  if (value.includes('moderno')) return 8
  if (value.includes('ok') || value.includes('compatible')) return 10
  return 6
}

function bucketFromStatus(statusText) {
  const row = statusText && typeof statusText === 'object' ? statusText : null
  const fallbackStatus = Array.isArray(row?.values) ? row.values[5] : ''
  const value = String(row?.status ?? fallbackStatus ?? statusText ?? '').toLowerCase()
  const explicitScore = Number(row?.compatibilityScore)

  if (value.includes('roto')) return 'broken'

  if (Number.isFinite(explicitScore)) {
    if (explicitScore <= 3) return 'incompatible'
    if (explicitScore <= 8) return 'warning'
    return 'compatible'
  }

  if (hasExplicitErrorStatus(value)) return 'incompatible'
  if (value.includes('incompatible')) return 'incompatible'
  if (value.includes('advertencia') || value.includes('warning') || value.includes('limitado')) return 'warning'
  return 'compatible'
}

function formatIntegrityStatus(statusText) {
  const value = String(statusText || '')
  if (value === 'Warning') return 'Advertencia'
  if (value === 'Error') return 'Error'
  if (value === 'OK') return 'OK'
  return value
}

function formatDurationSeconds(seconds) {
  const totalSeconds = Number(seconds)
  if (!Number.isFinite(totalSeconds) || totalSeconds <= 0) return '--'
  const rounded = Math.floor(totalSeconds)
  const mins = Math.floor(rounded / 60)
  const secs = rounded % 60
  return `${String(mins).padStart(2, '0')}:${String(secs).padStart(2, '0')}`
}

function App() {
  const [activeView, setActiveView] = useState('scan')
  const [backendUrl] = useState('http://127.0.0.1:8765')
  const [scanPath, setScanPath] = useState('')
  const [scanNoLufs, setScanNoLufs] = useState(true)
  const [scanRows, setScanRows] = useState([])
  const [scanRowsCapped, setScanRowsCapped] = useState(false)
  const [scanSummary, setScanSummary] = useState(null)
  const [scanState, setScanState] = useState({ state: 'idle' })
  const [scanError, setScanError] = useState('')
  const [scanPickerBusy, setScanPickerBusy] = useState(false)
  const [useMock, setUseMock] = useState(false)
  const [engineSettings, setEngineSettings] = useState({
    normalize: true,
    resample: true,
    losslessFormat: 'AIFF 16-bit',
    lossyFormat: 'MP3 320k',
  })
  const [selectedTrack, setSelectedTrack] = useState(null)
  const [selectedCoverImage, setSelectedCoverImage] = useState('')
  const [selectedRowKey, setSelectedRowKey] = useState('')
  const [showSettings, setShowSettings] = useState(false)
  const [cloneOutputDir, setCloneOutputDir] = useState('')
  const [waveformImage, setWaveformImage] = useState('')
  const [waveformState, setWaveformState] = useState('idle')
  const [audioSrc, setAudioSrc] = useState('')
  const [isPlaying, setIsPlaying] = useState(false)
  const [playbackTime, setPlaybackTime] = useState(0)
  const [playbackDuration, setPlaybackDuration] = useState(0)
  const [previewVolume, setPreviewVolume] = useState(() => {
    if (typeof window === 'undefined') return 1
    try {
      const raw = window.localStorage.getItem(previewVolumeStorageKey)
      const parsed = Number(raw)
      if (!Number.isFinite(parsed)) return 1
      return Math.max(0, Math.min(1, parsed))
    } catch {
      return 1
    }
  })
  const [playerTrack, setPlayerTrack] = useState(null)
  const [toggleIndicatorVisible, setToggleIndicatorVisible] = useState(false)
  const [skipIndicatorDirection, setSkipIndicatorDirection] = useState('')
  const [isPreparingPlayback, setIsPreparingPlayback] = useState(false)
  const [summaryFilter, setSummaryFilter] = useState('all')
  const [coverOverrides, setCoverOverrides] = useState({})
  const [scanColumnWidths, setScanColumnWidths] = useState(() => {
    if (typeof window === 'undefined') return defaultScanColumnWidths
    try {
      const raw = window.localStorage.getItem(scanColumnWidthsStorageKey)
      if (!raw) return defaultScanColumnWidths
      const parsed = JSON.parse(raw)
      if (!Array.isArray(parsed) || parsed.length !== scanColumns.length) return defaultScanColumnWidths
      const sanitized = parsed.map((value, index) => {
        const number = Number(value)
        const fallback = defaultScanColumnWidths[index] || 80
        if (!Number.isFinite(number) || number <= 0) return fallback
        return Math.max(minScanColumnWidths[index] || 56, Math.round(number))
      })
      return sanitized
    } catch {
      return defaultScanColumnWidths
    }
  })
  const [editArtist, setEditArtist] = useState('')
  const [editTitle, setEditTitle] = useState('')
  const [editBpm, setEditBpm] = useState('')
  const [editKey, setEditKey] = useState('')
  const [saveEditState, setSaveEditState] = useState('idle')
  const [saveEditMessage, setSaveEditMessage] = useState('')
  const coverDoctorInputRef = useRef(null)
  const waveformCacheRef = useRef(new Map())
  const coverCacheRef = useRef(new Map())
  const waveformInFlightRef = useRef(new Map())
  const waveformRequestSeqRef = useRef(0)
  const lufsRequestSeqRef = useRef(0)
  const maxWaveformCacheItems = 40
  const maxCoverCacheItems = 300

  const putRefCacheWithLimit = (cacheRef, key, value, maxItems) => {
    if (!key) return
    const cache = cacheRef.current
    if (cache.has(key)) {
      cache.delete(key)
    }
    cache.set(key, value)
    while (cache.size > maxItems) {
      const oldestKey = cache.keys().next().value
      if (oldestKey === undefined) break
      cache.delete(oldestKey)
    }
  }

  const [safePath, setSafePath] = useState('')
  const [safeOutput, setSafeOutput] = useState('')
  const [safeArtworkDir, setSafeArtworkDir] = useState('')
  const [safeOverwrite, setSafeOverwrite] = useState(true)
  const [safeState, setSafeState] = useState({ state: 'idle' })
  const [safeLog, setSafeLog] = useState([])
  const [safeSummary, setSafeSummary] = useState(null)

  const [singlePath, setSinglePath] = useState('')
  const [singleOutput, setSingleOutput] = useState('')
  const [singleState, setSingleState] = useState({ state: 'idle' })
  const [singleResult, setSingleResult] = useState(null)
  const [singleError, setSingleError] = useState('')

  const [integrityFolder, setIntegrityFolder] = useState('')
  const [integrityRows, setIntegrityRows] = useState([])
  const [integritySummary, setIntegritySummary] = useState(null)
  const [integrityState, setIntegrityState] = useState({ state: 'idle' })

  const [coversFolder, setCoversFolder] = useState('')
  const [coversMissing, setCoversMissing] = useState([])
  const [coversSource, setCoversSource] = useState('')
  const [coversOutput, setCoversOutput] = useState('')
  const [_coversState, setCoversState] = useState({ state: 'idle' })
  const [coversLog, setCoversLog] = useState('')

  const [usbSource, setUsbSource] = useState('')
  const [usbDest, setUsbDest] = useState('')
  const [usbDrives, setUsbDrives] = useState([])
  const [_usbState, setUsbState] = useState({ state: 'idle' })
  const [usbLog, setUsbLog] = useState([])

  const scanRef = useRef(null)
  const resizingRef = useRef(null)
  const audioRef = useRef(null)
  const preloadAudioRef = useRef(null)
  const waveformSeekRef = useRef(null)
  const autoPlayOnSourceChangeRef = useRef(false)
  const pendingSeekRatioRef = useRef(null)
  const pendingSeekSecondsRef = useRef(null)
  const playbackSourceModeRef = useRef(new Map())
  const currentPlaybackModeRef = useRef('direct')
  const toggleIndicatorTimeoutRef = useRef(null)
  const skipIndicatorTimeoutRef = useRef(null)
  const playbackPrewarmSeqRef = useRef(0)
  const preloadedTrackPathRef = useRef('')
  const preloadedAudioSrcRef = useRef('')
  const safeRef = useRef(null)
  const singleRef = useRef(null)
  const integrityRef = useRef(null)
  const coversRef = useRef(null)
  const usbRef = useRef(null)

  const client = useMemo(() => createApiClient(backendUrl), [backendUrl])

  const getCoverForTrack = async (trackPath) => {
    if (!trackPath) return { image: '' }
    if (coverCacheRef.current.has(trackPath)) {
      return { image: coverCacheRef.current.get(trackPath) }
    }
    const data = await client.cover(trackPath)
    if (data?.image) {
      putRefCacheWithLimit(coverCacheRef, trackPath, data.image, maxCoverCacheItems)
    }
    return data
  }

  const getPreviewSrcByMode = async (trackPath, mode) => {
    if (!trackPath) return ''
    if (mode === 'blob') {
      return await (client.audioPreviewBlobUrl?.(trackPath) || '')
    }
    const ramSrc = client.getRamCachedAudioSrc?.(trackPath)
    if (ramSrc) {
      return ramSrc
    }
    if (mode === 'compat') {
      return client.audioPreviewCompatUrl?.(trackPath) || ''
    }
    return client.audioPreviewUrl?.(trackPath) || ''
  }

  const getPlaybackModeOrder = (trackPath) => {
    const ext = String(trackPath || '').split('.').pop()?.toLowerCase() || ''
    if (ext === 'aif' || ext === 'aiff') {
      return ['compat', 'direct', 'blob']
    }
    return ['direct', 'compat', 'blob']
  }

  const getPreviewSrc = async (trackPath, preferredMode) => {
    if (!trackPath) return ''
    const defaultOrder = getPlaybackModeOrder(trackPath)
    const mode = preferredMode || playbackSourceModeRef.current.get(trackPath) || defaultOrder[0]
    const modes = preferredMode ? [mode, ...defaultOrder] : [mode, ...defaultOrder]
    const uniqueModes = [...new Set(modes)]
    for (const candidateMode of uniqueModes) {
      const src = await getPreviewSrcByMode(trackPath, candidateMode)
      if (!src) continue
      playbackSourceModeRef.current.set(trackPath, candidateMode)
      currentPlaybackModeRef.current = candidateMode
      return src
    }
    return ''
  }

  const handleAudioPlaybackError = async () => {
    const trackPath = String(playerTrack?.path || selectedTrack?.path || '').trim()
    const audioEl = audioRef.current
    if (!trackPath || !audioEl) {
      setIsPlaying(false)
      setIsPreparingPlayback(false)
      return
    }

    const modeOrder = getPlaybackModeOrder(trackPath)
    const currentMode = currentPlaybackModeRef.current || playbackSourceModeRef.current.get(trackPath) || modeOrder[0]
    const modeIndex = modeOrder.indexOf(currentMode)
    const nextMode = modeIndex >= 0 ? (modeOrder[modeIndex + 1] || '') : (modeOrder[1] || '')

    if (!nextMode) {
      setIsPlaying(false)
      setIsPreparingPlayback(false)
      setScanError('No se pudo reproducir este archivo en el navegador.')
      return
    }

    const resumeTime = Number.isFinite(audioEl.currentTime) ? audioEl.currentTime : 0
    const nextSrc = await getPreviewSrc(trackPath, nextMode)
    if (!nextSrc || nextSrc === audioSrc) {
      setIsPlaying(false)
      setIsPreparingPlayback(false)
      return
    }

    pendingSeekSecondsRef.current = resumeTime > 0 ? resumeTime : null
    autoPlayOnSourceChangeRef.current = true
    setIsPreparingPlayback(true)
    setAudioSrc(nextSrc)
  }

  const getWaveformForTrack = async (trackPath) => {
    if (!trackPath) return ''
    const cacheKey = getWaveformCacheKey(trackPath)
    const cached = waveformCacheRef.current.get(cacheKey)
    if (cached) return cached

    const inFlight = waveformInFlightRef.current.get(cacheKey)
    if (inFlight) return inFlight

    const request = client
      .waveform(trackPath)
      .then((data) => {
        const image = data?.image || ''
        if (image) {
          putRefCacheWithLimit(waveformCacheRef, cacheKey, image, maxWaveformCacheItems)
        }
        return image
      })
      .finally(() => {
        waveformInFlightRef.current.delete(cacheKey)
      })

    waveformInFlightRef.current.set(cacheKey, request)
    return request
  }

  const getOrderedPlayableRows = () => {
    const visiblePlayableRows = filteredRows.filter((row) => Boolean(row.path))
    const fallbackPlayableRows = scanRows.filter((row) => Boolean(row.path))
    const orderedSource = visiblePlayableRows.length ? visiblePlayableRows : fallbackPlayableRows
    const seenPaths = new Set()
    return orderedSource.filter((row) => {
      const path = String(row.path || '').trim()
      if (!path || seenPaths.has(path)) return false
      seenPaths.add(path)
      return true
    })
  }

  const prewarmNextTrackBrowserBuffer = async (anchorPath, playableRows) => {
    const anchor = String(anchorPath || '').trim()
    if (!anchor || !Array.isArray(playableRows) || !playableRows.length) return

    const preloadEl = preloadAudioRef.current
    if (!preloadEl) return

    const anchorIndex = playableRows.findIndex((row) => String(row.path || '').trim() === anchor)
    if (anchorIndex < 0) return

    const nextPath = String(playableRows[anchorIndex + 1]?.path || '').trim()
    if (!nextPath) return

    if (preloadedTrackPathRef.current === nextPath && preloadedAudioSrcRef.current) {
      return
    }

    const nextSrc = await getPreviewSrc(nextPath)
    if (!nextSrc) return

    preloadedTrackPathRef.current = nextPath
    preloadedAudioSrcRef.current = nextSrc

    if (preloadEl.src !== nextSrc) {
      preloadEl.src = nextSrc
      preloadEl.load()
    }
  }

  const prewarmPlaybackNeighborhood = async (anchorTrackPath) => {
    const anchorPath = String(anchorTrackPath || '').trim()
    if (!anchorPath) return

    const playableRows = getOrderedPlayableRows()
    if (!playableRows.length) return

    const anchorIndex = playableRows.findIndex((row) => row.path === anchorPath)
    if (anchorIndex < 0) return

    const currentPath = anchorPath
    const nextPath = playableRows[anchorIndex + 1]?.path || ''
    const prevPath = playableRows[anchorIndex - 1]?.path || ''

    const partialTargets = [currentPath, nextPath, prevPath]
      .map((path) => String(path || '').trim())
      .filter(Boolean)
    const extendedTargets = [nextPath, currentPath, prevPath]
      .map((path) => String(path || '').trim())
      .filter(Boolean)

    const sequence = ++playbackPrewarmSeqRef.current

    await prewarmNextTrackBrowserBuffer(anchorPath, playableRows)

    for (const path of partialTargets) {
      if (sequence !== playbackPrewarmSeqRef.current) return
      await client.prewarmAudioPreview?.(path, { stage: 'partial', clipSeconds: 10 })
      getWaveformForTrack(path).catch(() => '')
    }

    for (const path of extendedTargets) {
      if (sequence !== playbackPrewarmSeqRef.current) return
      await client.prewarmAudioPreview?.(path, { stage: 'extended' })
    }
  }

  useEffect(() => {
    const releaseSessionCache = () => {
      waveformCacheRef.current.clear()
      coverCacheRef.current.clear()
      preloadedTrackPathRef.current = ''
      preloadedAudioSrcRef.current = ''
      if (preloadAudioRef.current) {
        preloadAudioRef.current.removeAttribute('src')
        preloadAudioRef.current.load()
      }
      client.clearSessionCache?.()
    }

    window.addEventListener('beforeunload', releaseSessionCache)
    return () => {
      window.removeEventListener('beforeunload', releaseSessionCache)
      releaseSessionCache()
    }
  }, [client])

  useEffect(() => {
    let mounted = true
    client.getAppConfig?.()
      .then((cfg) => {
        if (!mounted) return
        const path = String(cfg?.cloneOutputDir || '').trim()
        setCloneOutputDir(path)
      })
      .catch(() => {
        // ignore config fetch errors
      })
    return () => {
      mounted = false
    }
  }, [client])

  const handleFilePick = (file, setPath) => {
    if (!file) return
    const filePath = file.path || file.name
    setPath(filePath)
  }

  const handleScanFilePick = async () => {
    if (scanPickerBusy) return
    setScanPickerBusy(true)
    try {
      const data = await client.pickFile?.(scanPath)
      const selectedPath = String(data?.path || '').trim()
      if (selectedPath) {
        setScanPath(selectedPath.replace(/\//g, '\\'))
        setScanError('')
      }
    } catch {
      setScanError('No se pudo abrir el selector nativo de archivo.')
    } finally {
      setScanPickerBusy(false)
    }
  }

  const handleScanFolderPick = async () => {
    if (scanPickerBusy) return
    setScanPickerBusy(true)
    try {
      const data = await client.pickFolder?.(scanPath)
      const selectedPath = String(data?.path || '').trim()
      if (selectedPath) {
        setScanPath(selectedPath.replace(/\//g, '\\'))
        setScanError('')
      }
    } catch {
      setScanError('No se pudo abrir el selector nativo de carpeta.')
    } finally {
      setScanPickerBusy(false)
    }
  }

  const handleCoverPick = (trackPath, file) => new Promise((resolve) => {
    if (!trackPath || !file) {
      resolve('')
      return
    }
    const reader = new FileReader()
    reader.onload = () => {
      const dataUrl = String(reader.result || '')
      if (!dataUrl.startsWith('data:image/')) {
        resolve('')
        return
      }
      setCoverOverrides((prev) => ({ ...prev, [trackPath]: dataUrl }))
      setSelectedCoverImage(dataUrl)
      resolve(dataUrl)
    }
    reader.onerror = () => resolve('')
    reader.readAsDataURL(file)
  })

  const handleCoverDrop = (event) => {
    event.preventDefault()
    const file = event.dataTransfer?.files?.[0]
    if (!file || !selectedTrack?.path) return
    handleCoverPick(selectedTrack.path, file)
  }

  const handleCoverDoctorSelection = async (file) => {
    if (!file || !selectedTrack?.path) return
    const sourcePath = String(file.path || file.name || '').trim()
    const ext = getFileExtension(sourcePath)
    const mimeType = String(file.type || '').toLowerCase()
    const isImageLike = mimeType.startsWith('image/') || IMAGE_EXTENSIONS.has(ext)

    if (isImageLike) {
      const imageCover = await handleCoverPick(selectedTrack.path, file)
      if (!imageCover) {
        setSaveEditState('error')
        setSaveEditMessage('No se pudo leer la imagen seleccionada')
        return
      }
      setSaveEditState('idle')
      setSaveEditMessage('Cover cargado. Presiona "Guardar cambios" para aplicar al archivo')
      return
    }

    const isAudioLike = AUDIO_EXTENSIONS.has(ext)
    if (!isAudioLike) {
      setSaveEditState('error')
      setSaveEditMessage('Selecciona una imagen o un archivo de audio para clonar cover')
      return
    }

    const candidatePath = String(file.path || '').trim()
    const sourceName = extractFileNameFromPath(candidatePath || file.name || '')
    const sourceNameLower = sourceName.toLowerCase()
    const sourceStem = getFileStem(candidatePath || file.name || '')
    const normalizedCandidatePath = normalizePathKey(candidatePath)

    let sourceRow = null
    if (candidatePath) {
      const exact = scanRowsByPath.get(candidatePath)
      if (exact && exact.path !== selectedTrack.path) {
        sourceRow = exact
      }
    }
    if (!sourceRow && normalizedCandidatePath) {
      sourceRow = scanRows.find((row) => row.path !== selectedTrack.path && normalizePathKey(row.path) === normalizedCandidatePath) || null
    }
    if (!sourceRow && sourceNameLower) {
      sourceRow = scanRows.find((row) => row.path !== selectedTrack.path && extractFileNameFromPath(row.path).toLowerCase() === sourceNameLower) || null
    }
    if (!sourceRow && sourceStem) {
      sourceRow = scanRows.find((row) => row.path !== selectedTrack.path && getFileStem(row.path) === sourceStem) || null
    }

    let sourceRowPath = sourceRow?.path || candidatePath || ''
    if (sourceRowPath && sourceRowPath === selectedTrack.path) {
      sourceRowPath = ''
    }

    let sourceCover = ''
    if (sourceRowPath) {
      sourceCover = coverOverrides[sourceRowPath] || coverCacheRef.current.get(sourceRowPath) || sourceRow?.coverImage || ''
      if (!sourceCover) {
        const data = await getCoverForTrack(sourceRowPath)
        sourceCover = data?.image || ''
      }
    }

    if (!sourceCover) {
      const uploaded = await client.extractCoverFromAudioFile?.(file)
      sourceCover = uploaded?.image || ''
    }

    if (!sourceCover) {
      const selectedName = extractFileNameFromPath(selectedTrack.path).toLowerCase()
      if (sourceNameLower && sourceNameLower === selectedName) {
        setSaveEditState('error')
        setSaveEditMessage('Seleccionaste el mismo track. Elige otro archivo para clonar cover')
        return
      }
      setSaveEditState('error')
      setSaveEditMessage('No se pudo extraer cover del archivo seleccionado')
      return
    }

    setCoverOverrides((prev) => ({ ...prev, [selectedTrack.path]: sourceCover }))
    setSelectedCoverImage(sourceCover)
    setSaveEditState('idle')
    setSaveEditMessage('Cover clonado. Presiona "Guardar cambios" para aplicar al archivo')
  }

  const filteredRows = useMemo(
    () => scanRows.filter((row) => (summaryFilter === 'all' ? true : bucketFromStatus(row) === summaryFilter)),
    [scanRows, summaryFilter],
  )

  const finalizedGlobalMetrics = useMemo(() => {
    if (scanRows.length === 0) return null

    const computedSummary = scanRows.reduce(
      (acc, row) => {
        const bucket = bucketFromStatus(row)
        acc[bucket] += 1
        return acc
      },
      { broken: 0, incompatible: 0, warning: 0, compatible: 0 },
    )

    const visibleGroups = DEVICE_ORDER.filter((device) => scanRows.some((row) => getDeviceStatus(row, device.key) !== 'bad'))

    const compatibleGroups = visibleGroups
      .filter((device) => scanRows.every((row) => getDeviceStatus(row, device.key) === 'ok'))
      .map((device) => DEVICE_GROUP_LABELS[device.key] || device.key)

    const partialCompatibleGroups = visibleGroups
      .map((device) => DEVICE_GROUP_LABELS[device.key] || device.key)

    const validScores = scanRows
      .map((row) => Number(row?.compatibilityScore))
      .filter((value) => Number.isFinite(value))

    const averageScore = validScores.length
      ? (validScores.reduce((acc, value) => acc + value, 0) / validScores.length)
      : null

    return {
      summary: computedSummary,
      fullyCompatibleGroups: compatibleGroups,
      partialCompatibleGroups,
      playlistAverageScore: averageScore,
    }
  }, [scanRows])

  const summaryCounts = finalizedGlobalMetrics?.summary || {
    broken: scanSummary?.broken ?? 0,
    incompatible: scanSummary?.incompatible ?? 0,
    warning: scanSummary?.warning ?? 0,
    compatible: scanSummary?.compatible ?? 0,
  }

  const visibleDeviceGroups = DEVICE_ORDER

  const displayedGlobalMetrics = useMemo(() => {
    if (filteredRows.length === 0) return null

    const displayedSummary = filteredRows.reduce(
      (acc, row) => {
        const bucket = bucketFromStatus(row)
        acc[bucket] += 1
        return acc
      },
      { broken: 0, incompatible: 0, warning: 0, compatible: 0 },
    )

    const visibleGroups = DEVICE_ORDER.filter((device) => filteredRows.some((row) => getDeviceStatus(row, device.key) !== 'bad'))

    const compatibleGroups = visibleGroups
      .filter((device) => filteredRows.every((row) => getDeviceStatus(row, device.key) === 'ok'))
      .map((device) => DEVICE_GROUP_LABELS[device.key] || device.key)

    const partialCompatibleGroups = visibleGroups
      .map((device) => DEVICE_GROUP_LABELS[device.key] || device.key)

    const validScores = filteredRows
      .map((row) => Number(row?.compatibilityScore))
      .filter((value) => Number.isFinite(value))

    const averageScore = validScores.length
      ? (validScores.reduce((acc, value) => acc + value, 0) / validScores.length)
      : null

    return {
      summary: displayedSummary,
      fullyCompatibleGroups: compatibleGroups,
      partialCompatibleGroups,
      playlistAverageScore: averageScore,
    }
  }, [filteredRows])

  const displayedFullyCompatibleGroups = displayedGlobalMetrics?.fullyCompatibleGroups || []
  const displayedPartialCompatibleGroups = displayedGlobalMetrics?.partialCompatibleGroups || []
  const displayedCompatibleGroups = displayedFullyCompatibleGroups.length
    ? displayedFullyCompatibleGroups
    : displayedPartialCompatibleGroups
  const compatibleGroupsText = displayedCompatibleGroups.length
    ? displayedCompatibleGroups.join(' + ')
    : 'Ninguno'

  const totalTracks = summaryCounts.broken + summaryCounts.incompatible + summaryCounts.warning + summaryCounts.compatible
  const hasBrokenTracks = summaryCounts.broken > 0
  const brokenFilesWarningText = 'Hay uno o mas archivos dañados'
  const hasAnalyzedTracks = filteredRows.length > 0
  const isScanning = scanState.state === 'starting' || scanState.state === 'progress'
  const rawPlaylistAverageScore = displayedGlobalMetrics?.playlistAverageScore ?? null
  const hasCompatibleGroup = displayedCompatibleGroups.length > 0
  const onlySoftwareGroup = displayedCompatibleGroups.length === 1 && displayedCompatibleGroups[0] === 'Software'
  const allGroupsCompatible = displayedFullyCompatibleGroups.length === DEVICE_ORDER.length
  const playlistAverageScore = rawPlaylistAverageScore
  const globalResultRisk = isScanning
    ? 'ok'
    : !hasAnalyzedTracks
    ? 'ok'
    : !hasCompatibleGroup
      ? 'critical'
      : playlistAverageScore === null
        ? 'ok'
        : playlistAverageScore >= 9
          ? 'ok'
          : playlistAverageScore >= 7
            ? 'warning'
            : playlistAverageScore >= 5
              ? 'warning'
              : 'warning'
  const averageScoreText = (isScanning || playlistAverageScore === null) ? '--' : playlistAverageScore.toFixed(1)
  const globalScoreLabel = `${averageScoreText}/10`
  const globalScoreRisk = (isScanning || !hasAnalyzedTracks || playlistAverageScore === null)
    ? 'neutral'
    : globalResultRisk
  const globalResultLabel = isScanning
    ? 'ANALIZANDO'
    : !hasAnalyzedTracks
    ? 'Playlist ready'
    : !hasCompatibleGroup
      ? 'INCOMPATIBLE'
      : onlySoftwareGroup
        ? 'SOLO SOFTWARE'
        : allGroupsCompatible
          ? 'TODOS COMPATIBLES'
          : 'COMPATIBLE PARCIAL'
  const globalResultDetail = isScanning
    ? 'Calculando compatibilidad global...'
    : !hasAnalyzedTracks
    ? 'No tracks analyzed'
    : summaryCounts.broken > 0
      ? 'Ningún grupo objetivo puede reproducir esta playlist'
      : `Compatible (${compatibleGroupsText})`
  const festivalSafeSummary = useMemo(() => {
    const total = scanRows.length
    if (!total) {
      return { ratio: null, safe: 0, total: 0, risk: 'neutral', label: 'FESTIVAL SAFE' }
    }
    const safe = scanRows.filter((row) => isFestivalSafeTrack(row)).length
    const ratio = safe / total
    const hasAnyIncompatible = scanRows.some((row) => {
      const bucket = bucketFromStatus(row)
      return bucket === 'broken' || bucket === 'incompatible'
    })

    // Special case: single track and not Festival Safe
    if (total === 1 && safe === 0) {
      return { ratio, safe, total, risk: 'critical', label: 'FESTIVAL SAFE' }
    }

    if (hasAnyIncompatible) {
      return { ratio, safe, total, risk: 'critical', label: 'FESTIVAL SAFE' }
    }

    if (ratio >= 1) {
      return { ratio, safe, total, risk: 'ok', label: 'FESTIVAL SAFE' }
    }

    return { ratio, safe, total, risk: 'warning', label: 'FESTIVAL SAFE' }
  }, [scanRows])
  const criticalPct = totalTracks ? Math.round(((summaryCounts.broken + summaryCounts.incompatible) / totalTracks) * 100) : 0
  const warningPct = totalTracks ? Math.round((summaryCounts.warning / totalTracks) * 100) : 0
  const okPct = totalTracks ? Math.round((summaryCounts.compatible / totalTracks) * 100) : 0
  const hasScanProgress = ['starting', 'progress', 'done'].includes(scanState.state)
  const scanProgressPct = scanState.state === 'done'
    ? 100
    : isScanning && scanState.total
      ? Math.round(((scanState.current || 0) / scanState.total) * 100)
      : 0
  const scanRowsByPath = useMemo(
    () => new Map(scanRows.filter((row) => Boolean(row.path)).map((row) => [row.path, row])),
    [scanRows],
  )

  const resolveTrackForUi = (track) => {
    if (!track?.path) return track
    return scanRowsByPath.get(track.path) || track
  }

  const getTrackCoverSrc = (track) => {
    const path = track?.path
    if (!path) return ''
    const resolved = resolveTrackForUi(track)
    const selectedCoverForPath = selectedTrack?.path === path ? (selectedCoverImage || '') : ''
    return coverOverrides[path] || coverCacheRef.current.get(path) || selectedCoverForPath || resolved?.coverImage || ''
  }

  const selectedTrackResolved = resolveTrackForUi(selectedTrack)
  const playerTrackResolved = resolveTrackForUi(playerTrack)
  const selectedCoverSrc = selectedTrackResolved ? getTrackCoverSrc(selectedTrackResolved) : ''
  const selectedMeta = splitTrackLabel(selectedTrackResolved?.track)
  const selectedBpmBase = String(selectedTrackResolved?.bpm || '').trim()
  const selectedKeyBase = String(selectedTrackResolved?.key || '').trim()
  const selectedBpmSourceBase = String(selectedTrackResolved?.bpmSource || '').trim().toLowerCase()
  const bpmEditedManually = String(editBpm || '').trim() !== selectedBpmBase
  const selectedBpmSourceLabel = bpmEditedManually
    ? 'MANUAL'
    : selectedBpmSourceBase === 'tag'
      ? 'TAG'
      : selectedBpmSourceBase === 'inferred'
        ? 'INFERIDO'
        : selectedBpmBase
          ? 'TAG'
          : '--'
  const selectedOriginalFilename = extractFileNameFromPath(selectedTrackResolved?.path) || '--'
  const selectedTrackLabel = playerTrackResolved?.track || selectedTrackResolved?.track || 'Sin pista seleccionada'
  const activePlaybackTrack = playerTrackResolved || selectedTrackResolved
  const activePlaybackCoverSrc = activePlaybackTrack ? getTrackCoverSrc(activePlaybackTrack) : ''
  const activeDurationSeconds = Number(activePlaybackTrack?.durationSeconds)
  const effectiveDurationSeconds = playbackDuration > 0
    ? playbackDuration
    : (Number.isFinite(activeDurationSeconds) && activeDurationSeconds > 0 ? activeDurationSeconds : 0)
  const selectedDurationSeconds = Number(selectedTrackResolved?.durationSeconds)
  const selectedDurationLabel = formatDurationSeconds(
    Number.isFinite(selectedDurationSeconds) && selectedDurationSeconds > 0 ? selectedDurationSeconds : 0,
  )
  const playbackDurationLabel = formatDurationSeconds(effectiveDurationSeconds)
  const playbackProgressPct = effectiveDurationSeconds > 0
    ? Math.max(0, Math.min(100, (playbackTime / effectiveDurationSeconds) * 100))
    : 0
  const previewVolumePct = Math.max(0, Math.min(100, Math.round(previewVolume * 100)))
  const playbackSeekSeconds = effectiveDurationSeconds > 0
    ? Math.max(0, Math.min(effectiveDurationSeconds, playbackTime))
    : 0
  const isSelectedTrackPlaybackSource = Boolean(selectedTrackResolved?.path && playerTrackResolved?.path && selectedTrackResolved.path === playerTrackResolved.path)
  const selectedWaveformProgressPct = isSelectedTrackPlaybackSource ? playbackProgressPct : 0
  const showGlobalPlayer = Boolean(playerTrack?.path) && (isPlaying || playbackTime > 0 || playbackDuration > 0 || isPreparingPlayback)
  const selectedArtistBase = selectedMeta.artist === '-' ? '' : String(selectedMeta.artist || '').trim()
  const selectedTitleBase = selectedMeta.title === '-' ? '' : String(selectedMeta.title || '').trim()
  const hasArtistChanged = Boolean(selectedTrack) && String(editArtist || '').trim() !== selectedArtistBase
  const hasTitleChanged = Boolean(selectedTrack) && String(editTitle || '').trim() !== selectedTitleBase
  const hasBpmChanged = Boolean(selectedTrack) && String(editBpm || '').trim() !== selectedBpmBase
  const hasKeyChanged = Boolean(selectedTrack) && String(editKey || '').trim() !== selectedKeyBase
  const hasMetadataChanges = Boolean(selectedTrack) && (
    hasArtistChanged ||
    hasTitleChanged ||
    hasBpmChanged ||
    hasKeyChanged
  )
  const hasCoverChanges = Boolean(selectedTrack?.path && coverOverrides[selectedTrack.path])
  const hasPendingTrackChanges = hasMetadataChanges || hasCoverChanges
  const selectedTrackScore = selectedTrackResolved?.compatibilityScore ?? inferScore(selectedTrackResolved?.status)
  const selectedQualityTier = inferQualityTier(selectedTrackResolved)
  const isAnalyzeReady = Boolean(scanPath.trim()) && scanState.state !== 'progress' && scanState.state !== 'done'
  const hasSingleProgress = ['starting', 'progress', 'done'].includes(singleState.state)
  const singleProgressPct = singleState.state === 'done'
    ? 100
    : singleState.total
      ? Math.round(((singleState.current || 0) / singleState.total) * 100)
      : 0

  const startResizeColumn = (index, event) => {
    if (index >= scanColumns.length - 1) return
    event.preventDefault()
    resizingRef.current = {
      index,
      startX: event.clientX,
      startWidth: scanColumnWidths[index] || 90,
    }
  }

  useEffect(() => {
    const onMouseMove = (event) => {
      const resizing = resizingRef.current
      if (!resizing) return
      const delta = event.clientX - resizing.startX
      const minWidth = minScanColumnWidths[resizing.index] || 56
      const nextWidth = Math.max(minWidth, resizing.startWidth + delta)
      setScanColumnWidths((prev) => {
        const copy = [...prev]
        copy[resizing.index] = nextWidth
        return copy
      })
    }

    const onMouseUp = () => {
      resizingRef.current = null
    }

    window.addEventListener('mousemove', onMouseMove)
    window.addEventListener('mouseup', onMouseUp)
    return () => {
      window.removeEventListener('mousemove', onMouseMove)
      window.removeEventListener('mouseup', onMouseUp)
    }
  }, [])

  useEffect(() => {
    if (typeof window === 'undefined') return
    try {
      window.localStorage.setItem(scanColumnWidthsStorageKey, JSON.stringify(scanColumnWidths))
    } catch {
      // ignore storage errors
    }
  }, [scanColumnWidths])

  useEffect(() => {
    return () => {
      if (toggleIndicatorTimeoutRef.current) {
        clearTimeout(toggleIndicatorTimeoutRef.current)
      }
      if (skipIndicatorTimeoutRef.current) {
        clearTimeout(skipIndicatorTimeoutRef.current)
      }
    }
  }, [])

  useEffect(() => {
    const audioEl = audioRef.current
    if (!audioEl) return
    audioEl.volume = previewVolume
  }, [previewVolume])

  useEffect(() => {
    if (typeof window === 'undefined') return
    try {
      window.localStorage.setItem(previewVolumeStorageKey, String(Math.max(0, Math.min(1, previewVolume))))
    } catch {
      // ignore storage errors
    }
  }, [previewVolume])

  useEffect(() => {
    const audioEl = audioRef.current
    if (!audioEl || !audioSrc || !autoPlayOnSourceChangeRef.current) return
    autoPlayOnSourceChangeRef.current = false
    audioEl
      .play()
      .then(() => {
        setIsPlaying(true)
        setIsPreparingPlayback(false)
      })
      .catch(() => {
        setIsPlaying(false)
        setIsPreparingPlayback(false)
      })
  }, [audioSrc])

  useEffect(() => {
    if (!playerTrackResolved?.path || playerTrackResolved.path !== selectedTrackResolved?.path) return
    setPlayerTrack((prev) => prev ? { ...prev, track: selectedTrackResolved.track } : prev)
  }, [selectedTrackResolved?.path, selectedTrackResolved?.track, playerTrackResolved?.path])

  const flashToggleIndicator = () => {
    if (toggleIndicatorTimeoutRef.current) {
      clearTimeout(toggleIndicatorTimeoutRef.current)
    }
    setToggleIndicatorVisible(true)
    toggleIndicatorTimeoutRef.current = setTimeout(() => {
      setToggleIndicatorVisible(false)
    }, 520)
  }

  const flashSkipIndicator = (direction) => {
    if (!direction) return
    if (skipIndicatorTimeoutRef.current) {
      clearTimeout(skipIndicatorTimeoutRef.current)
    }
    setSkipIndicatorDirection(direction)
    skipIndicatorTimeoutRef.current = setTimeout(() => {
      setSkipIndicatorDirection('')
    }, 520)
  }

  const resolvePlaybackTarget = () => {
    if (selectedTrackResolved?.path) {
      return {
        track: selectedTrackResolved,
        cover: selectedCoverSrc || selectedTrackResolved.coverImage || '',
      }
    }
    if (playerTrackResolved?.path) {
      return {
        track: playerTrackResolved,
        cover: getTrackCoverSrc(playerTrackResolved) || playerTrackResolved.coverImage || '',
      }
    }
    return null
  }

  useEffect(() => {
    const onKeyDown = async (event) => {
      if (event.code !== 'Space') return
      const target = event.target
      const tag = target?.tagName?.toLowerCase?.()
      if (tag === 'input' || tag === 'textarea' || tag === 'select' || target?.isContentEditable) return
      event.preventDefault()

      const audioEl = audioRef.current
      if (!audioEl || !audioSrc) return
      try {
        if (audioEl.paused) {
          await audioEl.play()
          setIsPlaying(true)
          flashToggleIndicator('PLAY')
        } else {
          audioEl.pause()
          setIsPlaying(false)
          flashToggleIndicator('PAUSE')
        }
      } catch {
        setIsPlaying(false)
      }
    }

    window.addEventListener('keydown', onKeyDown)
    return () => window.removeEventListener('keydown', onKeyDown)
  }, [audioSrc])

  const toggleAudioPlayback = async () => {
    const audioEl = audioRef.current
    if (!audioEl) return

    const target = resolvePlaybackTarget()
    if (!target?.track?.path) return

    const targetSrc = await getPreviewSrc(target.track.path)
    if (!targetSrc) return

    const switchingTrack = playerTrack?.path !== target.track.path

    try {
      if (isPlaying && !switchingTrack) {
        audioEl.pause()
        setIsPlaying(false)
        flashToggleIndicator('PAUSE')
      } else {
        setPlayerTrack(target.track)

        if (audioSrc !== targetSrc) {
          autoPlayOnSourceChangeRef.current = true
          setIsPreparingPlayback(true)
          setPlaybackTime(0)
          setPlaybackDuration(0)
          setAudioSrc(targetSrc)
          flashToggleIndicator('PLAY')
          return
        }

        await audioEl.play()
        setIsPlaying(true)
        flashToggleIndicator('PLAY')
      }
    } catch {
      setIsPlaying(false)
    }
  }

  const handleWaveformSeek = async (event) => {
    const audioEl = audioRef.current
    const shellEl = waveformSeekRef.current
    const target = resolvePlaybackTarget()
    const seekDuration = Number.isFinite(audioEl?.duration) && audioEl.duration > 0 ? audioEl.duration : effectiveDurationSeconds
    if (!audioEl || !shellEl || !target?.track?.path) return

    const rect = shellEl.getBoundingClientRect()
    if (!rect.width) return
    const ratio = Math.max(0, Math.min(1, (event.clientX - rect.left) / rect.width))

    const currentLoadedTrackPath = String(playerTrack?.path || '').trim()
    const targetTrackPath = String(target.track.path || '').trim()
    if (audioSrc && currentLoadedTrackPath && targetTrackPath && currentLoadedTrackPath === targetTrackPath) {
      setPlayerTrack(target.track)
      if (seekDuration > 0) {
        const nextTime = ratio * seekDuration
        audioEl.currentTime = nextTime
        setPlaybackTime(nextTime)
      }
      if (audioEl.paused) {
        audioEl.play().then(() => {
          setIsPlaying(true)
          flashToggleIndicator('PLAY')
        }).catch(() => setIsPlaying(false))
      }
      return
    }

    const targetSrc = await getPreviewSrc(target.track.path)
    if (!targetSrc) return

    setPlayerTrack(target.track)

    if (audioSrc !== targetSrc) {
      pendingSeekRatioRef.current = ratio
      autoPlayOnSourceChangeRef.current = true
      setIsPreparingPlayback(true)
      setPlaybackTime(0)
      setPlaybackDuration(0)
      setAudioSrc(targetSrc)
      flashToggleIndicator('PLAY')
      return
    }

    if (seekDuration > 0) {
      const nextTime = ratio * seekDuration
      audioEl.currentTime = nextTime
      setPlaybackTime(nextTime)
    }

    if (audioEl.paused) {
      audioEl.play().then(() => {
        setIsPlaying(true)
        flashToggleIndicator('PLAY')
      }).catch(() => setIsPlaying(false))
    }
  }

  const handleTimelineSeek = (event) => {
    const audioEl = audioRef.current
    const nextTime = Number(event.target.value)
    if (!audioEl || !Number.isFinite(nextTime)) return
    if (typeof audioEl.fastSeek === 'function') {
      audioEl.fastSeek(nextTime)
    } else {
      audioEl.currentTime = nextTime
    }
    setPlaybackTime(nextTime)
  }

  const selectTrackFromRow = (row, rowKey) => {
    if (!row) return
    setSaveEditState('idle')
    setSaveEditMessage('')
    setSelectedTrack(row)
    const existingCover = coverOverrides[row.path] || coverCacheRef.current.get(row.path) || row.coverImage || ''
    setSelectedCoverImage(existingCover)
    setSelectedRowKey(rowKey || '')

    const waveformCacheKey = getWaveformCacheKey(row.path)
    const cachedWaveform = waveformCacheRef.current.get(waveformCacheKey)
    const requestId = ++waveformRequestSeqRef.current
    if (cachedWaveform) {
      setWaveformImage(cachedWaveform)
    } else {
      setWaveformImage(useMock ? MOCK_WAVEFORM_IMAGE : '')
    }

    if (!row.path) {
      if (useMock) {
        setWaveformImage(row.waveformImage || cachedWaveform || MOCK_WAVEFORM_IMAGE)
      }
      setWaveformState('idle')
      return
    }

    if (!cachedWaveform) {
      setWaveformState('loading')
      getWaveformForTrack(row.path)
        .then((image) => {
          if (requestId !== waveformRequestSeqRef.current) {
            return
          }
          setWaveformImage(image || (useMock ? MOCK_WAVEFORM_IMAGE : ''))
          setWaveformState('idle')
        })
        .catch(() => {
          if (requestId !== waveformRequestSeqRef.current) {
            return
          }
          if (useMock) {
            setWaveformImage(MOCK_WAVEFORM_IMAGE)
            setWaveformState('idle')
            return
          }
          setWaveformState('error')
        })
    } else {
      setWaveformState('idle')
    }

    if (!existingCover) {
      getCoverForTrack(row.path)
        .then((data) => {
          setSelectedCoverImage(data.image || '')
        })
        .catch(() => {
          setSelectedCoverImage('')
        })
    }

    prewarmPlaybackNeighborhood(row.path).catch(() => {
      // ignore neighborhood prewarm errors
    })

    if (scanNoLufs && row.path) {
      const currentLufs = String(row.lufs || '').trim()
      if (!currentLufs || currentLufs === '-') {
        const requestId = ++lufsRequestSeqRef.current
        client.lufs(row.path)
          .then((result) => {
            const fetched = String(result?.lufs ?? '').trim()
            if (!fetched || fetched === '-') return

            setScanRows((prev) => prev.map((item) => (
              item.path === row.path ? { ...item, lufs: fetched } : item
            )))

            if (requestId !== lufsRequestSeqRef.current) return
            setSelectedTrack((prev) => (
              prev && prev.path === row.path ? { ...prev, lufs: fetched } : prev
            ))
          })
          .catch(() => {
            // ignore on-demand LUFS errors
          })
      }
    }
  }

  const playTrackRow = async (row, preferredSrc = '') => {
    if (!row?.path) return
    const audioEl = audioRef.current
    if (!audioEl) return

    const preferred = String(preferredSrc || '').trim()
    const targetSrc = preferred || await getPreviewSrc(row.path)
    if (!targetSrc) return

    setPlayerTrack(row)

    if (audioSrc !== targetSrc) {
      autoPlayOnSourceChangeRef.current = true
      setIsPreparingPlayback(true)
      setPlaybackTime(0)
      setPlaybackDuration(0)
      setAudioSrc(targetSrc)
      return
    }

    try {
      await audioEl.play()
      setIsPlaying(true)
    } catch {
      setIsPlaying(false)
    }
  }

  const playAdjacentTrack = (step) => {
    const playableRows = getOrderedPlayableRows()
    if (!playableRows.length) return

    const currentPath = playerTrack?.path || selectedTrack?.path || ''
    const currentIndex = playableRows.findIndex((row) => row.path === currentPath)
    const normalizedCurrent = currentIndex >= 0 ? currentIndex : (step > 0 ? -1 : 0)
    const nextIndex = Math.max(0, Math.min(playableRows.length - 1, normalizedCurrent + step))
    const nextRow = playableRows[nextIndex]
    if (!nextRow) return
    flashSkipIndicator(step < 0 ? 'prev' : 'next')

    const filteredIndex = filteredRows.findIndex((row) => row.path === nextRow.path)
    const nextRowKey = filteredIndex >= 0 ? `${nextRow.track}-${filteredIndex}` : ''
    const normalizedNextPath = String(nextRow.path || '').trim()
    const preloadedPath = String(preloadedTrackPathRef.current || '').trim()
    const preloadedSrc = String(preloadedAudioSrcRef.current || '').trim()
    const ramSrc = client.getRamCachedAudioSrc?.(normalizedNextPath) || ''
    const preloadedIsBlob = preloadedSrc.startsWith('blob:')
    const canUsePreloaded = preloadedPath && preloadedPath === normalizedNextPath && preloadedSrc
      ? (!preloadedIsBlob || preloadedSrc === ramSrc)
      : false
    const instantSrc = canUsePreloaded ? preloadedSrc : ramSrc
    selectTrackFromRow(nextRow, nextRowKey)
    playTrackRow(nextRow, instantSrc)
  }

  useEffect(() => {
    if (!selectedTrack) {
      setEditArtist('')
      setEditTitle('')
      setEditBpm('')
      setEditKey('')
      setSaveEditState('idle')
      setSaveEditMessage('')
      return
    }
    const selected = resolveTrackForUi(selectedTrack)
    const meta = splitTrackLabel(selected?.track)
    setEditArtist(meta.artist === '-' ? '' : meta.artist)
    setEditTitle(meta.title === '-' ? '' : meta.title)
    setEditBpm(String(selected?.bpm || '').trim())
    setEditKey(String(selected?.key || '').trim())
  }, [selectedTrack?.path])

  const pinActivePlaybackToBlob = async (trackPath) => {
    const targetPath = String(trackPath || '').trim()
    if (!targetPath) return

    const activePath = String(playerTrack?.path || selectedTrack?.path || '').trim()
    if (!activePath || activePath !== targetPath) return
    if (!audioSrc || String(audioSrc).startsWith('blob:')) return

    const audioEl = audioRef.current
    if (!audioEl) return

    const shouldResume = !audioEl.paused
    const resumeTime = Number.isFinite(audioEl.currentTime) ? audioEl.currentTime : 0

    let blobUrl = await (client.audioPreviewBlobUrl?.(targetPath) || '')
    if (!blobUrl) {
      const directUrl = client.audioPreviewUrl?.(targetPath)
      if (!directUrl) return
      const response = await fetch(directUrl)
      if (!response.ok) return
      const audioBlob = await response.blob()
      blobUrl = URL.createObjectURL(audioBlob)
    }
    if (!blobUrl) return
    playbackSourceModeRef.current.set(targetPath, 'blob')
    currentPlaybackModeRef.current = 'blob'

    await new Promise((resolve) => {
      let settled = false
      const finish = () => {
        if (settled) return
        settled = true
        resolve()
      }

      const onLoaded = () => {
        if (Number.isFinite(resumeTime) && resumeTime > 0) {
          try {
            audioEl.currentTime = resumeTime
            setPlaybackTime(resumeTime)
          } catch {
            // ignore seek restore errors
          }
        }

        if (shouldResume) {
          audioEl.play().then(() => {
            setIsPlaying(true)
            finish()
          }).catch(() => {
            setIsPlaying(false)
            finish()
          })
        } else {
          setIsPlaying(false)
          finish()
        }
      }

      audioEl.addEventListener('loadedmetadata', onLoaded, { once: true })
      setAudioSrc(blobUrl)
      setTimeout(finish, 2000)
    })
  }

  const saveSelectedTrackChanges = async () => {
    if (!selectedTrack?.path || !hasPendingTrackChanges) return
    const previousTrackPath = selectedTrack.path
    setSaveEditState('saving')
    setSaveEditMessage('')
    try {
      await pinActivePlaybackToBlob(previousTrackPath)

      const nextArtist = (editArtist || '').trim() || 'N-A'
      const nextTitle = (editTitle || '').trim() || selectedMeta.title || 'N-A'
      const nextBpm = (editBpm || '').trim()
      const nextKey = (editKey || '').trim()
      const coverForSave = hasCoverChanges ? String(selectedCoverSrc || '').trim() : ''
      const result = await client.updateTrackMetadata({
        trackPath: selectedTrack.path,
        artist: nextArtist,
        title: nextTitle,
        bpm: hasBpmChanged ? nextBpm : undefined,
        key: hasKeyChanged ? nextKey : undefined,
        coverDataUrl: hasCoverChanges ? coverForSave : undefined,
        cloneOutputDir,
        playlistPath: scanPath,
      })

      if (!result?.ok) {
        const backendError = result?.error || 'No se pudo guardar'
        const rollbackNote = result?.rolledBack ? ' Se hizo rollback automático.' : ' No se aplicaron cambios.'
        throw new Error(`${backendError}.${rollbackNote}`)
      }

      const savedTrackPath = String(result?.newPath || previousTrackPath).trim() || previousTrackPath

      const nextTrackLabel = `${nextArtist} - ${nextTitle}`
      setScanRows((prev) => prev.map((row) => {
        if (row.path !== previousTrackPath) return row
        return {
          ...row,
          path: savedTrackPath,
          track: nextTrackLabel,
          bpm: nextBpm || '--',
          bpmSource: nextBpm ? 'manual' : row.bpmSource,
          key: nextKey || '--',
          keySource: nextKey ? 'manual' : row.keySource,
          coverImage: coverForSave || row.coverImage,
        }
      }))

      setSelectedTrack((prev) => prev ? ({
        ...prev,
        path: savedTrackPath,
        track: nextTrackLabel,
        bpm: nextBpm || '--',
        bpmSource: nextBpm ? 'manual' : prev.bpmSource,
        key: nextKey || '--',
        keySource: nextKey ? 'manual' : prev.keySource,
        coverImage: coverForSave || prev.coverImage,
      }) : prev)

      setPlayerTrack((prev) => prev && prev.path === previousTrackPath ? {
        ...prev,
        path: savedTrackPath,
        track: nextTrackLabel,
        bpm: nextBpm || '--',
        bpmSource: nextBpm ? 'manual' : prev.bpmSource,
        key: nextKey || '--',
        keySource: nextKey ? 'manual' : prev.keySource,
        coverImage: coverForSave || prev.coverImage,
      } : prev)

      if (coverForSave) {
        putRefCacheWithLimit(coverCacheRef, savedTrackPath, coverForSave, maxCoverCacheItems)
      }
      if (savedTrackPath !== previousTrackPath) {
        coverCacheRef.current.delete(previousTrackPath)
      }
      waveformCacheRef.current.delete(getWaveformCacheKey(previousTrackPath))
      if (savedTrackPath !== previousTrackPath) {
        waveformCacheRef.current.delete(getWaveformCacheKey(savedTrackPath))
      }

      setCoverOverrides((prev) => {
        if (!prev?.[previousTrackPath]) return prev
        const next = { ...prev }
        delete next[previousTrackPath]
        if (savedTrackPath !== previousTrackPath && coverForSave) {
          next[savedTrackPath] = coverForSave
        }
        return next
      })
      if (hasCoverChanges) {
        setSelectedCoverImage(coverForSave)
      }
      setEditArtist(nextArtist)
      setEditTitle(nextTitle)
      setEditBpm(nextBpm)
      setEditKey(nextKey)

      setSaveEditState('done')
      const baseMessage = result?.message || 'Cambios guardados en archivo'
      if (result?.playlistUpdated) {
        setSaveEditMessage(`${baseMessage} · M3U8 actualizado automáticamente`)
      } else if (result?.playlistUpdateError) {
        setSaveEditMessage(`${baseMessage} · Aviso M3U8: ${result.playlistUpdateError}`)
      } else {
        setSaveEditMessage(baseMessage)
      }
    } catch (error) {
      setSaveEditState('error')
      setSaveEditMessage(error?.message || 'Error al guardar cambios')
    }
  }

  const resetScanUiState = (resetSummaryFilter = true) => {
    setScanRows([])
    waveformCacheRef.current.clear()
    waveformInFlightRef.current.clear()
    setScanRowsCapped(false)
    setScanSummary(null)
    setScanError('')
    setSelectedTrack(null)
    setSelectedCoverImage('')
    setSelectedRowKey('')
    setWaveformImage('')
    setWaveformState('idle')
    if (resetSummaryFilter) {
      setSummaryFilter('all')
    }
    setCoverOverrides({})
    setScanState({ state: 'starting' })
  }

  const runScanRequest = ({ playlistPath, useMockMode, resetSummaryFilter = true }) => {
    resetScanUiState(resetSummaryFilter)

    scanRef.current = client.scan({
      playlistPath,
      useMock: useMockMode,
      noLufs: scanNoLufs,
      onRow: (row) => {
        if (row?.path && row?.coverImage && !coverCacheRef.current.has(row.path)) {
          putRefCacheWithLimit(coverCacheRef, row.path, row.coverImage, maxCoverCacheItems)
        }
        setScanRows((prev) => {
          if (prev.length >= MAX_SCAN_ROWS_IN_MEMORY) {
            setScanRowsCapped(true)
            return prev
          }
          return [...prev, row]
        })
      },
      onSummary: setScanSummary,
      onStatus: setScanState,
      onError: (err) => {
        setScanError(err?.error || err?.message || 'Error de analisis')
        setScanState({ state: 'error' })
      },
    })
  }

  const startScan = () => {
    if (!scanPath && !useMock) {
      setScanError('Indica una ruta (.m3u/.m3u8), carpeta o archivo de audio para iniciar el analisis.')
      return
    }
    runScanRequest({ playlistPath: scanPath, useMockMode: useMock, resetSummaryFilter: true })
  }

  const refreshVisibleRows = async () => {
    if (isScanning) return

    const visibleRowsWithPath = filteredRows.filter((row) => Boolean(row?.path))
    if (!visibleRowsWithPath.length) {
      setScanError('No hay tracks visibles con ruta para refrescar en la tabla actual.')
      return
    }

    const visibleUniquePaths = Array.from(new Set(visibleRowsWithPath.map((row) => row.path)))
    const refreshName = summaryFilter === 'all' ? 'refresh_all' : `refresh_${summaryFilter}`

    try {
      const data = await client.createTempPlaylist({
        tracks: visibleUniquePaths,
        name: refreshName,
        coverOverrides: {},
      })
      const refreshPlaylistPath = String(data?.path || '').trim()
      if (!refreshPlaylistPath) {
        throw new Error(data?.error || 'No se pudo preparar el refresh')
      }
      runScanRequest({ playlistPath: refreshPlaylistPath, useMockMode: false, resetSummaryFilter: false })
    } catch (error) {
      setScanError(error?.message || 'Error al refrescar tracks visibles')
    }
  }

  const stopScan = () => {
    scanRef.current?.cancel?.()
    setScanState({ state: 'idle' })
  }

  const startSafeExportFromScan = async () => {
    let targetPath = scanPath || safePath
    let artworkDir = ''
    if (hasBrokenTracks) {
      setScanError(brokenFilesWarningText)
      return
    }
    if (!targetPath && filteredRows.length === 0) {
      setScanError('Indica una ruta de playlist para exportar el set seguro.')
      return
    }
    
    if (filteredRows.length > 0) {
      try {
        const validRows = filteredRows.filter((row) => row.path)
        const selectedCoverOverrides = {}
        validRows.forEach((row) => {
          if (coverOverrides[row.path]) {
            selectedCoverOverrides[row.path] = coverOverrides[row.path]
          }
        })

        const { path, artworkDir: createdArtworkDir } = await client.createTempPlaylist({
          tracks: validRows.map(r => r.path),
          name: targetPath ? targetPath.split('\\').pop().split('/').pop().replace('.m3u8', '') : 'MySet',
          coverOverrides: selectedCoverOverrides,
        })
        targetPath = path
        artworkDir = createdArtworkDir || ''
      } catch {
        setScanError('Error al crear tracklist temporal.')
        return
      }
    }

    setSafePath(targetPath)
    setSafeArtworkDir(artworkDir)
    setActiveView('safe')
    startSafeSet(targetPath, artworkDir)
  }

  const startSafeSet = (overridePath, overrideArtworkDir) => {
    const finalPath = typeof overridePath === 'string' ? overridePath : safePath
    const finalArtworkDir = typeof overrideArtworkDir === 'string' ? overrideArtworkDir : safeArtworkDir
    if (!finalPath) return
    setSafeState({ state: 'starting' })
    setSafeLog([])
    setSafeSummary(null)

    safeRef.current = client.safeSet({
      playlistPath: finalPath,
      output: safeOutput,
      artworkDir: finalArtworkDir,
      embedArt: Boolean(finalArtworkDir),
      overwrite: safeOverwrite,
      normalize: engineSettings.normalize,
      resample: engineSettings.resample,
      losslessFormat: engineSettings.losslessFormat,
      lossyFormat: engineSettings.lossyFormat,
      onEvent: (type, data) => {
        if (type === 'progress') {
          setSafeState({ state: 'progress', ...data })
        }
        if (type === 'item') {
          setSafeLog((prev) => [...prev, data])
        }
        if (type === 'summary') {
          setSafeSummary(data)
        }
        if (type === 'done') {
          setSafeState({ state: 'done' })
        }
        if (type === 'error') {
          setSafeState({ state: 'error' })
        }
      },
      onError: () => setSafeState({ state: 'error' }),
    })
  }

  const stopSafeSet = () => {
    safeRef.current?.cancel?.()
    setSafeState({ state: 'idle' })
  }

  const startSingle = (overrideSourcePath, options = {}) => {
    const sourcePath = String(overrideSourcePath || singlePath || '').trim()
    if (!sourcePath) return
    const universalTarget = Boolean(options.universalTarget)
    const normalizeForRun = options.normalize ?? engineSettings.normalize
    const resampleForRun = options.resample ?? engineSettings.resample
    setSingleState({ state: 'starting', current: 0, total: 5, name: 'Preparando' })
    setSingleResult(null)
    setSingleError('')

    singleRef.current = client.convertSingle({
      sourcePath,
      output: singleOutput,
      smart: true,
      universalTarget,
      normalize: normalizeForRun,
      resample: resampleForRun,
      losslessFormat: engineSettings.losslessFormat,
      lossyFormat: engineSettings.lossyFormat,
      onEvent: (type, data) => {
        if (type === 'progress') {
          setSingleState({ state: 'progress', ...data })
        }
        if (type === 'done') {
          setSingleResult(data)
          setSingleState({ state: 'done', current: 5, total: 5, name: 'Finalizado' })
          if (String(data?.status || '').toUpperCase() === 'FAIL') {
            setSingleError(String(data?.message || 'Error durante conversión'))
          }
        }
        if (type === 'error') {
          setSingleState({ state: 'error' })
          setSingleError(String(data?.error || data?.message || 'Error durante conversión'))
        }
      },
      onError: (err) => {
        setSingleState({ state: 'error' })
        setSingleError(String(err?.message || 'No se pudo iniciar la conversión. Revisa si el backend está activo.'))
      },
    })
  }

  const fixSelectedTrack = () => {
    const sourcePath = String(selectedTrack?.path || '').trim()
    if (!sourcePath) return
    setSinglePath(sourcePath)
    setActiveView('single')
  }

  const startIntegrity = () => {
    if (!integrityFolder) return
    setIntegrityRows([])
    setIntegritySummary(null)
    setIntegrityState({ state: 'starting' })

    integrityRef.current = client.integrityScan({
      folder: integrityFolder,
      onEvent: (type, data) => {
        if (type === 'progress') {
          setIntegrityState({ state: 'progress', ...data })
        }
        if (type === 'row') {
          setIntegrityRows((prev) => [...prev, data])
        }
        if (type === 'summary') {
          setIntegritySummary(data)
        }
        if (type === 'done') {
          setIntegrityState({ state: 'done' })
        }
      },
      onError: () => setIntegrityState({ state: 'error' }),
    })
  }

  const startCoversMissing = async () => {
    if (!coversFolder) return
    const data = await client.coversMissing(coversFolder)
    setCoversMissing(data.missing || [])
  }

  const startCoversProcess = () => {
    if (!coversSource || !coversOutput) return
    setCoversState({ state: 'starting' })
    setCoversLog('')

    coversRef.current = client.coversProcess({
      source: coversSource,
      output: coversOutput,
      onEvent: (type, data) => {
        if (type === 'done') {
          setCoversState({ state: 'done' })
          setCoversLog(`Guardado en ${data.output}`)
        }
        if (type === 'error') {
          setCoversState({ state: 'error' })
        }
      },
      onError: () => setCoversState({ state: 'error' }),
    })
  }

  const loadUsbDrives = async () => {
    const data = await client.seratoDrives()
    setUsbDrives(data.drives || [])
  }

  const startUsbClone = () => {
    if (!usbSource || !usbDest) return
    setUsbState({ state: 'starting' })
    setUsbLog([])

    usbRef.current = client.seratoClone({
      source: usbSource,
      dest: usbDest,
      onEvent: (type, data) => {
        if (type === 'progress') {
          setUsbState({ state: 'progress', ...data })
          setUsbLog((prev) => [...prev, data])
        }
        if (type === 'done') {
          setUsbState({ state: 'done' })
        }
        if (type === 'error') {
          setUsbState({ state: 'error' })
        }
      },
      onError: () => setUsbState({ state: 'error' }),
    })
  }

  useEffect(() => {
    const onMediaKeyDown = (event) => {
      if (event.code === 'MediaTrackNext') {
        event.preventDefault()
        playAdjacentTrack(1)
      } else if (event.code === 'MediaTrackPrevious') {
        event.preventDefault()
        playAdjacentTrack(-1)
      }
    }
    window.addEventListener('keydown', onMediaKeyDown)
    return () => window.removeEventListener('keydown', onMediaKeyDown)
  }, [scanRows, playerTrack?.path, selectedTrack?.path, filteredRows])

  useEffect(() => {
    if (typeof navigator === 'undefined' || !('mediaSession' in navigator)) return
    const session = navigator.mediaSession
    try {
      session.setActionHandler('nexttrack', () => playAdjacentTrack(1))
      session.setActionHandler('previoustrack', () => playAdjacentTrack(-1))
      session.setActionHandler('play', () => toggleAudioPlayback())
      session.setActionHandler('pause', () => toggleAudioPlayback())
    } catch {
      // ignore unsupported handlers
    }
    return () => {
      try {
        session.setActionHandler('nexttrack', null)
        session.setActionHandler('previoustrack', null)
        session.setActionHandler('play', null)
        session.setActionHandler('pause', null)
      } catch {
        // ignore cleanup failures
      }
    }
  }, [scanRows, playerTrack?.path, selectedTrack?.path, audioSrc, isPlaying])

  useEffect(() => {
    const trackPath = String(playerTrack?.path || '').trim()
    if (!trackPath || !isPlaying) return
    const timeoutId = window.setTimeout(() => {
      client.warmRamPlaybackCache?.(trackPath).catch(() => '')
    }, 250)
    return () => {
      window.clearTimeout(timeoutId)
    }
  }, [client, isPlaying, playerTrack?.path])

  const renderScan = () => (
    <section className="scan-view">
      <div className="scan-header">
        <div>
          <div className="scan-title">Analisis de playlist</div>
          <div className="cta-note">Analiza .m3u/.m3u8, carpeta o archivo de audio y muestra compatibilidad al instante.</div>
        </div>
      </div>
      <div className="view-grid">
        <div className="panel panel-controls">
          <h2>Control de Analisis</h2>
        <label className="field">
          <span>Ruta de entrada (.m3u/.m3u8, carpeta o archivo)</span>
          <input
            value={scanPath}
            onChange={(event) => setScanPath(event.target.value)}
            placeholder="C:\\Sets\\MySet.m3u8  o  C:\\Music\\MiCarpeta  o  C:\\Music\\track.flac"
          />
        </label>
        <div className="field">
          <button type="button" className="btn ghost file-upload-btn" onClick={handleScanFilePick} disabled={scanPickerBusy}>
            {scanPickerBusy ? 'Abriendo...' : 'Seleccionar Archivo'}
          </button>
        </div>
        <div className="field">
          <button type="button" className="btn ghost file-upload-btn" onClick={handleScanFolderPick} disabled={scanPickerBusy}>
            {scanPickerBusy ? 'Abriendo...' : 'Seleccionar Carpeta'}
          </button>
        </div>
        <label className="toggle">
          <input
            type="checkbox"
            checked={useMock}
            onChange={(event) => setUseMock(event.target.checked)}
          />
          <span>Modo Test</span>
        </label>
        <label className="toggle">
          <input
            type="checkbox"
            checked={scanNoLufs}
            onChange={(event) => setScanNoLufs(event.target.checked)}
          />
          <span>Modo rapido (solo detectar rotos/corruptos, sin LUFS)</span>
        </label>
        
        <div className="button-row" style={{ marginTop: '10px' }}>
          <button className={`btn primary ${isAnalyzeReady ? 'btn-ready' : ''}`} onClick={startScan}>Analizar playlist</button>
          <button className="btn primary" onClick={refreshVisibleRows} disabled={isScanning}>Refrescar visibles</button>
          {isScanning ? (
            <button className="btn danger" onClick={stopScan}>Detener</button>
          ) : null}
        </div>

        {hasScanProgress ? (
          <div className="scan-progress-shell">
            <div className="scan-progress-head">
              <span>{scanState.state === 'done' ? 'Análisis completado: 100%' : `Analizando: ${scanState.name || 'playlist'} (${scanProgressPct}%)`}</span>
            </div>
            <div className="scan-progress-track">
              <div className="scan-progress-fill" style={{ width: `${scanProgressPct}%` }} />
            </div>
          </div>
        ) : null}

        {scanError ? <div className="error-box">{scanError}</div> : null}
        {scanRowsCapped ? <div className="error-box">Vista limitada a 10000 filas para proteger memoria del navegador. El resumen sigue contando todo el análisis.</div> : null}

        <div className="summary-panel">
          <div className="summary-status">RESUMEN</div>
          <div className="summary-metrics summary-line">
            <div className={`summary-metric ${summaryFilter === 'all' ? 'active' : ''}`} onClick={() => setSummaryFilter('all')}>
              <span>Todos</span>
              <strong>{scanSummary?.tracks ?? scanRows.length}</strong>
            </div>
            <div className={`summary-metric ${summaryFilter === 'broken' ? 'active' : ''}`} onClick={() => setSummaryFilter('broken')}>
              <span>Roto</span>
              <strong>{summaryCounts.broken}</strong>
            </div>
            <div className={`summary-metric ${summaryFilter === 'incompatible' ? 'active' : ''}`} onClick={() => setSummaryFilter('incompatible')}>
              <span>Incompatible</span>
              <strong>{summaryCounts.incompatible}</strong>
            </div>
            <div className={`summary-metric ${summaryFilter === 'warning' ? 'active' : ''}`} onClick={() => setSummaryFilter('warning')}>
              <span>Advertencia</span>
              <strong>{summaryCounts.warning}</strong>
            </div>
            <div className={`summary-metric ${summaryFilter === 'compatible' ? 'active' : ''}`} onClick={() => setSummaryFilter('compatible')}>
              <span>Compatible</span>
              <strong>{summaryCounts.compatible}</strong>
            </div>
          </div>
          <div className="summary-bar">
            <span className="bar critical" style={{ width: `${criticalPct}%` }} />
            <span className="bar warning" style={{ width: `${warningPct}%` }} />
            <span className="bar ok" style={{ width: `${okPct}%` }} />
          </div>
          <div className="summary-total">Total: {scanSummary?.tracks ?? scanRows.length}</div>
        </div>

        <div className="settings-panel">
          <div className="insight-label">Exportación del set</div>
          <button className="btn primary premium-btn summary-cta" onClick={startSafeExportFromScan} disabled={hasBrokenTracks}>
            Compatibilizar Set
          </button>
          {hasBrokenTracks ? <div className="error-box" style={{ marginTop: '8px' }}>{brokenFilesWarningText}</div> : null}
          <div className="hint">Convierte solo cuando tu revisión esté lista.</div>
          <button className="settings-toggle" onClick={() => setShowSettings((prev) => !prev)}>
            Ajustes de motor
          </button>
          {showSettings ? (
            <div className="settings-body">
              <label className="settings-row toggle">
                <span>Normalizacion (-12 LUFS)</span>
                <input
                  type="checkbox"
                  className="ios-switch"
                  checked={engineSettings.normalize}
                  onChange={(e) => setEngineSettings(prev => ({ ...prev, normalize: e.target.checked }))}
                />
              </label>
              <label className="settings-row toggle">
                <span>Resample (44.1 kHz)</span>
                <input
                  type="checkbox"
                  className="ios-switch"
                  checked={engineSettings.resample}
                  onChange={(e) => setEngineSettings(prev => ({ ...prev, resample: e.target.checked }))}
                />
              </label>
              <div className="settings-row">
                <span>Lossless</span>
                <select
                  className="settings-select"
                  value={engineSettings.losslessFormat}
                  onChange={(e) => setEngineSettings(prev => ({ ...prev, losslessFormat: e.target.value }))}
                >
                  <option value="AIFF 24-bit">AIFF 24-bit</option>
                  <option value="AIFF 16-bit">AIFF 16-bit</option>
                  <option value="WAV 24-bit">WAV 24-bit</option>
                </select>
              </div>
              <div className="settings-row">
                <span>Lossy</span>
                <select
                  className="settings-select"
                  value={engineSettings.lossyFormat}
                  onChange={(e) => setEngineSettings(prev => ({ ...prev, lossyFormat: e.target.value }))}
                >
                  <option value="MP3 320k">MP3 320k</option>
                  <option value="AAC 256k">AAC 256k</option>
                </select>
              </div>
              <div className="settings-row">
                <span>Ruta clonado WAV→AIFF</span>
                <input
                  className="settings-select"
                  value={cloneOutputDir}
                  onChange={(e) => setCloneOutputDir(e.target.value)}
                  onBlur={async () => {
                    try {
                      const saved = await client.saveAppConfig?.({ cloneOutputDir })
                      if (saved?.cloneOutputDir !== undefined) {
                        setCloneOutputDir(String(saved.cloneOutputDir || '').trim())
                      }
                    } catch {
                      // ignore save errors
                    }
                  }}
                  placeholder="C:\\Users\\tu_usuario\\Music"
                />
              </div>
            </div>
          ) : null}
        </div>
        </div>

        <div className="panel panel-table">
          <div className="table-header">
            <h2>Resultados</h2>
            <div className="results-header-badge">
              <span className={`risk-indicator risk-${globalResultRisk}`}>{globalResultLabel}</span>
              <span className={`risk-indicator results-score-pill risk-${globalScoreRisk}`}>{globalScoreLabel}</span>
              <span className={`risk-indicator results-score-pill risk-${festivalSafeSummary.risk}`}>{festivalSafeSummary.label}</span>
              <span className="results-header-detail">{globalResultDetail}</span>
            </div>
          </div>
        <div className="table-shell">
          <table className="scan-table">
            <colgroup>
              {scanColumns.map((_, index) => (
                <col key={`scan-col-${index}`} style={{ width: `${scanColumnWidths[index] || defaultScanColumnWidths[index]}px` }} />
              ))}
            </colgroup>
            <thead>
              <tr>
                {scanColumns.map((label, index) => (
                  <th key={label} className={index < scanColumns.length - 1 ? 'scan-th-resizable' : 'scan-th-fixed'}>
                    <div className="scan-th-inner">
                      <span className="scan-th-label">{label}</span>
                      {index < scanColumns.length - 1 ? (
                        <button
                          type="button"
                          className="scan-col-resizer"
                          onMouseDown={(event) => startResizeColumn(index, event)}
                          aria-label={`Redimensionar columna ${label}`}
                        />
                      ) : null}
                    </div>
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {filteredRows.length === 0 ? (
                <tr className="empty-row">
                  <td colSpan={scanColumns.length}>Esperando datos. Inicia un analisis para ver resultados.</td>
                </tr>
              ) : (
                filteredRows.map((row, index) => {
                  const risk = classifyRisk(row)
                  const rowKey = `${row.track}-${index}`
                  const score = row.compatibilityScore ?? inferScore(row.status)
                  const trackIndex = row.trackIndex || index + 1
                  return (
                    <tr
                      key={rowKey}
                      className={`row-${risk} ${selectedRowKey === rowKey ? 'row-selected' : ''}`}
                      onClick={() => selectTrackFromRow(row, rowKey)}
                    >
                      <td>{trackIndex}</td>
                      <td style={{ padding: '4px' }}>
                        <CoverThumbnail
                          trackPath={row.path}
                          useMock={useMock}
                          customSrc={coverOverrides[row.path]}
                          initialSrc={row.coverImage}
                          getCoverForTrack={getCoverForTrack}
                          autoFetch={true}
                          editable={false}
                        />
                      </td>
                      <td><span className="track-cell-text" title={row.track}>{row.track}</span></td>
                      <td>{formatCodec(row.codec, row.bits, row.path)}</td>
                      <td>{row.sampleRate}</td>
                      <td>{formatBits(row.bits, row.codec, row.bitrate, row.path)}</td>
                      <td>{row.lufs}</td>
                      <td className="status-cell">
                        <span className="status-main">{formatStatus(row.status)}</span>
                        <span className={`score-pill score-${risk}`}>{score}/10</span>
                      </td>
                    </tr>
                  )
                })
              )}
            </tbody>
          </table>
        </div>
      </div>

        <aside className="panel panel-insights">
          <h2>RESUMEN</h2>
          <div className="summary-unified-card">
          <div className="insight track-hero-card summary-section">
            <div className="insight-label">Track seleccionado</div>
            {selectedTrack ? (
              <div className="track-hero-body">
                  <button
                  type="button"
                  className="track-hero-cover"
                  onClick={() => coverDoctorInputRef.current?.click()}
                  onKeyDown={(event) => {
                    if (event.key === 'Enter' || event.key === ' ') {
                      event.preventDefault()
                      coverDoctorInputRef.current?.click()
                    }
                  }}
                  onDragOver={(event) => event.preventDefault()}
                  onDrop={handleCoverDrop}
                  title="Cover Doctor: imagen o archivo de audio para clonar portada"
                >
                  {selectedCoverSrc ? (
                    <img src={selectedCoverSrc} alt="Cover grande" />
                  ) : (
                    <div className="track-hero-missing">
                      <AlertCircle size={24} />
                      <span>Sin cover / Haz clic para añadir</span>
                    </div>
                  )}
                  <div className="cover-edit-overlay" aria-hidden="true">
                    <Pencil size={14} />
                    <span>Editar cover</span>
                  </div>
                </button>
                <input
                  ref={coverDoctorInputRef}
                  id="cover-doctor-input"
                  type="file"
                  accept="image/*,.wav,.wave,.aif,.aiff,.flac,.mp3,.m4a,.aac,.alac,.mp4"
                  className="file-input-hidden"
                  onChange={async (event) => {
                    const file = event.target.files?.[0]
                    if (file && selectedTrack?.path) {
                      await handleCoverDoctorSelection(file)
                    }
                    event.target.value = ''
                  }}
                />
                <div className="track-hero-meta">
                  <label className="field field-compact">
                    <span>Título</span>
                    <input value={editTitle} onChange={(event) => setEditTitle(event.target.value)} placeholder="Título del track" />
                  </label>
                  <label className="field field-compact">
                    <span>Artista</span>
                    <input value={editArtist} onChange={(event) => setEditArtist(event.target.value)} placeholder="Artista" />
                  </label>
                  <div className="track-meta-inline-grid">
                    <label className="field field-compact">
                      <span>BPM ({selectedBpmSourceLabel})</span>
                      <input value={editBpm} onChange={(event) => setEditBpm(event.target.value)} placeholder="124" />
                    </label>
                    <label className="field field-compact">
                      <span>KEY</span>
                      <input value={editKey} onChange={(event) => setEditKey(event.target.value)} placeholder="8A / F#m" />
                    </label>
                  </div>
                  <div className="track-tech-grid">
                    <div className="track-tech-item">
                      <span>DURACIÓN</span>
                      <strong>{selectedDurationLabel}</strong>
                    </div>
                    <div className="track-tech-item">
                      <span>CALIDAD</span>
                      <strong className={`quality-pill quality-${String(selectedQualityTier || 'SD').toLowerCase()}`}>{selectedQualityTier}</strong>
                    </div>
                    <div className="track-tech-item track-tech-item-wide">
                      <span>ARCHIVO ORIGINAL</span>
                      <strong className="track-tech-file" title={selectedOriginalFilename}>{selectedOriginalFilename}</strong>
                    </div>
                  </div>
                  <button
                    type="button"
                    className={`btn save-track-btn ${hasPendingTrackChanges ? 'primary btn-ready' : 'ghost'}`}
                    onClick={saveSelectedTrackChanges}
                    disabled={saveEditState === 'saving' || !hasPendingTrackChanges}
                  >
                    {saveEditState === 'saving' ? 'Guardando...' : 'Guardar cambios'}
                  </button>
                  {saveEditMessage ? <div className={`save-edit-note ${saveEditState}`}>{saveEditMessage}</div> : null}
                </div>
              </div>
            ) : (
              <div className="details-empty">Selecciona una pista para ver detalles.</div>
            )}
          </div>

          <div className="insight waveform-card summary-section">
            <div className="insight-label">Análisis de Audio</div>
            <div className="waveform-row">
              <div
                className={`waveform-shell ${audioSrc ? 'waveform-shell-interactive' : ''}`}
                ref={waveformSeekRef}
                onClick={handleWaveformSeek}
                title={audioSrc ? 'Click para mover reproducción' : ''}
              >
                {waveformState === 'loading' ? (
                  <div className="waveform-placeholder">Generando...</div>
                ) : waveformState === 'error' ? (
                  <div className="waveform-placeholder">No disponible</div>
                ) : waveformImage ? (
                  <div className="waveform-canvas">
                    <img className="waveform-img" src={waveformImage} alt="Waveform" />
                    {isSelectedTrackPlaybackSource ? (
                      <>
                        <div className="waveform-played" style={{ width: `${selectedWaveformProgressPct}%` }} />
                        <div className="waveform-playhead" style={{ left: `${selectedWaveformProgressPct}%` }} />
                      </>
                    ) : null}
                  </div>
                ) : (
                  <div className="waveform-placeholder">Selecciona una pista</div>
                )}
              </div>
              {selectedTrack && String(selectedTrack.lufs || '').trim() !== '' && (
                <div className="waveform-meter-shell">
                  <LufsMeter lufs={selectedTrack.lufs} />
                </div>
              )}
            </div>
          </div>
          <div className="insight track-details track-details-box summary-section summary-section-last">
            <div className="insight-label">Detalles del track</div>
            {selectedTrack ? (
              <div className="details-body">
                <div className="compat-list">
                  {visibleDeviceGroups.map((device) => {
                    const status = getDeviceStatus(selectedTrack, device.key)
                    const reasons = getDeviceReasonsForDisplay(selectedTrack, device.key)
                    const statusEmoji = getDeviceEmoji(device.key, status)
                    const icon = status === 'bad' ? <AlertCircle size={14} /> : status === 'warn' ? <AlertTriangle size={14} /> : <CheckCircle size={14} />
                    return (
                      <div key={device.key} className={`compat-row compat-${status}`}>
                        <span className="compat-icon">{statusEmoji}</span>
                        <span className="compat-icon compat-icon-pro">{icon}</span>
                        <span className="compat-label">{device.label}<br />{device.models}</span>
                        <span className="compat-reason">{status === 'ok' ? 'OK' : formatDeviceReason(reasons, status)}</span>
                      </div>
                    )
                  })}
                </div>
                <div className="details-explain">
                  {buildHumanExplanation(selectedTrack.detailsText)}
                </div>
                <div className="details-software-note">
                  Software compatibility: Serato DJ Pro y Rekordbox Performance suelen reproducir sin bloqueos; el límite real es CPU/RAM y no el parser del hardware standalone.
                </div>
                {selectedTrackScore < 10 && (
                  <button className="btn secondary" style={{ marginTop: '15px', width: '100%', justifyContent: 'center' }} onClick={fixSelectedTrack}>
                    <Wrench size={16} style={{marginRight: '6px'}}/> Corregir este archivo
                  </button>
                )}
              </div>
            ) : (
              <div className="details-empty">Selecciona una pista para ver detalles.</div>
            )}
          </div>
          </div>
        </aside>
      </div>
    </section>
  )

  const renderSafe = () => (
    <section className="panel panel-form">
      <h2>Exportacion Segura</h2>
      <label className="field">
        <span>Ruta de playlist (.m3u8)</span>
        <input value={safePath} onChange={(event) => setSafePath(event.target.value)} placeholder="C:\\Sets\\MySet.m3u8" />
      </label>
      <label className="field">
        <span>Archivo de playlist</span>
        <input
          className="file-input"
          type="file"
          accept=".m3u8"
          onChange={(event) => handleFilePick(event.target.files[0], setSafePath)}
        />
      </label>
      <label className="field">
        <span>Carpeta de salida (opcional)</span>
        <input value={safeOutput} onChange={(event) => setSafeOutput(event.target.value)} placeholder="C:\\Exports\\Set_Ready" />
      </label>
      <label className="toggle">
        <input type="checkbox" checked={safeOverwrite} onChange={(event) => setSafeOverwrite(event.target.checked)} />
        <span>Sobrescribir salida</span>
      </label>
      <div className="button-row">
        <button className="btn primary" onClick={startSafeSet}>Iniciar exportacion</button>
        <button className="btn ghost" onClick={stopSafeSet}>Detener</button>
      </div>

      {safeState.state === 'progress' && safeState.total > 0 && (
        <div style={{ marginTop: '20px', marginBottom: '10px' }}>
          <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: '8px', fontSize: '0.9em', color: '#ccc' }}>
            <span>Exportando: {safeState.name}</span>
            <span style={{ fontWeight: 'bold', color: '#bb86fc' }}>{Math.round((safeState.current / safeState.total) * 100)}%</span>
          </div>
          <div style={{ width: '100%', height: '14px', background: '#332f3c', borderRadius: '7px', overflow: 'hidden' }}>
            <div style={{ height: '100%', background: 'linear-gradient(90deg, #bb86fc, #9c27b0)', width: `${(safeState.current / safeState.total) * 100}%`, transition: 'width 0.3s ease-out' }} />
          </div>
        </div>
      )}

      <div className="summary-box">
        <div className="summary-title">Resumen de exportacion</div>
        <div className="summary-row"><span>OK</span><strong>{safeSummary?.ok ?? 0}</strong></div>
        <div className="summary-row"><span>Error</span><strong>{safeSummary?.fail ?? 0}</strong></div>
        <div className="summary-row"><span>Salida</span><strong>{safeSummary?.output ?? '-'}</strong></div>
      </div>
      <div className="log-shell">
        {safeLog.length === 0 ? (
          <div className="empty-row">Aun no se procesaron archivos.</div>
        ) : (
          safeLog.map((item, index) => (
            <div key={`${item.source}-${index}`} className={`log-row ${item.status === 'FAIL' ? 'danger' : ''}`}>
              <span>{item.status}</span>
              <span>{item.output}</span>
            </div>
          ))
        )}
      </div>
    </section>
  )

  const renderSingle = () => (
    <section className="panel panel-form">
      <h2>Conversion individual</h2>
      <label className="field">
        <span>Archivo origen</span>
        <input value={singlePath} onChange={(event) => setSinglePath(event.target.value)} placeholder="C:\\Audio\\track.wav" />
      </label>
      <div className="field">
        <label htmlFor="single-file-upload" className="btn ghost file-upload-btn">
          Seleccionar archivo
        </label>
        <input
          id="single-file-upload"
          className="file-input-hidden"
          type="file"
          accept=".wav,.wave,.aif,.aiff,.flac,.mp3,.m4a,.aac,.alac,.mp4"
          onChange={(event) => handleFilePick(event.target.files[0], setSinglePath)}
        />
      </div>
      <div className="settings-body" style={{ marginTop: '8px' }}>
        <label className="settings-row toggle">
          <span>Normalizacion (-12 LUFS)</span>
          <input
            type="checkbox"
            className="ios-switch"
            checked={engineSettings.normalize}
            onChange={(e) => setEngineSettings(prev => ({ ...prev, normalize: e.target.checked }))}
          />
        </label>
        <label className="settings-row toggle">
          <span>Resample (44.1 kHz)</span>
          <input
            type="checkbox"
            className="ios-switch"
            checked={engineSettings.resample}
            onChange={(e) => setEngineSettings(prev => ({ ...prev, resample: e.target.checked }))}
          />
        </label>
        <div className="settings-row">
          <span>Lossless</span>
          <select
            className="settings-select"
            value={engineSettings.losslessFormat}
            onChange={(e) => setEngineSettings(prev => ({ ...prev, losslessFormat: e.target.value }))}
          >
            <option value="AIFF 24-bit">AIFF 24-bit</option>
            <option value="AIFF 16-bit">AIFF 16-bit</option>
            <option value="WAV 24-bit">WAV 24-bit</option>
          </select>
        </div>
        <div className="settings-row">
          <span>Lossy</span>
          <select
            className="settings-select"
            value={engineSettings.lossyFormat}
            onChange={(e) => setEngineSettings(prev => ({ ...prev, lossyFormat: e.target.value }))}
          >
            <option value="MP3 320k">MP3 320k</option>
            <option value="AAC 256k">AAC 256k</option>
          </select>
        </div>
      </div>
      <label className="field">
        <span>Archivo de salida (opcional)</span>
        <input value={singleOutput} onChange={(event) => setSingleOutput(event.target.value)} placeholder="C:\\Audio\\track_converted.aiff" />
      </label>
      <div className="button-row">
        <button className="btn primary" onClick={() => startSingle(singlePath, { universalTarget: true, resample: true })}>Convertir</button>
      </div>
      {singleError ? <div className="error-box" style={{ marginTop: '10px' }}>{singleError}</div> : null}
      {hasSingleProgress ? (
        <div className="scan-progress-shell" style={{ marginTop: '12px' }}>
          <div className="scan-progress-head">
            <span>{singleState.state === 'done' ? 'Conversión completada: 100%' : `Convirtiendo: ${singleState.name || 'archivo'} (${singleProgressPct}%)`}</span>
          </div>
          <div className="scan-progress-track">
            <div className="scan-progress-fill" style={{ width: `${singleProgressPct}%` }} />
          </div>
        </div>
      ) : null}
      {singleResult ? (
        <div className="summary-box">
          <div className="summary-title">Resultado</div>
          <div className="summary-row"><span>Estado</span><strong>{singleResult.status}</strong></div>
          <div className="summary-row"><span>Salida</span><strong>{singleResult.output}</strong></div>
          <div className="summary-row"><span>Mensaje</span><strong>{singleResult.message}</strong></div>
        </div>
      ) : null}
    </section>
  )

  const renderIntegrity = () => (
    <section className="panel panel-table">
      <div className="table-header">
        <h2>Chequeo de integridad</h2>
        <div className={`risk-indicator risk-${integrityState.state === 'error' ? 'critical' : 'ok'}`}>
          {(scanStateLabels[integrityState.state] || String(integrityState.state).toUpperCase())}
        </div>
      </div>
      <div className="panel-controls inline-controls">
        <label className="field">
          <span>Carpeta</span>
          <input value={integrityFolder} onChange={(event) => setIntegrityFolder(event.target.value)} placeholder="C:\\Music" />
        </label>
        <button className="btn primary" onClick={startIntegrity}>Ejecutar analisis</button>
      </div>
      <div className="summary-box inline-summary">
        <div className="summary-row"><span>OK</span><strong>{integritySummary?.ok ?? 0}</strong></div>
        <div className="summary-row"><span>Advertencias</span><strong>{integritySummary?.warning ?? 0}</strong></div>
        <div className="summary-row"><span>Errores</span><strong>{integritySummary?.error ?? 0}</strong></div>
      </div>
      <div className="table-shell">
        <table className="integrity-table">
          <thead>
            <tr>
              {integrityColumns.map((label) => (
                <th key={label}>{label}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {integrityRows.length === 0 ? (
              <tr className="empty-row">
                <td colSpan={integrityColumns.length}>Aun no hay datos de integridad.</td>
              </tr>
            ) : (
              integrityRows.map((row, index) => (
                <tr key={`${row.file}-${index}`}>
                  <td>{row.file}</td>
                  <td>{formatIntegrityStatus(row.status)}</td>
                  <td>{row.sampleRate}</td>
                  <td>{row.bits}</td>
                  <td>{row.bitrate}</td>
                  <td>{row.details}</td>
                </tr>
              ))
            )}
          </tbody>
        </table>
      </div>
    </section>
  )

  const renderCovers = () => (
    <section className="panel panel-form">
      <h2>Herramientas de portadas</h2>
      <label className="field">
        <span>Buscar portadas faltantes</span>
        <input value={coversFolder} onChange={(event) => setCoversFolder(event.target.value)} placeholder="C:\\Music" />
      </label>
      <div className="button-row">
        <button className="btn primary" onClick={startCoversMissing}>Buscar faltantes</button>
      </div>
      <div className="log-shell">
        {coversMissing.length === 0 ? (
          <div className="empty-row">Aun no hay faltantes.</div>
        ) : (
          coversMissing.map((item) => (
            <div key={item} className="log-row">{item}</div>
          ))
        )}
      </div>
      <div className="divider" />
      <label className="field">
        <span>Origen de portadas</span>
        <input value={coversSource} onChange={(event) => setCoversSource(event.target.value)} placeholder="C:\\Images" />
      </label>
      <label className="field">
        <span>Carpeta de salida</span>
        <input value={coversOutput} onChange={(event) => setCoversOutput(event.target.value)} placeholder="C:\\covers" />
      </label>
      <div className="button-row">
        <button className="btn primary" onClick={startCoversProcess}>Procesar imagenes</button>
      </div>
      {coversLog ? <div className="summary-box">{coversLog}</div> : null}
    </section>
  )

  const renderUsb = () => (
    <section className="panel panel-form">
      <h2>Clon USB Export</h2>
      <div className="button-row">
        <button className="btn ghost" onClick={loadUsbDrives}>Actualizar unidades</button>
      </div>
      <div className="log-shell">
        {usbDrives.length === 0 ? (
          <div className="empty-row">Sin unidades.</div>
        ) : (
          usbDrives.map((drive) => (
            <div key={drive.root} className="log-row">{drive.root} {drive.label}</div>
          ))
        )}
      </div>
      <label className="field">
        <span>Unidad origen</span>
        <input value={usbSource} onChange={(event) => setUsbSource(event.target.value)} placeholder="E:\\" />
      </label>
      <label className="field">
        <span>Unidad destino</span>
        <input value={usbDest} onChange={(event) => setUsbDest(event.target.value)} placeholder="F:\\" />
      </label>
      <div className="button-row">
        <button className="btn primary" onClick={startUsbClone}>Clonar</button>
      </div>
      <div className="log-shell">
        {usbLog.length === 0 ? (
          <div className="empty-row">Aun sin actividad.</div>
        ) : (
          usbLog.slice(-20).map((entry, index) => (
            <div key={`${entry.name}-${index}`} className="log-row">{entry.name}</div>
          ))
        )}
      </div>
    </section>
  )

  return (
    <div className="app-shell">
      <header className="topbar-min">
        <div className="app-title">CrateGuard</div>
      </header>
      <div className="layout">
        <aside className="sidebar">
          <div className="nav-title">Modulos</div>
          <div className="nav-items">
            {VIEWS.map((view) => (
              <button
                key={view.id}
                className={`nav-item ${activeView === view.id ? 'active' : ''}`}
                onClick={() => setActiveView(view.id)}
              >
                <span className="nav-icon"><view.icon className="nav-icon-svg" /></span>
                <span className="nav-text">{view.label}</span>
              </button>
            ))}
          </div>
        </aside>

        <main className="main-content">
          {activeView === 'scan' && renderScan()}
          {activeView === 'safe' && renderSafe()}
          {activeView === 'single' && renderSingle()}
          {activeView === 'integrity' && renderIntegrity()}
          {activeView === 'covers' && renderCovers()}
          {activeView === 'usb' && renderUsb()}
        </main>
      </div>

      <section className={`global-player ${showGlobalPlayer ? 'global-player-visible' : 'global-player-hidden'}`} aria-label="Reproductor global" aria-hidden={!showGlobalPlayer}>
        <div className="global-player-main">
          <div className="global-player-cover">
            {activePlaybackCoverSrc ? (
              <img src={activePlaybackCoverSrc} alt="Cover del track" />
            ) : (
              <div className="global-player-cover-empty">♪</div>
            )}
          </div>
          <button
            type="button"
            className={`btn ghost audio-skip-btn ${skipIndicatorDirection === 'prev' ? 'audio-skip-btn-flash-prev' : ''}`}
            onClick={() => playAdjacentTrack(-1)}
            disabled={scanRows.filter((row) => Boolean(row.path)).length < 2}
            title="Canción anterior"
          >
            <SkipBack size={14} />
          </button>
          <button
            type="button"
            className={`btn ghost audio-play-btn ${toggleIndicatorVisible ? 'audio-play-btn-flash' : ''}`}
            onClick={toggleAudioPlayback}
            disabled={!selectedTrack?.path && !playerTrack?.path}
          >
            {isPlaying ? <Pause size={14} /> : <Play size={14} />}
            <span>{isPlaying ? 'Pause' : 'Play'}</span>
          </button>
          <button
            type="button"
            className={`btn ghost audio-skip-btn ${skipIndicatorDirection === 'next' ? 'audio-skip-btn-flash-next' : ''}`}
            onClick={() => playAdjacentTrack(1)}
            disabled={scanRows.filter((row) => Boolean(row.path)).length < 2}
            title="Canción siguiente"
          >
            <SkipForward size={14} />
          </button>
          <div className="global-player-track" title={selectedTrackLabel}>{selectedTrackLabel}</div>
          <div className="audio-timecode">{formatDurationSeconds(playbackTime)} / {playbackDurationLabel}</div>
          <label className="audio-volume" title="Volumen lineal">
            <Volume2 size={14} />
            <input
              className="audio-volume-slider"
              type="range"
              min="0"
              max="100"
              step="1"
              value={previewVolumePct}
              onChange={(event) => setPreviewVolume(Number(event.target.value) / 100)}
              disabled={!audioSrc}
            />
            <span className="audio-volume-value">{previewVolumePct}%</span>
          </label>
        </div>
        <div className="audio-seekbar-row">
          <input
            className="audio-seekbar"
            type="range"
            min="0"
            max={effectiveDurationSeconds > 0 ? effectiveDurationSeconds : 0}
            step="0.01"
            value={playbackSeekSeconds}
            onChange={handleTimelineSeek}
            disabled={!audioSrc || effectiveDurationSeconds <= 0}
          />
        </div>
        <audio
          ref={audioRef}
          src={audioSrc || null}
          preload="auto"
          onLoadedMetadata={(event) => {
            const duration = event.currentTarget.duration || 0
            setPlaybackDuration(duration)
            if (pendingSeekSecondsRef.current !== null) {
              const seekSeconds = Number(pendingSeekSecondsRef.current) || 0
              const nextTime = Math.max(0, Math.min(duration || seekSeconds, seekSeconds))
              event.currentTarget.currentTime = nextTime
              setPlaybackTime(nextTime)
            } else if (pendingSeekRatioRef.current !== null && duration > 0) {
              const nextTime = Math.max(0, Math.min(duration, duration * pendingSeekRatioRef.current))
              event.currentTarget.currentTime = nextTime
              setPlaybackTime(nextTime)
            }
            pendingSeekSecondsRef.current = null
            pendingSeekRatioRef.current = null
          }}
          onTimeUpdate={(event) => setPlaybackTime(event.currentTarget.currentTime || 0)}
          onPlay={() => {
            setIsPlaying(true)
            setIsPreparingPlayback(false)
          }}
          onPause={() => {
            setIsPlaying(false)
            setIsPreparingPlayback(false)
          }}
          onEnded={() => {
            const audioEl = audioRef.current
            if (audioEl) {
              audioEl.currentTime = 0
            }
            setPlaybackTime(0)
            setIsPlaying(false)
            setIsPreparingPlayback(false)
          }}
          onError={handleAudioPlaybackError}
        />
        <audio
          ref={preloadAudioRef}
          preload="auto"
          muted
          aria-hidden="true"
          style={{ display: 'none' }}
        />
      </section>
    </div>
  )
}

export default App
