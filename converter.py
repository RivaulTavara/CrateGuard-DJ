import os
import re
import json
import random
import shutil
import struct
import subprocess
import threading
import queue
import sys
import base64
import tempfile
import mimetypes
import time
import hashlib
from concurrent.futures import ThreadPoolExecutor, as_completed
from collections import OrderedDict
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse, parse_qs, unquote
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

# =========================
# CONFIG
# =========================

INPUT_EXTS = {".flac", ".wav", ".wave", ".aif", ".aiff", ".m4a", ".aac", ".mp3", ".alac"}
COVER_AUDIO_EXTS = (".mp3", ".flac", ".wav", ".aiff", ".aif")
SAFE_FILENAME_MAX_LEN = 48
ARTWORK_SIZE = 500
ARTWORK_JPEG_QUALITY = 4
LOCAL_COVERS_DIR = "covers"
SAFE_SAMPLE_RATE_HZ = 44100
SAFE_PCM_CODEC = "pcm_s24be"
SERATO_FOLDERS_TO_COPY = ["_Serato_", "Music"]
ALLOWED_FILESYSTEMS = {
    "FAT32": "OK (maxima compatibilidad)",
    "EXFAT": "OK (compatibilidad moderna, archivos >4GB)",
}
RECOMMENDED_FS = "FAT32"
CONFIG_PATH = Path(__file__).with_name("config.json")
MAX_SAMPLE_RATE_HZ = 0
MAX_BIT_DEPTH = 24
MIN_MP3_BITRATE_KBPS = 320
LUFS_WARN_LOW = -14.0
LUFS_WARN_HIGH = -4.0
FAST_LUFS_WINDOW_SECONDS = 60
WAVEFORM_CACHE_MAX_ITEMS = 20
SERATO_PREFERRED_EXTS = {".wav", ".wave", ".aif", ".aiff", ".flac"}
DEVICE_GROUPS = {
    "Pro": {
        "models": ["CDJ-3000X", "CDJ-3000" , "CDJ-2000NXS2", "XDJ-AZ", "RX3", "OPUS-QUAD"],
        "formats": {".mp3", ".aac", ".m4a", ".alac", ".wav", ".wave", ".aif", ".aiff", ".flac"},
        "max_sample_rate": 96000,
        "max_bit_depth": 24,
    },
    "Club": {
        "models": ["XDJ-XZ", "RX2"],
        "formats": {".mp3", ".aac", ".m4a", ".wav", ".wave", ".aif", ".aiff"},
        "max_sample_rate": 48000,
        "max_bit_depth": 24,
    },
    "Legacy": {
        "models": ["CDJ-850", "CDJ-900", "CDJ-350", "RX1"],
        "formats": {".mp3", ".aac", ".m4a", ".wav", ".wave", ".aif", ".aiff"},
        "max_sample_rate": 48000,
        "max_bit_depth": 16,
    },
    "Software": {
        "models": ["Serato", "Rekordbox", "Traktor", "VirtualDJ"],
        "formats": {".mp3", ".aac", ".m4a", ".alac", ".wav", ".wave", ".aif", ".aiff", ".flac"},
        "max_sample_rate": 192000,
        "max_bit_depth": 32,
    },
}

# derivar dinámicamente del DEVICE_GROUPS para evitar chequeos globales inconsistentes
MAX_SAMPLE_RATE_HZ = max(int(spec.get("max_sample_rate", 0)) for spec in DEVICE_GROUPS.values())

DEVICE_GROUP_ORDER = ["Pro", "Club", "Legacy", "Software"]


def _device_group_label(group_key: str) -> str:
    return str(group_key or "")


def _group_reason_map(reason: str):
    return {group: [reason] for group in DEVICE_GROUP_ORDER}


def _empty_device_support(incompatible_all: bool = False):
    if incompatible_all:
        return {"compatible": [], "warning": [], "incompatible": list(DEVICE_GROUP_ORDER)}
    return {"compatible": list(DEVICE_GROUP_ORDER), "warning": [], "incompatible": []}


def _empty_device_reasons():
    return {group: [] for group in DEVICE_GROUP_ORDER}


def _device_groups_from_support(device_support: dict | None):
    support = device_support or {}
    compatible = set(support.get("compatible") or [])
    warning = set(support.get("warning") or [])
    incompatible = set(support.get("incompatible") or [])
    groups = {}
    for group in DEVICE_GROUP_ORDER:
        if group in incompatible:
            status = "incompatible"
        elif group in warning:
            status = "warning"
        elif group in compatible:
            status = "compatible"
        else:
            status = "compatible"
        groups[group] = {"status": status}
    return groups


HARDWARE_SPECS = {
    _device_group_label(group_key): {
        "group": group_key,
        "formats": set(spec["formats"]),
        "max_sr": int(spec["max_sample_rate"]),
        "max_bit_depth": int(spec["max_bit_depth"]),
    }
    for group_key, spec in DEVICE_GROUPS.items()
}
DEVICE_LABEL_TO_GROUP = {label: spec["group"] for label, spec in HARDWARE_SPECS.items()}
DEVICE_FORMATS = {name: spec["formats"] for name, spec in HARDWARE_SPECS.items()}
WAVEFORM_CACHE = OrderedDict()
WAVEFORM_CACHE_LOCK = threading.Lock()
LUFS_CACHE_MAX_ITEMS = 100
LUFS_CACHE = OrderedDict()
LUFS_CACHE_LOCK = threading.Lock()
WAVEFORM_RENDER_VERSION = 7
WAVEFORM_COLOR_MAIN_HEX = "bc13fe"
WAVEFORM_COLOR_LOW_HEX = "2f7bff"
WAVEFORM_COLOR_HIGH_HEX = "ffffff"
WAVEFORM_ANALYSIS_MAX_SECONDS = 180
TEMP_ARTIFACT_RETENTION_HOURS = 24

AUDIO_CONTENT_TYPE_BY_EXT = {
    ".mp3": "audio/mpeg",
    ".aac": "audio/aac",
    ".m4a": "audio/mp4",
    ".mp4": "audio/mp4",
    ".alac": "audio/mp4",
    ".wav": "audio/wav",
    ".wave": "audio/wav",
    ".aif": "audio/x-aiff",
    ".aiff": "audio/x-aiff",
    ".flac": "audio/flac",
}

PREVIEW_AUDIO_CACHE_DIR = Path(__file__).parent / "exports" / "_temp" / "preview_audio"
PREVIEW_AUDIO_LOCK = threading.Lock()
PREVIEW_AUDIO_LOCKS = {}


def _waveform_cache_key(src_path: Path):
    try:
        stat = src_path.stat()
        return (WAVEFORM_RENDER_VERSION, str(src_path.resolve()).lower(), int(stat.st_mtime_ns), int(stat.st_size))
    except Exception:
        return (WAVEFORM_RENDER_VERSION, str(src_path).lower(), 0, 0)


def _waveform_cache_get(src_path: Path):
    key = _waveform_cache_key(src_path)
    with WAVEFORM_CACHE_LOCK:
        cached = WAVEFORM_CACHE.get(key)
        if cached is None:
            return None
        WAVEFORM_CACHE.move_to_end(key)
        return cached


def _get_system_memory_gb() -> float | None:
    try:
        env_ram = str(os.environ.get("CRATEGUARD_RAM_GB", "")).strip()
        if env_ram:
            value = float(env_ram)
            if value > 0:
                return value
    except Exception:
        pass

    if os.name == "nt":
        try:
            import ctypes

            class MEMORYSTATUSEX(ctypes.Structure):
                _fields_ = [
                    ("dwLength", ctypes.c_ulong),
                    ("dwMemoryLoad", ctypes.c_ulong),
                    ("ullTotalPhys", ctypes.c_ulonglong),
                    ("ullAvailPhys", ctypes.c_ulonglong),
                    ("ullTotalPageFile", ctypes.c_ulonglong),
                    ("ullAvailPageFile", ctypes.c_ulonglong),
                    ("ullTotalVirtual", ctypes.c_ulonglong),
                    ("ullAvailVirtual", ctypes.c_ulonglong),
                    ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
                ]

            status = MEMORYSTATUSEX()
            status.dwLength = ctypes.sizeof(MEMORYSTATUSEX)
            if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
                return float(status.ullTotalPhys) / (1024 ** 3)
        except Exception:
            pass

    try:
        if hasattr(os, "sysconf"):
            pages = os.sysconf("SC_PHYS_PAGES")
            page_size = os.sysconf("SC_PAGE_SIZE")
            if pages and page_size:
                return float(pages * page_size) / (1024 ** 3)
    except Exception:
        pass

    return None


def _adaptive_worker_cap(cpu_count: int, ram_gb: float | None, profile: str):
    cpu = max(1, int(cpu_count))
    profile_value = str(profile or "io").lower()

    if profile_value == "decode":
        if cpu <= 4:
            cpu_cap = max(1, cpu - 2)
        elif cpu <= 8:
            cpu_cap = cpu - 3
        elif cpu <= 12:
            cpu_cap = cpu - 4
        else:
            cpu_cap = cpu - 5
    elif profile_value == "transcode":
        if cpu <= 4:
            cpu_cap = 1
        elif cpu <= 8:
            cpu_cap = 2
        elif cpu <= 12:
            cpu_cap = 3
        else:
            cpu_cap = max(3, cpu // 4)
    else:
        if cpu <= 4:
            cpu_cap = max(2, cpu - 1)
        elif cpu <= 8:
            cpu_cap = cpu - 1
        else:
            cpu_cap = cpu - 2

    if ram_gb is None:
        ram_cap = cpu_cap
    elif profile_value == "decode":
        if ram_gb <= 8:
            ram_cap = 2
        elif ram_gb <= 16:
            ram_cap = 3
        elif ram_gb <= 24:
            ram_cap = 4
        elif ram_gb <= 32:
            ram_cap = 6
        elif ram_gb <= 64:
            ram_cap = 8
        else:
            ram_cap = 12
    elif profile_value == "transcode":
        if ram_gb <= 8:
            ram_cap = 1
        elif ram_gb <= 16:
            ram_cap = 2
        elif ram_gb <= 24:
            ram_cap = 3
        elif ram_gb <= 32:
            ram_cap = 4
        elif ram_gb <= 64:
            ram_cap = 6
        else:
            ram_cap = 8
    else:
        if ram_gb <= 8:
            ram_cap = 3
        elif ram_gb <= 16:
            ram_cap = 5
        elif ram_gb <= 24:
            ram_cap = 7
        elif ram_gb <= 32:
            ram_cap = 9
        elif ram_gb <= 64:
            ram_cap = 12
        else:
            ram_cap = 16

    return max(1, min(int(cpu_cap), int(ram_cap)))


def _max_parallel_workers(total_items: int | None = None, io_heavy: bool = True, profile: str | None = None):
    env_raw = os.environ.get("CRATEGUARD_WORKERS", "").strip()
    try:
        env_workers = int(env_raw) if env_raw else None
    except Exception:
        env_workers = None

    env_max_raw = os.environ.get("CRATEGUARD_WORKERS_MAX", "").strip()
    try:
        env_max_workers = int(env_max_raw) if env_max_raw else None
    except Exception:
        env_max_workers = None

    cpu_count = os.cpu_count() or 4
    if env_workers and env_workers > 0:
        base = env_workers
    else:
        mode = str(profile or ("io" if io_heavy else "cpu")).lower()
        ram_gb = _get_system_memory_gb()
        base = _adaptive_worker_cap(cpu_count, ram_gb, mode)

    if env_max_workers and env_max_workers > 0:
        base = min(base, env_max_workers)

    if total_items is not None and total_items > 0:
        return max(1, min(base, int(total_items)))
    return max(1, base)


def _waveform_cache_put(src_path: Path, image_data_url: str):
    key = _waveform_cache_key(src_path)
    with WAVEFORM_CACHE_LOCK:
        WAVEFORM_CACHE[key] = image_data_url
        WAVEFORM_CACHE.move_to_end(key)
        while len(WAVEFORM_CACHE) > WAVEFORM_CACHE_MAX_ITEMS:
            WAVEFORM_CACHE.popitem(last=False)


def _lufs_cache_get(track_path: Path):
    key = str(track_path.resolve()).lower()
    with LUFS_CACHE_LOCK:
        if key not in LUFS_CACHE:
            return None
        value = LUFS_CACHE[key]
        LUFS_CACHE.move_to_end(key)
        return value


def _lufs_cache_put(track_path: Path, value: float):
    key = str(track_path.resolve()).lower()
    with LUFS_CACHE_LOCK:
        LUFS_CACHE[key] = value
        LUFS_CACHE.move_to_end(key)
        while len(LUFS_CACHE) > LUFS_CACHE_MAX_ITEMS:
            LUFS_CACHE.popitem(last=False)


def _resolve_audio_content_type(src_path: Path):
    ext = src_path.suffix.lower()
    if ext in AUDIO_CONTENT_TYPE_BY_EXT:
        return AUDIO_CONTENT_TYPE_BY_EXT[ext]
    mime_type, _ = mimetypes.guess_type(str(src_path))
    return mime_type or "application/octet-stream"


def _get_preview_audio_lock(cache_key: str):
    with PREVIEW_AUDIO_LOCK:
        lock = PREVIEW_AUDIO_LOCKS.get(cache_key)
        if lock is None:
            lock = threading.Lock()
            PREVIEW_AUDIO_LOCKS[cache_key] = lock
        return lock


def _preview_audio_cache_key(src_path: Path, clip_seconds: int | None = None):
    try:
        stat = src_path.stat()
        clip_part = int(clip_seconds) if clip_seconds and int(clip_seconds) > 0 else 0
        return f"{str(src_path.resolve()).lower()}::{int(stat.st_mtime_ns)}::{int(stat.st_size)}::clip={clip_part}"
    except Exception:
        clip_part = int(clip_seconds) if clip_seconds and int(clip_seconds) > 0 else 0
        return f"{str(src_path).lower()}::clip={clip_part}"


def _ensure_preview_audio_file(src_path: Path, ffmpeg_cmd: str | None, clip_seconds: int | None = None):
    if not ffmpeg_cmd:
        return None

    safe_clip_seconds = None
    try:
        if clip_seconds is not None:
            clip_value = int(clip_seconds)
            if clip_value > 0:
                safe_clip_seconds = max(1, min(120, clip_value))
    except Exception:
        safe_clip_seconds = None

    cache_key = _preview_audio_cache_key(src_path, safe_clip_seconds)
    cache_hash = hashlib.sha1(cache_key.encode("utf-8", errors="ignore")).hexdigest()
    ensure_dir(PREVIEW_AUDIO_CACHE_DIR)
    out_path = PREVIEW_AUDIO_CACHE_DIR / f"{cache_hash}.mp3"

    if out_path.exists() and out_path.stat().st_size > 0:
        return out_path

    lock = _get_preview_audio_lock(cache_key)
    with lock:
        if out_path.exists() and out_path.stat().st_size > 0:
            return out_path

        cmd = [
            ffmpeg_cmd,
            "-v", "error",
            "-y",
            "-i", str(src_path),
            "-map", "0:a:0",
            "-vn",
            "-sn",
            "-dn",
            "-ac", "2",
            "-ar", "44100",
            "-b:a", "192k",
        ]

        if safe_clip_seconds:
            cmd += ["-t", str(safe_clip_seconds)]

        cmd += [str(out_path)]

        try:
            res = subprocess.run(cmd, capture_output=True, timeout=180)
            if res.returncode != 0 or not out_path.exists() or out_path.stat().st_size <= 0:
                return None
        except Exception:
            return None

    return out_path

# =========================
# FFMPEG / FFPROBE
# =========================

FFMPEG_CMD_OVERRIDE = None
FFPROBE_CMD_OVERRIDE = None


def load_app_config():
    try:
        if not CONFIG_PATH.exists():
            return {}
        with CONFIG_PATH.open("r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def save_app_config(data: dict):
    try:
        with CONFIG_PATH.open("w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
    except Exception:
        pass


def get_clone_output_dir_config():
    cfg = load_app_config()
    raw = str(cfg.get("clone_output_dir") or "").strip()
    if raw:
        return raw
    try:
        return str((Path.home() / "Music").resolve())
    except Exception:
        return ""


def set_clone_output_dir_config(path_value: str):
    cfg = load_app_config()
    cfg["clone_output_dir"] = str(path_value or "").strip()
    save_app_config(cfg)


def _normalize_playlist_path_value(value: str):
    return str(value or "").strip().replace("\\", "/").lower()


def update_m3u8_track_path(m3u_path: Path, old_track_path: Path, new_track_path: Path):
    m3u_path = Path(m3u_path)
    if not m3u_path.exists() or not m3u_path.is_file():
        return {"ok": False, "error": "Playlist no existe"}

    old_abs = str(old_track_path.resolve())
    new_abs = str(new_track_path.resolve())
    old_abs_norm = _normalize_playlist_path_value(old_abs)

    try:
        lines = m3u_path.read_text(encoding="utf-8-sig").splitlines(keepends=True)
    except Exception as exc:
        return {"ok": False, "error": f"No se pudo leer playlist: {exc}"}

    changed = False
    replaced_lines = []

    for line in lines:
        content = line.rstrip("\r\n")
        stripped = content.strip()

        if not stripped or stripped.startswith("#"):
            replaced_lines.append(line)
            continue

        candidate_norm = _normalize_playlist_path_value(stripped)
        should_replace = candidate_norm == old_abs_norm

        if not should_replace:
            try:
                candidate_abs_norm = _normalize_playlist_path_value(str(Path(stripped).resolve()))
                should_replace = candidate_abs_norm == old_abs_norm
            except Exception:
                should_replace = False

        if should_replace:
            newline = "\r\n" if line.endswith("\r\n") else ("\n" if line.endswith("\n") else "")
            replaced_lines.append(f"{new_abs}{newline}")
            changed = True
        else:
            replaced_lines.append(line)

    if not changed:
        return {"ok": False, "error": "No se encontro la ruta original en la playlist"}

    try:
        m3u_path.write_text("".join(replaced_lines), encoding="utf-8")
    except Exception as exc:
        return {"ok": False, "error": f"No se pudo escribir playlist: {exc}"}

    return {"ok": True, "updated": True}


def is_valid_tool(path: str, name: str):
    try:
        subprocess.run([path, "-version"], capture_output=True, text=True, check=True, encoding="utf-8", errors="ignore")
        return True
    except Exception:
        return False


def _default_ff_paths(tool_name: str):
    tool = f"{tool_name}.exe"
    return [
        Path.home() / "AppData/Local/Microsoft/WinGet/Links" / tool,
        Path.home() / "AppData/Local/Microsoft/WinGet/Packages" / "Gyan.FFmpeg_Microsoft.Winget.Source_8wekyb3d8bbwe/ffmpeg-8.0.1-full_build/bin" / tool,
    ]


def resolve_ff_tool(tool_name: str, override: str | None, env_var: str, config_key: str):
    if override and is_valid_tool(override, tool_name):
        return override

    env_path = os.environ.get(env_var)
    if env_path and is_valid_tool(env_path, tool_name):
        return env_path

    cfg = load_app_config()
    cfg_path = cfg.get(config_key)
    if cfg_path and is_valid_tool(cfg_path, tool_name):
        return cfg_path

    for path in _default_ff_paths(tool_name):
        if path.exists() and is_valid_tool(str(path), tool_name):
            return str(path)

    try:
        subprocess.run([tool_name, "-version"], capture_output=True, text=True, check=True, encoding="utf-8", errors="ignore")
        return tool_name
    except Exception:
        return None


def get_ffmpeg_cmd():
    return resolve_ff_tool("ffmpeg", FFMPEG_CMD_OVERRIDE, "FFMPEG_PATH", "ffmpeg_path")


def get_ffprobe_cmd():
    return resolve_ff_tool("ffprobe", FFPROBE_CMD_OVERRIDE, "FFPROBE_PATH", "ffprobe_path")


def set_ffmpeg_paths_gui(parent):
    global FFMPEG_CMD_OVERRIDE, FFPROBE_CMD_OVERRIDE

    ffmpeg_path = filedialog.askopenfilename(
        title="Selecciona ffmpeg.exe",
        filetypes=[("ffmpeg.exe", "ffmpeg.exe"), ("EXE", "*.exe"), ("All files", "*.*")]
    )
    if not ffmpeg_path:
        return

    if not is_valid_tool(ffmpeg_path, "ffmpeg"):
        messagebox.showerror("Error", "No se pudo validar ffmpeg.exe")
        return

    FFMPEG_CMD_OVERRIDE = ffmpeg_path

    ffprobe_candidate = str(Path(ffmpeg_path).with_name("ffprobe.exe"))
    if Path(ffprobe_candidate).exists():
        FFPROBE_CMD_OVERRIDE = ffprobe_candidate
        cfg = load_app_config()
        cfg["ffmpeg_path"] = FFMPEG_CMD_OVERRIDE
        cfg["ffprobe_path"] = FFPROBE_CMD_OVERRIDE
        save_app_config(cfg)
        messagebox.showinfo("OK", f"ffmpeg configurado manualmente.\nffprobe detectado: {ffprobe_candidate}")
        return

    if messagebox.askyesno("ffprobe", "No encontre ffprobe.exe en la misma carpeta. Deseas seleccionarlo manualmente?"):
        ffprobe_path = filedialog.askopenfilename(
            title="Selecciona ffprobe.exe",
            filetypes=[("ffprobe.exe", "ffprobe.exe"), ("EXE", "*.exe"), ("All files", "*.*")]
        )
        if ffprobe_path:
            if is_valid_tool(ffprobe_path, "ffprobe"):
                FFPROBE_CMD_OVERRIDE = ffprobe_path
            else:
                messagebox.showerror("Error", "No se pudo validar ffprobe.exe")

    cfg = load_app_config()
    cfg["ffmpeg_path"] = FFMPEG_CMD_OVERRIDE
    if FFPROBE_CMD_OVERRIDE:
        cfg["ffprobe_path"] = FFPROBE_CMD_OVERRIDE
    save_app_config(cfg)

    messagebox.showinfo("OK", "ffmpeg configurado manualmente.")


def get_audio_metadata(audio_path: str, ffprobe_cmd: str):
    cmd = [
        ffprobe_cmd,
        "-v", "error",
        "-select_streams", "a:0",
        "-show_entries", "stream=codec_name,sample_fmt,bits_per_raw_sample,bits_per_sample,sample_rate,channels,bit_rate,duration",
        "-of", "default=nw=1",
        audio_path
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, check=True, encoding="utf-8", errors="ignore", timeout=20)
    except Exception:
        return {}

    info = {}
    for line in result.stdout.splitlines():
        if "=" not in line:
            continue
        key, val = line.split("=", 1)
        info[key.strip()] = val.strip()

    return info


def get_lufs_integrated(audio_path: str, ffmpeg_cmd: str, max_seconds: int | None = None):
    cmd = [
        ffmpeg_cmd,
        "-hide_banner",
        "-nostats",
    ]
    if max_seconds:
        cmd += ["-t", str(max_seconds)]
    cmd += [
        "-i", audio_path,
        "-vn",
        "-sn",
        "-dn",
        "-filter_complex", "ebur128=peak=true",
        "-f", "null",
        "-"
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="ignore", timeout=60)
    except Exception:
        return None

    text = result.stderr or ""
    matches = re.findall(r"I:\s*(-?\d+(?:\.\d+)?)\s*LUFS", text)
    if not matches:
        matches = re.findall(r"Integrated loudness:\s*I:\s*(-?\d+(?:\.\d+)?)", text)
    if not matches:
        return None
    try:
        return float(matches[-1])
    except Exception:
        return None


def get_lufs_and_true_peak(audio_path: str, ffmpeg_cmd: str, max_seconds: int | None = None):
    cmd = [
        ffmpeg_cmd,
        "-hide_banner",
        "-nostats",
    ]
    if max_seconds:
        cmd += ["-t", str(max_seconds)]
    cmd += [
        "-i", audio_path,
        "-vn",
        "-sn",
        "-dn",
        "-filter_complex", "ebur128=peak=true",
        "-f", "null",
        "-"
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="ignore", timeout=90)
    except Exception:
        return None, None

    text = result.stderr or ""
    lufs = None
    true_peak = None

    lufs_matches = re.findall(r"I:\s*(-?\d+(?:\.\d+)?)\s*LUFS", text)
    if lufs_matches:
        try:
            lufs = float(lufs_matches[-1])
        except Exception:
            lufs = None

    peak_candidates = []
    for pattern in (
        r"TPK:\s*(-?\d+(?:\.\d+)?)\s*dBFS",
        r"Peak:\s*(-?\d+(?:\.\d+)?)\s*dBFS",
    ):
        for val in re.findall(pattern, text):
            try:
                peak_candidates.append(float(val))
            except Exception:
                pass

    if peak_candidates:
        true_peak = max(peak_candidates)

    if true_peak is None:
        try:
            vd_cmd = [
                ffmpeg_cmd,
                "-hide_banner",
                "-nostats",
                "-i", audio_path,
                "-vn",
                "-sn",
                "-dn",
                "-af", "volumedetect",
                "-f", "null",
                "-",
            ]
            vd = subprocess.run(vd_cmd, capture_output=True, text=True, encoding="utf-8", errors="ignore", timeout=60)
            vd_text = vd.stderr or ""
            match = re.search(r"max_volume:\s*(-?\d+(?:\.\d+)?)\s*dB", vd_text)
            if match:
                true_peak = float(match.group(1))
        except Exception:
            pass

    return lufs, true_peak


def compute_safe_normalize_gain_db(audio_path: str, ffmpeg_cmd: str, target_lufs: float = -12.0, target_tp_db: float = -1.0):
    measured_lufs, measured_tp = get_lufs_and_true_peak(audio_path, ffmpeg_cmd)
    if measured_lufs is None:
        return 0.0

    wanted_gain = target_lufs - measured_lufs
    if wanted_gain <= 0:
        return 0.0

    if measured_tp is None:
        return round(max(0.0, wanted_gain), 2)

    tp_headroom = target_tp_db - measured_tp
    safe_gain = min(wanted_gain, tp_headroom)
    if safe_gain <= 0:
        return 0.0
    return round(safe_gain, 2)


def extract_cover_base64(audio_path: Path, ffmpeg_cmd: str | None):
    if not audio_path.exists():
        return None

    try:
        from mutagen import File as MutagenFile
        mut = MutagenFile(str(audio_path))
        if mut:
            pictures = getattr(mut, "pictures", None)
            if pictures:
                picture = pictures[0]
                image_bytes = bytes(getattr(picture, "data", b""))
                mime = str(getattr(picture, "mime", "") or "image/jpeg")
                if image_bytes:
                    encoded = base64.b64encode(image_bytes).decode("ascii")
                    return f"data:{mime};base64,{encoded}"

        tags = getattr(mut, "tags", None)
        if tags:
            pictures = getattr(tags, "pictures", None)
            if pictures:
                picture = pictures[0]
                image_bytes = bytes(getattr(picture, "data", b""))
                mime = str(getattr(picture, "mime", "") or "image/jpeg")
                if image_bytes:
                    encoded = base64.b64encode(image_bytes).decode("ascii")
                    return f"data:{mime};base64,{encoded}"

            try:
                from mutagen.id3 import APIC
                apic_frames = tags.getall("APIC") if hasattr(tags, "getall") else []
                if apic_frames:
                    apic = apic_frames[0]
                    image_bytes = bytes(getattr(apic, "data", b""))
                    mime = str(getattr(apic, "mime", "") or "image/jpeg")
                    if image_bytes:
                        encoded = base64.b64encode(image_bytes).decode("ascii")
                        return f"data:{mime};base64,{encoded}"
            except Exception:
                pass

            covr = tags.get("covr") if hasattr(tags, "get") else None
            if covr:
                first = covr[0] if isinstance(covr, (list, tuple)) else covr
                image_bytes = bytes(first)
                if image_bytes:
                    mime = "image/png" if image_bytes.startswith(b"\x89PNG") else "image/jpeg"
                    encoded = base64.b64encode(image_bytes).decode("ascii")
                    return f"data:{mime};base64,{encoded}"
    except Exception:
        pass

    if not ffmpeg_cmd:
        return None

    try:
        with tempfile.TemporaryDirectory() as tmp_dir:
            out_path = Path(tmp_dir) / "cover.png"
            cmd = [
                ffmpeg_cmd,
                "-v", "error",
                "-y",
                "-i", str(audio_path),
                "-map", "0:v:0",
                "-frames:v", "1",
                str(out_path)
            ]
            res = subprocess.run(cmd, capture_output=True, timeout=12)
            if res.returncode != 0 or not out_path.exists():
                cmd_transcode = [
                    ffmpeg_cmd,
                    "-v", "error",
                    "-y",
                    "-i", str(audio_path),
                    "-an",
                    "-frames:v", "1",
                    str(out_path)
                ]
                subprocess.run(cmd_transcode, capture_output=True, timeout=12)

            if out_path.exists():
                image_bytes = out_path.read_bytes()
                mime = "image/png" if image_bytes.startswith(b"\x89PNG") else "image/jpeg"
                encoded = base64.b64encode(image_bytes).decode("ascii")
                return f"data:{mime};base64,{encoded}"
    except Exception:
        return None
    return None

# =========================
# UTILS
# =========================

def ensure_dir(p: Path):
    p.mkdir(parents=True, exist_ok=True)


def safe_filename(name: str):
    import unicodedata

    name = name.replace("\n", " ").replace("\r", " ")
    name = unicodedata.normalize("NFKD", name)
    name = name.encode("ascii", "ignore").decode("ascii")

    allowed = set("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789 _-.")
    cleaned = []
    for ch in name:
        if ch in allowed:
            cleaned.append(ch)
        elif ch in "()":
            continue
        else:
            cleaned.append("_")

    name = "".join(cleaned)
    while "  " in name:
        name = name.replace("  ", " ")

    name = name.replace(" ", "_")
    name = name.replace("+", "_")
    name = name.replace("(", "").replace(")", "")
    name = name.replace(".", "")
    while "__" in name:
        name = name.replace("__", "_")
        
    name = name.strip("_").strip()
    if not name:
        name = "Unknown"

    reserved = {
        "CON", "PRN", "AUX", "NUL",
        "COM1", "COM2", "COM3", "COM4", "COM5", "COM6", "COM7", "COM8", "COM9",
        "LPT1", "LPT2", "LPT3", "LPT4", "LPT5", "LPT6", "LPT7", "LPT8", "LPT9"
    }
    if name.upper() in reserved:
        name = f"_{name}"

    if len(name) > SAFE_FILENAME_MAX_LEN:
        name = name[:SAFE_FILENAME_MAX_LEN].rstrip(" .")

    return name.upper()


def parse_m3u(m3u_path: Path):
    tracks = []
    with m3u_path.open("r", encoding="utf-8", errors="ignore") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            tracks.append(line)
    return tracks


def normalize_track_path(track_line: str, m3u_dir: Path):
    p = Path(track_line)
    if p.is_absolute():
        return p
    return (m3u_dir / p).resolve()


def resolve_scan_input_paths(path_value: str):
    raw = str(path_value or "").strip()
    raw = unquote(raw)
    if raw.lower().startswith("file://"):
        parsed_uri = urlparse(raw)
        raw = unquote(parsed_uri.path or "").strip()
        if re.match(r"^/[A-Za-z]:/", raw):
            raw = raw[1:]
        raw = raw.replace("/", "\\")
    if raw.endswith("\\*") or raw.endswith("/*"):
        raw = raw[:-2].strip()
    if (raw.startswith('"') and raw.endswith('"')) or (raw.startswith("'") and raw.endswith("'")):
        raw = raw[1:-1].strip()
    if raw.startswith("`") and raw.endswith("`"):
        raw = raw[1:-1].strip()
    if not raw:
        return {"ok": False, "error": "Ruta faltante"}

    source_path = Path(raw).expanduser()
    if not source_path.exists() and not source_path.is_absolute():
        cwd_candidate = (Path.cwd() / source_path).expanduser()
        script_candidate = (Path(__file__).parent / source_path).expanduser()
        if cwd_candidate.exists():
            source_path = cwd_candidate
        elif script_candidate.exists():
            source_path = script_candidate

    if not source_path.exists():
        normalized_raw = raw.replace("\\", "/").strip()
        file_name_only = Path(normalized_raw).name
        looks_like_browser_fakepath = "fakepath" in normalized_raw.lower()
        has_no_path_separators = ("/" not in normalized_raw and "\\" not in raw)
        if file_name_only and (looks_like_browser_fakepath or has_no_path_separators):
            matches = []
            for root, _, names in os.walk(Path.cwd()):
                for name in names:
                    if name.lower() == file_name_only.lower():
                        match_path = (Path(root) / name).resolve()
                        matches.append(match_path)
                        if len(matches) > 1:
                            break
                if len(matches) > 1:
                    break

            if len(matches) == 1:
                source_path = matches[0]
            elif len(matches) > 1:
                return {"ok": False, "error": f"Nombre ambiguo: {file_name_only}. Pega la ruta completa."}

    if not source_path.exists():
        return {"ok": False, "error": f"Ruta no encontrada: {raw}"}

    if source_path.is_dir():
        tracks = []
        for root, _, names in os.walk(source_path):
            for name in names:
                p = (Path(root) / name)
                if p.suffix.lower() in INPUT_EXTS:
                    tracks.append(p.resolve())
        tracks = sorted(tracks, key=lambda p: str(p).lower())
        if not tracks:
            return {"ok": False, "error": "No hay archivos de audio en la carpeta"}
        return {"ok": True, "m3u_path": None, "track_paths": tracks}

    ext = source_path.suffix.lower()
    if ext in {".m3u", ".m3u8"}:
        return {"ok": True, "m3u_path": source_path, "track_paths": None}

    if ext in INPUT_EXTS:
        return {"ok": True, "m3u_path": None, "track_paths": [source_path.resolve()]}

    return {"ok": False, "error": "Formato no soportado. Usa .m3u/.m3u8, carpeta o archivo de audio"}


def _audio_quality_tier(ext: str):
    value = str(ext or "").lower()
    if value in {".mp3", ".aac", ".m4a"}:
        return "LOSSY"
    if value in {".flac", ".wav", ".wave", ".aif", ".aiff", ".alac"}:
        return "HQ"
    return "STD"


def _has_container_codec_mismatch(ext: str, codec_name: str):
    ext_value = str(ext or "").lower().strip()
    codec_value = str(codec_name or "").lower().strip()
    if not ext_value or not codec_value:
        return False

    if ext_value == ".flac":
        return codec_value != "flac"

    return False


def deep_scan_playlist(
    m3u_path: Path | None,
    ffprobe_cmd: str,
    ffmpeg_cmd: str | None,
    fast_lufs: bool = True,
    enable_lufs: bool = True,
    include_cover_image: bool = False,
    enable_decode_check: bool = True,
    track_paths: list[Path] | None = None,
    collect_rows: bool = True,
    collect_report: bool = True,
    fast_scan_mode: bool = False,
    progress_cb=None,
    row_cb=None,
):
    if track_paths is not None:
        src_paths = [Path(p) for p in track_paths]
    else:
        if m3u_path is None:
            raise ValueError("m3u_path es requerido cuando no se proveen track_paths")
        tracks_raw = parse_m3u(m3u_path)
        m3u_dir = m3u_path.parent
        src_paths = [normalize_track_path(x, m3u_dir) for x in tracks_raw]

    missing = [p for p in src_paths if not p.exists()]
    existing = [p for p in src_paths if p.exists()]

    per_track = {} if collect_report else None
    rows = [] if collect_rows else None
    device_results = None
    if collect_report:
        device_results = {}
        for device in HARDWARE_SPECS:
            device_results[device] = {
                "unsupported": [],
                "missing": missing,
                "issues_by_file": {},
                "ok": False,
            }

    total = len(src_paths)
    lufs_window = FAST_LUFS_WINDOW_SECONDS if fast_lufs else None

    if fast_scan_mode and not collect_report:
        def _build_fast_row(idx: int, p: Path):
            if not p.exists():
                missing_reasons = _group_reason_map("Archivo faltante")
                row = {
                    "iid": f"missing::{idx}::{p}",
                    "track_index": idx,
                    "values": (p.name, "-", "-", "-", "-", "INCOMPATIBLE"),
                    "tags": ("missing",),
                    "path": None,
                    "bpm": "--",
                    "key": "--",
                    "duration_seconds": None,
                    "compatibility_score": 0,
                    "device_support": _empty_device_support(incompatible_all=True),
                    "device_reasons": missing_reasons,
                    "details_text": "Archivo faltante",
                    "quality_tier": "STD",
                    "bitrate_kbps": None,
                    "cover_image": None,
                    "bpm_source": "",
                    "key_source": "",
                }
                return idx, p.name, row

            try:
                info = get_audio_metadata(str(p), ffprobe_cmd)
                try:
                    sr = int(info.get("sample_rate")) if info.get("sample_rate") else None
                except Exception:
                    sr = None
                bits = None
                # Try Mutagen first for WAV/AIFF
                if p.suffix.lower() in {".wav", ".wave", ".aif", ".aiff"}:
                    bits = get_mutagen_bit_depth(str(p))
                if not bits:
                    bits_raw = info.get("bits_per_raw_sample") or info.get("bits_per_sample")
                    if bits_raw:
                        try:
                            bits = int(bits_raw)
                        except Exception:
                            bits = None
                if bits is None:
                    sample_fmt = str(info.get("sample_fmt") or "").strip().lower()
                    bits = {"u8": 8, "s16": 16, "s24": 24, "s32": 32, "flt": 32, "dbl": 64}.get(sample_fmt)
                br = _parse_bitrate_kbps(info)

                header_err, is_float_32 = check_container_header_and_format(str(p))
                decode_ok, decode_err, _, _ = decode_integrity_check(
                    str(p),
                    ffmpeg_cmd,
                    ffprobe_cmd,
                    max_probe_seconds=None,
                    header_duration_hint=_duration_hint_from_metadata_info(info),
                    low_priority=True,
                )

                has_audio_stream = bool(info) or (isinstance(sr, int) and sr > 0)
                metadata_unreadable = not has_audio_stream
                codec_name = str(info.get("codec_name") or "").strip().lower()
                duration_hint = _duration_hint_from_metadata_info(info)
                codec_mismatch = _has_container_codec_mismatch(p.suffix.lower(), codec_name)
                is_broken = False
                if not decode_ok and _is_fatal_decode_error(decode_err):
                    is_broken = True
                elif not decode_ok and "truncado" in str(decode_err or "").lower():
                    is_broken = True
                elif metadata_unreadable:
                    is_broken = True
                elif not codec_name:
                    is_broken = True
                elif codec_mismatch:
                    is_broken = True
                elif duration_hint is not None and duration_hint <= 0:
                    is_broken = True
                elif metadata_unreadable and header_err:
                    is_broken = True

                if is_float_32:
                    bits_value = "32-float"
                elif bits:
                    bits_value = bits
                else:
                    sample_fmt = info.get("sample_fmt")
                    bits_value = {"s16": 16, "s24": 24, "s32": 32, "flt": "32-float", "dbl": 64}.get(sample_fmt, "-")

                fmt = info.get("codec_name", "").upper() or (p.suffix.lstrip(".").upper() if p.suffix else "-")
                stem = p.stem.replace("_", " ").strip()
                if " - " in stem:
                    artist_part, title_part = stem.split(" - ", 1)
                    quick_artist = artist_part.strip() or "N-A"
                    quick_title = title_part.strip() or "N-A"
                else:
                    quick_artist = "N-A"
                    quick_title = stem or "N-A"
                display_name = f"{quick_artist} - {quick_title}"

                if is_broken:
                    result = "ROTO"
                    score_value = 0
                    tags = ("critical",)
                    device_support = _empty_device_support(incompatible_all=True)
                    device_reasons = _group_reason_map("Archivo roto o corrupto")
                else:
                    result = "COMPATIBLE"
                    score_value = 10
                    tags = ()
                    device_support = _empty_device_support(incompatible_all=False)
                    device_reasons = _empty_device_reasons()

                issues_summary = []
                if is_broken:
                    issues_summary.append("Archivo roto")
                    if decode_err:
                        issues_summary.append(f"Error de lectura: {decode_err}")
                    if codec_mismatch:
                        issues_summary.append(f"Contenedor/codec inconsistente: ext {p.suffix.lower()} con codec {codec_name or '-'}")
                elif header_err:
                    issues_summary.append("Header con inconsistencias")

                row = {
                    "iid": f"track::{idx}::{p}",
                    "track_index": idx,
                    "values": (display_name, fmt, sr or "-", bits_value, "-", result),
                    "tags": tags,
                    "path": p,
                    "bpm": "--",
                    "bpm_source": "",
                    "key": "--",
                    "key_source": "",
                    "duration_seconds": None,
                    "bitrate_kbps": br,
                    "cover_image": extract_cover_base64(p, ffmpeg_cmd),
                    "compatibility_score": score_value,
                    "device_support": device_support,
                    "device_reasons": device_reasons,
                    "details_text": "; ".join(issues_summary) if issues_summary else "Chequeo rápido de integridad OK",
                    "quality_tier": _audio_quality_tier(p.suffix.lower()),
                }
                return idx, p.name, row
            except Exception as e:
                error_reasons = _group_reason_map("Error de lectura")
                row = {
                    "iid": f"error::{idx}::{p}",
                    "track_index": idx,
                    "values": (p.name, "-", "-", "-", "-", "ROTO"),
                    "tags": ("critical",),
                    "path": p,
                    "bpm": "--",
                    "key": "--",
                    "duration_seconds": None,
                    "bitrate_kbps": None,
                    "cover_image": None,
                    "compatibility_score": 0,
                    "device_support": _empty_device_support(incompatible_all=True),
                    "device_reasons": error_reasons,
                    "details_text": f"Error de lectura: {e}",
                    "quality_tier": _audio_quality_tier(p.suffix.lower()),
                    "bpm_source": "",
                    "key_source": "",
                }
                return idx, p.name, row

        workers = _max_parallel_workers(total, io_heavy=True, profile="decode")
        if workers <= 1 or total <= 1:
            for idx, p in enumerate(src_paths, start=1):
                idx_out, name_out, row = _build_fast_row(idx, p)
                if progress_cb:
                    progress_cb(idx_out, total, name_out)
                if rows is not None:
                    rows.append(row)
                if row_cb:
                    row_cb(row)
        else:
            completed = 0
            with ThreadPoolExecutor(max_workers=workers) as executor:
                future_map = {executor.submit(_build_fast_row, idx, p): (idx, p) for idx, p in enumerate(src_paths, start=1)}
                for fut in as_completed(future_map):
                    idx_out, name_out, row = fut.result()
                    completed += 1
                    if progress_cb:
                        progress_cb(completed, total, name_out)
                    if rows is not None:
                        rows.append(row)
                    if row_cb:
                        row_cb(row)

        report = {
            "tracks_count": total,
        }
        return report, (rows or [])

    for idx, p in enumerate(src_paths, start=1):
        if progress_cb:
            progress_cb(idx, total, p.name)

        if not p.exists():
            missing_reasons = _group_reason_map("Archivo faltante")
            row = {
                "iid": f"missing::{idx}::{p}",
                "track_index": idx,
                "values": (p.name, "-", "-", "-", "-", "INCOMPATIBLE"),
                "tags": ("missing",),
                "path": None,
                "bpm": "--",
                "key": "--",
                "duration_seconds": None,
                "compatibility_score": 0,
                "device_support": _empty_device_support(incompatible_all=True),
                "device_reasons": missing_reasons,
                "details_text": "Archivo faltante",
                "quality_tier": "STD",
            }
            if rows is not None:
                rows.append(row)
            if row_cb:
                row_cb(row)
            continue

        try:
            info = get_audio_metadata(str(p), ffprobe_cmd)
            if fast_scan_mode:
                try:
                    sr = int(info.get("sample_rate")) if info.get("sample_rate") else None
                except Exception:
                    sr = None
                bits = None
                bits_raw = info.get("bits_per_raw_sample") or info.get("bits_per_sample")
                if bits_raw:
                    try:
                        bits = int(bits_raw)
                    except Exception:
                        bits = None
                if bits is None:
                    sample_fmt = str(info.get("sample_fmt") or "").strip().lower()
                    bits = {"u8": 8, "s16": 16, "s24": 24, "s32": 32, "flt": 32, "dbl": 64}.get(sample_fmt)
                br = _parse_bitrate_kbps(info)
            else:
                sr, bits, br = get_audio_metrics(str(p), ffprobe_cmd, metadata_info=info)
            header_err, is_float_32 = check_container_header_and_format(str(p))
            if fast_scan_mode:
                duration_hint = _duration_hint_from_metadata_info(info)
                stem = p.stem.replace("_", " ").strip()
                if " - " in stem:
                    artist_part, title_part = stem.split(" - ", 1)
                    quick_artist = artist_part.strip() or "N-A"
                    quick_title = title_part.strip() or "N-A"
                else:
                    quick_artist = "N-A"
                    quick_title = stem or "N-A"
                track_meta = {
                    "artist": quick_artist,
                    "title": quick_title,
                    "bpm": "--",
                    "bpm_source": "",
                    "key": "--",
                    "key_source": "",
                    "duration_seconds": duration_hint,
                }
                duration_seconds = duration_hint
            else:
                duration_hint = _duration_hint_from_metadata_info(info)
                track_meta = get_track_metadata(
                    p,
                    duration_seconds_hint=duration_hint,
                    ffprobe_cmd=ffprobe_cmd,
                    infer_bpm_key=False,
                )
                duration_seconds = track_meta.get("duration_seconds")
            lufs = None
            if enable_lufs and ffmpeg_cmd and not fast_scan_mode:
                lufs = get_lufs_integrated(str(p), ffmpeg_cmd, lufs_window)

            decode_ok = True
            decode_err = ""
            if enable_decode_check and ffmpeg_cmd and ffprobe_cmd:
                decode_ok, decode_err, _, _ = decode_integrity_check(
                    str(p),
                    ffmpeg_cmd,
                    ffprobe_cmd,
                    max_probe_seconds=None,
                    header_duration_hint=duration_seconds,
                    low_priority=fast_scan_mode,
                )

            meta = {
                "ext": p.suffix.lower(),
                "codec": info.get("codec_name", ""),
                "sample_fmt": info.get("sample_fmt", ""),
                "sample_rate": sr,
                "bit_depth": bits,
                "bitrate_kbps": br,
                "header_err": header_err,
                "is_float_32": is_float_32,
                "lufs": lufs,
            }
            if per_track is not None:
                per_track[p] = meta

            lossy_exts = {".mp3", ".aac", ".m4a"}
            is_lossy = meta["ext"] in lossy_exts
            has_audio_stream = bool(info) or (isinstance(sr, int) and sr > 0)
            duration_missing = (duration_seconds is None) or (duration_seconds <= 0)
            metadata_unreadable = not has_audio_stream
            codec_name = str(info.get("codec_name") or "").strip().lower()
            codec_mismatch = _has_container_codec_mismatch(meta["ext"], codec_name)
            is_broken = False
            if enable_decode_check and ffmpeg_cmd and ffprobe_cmd and not decode_ok and _is_fatal_decode_error(decode_err):
                is_broken = True
            elif enable_decode_check and ffmpeg_cmd and ffprobe_cmd and not decode_ok and "truncado" in str(decode_err or "").lower():
                is_broken = True
            elif metadata_unreadable:
                is_broken = True
            elif not codec_name:
                is_broken = True
            elif codec_mismatch:
                is_broken = True
            elif duration_missing:
                is_broken = True
            elif metadata_unreadable and header_err:
                is_broken = True

            if fast_scan_mode:
                if meta["is_float_32"]:
                    bits_value = "32-float"
                elif meta["bit_depth"]:
                    bits_value = meta["bit_depth"]
                else:
                    sample_fmt = meta.get("sample_fmt")
                    bits_value = {"s16": 16, "s24": 24, "s32": 32, "flt": "32-float", "dbl": 64}.get(sample_fmt, "-")

                fmt = meta.get("codec", "").upper() or (meta["ext"].lstrip(".").upper() if meta["ext"] else "-")
                artist = track_meta.get("artist") or "N-A"
                title = track_meta.get("title") or "N-A"
                display_name = f"{artist} - {title}"

                if is_broken:
                    result = "ROTO"
                    score_value = 0
                    tags = ("critical",)
                    device_support = _empty_device_support(incompatible_all=True)
                    device_reasons = _group_reason_map("Archivo roto o corrupto")
                else:
                    result = "COMPATIBLE"
                    score_value = 10
                    tags = ()
                    device_support = _empty_device_support(incompatible_all=False)
                    device_reasons = _empty_device_reasons()

                issues_summary = []
                if is_broken:
                    issues_summary.append("Archivo roto")
                    if decode_err:
                        issues_summary.append(f"Error de lectura: {decode_err}")
                    if codec_mismatch:
                        issues_summary.append(f"Contenedor/codec inconsistente: ext {meta.get('ext') or '-'} con codec {codec_name or '-'}")
                elif meta["header_err"]:
                    issues_summary.append("Header con inconsistencias")

                row = {
                    "iid": f"track::{idx}::{p}",
                    "track_index": idx,
                    "values": (display_name, fmt, sr or "-", bits_value, "-", result),
                    "tags": tags,
                    "path": p,
                    "bpm": "--",
                    "bpm_source": "",
                    "key": "--",
                    "key_source": "",
                    "duration_seconds": None,
                    "bitrate_kbps": meta.get("bitrate_kbps"),
                    "cover_image": None,
                    "compatibility_score": score_value,
                    "device_support": device_support,
                    "device_reasons": device_reasons,
                    "details_text": "; ".join(issues_summary) if issues_summary else "Chequeo rápido de integridad OK",
                    "quality_tier": _audio_quality_tier(meta.get("ext")),
                }
                if rows is not None:
                    rows.append(row)
                if row_cb:
                    row_cb(row)
                continue

            classification = _classify_dj_smart_check(meta, is_broken)
            device_support = classification.get("device_support") or {"compatible": [], "warning": [], "incompatible": []}
            device_reasons = classification.get("device_reasons") or _empty_device_reasons()
            score_raw = classification.get("score")
            base_score = int(score_raw) if score_raw is not None else 6
            base_result = classification.get("status") or "ADVERTENCIA"
            if device_results is not None:
                for device in HARDWARE_SPECS:
                    device_label = DEVICE_LABEL_TO_GROUP.get(device, device)

                    reasons = list(device_reasons.get(device_label) or [])
                    issues = []
                    if device_label in (device_support.get("incompatible") or []):
                        issues = [("INCOMPATIBLE", f"{device}: {reason}") for reason in reasons] or [("INCOMPATIBLE", f"{device}: Incompatible")]
                        device_results[device]["unsupported"].append(p)
                    elif device_label in (device_support.get("warning") or []):
                        issues = [("WARNING", f"{device}: {reason}") for reason in reasons] or [("WARNING", f"{device}: Advertencia")]
                    elif reasons:
                        issues = [("INFO", f"{device}: {reason}") for reason in reasons]

                    if issues:
                        device_results[device]["issues_by_file"][p] = issues

            lufs_out_of_range = lufs is not None and (lufs < LUFS_WARN_LOW or lufs > LUFS_WARN_HIGH)
            warning_any = base_result == "ADVERTENCIA"
            score_value, result = _compute_smart_usb_score(
                meta,
                is_broken,
                format_unsupported=False,
                is_lossy=is_lossy,
                lufs_out_of_range=lufs_out_of_range,
                warning_any=warning_any,
            )
            if result in {"ROTO", "INCOMPATIBLE"}:
                tags = ("critical",)
            elif result in {"ADVERTENCIA", "LIMITADO"}:
                tags = ("warning",)
            else:
                tags = ()

            if meta["is_float_32"]:
                bits_value = "32-float"
            elif meta["bit_depth"]:
                bits_value = meta["bit_depth"]
            else:
                sample_fmt = meta.get("sample_fmt")
                bits_value = {"s16": 16, "s24": 24, "s32": 32, "flt": "32-float", "dbl": 64}.get(sample_fmt, "-")

            fmt = meta.get("codec", "").upper() or (meta["ext"].lstrip(".").upper() if meta["ext"] else "-")
            lufs_value = "-"
            if meta.get("lufs") is not None:
                lufs_value = f"{meta['lufs']:.1f}"

            artist = track_meta.get("artist") or "N-A"
            title = track_meta.get("title") or "N-A"
            display_name = f"{artist} - {title}"

            issues_summary = []
            if is_broken:
                issues_summary.append("Archivo roto")
                if decode_err:
                    issues_summary.append(f"Error de lectura: {decode_err}")
                if codec_mismatch:
                    issues_summary.append(f"Contenedor/codec inconsistente: ext {meta.get('ext') or '-'} con codec {codec_name or '-'}")
            elif metadata_unreadable:
                issues_summary.append("No se pudo leer metadata completa (se evaluó por extensión)")
            elif not decode_ok and decode_err:
                issues_summary.append(f"Advertencia de decodificacion: {decode_err}")
            if duration_missing and not fast_scan_mode:
                issues_summary.append("Duración no detectada (archivo reproducible)")
            if lufs_out_of_range:
                issues_summary.append("Volumen fuera de rango")
            if meta["header_err"]:
                issues_summary.append("Header roto o truncado")

            class_details = str(classification.get("details_text") or "").strip()
            if class_details:
                issues_summary.insert(0, class_details)

            if score_value < base_score and not is_broken:
                if is_lossy:
                    issues_summary.append("Score ajustado por formato Lossy (SD)")
                if lufs_out_of_range:
                    issues_summary.append("Score ajustado por LUFS fuera de rango")

            row = {
                "iid": f"track::{idx}::{p}",
                "track_index": idx,
                "values": (display_name, fmt, sr or "-", bits_value, lufs_value, result),
                "tags": tags,
                "path": p,
                "bpm": track_meta.get("bpm") or "--",
                "bpm_source": track_meta.get("bpm_source") or "",
                "key": track_meta.get("key") or "--",
                "key_source": track_meta.get("key_source") or "",
                "duration_seconds": track_meta.get("duration_seconds"),
                "bitrate_kbps": meta.get("bitrate_kbps"),
                "cover_image": extract_cover_base64(p, ffmpeg_cmd) if include_cover_image else None,
                "compatibility_score": score_value,
                "device_support": device_support,
                "device_reasons": device_reasons,
                "details_text": "; ".join(issues_summary) if issues_summary else (classification.get("message") or "OK"),
                "quality_tier": _audio_quality_tier(meta.get("ext")),
            }
            if rows is not None:
                rows.append(row)
            if row_cb:
                row_cb(row)
        except Exception as e:
            error_reasons = _group_reason_map("Error de lectura")
            row = {
                "iid": f"error::{idx}::{p}",
                "track_index": idx,
                "values": (p.name, "-", "-", "-", "-", "ROTO"),
                "tags": ("critical",),
                "path": p,
                "bpm": "--",
                "key": "--",
                "duration_seconds": None,
                "bitrate_kbps": None,
                "cover_image": None,
                "compatibility_score": 0,
                "device_support": _empty_device_support(incompatible_all=True),
                "device_reasons": error_reasons,
                "details_text": f"Error de lectura: {e}",
                "quality_tier": _audio_quality_tier(p.suffix.lower()),
            }
            if rows is not None:
                rows.append(row)
            if row_cb:
                row_cb(row)

    if device_results is not None:
        for device, r in device_results.items():
            r["ok"] = (len(r.get("unsupported", [])) == 0)

        compatible = [d for d, r in device_results.items() if r["ok"]]
        incompatible = [d for d, r in device_results.items() if not r["ok"]]

        report = {
            "tracks": src_paths,
            "missing": missing,
            "existing": existing,
            "compatible": compatible,
            "incompatible": incompatible,
            "device_results": device_results,
            "per_track": per_track,
        }
    else:
        report = {
            "tracks_count": total,
        }

    return report, (rows or [])


def analyze_playlist_compatibility(m3u_path: Path, ffprobe_cmd: str, ffmpeg_cmd: str | None = None, compute_lufs: bool = False):
    return deep_scan_playlist(
        m3u_path,
        ffprobe_cmd,
        ffmpeg_cmd,
        fast_lufs=compute_lufs,
        enable_lufs=compute_lufs,
        progress_cb=None,
    )


def convert_playlist_to_safe_set(parent, script_dir: Path, m3u_path: Path, report: dict):
    ffmpeg_cmd = get_ffmpeg_cmd()
    if not ffmpeg_cmd:
        messagebox.showerror("Error", "No encuentro ffmpeg. Instala con: winget install ffmpeg")
        return
    ffprobe_cmd = get_ffprobe_cmd()
    if not ffprobe_cmd:
        messagebox.showerror("Error", "No encuentro ffprobe. Instala con: winget install 'Gyan.FFmpeg'")
        return
    playlist_name = safe_filename(m3u_path.stem)
    exports_dir = script_dir / "exports"
    ensure_dir(exports_dir)
    safe_dir = exports_dir / f"Set_Ready_{playlist_name}"

    if safe_dir.exists():
        if not messagebox.askyesno("Sobrescribir", f"La carpeta {safe_dir} ya existe. Sobrescribir?"):
            return
        try:
            shutil.rmtree(safe_dir)
        except Exception as e:
            messagebox.showerror("Error", f"No se pudo borrar carpeta: {e}")
            return

    ensure_dir(safe_dir)

    lossless_exts = {".flac", ".wav", ".wave", ".aif", ".aiff", ".alac"}
    jobs = []

    ordered_existing = [p for p in report["tracks"] if p.exists()]
    for idx, src in enumerate(ordered_existing, start=1):
        ext = src.suffix.lower()
        is_lossless = ext in lossless_exts
        out_ext = ".aiff" if is_lossless else ".mp3"
        artist, title = get_track_info(src)
        final_name = f"{idx:02d}_{safe_filename(artist)}_{safe_filename(title)}{out_ext}"
        final_path = safe_dir / final_name
        artwork_path = None
        if not has_artwork(src, ffprobe_cmd):
            artwork_path = choose_cover_for_track(src)
        jobs.append((src, final_path, is_lossless, artwork_path))

    if not jobs:
        messagebox.showinfo("Listo", "No hay archivos para convertir.")
        return

    progress_win, label, bar = show_progress_window(parent, len(jobs))
    result_queue = queue.Queue()
    state = {"done": False}

    def build_safe_cmd(src_path: Path, dst_path: Path, is_lossless: bool, artwork_path: Path | None):
        filters = "loudnorm=I=-12:LRA=11:tp=-1.0,aresample=44100:resampler=soxr:precision=28:dither_method=triangular"

        cmd = [
            ffmpeg_cmd,
            "-y",
            "-i", str(src_path)
        ]

        if artwork_path:
            cmd += [
                "-i", str(artwork_path),
                "-map", "0:a",
                "-map", "1:v",
                "-c:v", "mjpeg",
                "-vf", f"scale={ARTWORK_SIZE}:{ARTWORK_SIZE}:force_original_aspect_ratio=decrease,pad={ARTWORK_SIZE}:{ARTWORK_SIZE}:(ow-iw)/2:(oh-ih)/2:black",
                "-q:v", str(ARTWORK_JPEG_QUALITY),
                "-disposition:v", "attached_pic"
            ]
        else:
            cmd += ["-map", "0:a", "-map", "0:v?"]

        cmd += [
            "-map_metadata", "0",
            "-af", filters,
            "-ar", "44100",
            "-ac", "2"
        ]

        if is_lossless:
            cmd += [
                "-c:a", "pcm_s24be",
                "-write_id3v2", "1",
                str(dst_path)
            ]
        else:
            cmd += [
                "-c:a", "libmp3lame",
                "-b:a", "320k",
                "-write_id3v2", "1",
                str(dst_path)
            ]

        return cmd

    def worker():
        results = []
        total = len(jobs)
        for idx, (src, dst, is_lossless, artwork_path) in enumerate(jobs, start=1):
            result_queue.put(("progress", idx, total, src.name))
            cmd = build_safe_cmd(src, dst, is_lossless, artwork_path)
            try:
                proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="ignore")
                if proc.returncode == 0:
                    results.append(("OK", src, dst, ""))
                else:
                    results.append(("FAIL", src, dst, proc.stderr.strip()))
            except Exception as e:
                results.append(("FAIL", src, dst, str(e)))
        result_queue.put(("done", results))

    def poll_queue():
        try:
            while True:
                msg = result_queue.get_nowait()
                if msg[0] == "progress":
                    _, idx, total, name = msg
                    label.config(text=f"Procesando {idx}/{total}: {name}")
                    bar["value"] = idx
                    progress_win.update_idletasks()
                elif msg[0] == "done":
                    _, results = msg
                    state["done"] = True
                    progress_win.destroy()

                    final_files = []
                    for status, _, out_file, _ in results:
                        if status == "OK" and Path(out_file).exists():
                            final_files.append(Path(out_file))

                    out_m3u = safe_dir / f"Set_Ready_{playlist_name}.m3u8"
                    write_m3u(final_files, out_m3u)

                    ok = sum(1 for r in results if r[0] == "OK")
                    fail = sum(1 for r in results if r[0] == "FAIL")
                    summary = (
                        f"OK: {ok} | FAIL: {fail}\n\n"
                        f"Archivos en: {safe_dir}\nM3U: {out_m3u}"
                    )
                    if fail > 0:
                        messagebox.showwarning("Finalizado con errores", summary)
                    else:
                        messagebox.showinfo("Listo", summary)
        except queue.Empty:
            pass

        if not state["done"]:
            parent.after(100, poll_queue)

    threading.Thread(target=worker, daemon=True).start()
    parent.after(100, poll_queue)


def check_m3u8_compatibility_gui(parent, script_dir: Path):
    m3u_path = filedialog.askopenfilename(
        title="Selecciona M3U8",
        filetypes=[("M3U8", "*.m3u8"), ("All files", "*.*")]
    )
    if not m3u_path:
        return

    ffmpeg_cmd = get_ffmpeg_cmd()
    if not ffmpeg_cmd:
        messagebox.showerror("Error", "No encuentro ffmpeg. Instala con: winget install ffmpeg")
        return
    ffprobe_cmd = get_ffprobe_cmd()
    if not ffprobe_cmd:
        messagebox.showerror("Error", "No encuentro ffprobe. Instala con: winget install 'Gyan.FFmpeg'")
        return

    m3u = Path(m3u_path)

    win = tk.Toplevel(parent)
    win.title("Compatibilidad M3U8 (Deep Scan)")
    win.geometry("1020x640")

    header = tk.Label(win, text=f"Playlist: {m3u}", anchor="w")
    header.pack(fill="x", padx=8, pady=6)

    summary = tk.Label(win, text="Analizando...", anchor="w")
    summary.pack(fill="x", padx=8)

    progress_label = tk.Label(win, text="0/0", anchor="w")
    progress_label.pack(fill="x", padx=8, pady=(0, 2))

    progress = ttk.Progressbar(win, mode="determinate")
    progress.pack(fill="x", padx=8, pady=(0, 6))

    fast_lufs_var = tk.BooleanVar(value=True)
    tk.Checkbutton(
        win,
        text="LUFS rapido (primeros 90s)",
        variable=fast_lufs_var
    ).pack(anchor="w", padx=8, pady=(0, 6))

    columns = ("track", "codec", "sr", "bits", "lufs", "status")
    tree = ttk.Treeview(win, columns=columns, show="headings")
    tree.heading("track", text="Track")
    tree.heading("codec", text="Codec")
    tree.heading("sr", text="Sample Rate")
    tree.heading("bits", text="Bits")
    tree.heading("lufs", text="LUFS")
    tree.heading("status", text="Status")

    tree.column("track", width=360, anchor="w")
    tree.column("codec", width=90, anchor="center")
    tree.column("sr", width=110, anchor="center")
    tree.column("bits", width=80, anchor="center")
    tree.column("lufs", width=90, anchor="center")
    tree.column("status", width=240, anchor="w")

    tree.tag_configure("critical", background="#ffb3b3")
    tree.tag_configure("high_sr", background="#ffd9b3")
    tree.tag_configure("missing", background="#ffcccc")

    vsb = ttk.Scrollbar(win, orient="vertical", command=tree.yview)
    tree.configure(yscrollcommand=vsb.set)

    content = tk.Frame(win)
    content.pack(fill="both", expand=True, padx=8, pady=8)

    left = tk.Frame(content)
    left.pack(side="left", fill="both", expand=True)
    right = tk.Frame(content)
    right.pack(side="right", fill="y")

    tree.pack(in_=left, side="left", fill="both", expand=True)
    vsb.pack(in_=left, side="right", fill="y")

    details_label = tk.Label(right, text="Detalles de compatibilidad:", anchor="w")
    details_label.pack(fill="x", pady=(0, 4))

    details_frame = tk.Frame(right)
    details_frame.pack(fill="both", expand=True)

    details_box = tk.Text(details_frame, height=10, wrap="word")
    details_scroll = ttk.Scrollbar(details_frame, orient="vertical", command=details_box.yview)
    details_box.configure(yscrollcommand=details_scroll.set)

    details_box.pack(side="left", fill="both", expand=True)
    details_scroll.pack(side="right", fill="y")

    result_queue = queue.Queue()
    state = {"done": False}

    tree.delete(*tree.get_children())
    details_box.delete("1.0", "end")
    progress_label.config(text="0/0")
    progress["value"] = 0

    def worker():
        def progress_cb(idx, total, name):
            result_queue.put(("progress", idx, total, name))

        report, rows = deep_scan_playlist(
            m3u,
            ffprobe_cmd,
            ffmpeg_cmd,
            fast_lufs=fast_lufs_var.get(),
            enable_lufs=True,
            progress_cb=progress_cb,
        )

        for row in rows:
            result_queue.put(("row", row))

        result_queue.put(("done", report))

    def on_select(event=None):
        details_box.delete("1.0", "end")
        selected = tree.selection()
        if not selected:
            return
        row_id = selected[0]
        report = state.get("report")
        if not report:
            return
        if row_id.startswith("missing::"):
            details_box.insert("end", "FILE MISSING\n")
            return
        p = state.get("row_to_path", {}).get(row_id)
        if not p:
            return
        incompatible = []
        warnings = []
        compatible = []
        compatible_warn = []
        meta = report["per_track"].get(p, {})
        bitrate_kbps = meta.get("bitrate_kbps")
        if bitrate_kbps:
            details_box.insert("end", f"Bitrate: {bitrate_kbps} kbps\n")
        lufs = meta.get("lufs")
        if lufs is not None:
            details_box.insert("end", f"LUFS: {lufs:.1f}\n")
        for device, r in report["device_results"].items():
            issues = r["issues_by_file"].get(p)
            if not issues:
                compatible.append(device)
                continue
            incompatible_msgs = [msg for level, msg in issues if level == "INCOMPATIBLE"]
            warning_msgs = [msg for level, msg in issues if level == "WARNING"]
            if incompatible_msgs:
                incompatible.extend(incompatible_msgs)
            if warning_msgs:
                warnings.extend(warning_msgs)
                if not incompatible_msgs:
                    compatible_warn.append(device)

        if compatible:
            details_box.insert("end", "Compatibles (sin warnings):\n")
            details_box.insert("end", "\n".join(sorted(compatible)) + "\n\n")

        if compatible_warn:
            details_box.insert("end", "Compatibles con warnings:\n")
            details_box.insert("end", "\n".join(sorted(set(compatible_warn))) + "\n\n")

        if not incompatible:
            details_box.insert("end", "Modelos incompatibles: ninguno\n")
        else:
            details_box.insert("end", "Modelos incompatibles:\n")
            details_box.insert("end", "\n".join(sorted(set(incompatible))) + "\n")

        if warnings:
            details_box.insert("end", "\nWarnings:\n")
            details_box.insert("end", "\n".join(sorted(set(warnings))))
        return

    tree.bind("<<TreeviewSelect>>", on_select)

    def poll_queue():
        try:
            while True:
                msg = result_queue.get_nowait()
                if msg[0] == "progress":
                    _, idx, total, name = msg
                    progress["maximum"] = max(total, 1)
                    progress["value"] = idx
                    progress_label.config(text=f"{idx}/{total}: {name}")
                elif msg[0] == "row":
                    _, row = msg
                    try:
                        item_id = tree.insert("", "end", values=row["values"], tags=row["tags"])
                    except Exception:
                        item_id = tree.insert("", "end", values=row["values"], tags=row["tags"])
                    if row.get("path"):
                        state.setdefault("row_to_path", {})[item_id] = row["path"]
                elif msg[0] == "done":
                    _, report = msg
                    state["done"] = True
                    state["report"] = report
                    summary.config(
                        text=(
                            f"Archivos: {len(report['tracks'])} | Encontrados: {len(report['existing'])} | "
                            f"Faltantes: {len(report['missing'])} | Compatible: {len(report['compatible'])} | "
                            f"Incompatible: {len(report['incompatible'])}"
                        )
                    )
                    first = tree.get_children()
                    if first and not tree.selection():
                        tree.selection_set(first[0])
                    on_select()
        except queue.Empty:
            pass

        if not state["done"]:
            parent.after(100, poll_queue)

    threading.Thread(target=worker, daemon=True).start()
    parent.after(100, poll_queue)

    def do_safe_convert():
        report = state.get("report")
        if not report:
            return
        if report["missing"]:
            if not messagebox.askyesno("Faltantes", "Hay archivos faltantes en el M3U8. Continuar igual?"):
                return
        convert_playlist_to_safe_set(parent, script_dir, m3u, report)

    buttons = tk.Frame(win)
    buttons.pack(fill="x", padx=8, pady=(0, 8))
    tk.Button(buttons, text="Generar Set Seguro (-12 LUFS / 44.1k)", command=do_safe_convert).pack(side="left", padx=6)


def _fmt_bpm_value(value):
    try:
        bpm_float = float(str(value).strip())
        if bpm_float <= 0:
            return "--"
        if abs(bpm_float - round(bpm_float)) < 0.01:
            return str(int(round(bpm_float)))
        return f"{bpm_float:.2f}".rstrip("0").rstrip(".")
    except Exception:
        cleaned = str(value or "").strip()
        return cleaned if cleaned else "--"


def _estimate_bpm_key_quick(path: Path, ffmpeg_cmd: str | None, seconds: int = 30):
    if not ffmpeg_cmd:
        return ("--", "--")

    try:
        np = __import__("numpy")
    except Exception:
        return ("--", "--")

    try:
        sample_rate = 22050
        cmd = [
            ffmpeg_cmd,
            "-v", "error",
            "-y",
            "-ss", "0",
            "-t", str(seconds),
            "-i", str(path),
            "-ac", "1",
            "-ar", str(sample_rate),
            "-f", "f32le",
            "-",
        ]
        proc = subprocess.run(cmd, capture_output=True, timeout=45)
        if proc.returncode != 0 or not proc.stdout:
            return ("--", "--")

        audio = np.frombuffer(proc.stdout, dtype=np.float32)
        if audio.size < sample_rate * 4:
            return ("--", "--")

        audio = np.nan_to_num(audio, nan=0.0, posinf=0.0, neginf=0.0)
        max_abs = np.max(np.abs(audio))
        if max_abs > 0:
            audio = audio / max_abs

        frame = 1024
        hop = 512
        if audio.size <= frame:
            return ("--", "--")

        frame_count = 1 + (audio.size - frame) // hop
        energy = np.empty(frame_count, dtype=np.float32)
        for i in range(frame_count):
            start = i * hop
            x = audio[start:start + frame]
            energy[i] = float(np.mean(x * x))

        onset = np.diff(energy)
        onset = np.where(onset > 0, onset, 0)
        bpm_value = "--"
        if onset.size > 32 and np.max(onset) > 0:
            onset = onset - np.mean(onset)
            autocorr = np.correlate(onset, onset, mode="full")
            autocorr = autocorr[autocorr.size // 2:]
            fps = sample_rate / hop
            min_lag = int(max(1, round((60.0 / 180.0) * fps)))
            max_lag = int(max(min_lag + 1, round((60.0 / 70.0) * fps)))
            max_lag = min(max_lag, autocorr.size - 1)
            if max_lag > min_lag:
                segment = autocorr[min_lag:max_lag]
                best_lag = int(np.argmax(segment)) + min_lag
                raw_bpm = 60.0 * fps / max(best_lag, 1)
                while raw_bpm < 80:
                    raw_bpm *= 2.0
                while raw_bpm > 170:
                    raw_bpm *= 0.5
                bpm_value = _fmt_bpm_value(raw_bpm)

        window = np.hanning(frame).astype(np.float32)
        freqs = np.fft.rfftfreq(frame, d=1.0 / sample_rate)
        valid = (freqs >= 40.0) & (freqs <= 5000.0)
        chroma = np.zeros(12, dtype=np.float64)
        for i in range(frame_count):
            start = i * hop
            x = audio[start:start + frame] * window
            spec = np.abs(np.fft.rfft(x))
            mags = spec[valid]
            f = freqs[valid]
            if mags.size == 0:
                continue
            midi = 69 + 12 * np.log2(np.maximum(f, 1e-6) / 440.0)
            pitch_class = np.mod(np.round(midi).astype(int), 12)
            for pc, mag in zip(pitch_class, mags):
                chroma[int(pc)] += float(mag)

        if np.max(chroma) <= 0:
            return (bpm_value, "--")

        major_profile = np.array([6.35, 2.23, 3.48, 2.33, 4.38, 4.09, 2.52, 5.19, 2.39, 3.66, 2.29, 2.88], dtype=np.float64)
        minor_profile = np.array([6.33, 2.68, 3.52, 5.38, 2.60, 3.53, 2.54, 4.75, 3.98, 2.69, 3.34, 3.17], dtype=np.float64)

        best_score = -1.0
        best_root = 0
        best_mode = "major"
        chroma_norm = chroma / max(np.sum(chroma), 1e-9)
        for root in range(12):
            maj = np.roll(major_profile, root)
            minp = np.roll(minor_profile, root)
            maj_score = float(np.dot(chroma_norm, maj / np.sum(maj)))
            min_score = float(np.dot(chroma_norm, minp / np.sum(minp)))
            if maj_score > best_score:
                best_score = maj_score
                best_root = root
                best_mode = "major"
            if min_score > best_score:
                best_score = min_score
                best_root = root
                best_mode = "minor"

        note_names = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]
        camelot_major = {"C": "8B", "G": "9B", "D": "10B", "A": "11B", "E": "12B", "B": "1B", "F#": "2B", "C#": "3B", "G#": "4B", "D#": "5B", "A#": "6B", "F": "7B"}
        camelot_minor = {"A": "8A", "E": "9A", "B": "10A", "F#": "11A", "C#": "12A", "G#": "1A", "D#": "2A", "A#": "3A", "F": "4A", "C": "5A", "G": "6A", "D": "7A"}
        root_name = note_names[best_root]
        key_value = camelot_minor.get(root_name, "--") if best_mode == "minor" else camelot_major.get(root_name, "--")

        return (bpm_value, key_value)
    except Exception:
        return ("--", "--")


def _classify_dj_smart_check(meta: dict, is_broken: bool):
    ext = str(meta.get("ext") or "").lower()
    try:
        sr = int(meta.get("sample_rate") or 0)
    except Exception:
        sr = 0
    try:
        bit_depth = int(meta.get("bit_depth") or 0)
    except Exception:
        bit_depth = 0
    bitrate_raw = meta.get("bitrate_kbps")
    try:
        bitrate_kbps = int(float(str(bitrate_raw).strip())) if bitrate_raw is not None else 0
    except Exception:
        bitrate_kbps = 0
    is_float_32 = bool(meta.get("is_float_32"))

    groups = list(DEVICE_GROUP_ORDER)
    group_specs = {
        key: {
            "formats": set(spec.get("formats") or set()),
            "max_sr": int(spec.get("max_sample_rate") or 0),
            "max_bits": int(spec.get("max_bit_depth") or 0),
        }
        for key, spec in DEVICE_GROUPS.items()
    }

    device_state = {group: "ok" for group in groups}
    device_reasons = {group: [] for group in groups}

    def _build_support_from_state():
        support = {"compatible": [], "warning": [], "incompatible": []}
        for key, state in device_state.items():
            if state == "ok":
                support["compatible"].append(key)
            elif state == "warn":
                support["warning"].append(key)
            else:
                support["incompatible"].append(key)
        return support

    def _add_reason(group: str, reason: str):
        if reason and reason not in device_reasons[group]:
            device_reasons[group].append(reason)

    def _set_warn(group: str, reason: str | None = None):
        if device_state[group] == "bad":
            if reason:
                _add_reason(group, reason)
            return
        if device_state[group] == "ok":
            device_state[group] = "warn"
        if reason:
            _add_reason(group, reason)

    def _set_bad(group: str, reason: str | None = None):
        device_state[group] = "bad"
        if reason:
            _add_reason(group, reason)

    def _score_from_rules():
        explicit_profile = False
        includes_sr_penalty = False
        includes_bit_penalty = False
        includes_mp3_penalty = False

        # WAV/AIFF scoring
        if ext in {".aif", ".aiff", ".wav", ".wave"}:
            if is_float_32:
                score = 3  # 32-bit float WAV/AIFF
                explicit_profile = True
            elif sr == 44100 and bit_depth == 16:
                score = 10  # Only true 10/10
                explicit_profile = True
            elif sr == 44100 and bit_depth == 24:
                score = 9
                explicit_profile = True
                includes_bit_penalty = True
            elif sr == 48000 and bit_depth == 24:
                score = 8
                explicit_profile = True
                includes_bit_penalty = True
            elif sr >= 96000:
                score = 4  # High-res WAV/AIFF ≤4/10
                explicit_profile = True
                includes_sr_penalty = True
            else:
                score = 8
                explicit_profile = True
        elif ext == ".flac":
            # FLAC should never score higher than MP3 320 (8/10)
            if sr == 44100 and bit_depth == 24:
                score = 8
                explicit_profile = True
                includes_bit_penalty = True
            elif sr >= 96000:
                score = 4  # High-res FLAC ≤4/10
                explicit_profile = True
                includes_sr_penalty = True
            else:
                score = 8
                explicit_profile = True
        elif ext == ".mp3":
            if bitrate_kbps >= 320:
                score = 8
                explicit_profile = True
            elif bitrate_kbps >= 256:
                score = 6
                explicit_profile = True
                includes_mp3_penalty = True
            elif bitrate_kbps >= 192:
                score = 5
                explicit_profile = True
                includes_mp3_penalty = True
            elif bitrate_kbps >= 128:
                score = 4
                explicit_profile = True
                includes_mp3_penalty = True
            else:
                score = 4
                explicit_profile = True
        elif ext in {".aac", ".m4a"}:
            if bitrate_kbps >= 256:
                score = 7
                explicit_profile = True
            else:
                score = 6
                explicit_profile = True
        elif ext == ".alac":
            score = 8
            explicit_profile = True
        else:
            score = 5
            explicit_profile = True

        # Penalties for non-explicit profiles (should rarely trigger now)
        if not explicit_profile and sr > 48000 and not includes_sr_penalty:
            score -= 1
        if not explicit_profile and bit_depth > 16 and not includes_bit_penalty:
            score -= 1
        if ext == ".mp3" and bitrate_kbps > 0 and bitrate_kbps < 320 and not includes_mp3_penalty:
            score -= 1

        return max(0, min(int(score), 10))

    def _finalize_result():
        support = _build_support_from_state()
        score = _score_from_rules()

        # Visual status depends ONLY on score
        if score >= 9:
            status = "COMPATIBLE"
            message = "Compatibilidad perfecta."
            advice = "Sin acciones requeridas."
        elif score >= 7:
            status = "LIMITADO"
            message = "Compatible pero limitado por formato o calidad."
            advice = "Revisa compatibilidad en hardware antiguo."
        elif score >= 4:
            status = "ADVERTENCIA"
            message = "Compatible con advertencias."
            advice = "Convierte a formato estándar para máxima compatibilidad."
        else:
            status = "INCOMPATIBLE"
            message = "No compatible universalmente."
            advice = "Convierte a AIFF/WAV 44.1kHz 16-bit o MP3 320k para máxima compatibilidad."

        return {
            "score": int(score),
            "status": status,
            "message": message,
            "advice": advice,
            "details_text": f"{message} Consejo: {advice}",
            "device_support": support,
            "device_reasons": device_reasons,
        }

    if is_broken:
        for group in groups:
            _set_bad(group, "Archivo corrupto/ilegible")
        return {
            "score": 0,
            "status": "ROTO",
            "message": "Archivo roto o ilegible",
            "advice": "Re-exporta o reemplaza el archivo para evitar errores de lectura en cabina.",
            "details_text": "Archivo roto o ilegible. Consejo: Re-exporta o reemplaza el archivo para evitar errores de lectura en cabina.",
            "device_support": _build_support_from_state(),
            "device_reasons": device_reasons,
        }

    if ext in {".wav", ".wave", ".aif", ".aiff"} and is_float_32:
        _set_warn("Pro", "32-bit float no soportado por hardware DJ")
        _set_bad("Club", "32-bit float no soportado por hardware DJ")
        _set_bad("Legacy", "32-bit float no soportado por hardware DJ")
        if sr > group_specs["Software"]["max_sr"]:
            _set_bad("Software", f"Sample rate excede límite Software ({sr} > {group_specs['Software']['max_sr']})")
        elif bit_depth > group_specs["Software"]["max_bits"]:
            _set_bad("Software", f"Bit depth excede límite Software ({bit_depth} > {group_specs['Software']['max_bits']})")
        else:
            _add_reason("Software", "32-bit float permitido en Software")

    elif ext == ".flac":
        _set_bad("Club", "FLAC no soportado en Club")
        _set_bad("Legacy", "FLAC no soportado en Legacy")

        if sr > group_specs["Pro"]["max_sr"]:
            _set_bad("Pro", f"Sample rate excede límite Pro ({sr} > {group_specs['Pro']['max_sr']})")
        elif sr > 48000:
            _set_warn("Pro", f"Sample rate alto en Pro ({sr} Hz)")
        if bit_depth > group_specs["Pro"]["max_bits"]:
            _set_bad("Pro", f"Bit depth excede límite Pro ({bit_depth} > {group_specs['Pro']['max_bits']})")
        if sr > group_specs["Software"]["max_sr"]:
            _set_bad("Software", f"Sample rate excede límite Software ({sr} > {group_specs['Software']['max_sr']})")
        if bit_depth > group_specs["Software"]["max_bits"]:
            _set_bad("Software", f"Bit depth excede límite Software ({bit_depth} > {group_specs['Software']['max_bits']})")

    elif ext in {".wav", ".wave", ".aif", ".aiff"}:
        for group in groups:
            max_sr = group_specs[group]["max_sr"]
            # 96kHz WAV/AIFF logic
            if sr == 96000:
                if group == "Pro":
                    _set_warn(group, "96kHz: carga lenta, no ideal")
                else:
                    _set_bad(group, "96kHz: incompatible")
                continue
            # Format not supported
            if ext not in group_specs[group]["formats"]:
                _set_bad(group, f"Formato {ext} no soportado")
                continue
            # Sample rate above group limit
            if sr > max_sr:
                _set_bad(group, f"Sample rate excede límite {group} ({sr} > {max_sr})")
                continue
            # Bit depth above group limit
            max_bits = group_specs[group]["max_bits"]
            if bit_depth > max_bits:
                _set_warn(group, f"Bit depth {bit_depth} no ideal para {group}")
                continue
            # OK
            _add_reason(group, "OK: track plays natively")
            max_bits = group_specs[group]["max_bits"]
            if sr > max_sr:
                _set_bad(group, f"Sample rate excede límite {group} ({sr} > {max_sr})")
                continue

            if group == "Legacy" and bit_depth > 16:
                _set_warn(group, f"Bit depth alto para Legacy ({bit_depth}-bit)")
                continue

            if bit_depth > max_bits:
                _set_bad(group, f"Bit depth excede límite {group} ({bit_depth} > {max_bits})")

            if group == "Pro" and sr > 48000:
                _set_warn(group, f"Sample rate alto en Pro ({sr} Hz)")

    elif ext == ".mp3":
        if bitrate_kbps > 0 and bitrate_kbps < MIN_MP3_BITRATE_KBPS:
            for group in groups:
                _set_warn(group, f"MP3 bitrate bajo ({bitrate_kbps} kbps < {MIN_MP3_BITRATE_KBPS} kbps)")

    elif ext in {".aac", ".m4a"}:
        for group in groups:
            _add_reason(group, "AAC/M4A compatible")
        for group in groups:
            max_sr = group_specs[group]["max_sr"]
            if sr > max_sr:
                _set_bad(group, f"Sample rate excede límite {group} ({sr} > {max_sr})")

    elif ext == ".alac":
        _set_bad("Club", "ALAC no soportado en Club")
        _set_bad("Legacy", "ALAC no soportado en Legacy")
        if sr > group_specs["Pro"]["max_sr"]:
            _set_bad("Pro", f"Sample rate excede límite Pro ({sr} > {group_specs['Pro']['max_sr']})")
        if bit_depth > group_specs["Pro"]["max_bits"]:
            _set_bad("Pro", f"Bit depth excede límite Pro ({bit_depth} > {group_specs['Pro']['max_bits']})")
        if sr > group_specs["Software"]["max_sr"]:
            _set_bad("Software", f"Sample rate excede límite Software ({sr} > {group_specs['Software']['max_sr']})")
        if bit_depth > group_specs["Software"]["max_bits"]:
            _set_bad("Software", f"Bit depth excede límite Software ({bit_depth} > {group_specs['Software']['max_bits']})")

    else:
        for group in groups:
            _set_bad(group, f"Formato no soportado ({ext or '-'})")

    return _finalize_result()


def _compute_smart_usb_score(meta: dict, is_broken: bool, format_unsupported: bool, is_lossy: bool, lufs_out_of_range: bool, warning_any: bool):
    classification = _classify_dj_smart_check(meta, is_broken)
    score_raw = classification.get("score")
    score = int(score_raw) if score_raw is not None else 6
    status = classification.get("status") or "ADVERTENCIA"
    return score, status


def get_track_metadata(path: Path, duration_seconds_hint: float | None = None, ffprobe_cmd: str | None = None, infer_bpm_key: bool = True):
    artist = "N-A"
    title = path.stem.replace("_", " ").strip() or "N-A"
    bpm = "--"
    musical_key = "--"
    bpm_source = ""
    key_source = ""
    duration_seconds = duration_seconds_hint if duration_seconds_hint and duration_seconds_hint > 0 else None

    def _first_text(tags_obj, candidates):
        for key_name in candidates:
            val = tags_obj.get(key_name)
            if not val:
                continue
            if isinstance(val, (list, tuple)):
                candidate = str(val[0]).strip() if val else ""
            else:
                text_attr = getattr(val, "text", None)
                if isinstance(text_attr, (list, tuple)) and text_attr:
                    candidate = str(text_attr[0]).strip()
                else:
                    candidate = str(val).strip()
            if candidate:
                return candidate
        return ""

    try:
        from mutagen import File as MutagenFile

        mut = MutagenFile(str(path))
        if mut is not None:
            if getattr(mut, "info", None) and getattr(mut.info, "length", None):
                try:
                    info_len = float(mut.info.length)
                    if info_len > 0:
                        duration_seconds = info_len
                except Exception:
                    pass

            tags = getattr(mut, "tags", None) or {}
            if tags:
                artist_tag = _first_text(tags, ["artist", "ARTIST", "TPE1", "\xa9ART"])
                title_tag = _first_text(tags, ["title", "TITLE", "TIT2", "\xa9nam"])
                bpm_tag = _first_text(tags, ["TBPM", "tbpm", "bpm", "BPM"])
                key_tag = _first_text(tags, ["TKEY", "tkey", "initialkey", "INITIALKEY", "key", "KEY"])

                if artist_tag:
                    artist = artist_tag
                if title_tag:
                    title = title_tag
                if bpm_tag:
                    bpm = _fmt_bpm_value(bpm_tag)
                    bpm_source = "tag"
                if key_tag:
                    musical_key = key_tag
                    key_source = "tag"
    except Exception:
        pass

    needs_quick_analysis = infer_bpm_key and (path.suffix.lower() in {".wav", ".wave"} or bpm == "--" or musical_key == "--")
    if needs_quick_analysis:
        quick_bpm, quick_key = _estimate_bpm_key_quick(path, get_ffmpeg_cmd(), seconds=30)
        if bpm == "--" and quick_bpm != "--":
            bpm = quick_bpm
            bpm_source = "inferred"
        if musical_key == "--" and quick_key != "--":
            musical_key = quick_key
            key_source = "inferred"

    stem = path.stem
    if " - " in stem:
        parts = stem.split(" - ", 1)
        if artist in {"", "N-A"}:
            artist = parts[0].strip() or "N-A"
        if title in {"", "N-A", stem.replace("_", " ").strip()}:
            title = parts[1].strip() or "N-A"

    if (not duration_seconds or duration_seconds <= 0) and ffprobe_cmd:
        try:
            duration_probe = probe_duration_seconds(str(path), ffprobe_cmd)
            if duration_probe and duration_probe > 0:
                duration_seconds = duration_probe
        except Exception:
            pass

    if not duration_seconds or duration_seconds <= 0:
        duration_seconds = None

    return {
        "artist": artist or "N-A",
        "title": title or "N-A",
        "bpm": bpm or "--",
        "bpm_source": bpm_source,
        "key": musical_key or "--",
        "key_source": key_source,
        "duration_seconds": round(float(duration_seconds), 3) if duration_seconds else None,
    }


def get_track_info(path: Path):
    meta = get_track_metadata(path)
    return (meta.get("artist") or "N-A", meta.get("title") or "N-A")


def has_artwork(src_path: Path, ffprobe_cmd: str):
    try:
        cmd = [
            ffprobe_cmd,
            "-v", "error",
            "-select_streams", "v",
            "-show_entries", "stream=codec_type:disposition=attached_pic",
            "-of", "csv=p=0",
            str(src_path)
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="ignore", timeout=20)
        output = result.stdout.strip()
        if not output:
            return False
        for line in output.splitlines():
            if "attached_pic=1" in line or "video" in line:
                return True
        return False
    except Exception:
        return False


def list_tracks_without_cover(folder: Path):
    from mutagen import File
    from mutagen.id3 import ID3, APIC
    from mutagen.flac import FLAC
    from mutagen.wave import WAVE
    from mutagen.aiff import AIFF

    missing = []
    for root, _, files in os.walk(folder):
        for name in files:
            if not name.lower().endswith(COVER_AUDIO_EXTS):
                continue
            path = Path(root) / name
            has_cover = False
            try:
                ext = path.suffix.lower()
                if ext == ".mp3":
                    tags = ID3(str(path))
                    has_cover = any(isinstance(frame, APIC) for frame in tags.values())
                elif ext == ".flac":
                    audio = FLAC(str(path))
                    has_cover = bool(audio.pictures)
                elif ext == ".wav":
                    audio = WAVE(str(path))
                    has_cover = any(isinstance(frame, APIC) for frame in audio.tags.values()) if audio.tags else False
                elif ext in (".aif", ".aiff"):
                    audio = AIFF(str(path))
                    has_cover = any(isinstance(frame, APIC) for frame in audio.tags.values()) if audio.tags else False
                else:
                    audio = File(str(path))
                    has_cover = bool(audio and audio.tags)
            except Exception:
                has_cover = False

            if not has_cover:
                missing.append(str(path))

    return missing


def process_cover_images(source_folder: Path, output_folder: Path, size_px: int):
    try:
        from PIL import Image
    except Exception:
        messagebox.showerror("Error", "Pillow no esta instalado. Ejecuta: pip install pillow")
        return

    ensure_dir(output_folder)

    image_paths = []
    for root, _, files in os.walk(source_folder):
        for name in files:
            if name.lower().endswith((".png", ".jpg", ".jpeg")):
                image_paths.append(Path(root) / name)

    def _process_one(src: Path):
        try:
            img = Image.open(src)
            img.thumbnail((size_px, size_px))
            out_name = f"{src.stem}.jpg"
            out_path = output_folder / out_name
            img.convert("RGB").save(out_path, "JPEG", quality=85, optimize=True)
            return True
        except Exception:
            return False

    workers = _max_parallel_workers(len(image_paths), io_heavy=True)
    if workers <= 1 or len(image_paths) <= 1:
        for src in image_paths:
            _process_one(src)
        return

    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = [executor.submit(_process_one, src) for src in image_paths]
        for _ in as_completed(futures):
            pass

def probe_duration_seconds(audio_path: str, ffprobe_cmd: str):
    stream_cmd = [
        ffprobe_cmd,
        "-v", "error",
        "-select_streams", "a:0",
        "-show_entries", "stream=duration",
        "-of", "default=nw=1:nk=1",
        audio_path
    ]
    try:
        stream_result = subprocess.run(stream_cmd, capture_output=True, text=True, check=True, encoding="utf-8", errors="ignore", timeout=20)
        stream_raw = stream_result.stdout.strip()
        if stream_raw:
            stream_duration = float(stream_raw)
            if stream_duration > 0:
                return stream_duration
    except Exception:
        pass

    format_cmd = [
        ffprobe_cmd,
        "-v", "error",
        "-show_entries", "format=duration",
        "-of", "default=nw=1:nk=1",
        audio_path
    ]
    try:
        format_result = subprocess.run(format_cmd, capture_output=True, text=True, check=True, encoding="utf-8", errors="ignore", timeout=20)
    except Exception:
        return None
    raw = format_result.stdout.strip()
    if not raw:
        return None
    try:
        duration = float(raw)
        return duration if duration > 0 else None
    except Exception:
        return None


def _parse_ffmpeg_errors(stderr_text: str):
    lines = [line.strip() for line in stderr_text.splitlines() if line.strip()]
    if not lines:
        return ""
    for line in lines:
        if _is_fatal_decode_error(line):
            return line[:300]
    return lines[0][:300]


def _parse_ffmpeg_progress_seconds(stdout_text: str):
    last_seconds = None
    for line in stdout_text.splitlines():
        if line.startswith("out_time_ms="):
            try:
                ms = int(line.split("=", 1)[1].strip())
                last_seconds = ms / 1000000.0
            except Exception:
                continue
        elif line.startswith("out_time="):
            try:
                raw = line.split("=", 1)[1].strip()
                parts = raw.split(":")
                if len(parts) == 3:
                    h, m, s = parts
                    last_seconds = int(h) * 3600 + int(m) * 60 + float(s)
            except Exception:
                continue
    return last_seconds


def _is_fatal_decode_error(error_text: str):
    value = str(error_text or "").strip().lower()
    if not value:
        return False

    fatal_markers = (
        "invalid sync code",
        "moov atom not found",
        "invalid data found when processing input",
        "header missing",
        "truncated",
        "file ended prematurely",
        "could not find codec parameters",
        "cannot find codec parameters",
        "invalid frame",
        "failed to read frame",
        "corrupt",
    )
    return any(marker in value for marker in fatal_markers)


def decode_integrity_check(audio_path: str, ffmpeg_cmd: str, ffprobe_cmd: str, max_probe_seconds: int | None = None, header_duration_hint: float | None = None, low_priority: bool = False):
    if header_duration_hint and header_duration_hint > 0:
        header_duration = float(header_duration_hint)
    elif max_probe_seconds and max_probe_seconds > 0:
        header_duration = None
    else:
        header_duration = probe_duration_seconds(audio_path, ffprobe_cmd)
    cmd = [
        ffmpeg_cmd,
        "-v", "error",
        "-i", audio_path,
        "-map", "0:a:0",
        "-vn",
        "-sn",
        "-dn",
    ]
    if max_probe_seconds and max_probe_seconds > 0:
        cmd += ["-t", str(int(max_probe_seconds))]
    cmd += [
        "-f", "null",
        "-",
        "-progress", "pipe:1",
        "-nostats"
    ]
    try:
        run_kwargs = {
            "capture_output": True,
            "text": True,
            "encoding": "utf-8",
            "errors": "ignore",
            "timeout": 600,
        }
        if low_priority and os.name == "nt":
            priority_flag = int(getattr(subprocess, "BELOW_NORMAL_PRIORITY_CLASS", 0) or 0)
            if priority_flag:
                run_kwargs["creationflags"] = priority_flag
        result = subprocess.run(cmd, **run_kwargs)
    except Exception as e:
        return False, f"ffmpeg error: {e}", header_duration, None

    err = _parse_ffmpeg_errors(result.stderr)
    if result.returncode != 0:
        if err and not _is_fatal_decode_error(err):
            return True, err, header_duration, None
        return False, err or "ffmpeg fallo sin detalles", header_duration, None
    if err:
        if _is_fatal_decode_error(err):
            return False, err, header_duration, None
        return True, err, header_duration, None

    decoded_duration = _parse_ffmpeg_progress_seconds(result.stdout)
    if (not max_probe_seconds) and header_duration and decoded_duration:
        if decoded_duration < header_duration:
            missing = header_duration - decoded_duration
            if missing > 1.0:
                return False, "CORRUPTO (Truncado)", header_duration, decoded_duration

    return True, "", header_duration, decoded_duration


def quick_decode_integrity_check(audio_path: str, ffmpeg_cmd: str, ffprobe_cmd: str):
    header_duration = probe_duration_seconds(audio_path, ffprobe_cmd)

    head_ok, head_err, _, _ = decode_integrity_check(
        audio_path,
        ffmpeg_cmd,
        ffprobe_cmd,
        max_probe_seconds=8,
        header_duration_hint=header_duration,
    )
    if not head_ok and _is_fatal_decode_error(head_err):
        return False, head_err or "Error fatal de decodificación (inicio)"

    tail_cmd = [
        ffmpeg_cmd,
        "-v", "error",
        "-sseof", "-5",
        "-i", audio_path,
        "-map", "0:a:0",
        "-vn",
        "-sn",
        "-dn",
        "-t", "5",
        "-f", "null",
        "-",
        "-nostats",
    ]

    try:
        tail_result = subprocess.run(tail_cmd, capture_output=True, text=True, encoding="utf-8", errors="ignore", timeout=120)
        tail_err_full = str(tail_result.stderr or "").strip()
        tail_err = _parse_ffmpeg_errors(tail_err_full)
        if tail_result.returncode != 0 and _is_fatal_decode_error(tail_err_full or tail_err):
            return False, tail_err or "Error fatal de decodificación (final)"
        if tail_err and _is_fatal_decode_error(tail_err_full or tail_err):
            return False, tail_err
    except Exception:
        pass

    return True, ""


def _read_exact(f, size: int):
    data = f.read(size)
    if len(data) != size:
        return None
    return data


def _check_wav_header(audio_path: str):
    try:
        file_size = os.path.getsize(audio_path)
        if file_size < 44:
            return "Header truncated", False
        with open(audio_path, "rb") as f:
            header = _read_exact(f, 12)
            if not header:
                return "Header unreadable", False
            riff, riff_size, wave = struct.unpack("<4sI4s", header)
            if riff != b"RIFF" or wave != b"WAVE":
                return "Header unreadable", False
            if riff_size + 8 > file_size:
                return "Truncated file", False

            fmt_found = False
            data_found = False
            is_float_32 = False
            while True:
                chunk = _read_exact(f, 8)
                if not chunk:
                    break
                chunk_id, chunk_size = struct.unpack("<4sI", chunk)
                chunk_id = chunk_id.decode("ascii", errors="ignore")
                if chunk_id == "fmt ":
                    fmt_found = True
                    fmt_data = _read_exact(f, chunk_size)
                    if not fmt_data or len(fmt_data) < 16:
                        return "Header truncated", False
                    w_format = struct.unpack("<H", fmt_data[0:2])[0]
                    bits_per_sample = struct.unpack("<H", fmt_data[14:16])[0]
                    if w_format == 3 and bits_per_sample == 32:
                        is_float_32 = True
                    if w_format == 65534 and len(fmt_data) >= 40:
                        sub_guid = fmt_data[24:40]
                        if sub_guid == b"\x03\x00\x00\x00\x00\x00\x10\x00\x80\x00\x00\xaa\x00\x38\x9b\x71" and bits_per_sample == 32:
                            is_float_32 = True
                elif chunk_id == "data":
                    data_found = True
                    data_end = f.tell() + chunk_size
                    if data_end > file_size:
                        return "Truncated file", False
                    f.seek(chunk_size, os.SEEK_CUR)
                else:
                    f.seek(chunk_size, os.SEEK_CUR)
                if chunk_size % 2 == 1:
                    f.seek(1, os.SEEK_CUR)

            if not fmt_found or not data_found:
                return "Header unreadable", False
            return "", is_float_32
    except Exception:
        return "Header unreadable", False


def _check_aiff_header(audio_path: str):
    try:
        file_size = os.path.getsize(audio_path)
        if file_size < 54:
            return "Header truncated", False
        with open(audio_path, "rb") as f:
            header = _read_exact(f, 12)
            if not header:
                return "Header unreadable", False
            form, form_size, form_type = struct.unpack(">4sI4s", header)
            if form != b"FORM" or form_type not in (b"AIFF", b"AIFC"):
                return "Header unreadable", False
            if form_size + 8 > file_size:
                return "Truncated file", False

            comm_found = False
            ssnd_found = False
            is_float_32 = False
            is_aifc = (form_type == b"AIFC")

            while True:
                chunk = _read_exact(f, 8)
                if not chunk:
                    break
                chunk_id, chunk_size = struct.unpack(">4sI", chunk)
                chunk_id = chunk_id.decode("ascii", errors="ignore")
                if chunk_id == "COMM":
                    comm_found = True
                    comm_data = _read_exact(f, chunk_size)
                    min_size = 22 if is_aifc else 18
                    if not comm_data or len(comm_data) < min_size:
                        return "Header truncated", False
                    if is_aifc:
                        comp = comm_data[18:22].decode("ascii", errors="ignore").lower()
                        if comp in ("fl32", "fl64"):
                            is_float_32 = (comp == "fl32")
                elif chunk_id == "SSND":
                    ssnd_found = True
                    ssnd_end = f.tell() + chunk_size
                    if ssnd_end > file_size:
                        return "Truncated file", False
                    f.seek(chunk_size, os.SEEK_CUR)
                else:
                    f.seek(chunk_size, os.SEEK_CUR)
                if chunk_size % 2 == 1:
                    f.seek(1, os.SEEK_CUR)

            if not comm_found or not ssnd_found:
                return "Header unreadable", False
            return "", is_float_32
    except Exception:
        return "Header unreadable", False


def get_mutagen_bit_depth(audio_path: str):
    try:
        from mutagen import File as MutagenFile
        mut = MutagenFile(audio_path)
        if not mut or not getattr(mut, "info", None):
            return None
        info = mut.info
        if hasattr(info, "bits_per_sample") and info.bits_per_sample:
            return int(info.bits_per_sample)
        if hasattr(info, "sample_width") and info.sample_width:
            return int(info.sample_width) * 8
    except Exception:
        return None
    return None


def check_container_header_and_format(audio_path: str):
    ext = Path(audio_path).suffix.lower()
    if ext in (".wav", ".wave"):
        header_err, is_float_32 = _check_wav_header(audio_path)
    elif ext in (".aif", ".aiff"):
        header_err, is_float_32 = _check_aiff_header(audio_path)
    else:
        header_err, is_float_32 = "", False

    return header_err, is_float_32


def _parse_bitrate_kbps(info: dict):
    raw = info.get("bit_rate")
    if not raw:
        return None
    try:
        return int(int(raw) / 1000)
    except Exception:
        return None


def _duration_hint_from_metadata_info(info: dict | None):
    if not isinstance(info, dict):
        return None
    for key in ("duration", "format.duration", "stream.duration"):
        raw = info.get(key)
        if raw is None:
            continue
        try:
            value = float(str(raw).strip())
            if value > 0:
                return value
        except Exception:
            continue
    return None


def get_audio_metrics(audio_path: str, ffprobe_cmd: str, metadata_info: dict | None = None):
    info = metadata_info if isinstance(metadata_info, dict) else get_audio_metadata(audio_path, ffprobe_cmd)
    sample_rate = None
    bit_depth = None
    bitrate_kbps = None

    try:
        sample_rate = int(info.get("sample_rate")) if info.get("sample_rate") else None
    except Exception:
        sample_rate = None

    bit_depth = get_mutagen_bit_depth(audio_path)
    if not bit_depth:
        bits = info.get("bits_per_raw_sample") or info.get("bits_per_sample")
        try:
            bit_depth = int(bits) if bits else None
        except Exception:
            bit_depth = None

    bitrate_kbps = _parse_bitrate_kbps(info)
    if bitrate_kbps is None:
        try:
            from mutagen import File as MutagenFile
            mut = MutagenFile(audio_path)
            if mut and getattr(mut, "info", None) and getattr(mut.info, "bitrate", None):
                bitrate_kbps = int(mut.info.bitrate / 1000)
        except Exception:
            bitrate_kbps = None

    return sample_rate, bit_depth, bitrate_kbps


def evaluate_hardware_compatibility(audio_path: str, ffprobe_cmd: str):
    issues = []
    tags = []

    ext = Path(audio_path).suffix.lower()
    supported_union = set().union(*DEVICE_FORMATS.values())
    if ext not in supported_union:
        issues.append("INCOMPATIBLE / Formato no soportado")

    if ext in DEVICE_GROUPS.get("Software", {}).get("formats", set()) and ext not in SERATO_PREFERRED_EXTS:
        issues.append("SERATO: preferir WAV/AIFF/FLAC")

    header_err, is_float_32 = check_container_header_and_format(audio_path)
    if header_err:
        issues.append(header_err)

    sample_rate, bit_depth, bitrate_kbps = get_audio_metrics(audio_path, ffprobe_cmd)

    if sample_rate and sample_rate > MAX_SAMPLE_RATE_HZ:
        issues.append(f"INCOMPATIBLE / REDUCIR (Sample Rate > {int(MAX_SAMPLE_RATE_HZ / 1000)}k)")
        tags.append("high_sr")

    if is_float_32:
        issues.append("CRITICO (Causa error en CDJ)")
        tags.append("critical")
    elif bit_depth and bit_depth > MAX_BIT_DEPTH:
        issues.append("INCOMPATIBLE / REDUCIR (Bits > 24)")

    if ext == ".mp3" and bitrate_kbps is not None and bitrate_kbps < MIN_MP3_BITRATE_KBPS:
        issues.append(f"BAJA CALIDAD (MP3 < {MIN_MP3_BITRATE_KBPS} kbps)")

    severity = _derive_severity(issues)

    return {
        "sample_rate": sample_rate,
        "bit_depth": bit_depth,
        "bitrate_kbps": bitrate_kbps,
        "issues": issues,
        "severity": severity,
        "tags": tags,
    }


def _derive_severity(issues: list[str]):
    if not issues:
        return "OK"
    for msg in issues:
        low = msg.lower()
        if msg.startswith("CRITICO") or msg.startswith("INCOMPATIBLE") or msg.startswith("CORRUPTO") or msg.startswith("Header") or msg.startswith("Duracion"):
            return "Error"
        if "ffmpeg" in low or "error" in low:
            return "Error"
    return "Warning"


# =========================
# AUDIO OPS
# =========================

def classify_action(src_path: Path, ffprobe_cmd: str):
    ext = src_path.suffix.lower()
    if ext == ".mp3":
        return "COPY", ext
    if ext in (".wav", ".wave", ".aif", ".aiff"):
        info = get_audio_metadata(str(src_path), ffprobe_cmd)
        if info:
            return "COPY", ext
        return "CONVERT_AIFF", ".aiff"
    if ext in (".flac", ".m4a", ".aac"):
        return "CONVERT_AIFF", ".aiff"
    return "CONVERT_AIFF", ".aiff"


def convert_to_aiff_transparent(src_path: Path, dst_path: Path, ffmpeg_cmd: str, ffprobe_cmd: str, artwork_path: Path | None):
    ensure_dir(dst_path.parent)

    info = get_audio_metadata(str(src_path), ffprobe_cmd)
    channels = info.get("channels")

    cmd = [
        ffmpeg_cmd,
        "-y",
        "-i", str(src_path)
    ]

    if artwork_path:
        cmd += [
            "-i", str(artwork_path),
            "-map", "0",
            "-map", "1",
            "-c:v", "mjpeg",
            "-vf", f"scale={ARTWORK_SIZE}:{ARTWORK_SIZE}:force_original_aspect_ratio=decrease,pad={ARTWORK_SIZE}:{ARTWORK_SIZE}:(ow-iw)/2:(oh-ih)/2:black",
            "-q:v", str(ARTWORK_JPEG_QUALITY),
            "-disposition:v", "attached_pic"
        ]
    else:
        cmd += ["-map", "0"]

    cmd += ["-map_metadata", "0"]

    cmd += ["-ar", str(SAFE_SAMPLE_RATE_HZ)]
    if channels:
        cmd += ["-ac", str(channels)]
    else:
        cmd += ["-ac", "2"]

    cmd += [
        "-c:a", SAFE_PCM_CODEC,
        "-write_id3v2", "1",
        str(dst_path)
    ]

    try:
        subprocess.run(cmd, check=True, capture_output=True, timeout=180)
        return ("OK", str(dst_path), "Convertido a AIFF (transparente)")
    except subprocess.CalledProcessError as e:
        err = e.stderr.decode("utf-8", errors="replace").strip()
        return ("FAIL", str(dst_path), f"ffmpeg error: {err}")


def copy_audio(src_path: Path, dst_path: Path, ffmpeg_cmd: str | None):
    ensure_dir(dst_path.parent)
    ext = src_path.suffix.lower()

    if ext == ".mp3":
        try:
            shutil.copy2(str(src_path), str(dst_path))
            return ("OK", str(dst_path), "Copiado (MP3)")
        except Exception as e:
            return ("FAIL", str(dst_path), f"copy error: {e}")

    if ext in (".wav", ".wave", ".aif", ".aiff"):
        if not ffmpeg_cmd:
            try:
                shutil.copy2(str(src_path), str(dst_path))
                return ("WARN", str(dst_path), "Copiado sin ffmpeg")
            except Exception as e:
                return ("FAIL", str(dst_path), f"copy error: {e}")

        cmd = [
            ffmpeg_cmd,
            "-y",
            "-i", str(src_path),
            "-map", "0",
            "-c", "copy",
            str(dst_path)
        ]
        try:
            subprocess.run(cmd, check=True, capture_output=True, timeout=180)
            return ("OK", str(dst_path), "Copiado (remux)")
        except subprocess.CalledProcessError as e:
            err = e.stderr.decode("utf-8", errors="replace").strip()
            try:
                shutil.copy2(str(src_path), str(dst_path))
                return ("WARN", str(dst_path), f"ffmpeg fallo ({err[:200]}), copy2")
            except Exception as e2:
                return ("FAIL", str(dst_path), f"ffmpeg error: {err} | copy error: {e2}")

    try:
        shutil.copy2(str(src_path), str(dst_path))
        return ("OK", str(dst_path), "Copiado")
    except Exception as e:
        return ("FAIL", str(dst_path), f"copy error: {e}")


def write_m3u(paths, out_m3u: Path):
    ensure_dir(out_m3u.parent)
    with out_m3u.open("w", encoding="utf-8") as f:
        f.write("#EXTM3U\n")
        for p in paths:
            f.write(str(p) + "\n")

# =========================
# USB COPY
# =========================

def list_external_drives():
    try:
        import ctypes
        from ctypes import wintypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        GetLogicalDrives = kernel32.GetLogicalDrives
        GetDriveTypeW = kernel32.GetDriveTypeW
        GetVolumeInformationW = kernel32.GetVolumeInformationW

        drives_bitmask = GetLogicalDrives()
        if drives_bitmask == 0:
            return []

        system_drive = os.environ.get("SystemDrive", "C:").upper()
        results = []

        for i in range(26):
            if not (drives_bitmask & (1 << i)):
                continue
            letter = chr(ord("A") + i)
            root = f"{letter}:\\"
            drive_type = GetDriveTypeW(wintypes.LPCWSTR(root))

            if drive_type not in (2, 3):
                continue
            if root.upper().startswith(system_drive):
                continue

            vol_name_buf = ctypes.create_unicode_buffer(1024)
            fs_name_buf = ctypes.create_unicode_buffer(1024)
            serial = wintypes.DWORD()
            max_comp_len = wintypes.DWORD()
            fs_flags = wintypes.DWORD()

            label = ""
            ok = GetVolumeInformationW(
                wintypes.LPCWSTR(root),
                vol_name_buf, len(vol_name_buf),
                ctypes.byref(serial),
                ctypes.byref(max_comp_len),
                ctypes.byref(fs_flags),
                fs_name_buf, len(fs_name_buf)
            )
            if ok:
                label = vol_name_buf.value

            results.append({
                "root": root,
                "label": label,
                "type": "REMOVIBLE" if drive_type == 2 else "FIJA"
            })

        return results
    except Exception:
        return []


def get_filesystem(root: str):
    try:
        import ctypes
        from ctypes import wintypes

        fs_name_buf = ctypes.create_unicode_buffer(1024)
        vol_name_buf = ctypes.create_unicode_buffer(1024)
        serial = wintypes.DWORD()
        max_comp_len = wintypes.DWORD()
        fs_flags = wintypes.DWORD()

        ok = ctypes.windll.kernel32.GetVolumeInformationW(
            wintypes.LPCWSTR(root),
            vol_name_buf, len(vol_name_buf),
            ctypes.byref(serial),
            ctypes.byref(max_comp_len),
            ctypes.byref(fs_flags),
            fs_name_buf, len(fs_name_buf)
        )
        if not ok:
            return None
        return fs_name_buf.value.upper() if fs_name_buf.value else None
    except Exception:
        return None


def get_folder_size_bytes(folder: Path):
    total = 0
    for root, _, files in os.walk(folder):
        for name in files:
            try:
                total += (Path(root) / name).stat().st_size
            except Exception:
                pass
    return total


def format_bytes(num_bytes: int):
    units = ["B", "KB", "MB", "GB", "TB"]
    size = float(num_bytes)
    for unit in units:
        if size < 1024.0:
            return f"{size:.2f} {unit}"
        size /= 1024.0
    return f"{size:.2f} PB"


def copy_folder_with_progress(src_path: Path, dst_path: Path, label, bar, total_bytes: int, copied_bytes: int):
    for root, _, files in os.walk(src_path):
        for name in files:
            src_file = Path(root) / name
            rel = src_file.relative_to(src_path)
            dst_file = dst_path / rel
            ensure_dir(dst_file.parent)
            try:
                shutil.copy2(str(src_file), str(dst_file))
                copied_bytes += src_file.stat().st_size
            except Exception:
                pass
            if total_bytes > 0:
                bar["value"] = min(copied_bytes, total_bytes)
                label.config(text=f"Copiando: {format_bytes(copied_bytes)} / {format_bytes(total_bytes)}")
                label.update_idletasks()
                bar.update_idletasks()
    return copied_bytes


def clone_serato_usb_gui(parent):
    drives = list_external_drives()
    if len(drives) < 2:
        messagebox.showinfo("Info", "Necesitas al menos 2 unidades externas para clonar.")
        return

    win = tk.Toplevel(parent)
    win.title("Clonar USB Serato + Music")
    win.geometry("560x300")

    tk.Label(win, text="Fuente:").pack(anchor="w", padx=12, pady=6)
    src_list = tk.Listbox(win, height=4)
    for d in drives:
        label = f"{d['root']} {d['label']} ({d['type']})".strip()
        src_list.insert("end", label)
    src_list.pack(fill="x", padx=12)

    tk.Label(win, text="Destino:").pack(anchor="w", padx=12, pady=6)
    dst_list = tk.Listbox(win, height=4)
    for d in drives:
        label = f"{d['root']} {d['label']} ({d['type']})".strip()
        dst_list.insert("end", label)
    dst_list.pack(fill="x", padx=12)

    def do_clone():
        if not src_list.curselection() or not dst_list.curselection():
            messagebox.showerror("Error", "Seleccion invalida.")
            return
        src_idx = src_list.curselection()[0]
        dst_idx = dst_list.curselection()[0]
        if src_idx == dst_idx:
            messagebox.showerror("Error", "Fuente y destino no pueden ser iguales.")
            return

        source_root = Path(drives[src_idx]["root"])
        dest_root = Path(drives[dst_idx]["root"])

        fs = get_filesystem(str(dest_root))
        if fs:
            if fs not in ALLOWED_FILESYSTEMS:
                cont = messagebox.askyesno(
                    "Formato no recomendado",
                    f"Formato destino: {fs}. Recomendado: {RECOMMENDED_FS}. Continuar igual?"
                )
                if not cont:
                    return
        else:
            cont = messagebox.askyesno("Formato desconocido", "No pude detectar el formato. Continuar igual?")
            if not cont:
                return

        folders = []
        for folder in SERATO_FOLDERS_TO_COPY:
            src_folder = source_root / folder
            if src_folder.exists():
                folders.append((src_folder, dest_root / folder))

        if not folders:
            messagebox.showerror("Error", "No se encontraron _Serato_ ni Music en la unidad fuente.")
            return

        total_bytes = 0
        for src_folder, _ in folders:
            total_bytes += get_folder_size_bytes(src_folder)

        progress_win, label, bar = show_progress_window(parent, max(total_bytes, 1))
        bar.configure(mode="determinate", maximum=max(total_bytes, 1))
        copied = 0

        for src_folder, dst_folder in folders:
            copied = copy_folder_with_progress(src_folder, dst_folder, label, bar, total_bytes, copied)

        progress_win.destroy()
        messagebox.showinfo("Listo", f"Clonado completo a: {dest_root}")
        win.destroy()

    tk.Button(win, text="Clonar", command=do_clone).pack(pady=10)

# =========================
# GUI
# =========================

def choose_cover_for_track(track_path: Path):
    title = f"Cover para: {track_path.name}"
    image_file = filedialog.askopenfilename(
        title=title,
        filetypes=[("Image files", "*.jpg *.jpeg *.png"), ("All files", "*.*")]
    )
    if not image_file:
        return None
    return Path(image_file)


def show_progress_window(parent, total):
    win = tk.Toplevel(parent)
    win.title("Procesando")
    win.geometry("420x120")
    win.resizable(False, False)

    label = tk.Label(win, text="Iniciando...", anchor="w")
    label.pack(fill="x", padx=12, pady=8)

    bar = ttk.Progressbar(win, maximum=total, length=380)
    bar.pack(padx=12, pady=8)

    return win, label, bar


def list_tracks_without_cover_gui(parent):
    folder = filedialog.askdirectory(title="Selecciona carpeta de musica")
    if not folder:
        return

    missing = list_tracks_without_cover(Path(folder))
    win = tk.Toplevel(parent)
    win.title("Temas sin cover")
    win.geometry("700x400")

    text = tk.Text(win, wrap="none")
    text.pack(fill="both", expand=True)

    if not missing:
        text.insert("1.0", "No se encontraron temas sin cover.\n")
    else:
        text.insert("1.0", "Temas sin cover:\n")
        for item in missing:
            text.insert("end", f"{item}\n")
        text.insert("end", f"\nTotal: {len(missing)}\n")


def process_images_gui(parent, script_dir: Path):
    folder = filedialog.askdirectory(title="Selecciona carpeta con imagenes")
    if not folder:
        return
    covers_dir = (script_dir / LOCAL_COVERS_DIR).resolve()
    process_cover_images(Path(folder), covers_dir, ARTWORK_SIZE)
    messagebox.showinfo("Listo", f"Imagenes procesadas en: {covers_dir}")


def scan_folder_integrity_gui(parent):
    folder = filedialog.askdirectory(title="Selecciona carpeta de musica")
    if not folder:
        return

    ffmpeg_cmd = get_ffmpeg_cmd()
    if not ffmpeg_cmd:
        messagebox.showerror("Error", "No encuentro ffmpeg. Instala con: winget install ffmpeg")
        return
    ffprobe_cmd = get_ffprobe_cmd()
    if not ffprobe_cmd:
        messagebox.showerror("Error", "No encuentro ffprobe. Instala con: winget install 'Gyan.FFmpeg'")
        return

    files = []
    for root, _, names in os.walk(folder):
        for name in names:
            p = Path(root) / name
            if p.suffix.lower() in INPUT_EXTS:
                files.append(p)

    if not files:
        messagebox.showinfo("Info", "No encontre archivos de audio en esa carpeta.")
        return

    progress_win, label, bar = show_progress_window(parent, len(files))
    result_queue = queue.Queue()
    state = {"done": False}

    def worker():
        results_local = []

        for idx, p in enumerate(files, start=1):
            result_queue.put(("progress", idx, p.name))

            metrics = evaluate_hardware_compatibility(str(p), ffprobe_cmd)
            ok, err, header_dur, _ = decode_integrity_check(str(p), ffmpeg_cmd, ffprobe_cmd)

            if header_dur is None or header_dur <= 0:
                metrics["issues"].append("Duracion invalida")

            if not ok:
                metrics["issues"].append(err)

            metrics["severity"] = _derive_severity(metrics["issues"])

            results_local.append((p, metrics))

        result_queue.put(("done", results_local))

    def finalize(results):
        progress_win.destroy()

        win = tk.Toplevel(parent)
        win.title("Revision de integridad")
        win.geometry("980x520")

        header = tk.Label(win, text=f"Archivos revisados: {len(results)}", anchor="w")
        header.pack(fill="x", padx=10, pady=6)

        columns = ("file", "status", "sr", "bits", "bitrate", "details")
        tree = ttk.Treeview(win, columns=columns, show="headings")
        tree.heading("file", text="Archivo")
        tree.heading("status", text="Estado")
        tree.heading("sr", text="Sample Rate")
        tree.heading("bits", text="Bits")
        tree.heading("bitrate", text="Bitrate")
        tree.heading("details", text="Detalle")

        tree.column("file", width=380, anchor="w")
        tree.column("status", width=90, anchor="center")
        tree.column("sr", width=100, anchor="center")
        tree.column("bits", width=70, anchor="center")
        tree.column("bitrate", width=90, anchor="center")
        tree.column("details", width=280, anchor="w")

        tree.tag_configure("critical", background="#ffb3b3")
        tree.tag_configure("high_sr", background="#ffd9b3")

        vsb = ttk.Scrollbar(win, orient="vertical", command=tree.yview)
        tree.configure(yscrollcommand=vsb.set)

        tree.pack(side="left", fill="both", expand=True, padx=8, pady=8)
        vsb.pack(side="right", fill="y")

        counts = {"OK": 0, "Warning": 0, "Error": 0}
        for p, metrics in results:
            sr = metrics["sample_rate"]
            bits = metrics["bit_depth"]
            br = metrics["bitrate_kbps"]
            issues_text = "; ".join(metrics["issues"]) if metrics["issues"] else ""
            status = metrics["severity"]
            counts[status] += 1

            tag = None
            if "critical" in metrics["tags"]:
                tag = "critical"
            elif "high_sr" in metrics["tags"]:
                tag = "high_sr"

            tree.insert(
                "",
                "end",
                values=(str(p), status, sr or "-", bits or "-", br or "-", issues_text),
                tags=(tag,) if tag else None
            )

        footer = tk.Label(
            win,
            text=f"OK: {counts['OK']} | Warning: {counts['Warning']} | Error: {counts['Error']}",
            anchor="w"
        )
        footer.pack(fill="x", padx=10, pady=6)

    def poll_queue():
        try:
            while True:
                msg = result_queue.get_nowait()
                if msg[0] == "progress":
                    _, idx, name = msg
                    label.config(text=f"Revisando {idx}/{len(files)}: {name}")
                    bar["value"] = idx
                elif msg[0] == "done":
                    _, results_local = msg
                    state["done"] = True
                    finalize(results_local)
        except queue.Empty:
            pass

        if not state["done"]:
            parent.after(100, poll_queue)

    threading.Thread(target=worker, daemon=True).start()
    parent.after(100, poll_queue)


def copy_set_to_drive_gui(parent, set_folder: Path, set_name: str):
    drives = list_external_drives()
    if not drives:
        messagebox.showinfo("Info", "No encontre unidades externas disponibles.")
        return

    win = tk.Toplevel(parent)
    win.title("Elegir unidad")
    win.geometry("420x240")

    listbox = tk.Listbox(win)
    for d in drives:
        label = f"{d['root']} {d['label']} ({d['type']})".strip()
        listbox.insert("end", label)
    listbox.pack(fill="both", expand=True, padx=12, pady=8)

    def do_copy():
        sel = listbox.curselection()
        if not sel:
            messagebox.showerror("Error", "Seleccion invalida.")
            return
        idx = sel[0]
        target_root = Path(drives[idx]["root"])
        target_path = target_root / set_name

        try:
            required_bytes = get_folder_size_bytes(set_folder)
            free_bytes = shutil.disk_usage(str(target_root)).free
            if required_bytes > free_bytes:
                messagebox.showerror(
                    "Error",
                    f"No hay suficiente espacio. Necesario: {format_bytes(required_bytes)} | Libre: {format_bytes(free_bytes)}"
                )
                return
        except Exception:
            pass

        if target_path.exists():
            if not messagebox.askyesno("Sobrescribir", f"La carpeta {set_name} ya existe. Sobrescribir?"):
                return
            try:
                shutil.rmtree(target_path)
            except Exception as e:
                messagebox.showerror("Error", f"No se pudo borrar carpeta: {e}")
                return

        files_to_copy = []
        for root, _, files in os.walk(set_folder):
            for name in files:
                src_file = Path(root) / name
                rel_path = src_file.relative_to(set_folder)
                dst_file = target_path / rel_path
                files_to_copy.append((src_file, dst_file))

        if not files_to_copy:
            messagebox.showinfo("Info", "La carpeta del set esta vacia.")
            return

        progress_win, label, bar = show_progress_window(parent, len(files_to_copy))
        for idx2, (src_file, dst_file) in enumerate(files_to_copy, start=1):
            ensure_dir(dst_file.parent)
            try:
                shutil.copy2(str(src_file), str(dst_file))
            except Exception:
                pass
            label.config(text=f"Copiando {idx2}/{len(files_to_copy)}: {src_file.name}")
            bar["value"] = idx2
            progress_win.update_idletasks()

        progress_win.destroy()
        messagebox.showinfo("Listo", f"Copiado a: {target_path}")
        win.destroy()

    btn = tk.Button(win, text="Copiar", command=do_copy)
    btn.pack(pady=8)


def run_single_conversion(parent):
    audio_file = filedialog.askopenfilename(
        title="Selecciona archivo de audio",
        filetypes=[("Audio files", "*.mp3 *.wav *.flac *.m4a *.ogg"), ("All files", "*.*")]
    )
    if not audio_file:
        return

    lossy_exts = {".mp3", ".aac", ".m4a", ".ogg"}
    if Path(audio_file).suffix.lower() in lossy_exts:
        if not messagebox.askyesno(
            "Aviso",
            "Estas convirtiendo un archivo con perdida a AIFF. El archivo sera mas grande sin mejorar calidad. Continuar?"
        ):
            return

    image_file = filedialog.askopenfilename(
        title="Selecciona imagen para incrustar (opcional)",
        filetypes=[("Image files", "*.jpg *.jpeg *.png"), ("All files", "*.*")]
    )

    ffmpeg_cmd = get_ffmpeg_cmd()
    if not ffmpeg_cmd:
        messagebox.showerror("Error", "No encuentro ffmpeg. Instala con: winget install ffmpeg")
        return
    ffprobe_cmd = get_ffprobe_cmd()
    if not ffprobe_cmd:
        messagebox.showerror("Error", "No encuentro ffprobe. Instala con: winget install 'Gyan.FFmpeg'")
        return

    output_file = os.path.splitext(audio_file)[0] + "_converted.aiff"
    status, out_file, msg = convert_to_aiff_transparent(
        Path(audio_file),
        Path(output_file),
        ffmpeg_cmd,
        ffprobe_cmd,
        Path(image_file) if image_file else None
    )

    if status == "OK":
        messagebox.showinfo("Exito", f"Archivo convertido:\n{out_file}")
    else:
        messagebox.showerror("Error", msg)


def run_set_conversion(parent, script_dir: Path, m3u_path: str, set_name: str, ask_cover: bool, copy_usb: bool):
    if not m3u_path or not Path(m3u_path).exists():
        messagebox.showerror("Error", "M3U8 invalido o no existe.")
        return
    if not set_name:
        messagebox.showerror("Error", "Nombre de set invalido.")
        return

    set_name_safe = safe_filename(set_name)
    exports_dir = script_dir / "exports"
    ensure_dir(exports_dir)
    usb_out = exports_dir / set_name_safe
    ensure_dir(usb_out)

    ffmpeg_cmd = get_ffmpeg_cmd()
    if not ffmpeg_cmd:
        messagebox.showerror("Error", "No encuentro ffmpeg. Instala con: winget install ffmpeg")
        return
    ffprobe_cmd = get_ffprobe_cmd()
    if not ffprobe_cmd:
        messagebox.showerror("Error", "No encuentro ffprobe. Instala con: winget install 'Gyan.FFmpeg'")
        return

    tracks_raw = parse_m3u(Path(m3u_path))
    if not tracks_raw:
        messagebox.showerror("Error", "El M3U no tiene tracks.")
        return

    m3u_dir = Path(m3u_path).parent
    src_paths = [normalize_track_path(x, m3u_dir) for x in tracks_raw]

    valid_src = [p for p in src_paths if p.exists() and p.suffix.lower() in INPUT_EXTS]
    if not valid_src:
        messagebox.showerror("Error", "No hay archivos validos en el M3U.")
        return

    lossy_exts = {".mp3", ".aac", ".m4a", ".ogg"}
    lossy_to_convert = []
    for src in valid_src:
        if src.suffix.lower() in lossy_exts:
            action, _ = classify_action(src, ffprobe_cmd)
            if action != "COPY":
                lossy_to_convert.append(src)

    if lossy_to_convert:
        cont = messagebox.askyesno(
            "Aviso",
            f"Se van a convertir {len(lossy_to_convert)} archivos con perdida a AIFF.\n"
            "Los archivos seran mas grandes sin mejorar calidad.\nContinuar?"
        )
        if not cont:
            return

    jobs = []
    for idx, src in enumerate(valid_src, start=1):
        artist, title = get_track_info(src)
        action, out_ext = classify_action(src, ffprobe_cmd)
        final_name = f"{idx:02d}_{safe_filename(artist)}_{safe_filename(title)}{out_ext}"
        final_path = usb_out / final_name

        artwork_path = None
        if action != "COPY" and ask_cover:
            if not has_artwork(src, ffprobe_cmd):
                artwork_path = choose_cover_for_track(src)

        jobs.append((src, final_path, action, artwork_path))

    logs_dir = script_dir / "logs"
    ensure_dir(logs_dir)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_path = logs_dir / f"conversion_{set_name_safe}_{timestamp}.log"
    out_m3u = usb_out / f"{set_name_safe}.m3u"

    progress_win, label, bar = show_progress_window(parent, len(jobs))
    results = []

    with log_path.open("w", encoding="utf-8") as log:
        log.write("=== CONVERSION LOG ===\n")
        log.write(f"Timestamp: {timestamp}\n")
        log.write(f"M3U: {m3u_path}\n")
        log.write(f"Set: {set_name_safe}\n")
        log.write("Workflow: copy-first, transparent conversion\n\n")

        for idx, (src, dst, action, artwork_path) in enumerate(jobs, start=1):
            label.config(text=f"Procesando {idx}/{len(jobs)}: {src.name}")
            bar["value"] = idx
            progress_win.update_idletasks()

            if dst.exists():
                results.append(("SKIP", src, str(dst), "Ya existe"))
                log.write(f"[SKIP] {src.name} -> {dst.name} | Ya existe\n")
                continue

            if action == "COPY":
                status, out_file, msg = copy_audio(src, dst, ffmpeg_cmd)
            else:
                status, out_file, msg = convert_to_aiff_transparent(src, dst, ffmpeg_cmd, ffprobe_cmd, artwork_path)

            results.append((status, src, out_file, msg))
            level = status if status in ("OK", "WARN", "SKIP") else "ERROR"
            log.write(f"[{level}] {src.name} -> {Path(out_file).name} | {msg}\n")

    progress_win.destroy()

    final_files = []
    for status, _, out_file, _ in results:
        if status in ("OK", "WARN", "SKIP") and Path(out_file).exists():
            final_files.append(Path(out_file))

    write_m3u(final_files, out_m3u)

    ok = sum(1 for r in results if r[0] == "OK")
    warn = sum(1 for r in results if r[0] == "WARN")
    fail = sum(1 for r in results if r[0] == "FAIL")
    skip = sum(1 for r in results if r[0] == "SKIP")

    summary = f"OK: {ok} | WARN: {warn} | SKIP: {skip} | FAIL: {fail}\n\nArchivos en: {usb_out}\nM3U: {out_m3u}"
    if fail > 0:
        messagebox.showwarning("Finalizado con errores", summary)
    else:
        messagebox.showinfo("Listo", summary)

    if copy_usb:
        copy_set_to_drive_gui(parent, usb_out, set_name_safe)


def build_gui():
    root = tk.Tk()
    root.title("Converter GUI")
    root.geometry("760x620")
    root.minsize(720, 520)

    script_dir = Path(__file__).parent.resolve()

    container = tk.Frame(root)
    container.pack(fill="both", expand=True)

    canvas = tk.Canvas(container, highlightthickness=0)
    scrollbar = tk.Scrollbar(container, orient="vertical", command=canvas.yview)
    content = tk.Frame(canvas)

    content.bind(
        "<Configure>",
        lambda e: canvas.configure(scrollregion=canvas.bbox("all"))
    )

    canvas.create_window((0, 0), window=content, anchor="nw")
    canvas.configure(yscrollcommand=scrollbar.set)

    canvas.pack(side="left", fill="both", expand=True)
    scrollbar.pack(side="right", fill="y")

    def _on_mousewheel(event):
        if event.delta:
            canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")

    canvas.bind_all("<MouseWheel>", _on_mousewheel)

    title = tk.Label(content, text="Converter GUI", font=("Segoe UI", 14, "bold"))
    title.pack(pady=10)

    # Modo 1
    frame_single = tk.LabelFrame(content, text="Convertidor individual (transparente)")
    frame_single.pack(fill="x", padx=12, pady=8)

    btn_single = tk.Button(frame_single, text="Convertir archivo", command=lambda: run_single_conversion(root))
    btn_single.pack(padx=12, pady=10)

    # Modo 2
    frame_set = tk.LabelFrame(content, text="Preparar set desde M3U8")
    frame_set.pack(fill="both", expand=False, padx=12, pady=8)

    tk.Label(frame_set, text="M3U8:").grid(row=0, column=0, sticky="w", padx=8, pady=6)
    m3u_var = tk.StringVar()
    m3u_entry = tk.Entry(frame_set, textvariable=m3u_var, width=60)
    m3u_entry.grid(row=0, column=1, padx=8, pady=6)
    tk.Button(frame_set, text="Buscar", command=lambda: m3u_var.set(filedialog.askopenfilename(title="Selecciona M3U8", filetypes=[("M3U8", "*.m3u8"), ("All files", "*.*")]))).grid(row=0, column=2, padx=8, pady=6)

    tk.Label(frame_set, text="Nombre del set:").grid(row=1, column=0, sticky="w", padx=8, pady=6)
    set_var = tk.StringVar()
    tk.Entry(frame_set, textvariable=set_var, width=40).grid(row=1, column=1, padx=8, pady=6, sticky="w")

    ask_cover_var = tk.BooleanVar(value=True)
    copy_usb_var = tk.BooleanVar(value=False)

    tk.Checkbutton(frame_set, text="Preguntar cover por track si no tiene", variable=ask_cover_var).grid(row=2, column=1, sticky="w", padx=8, pady=4)
    tk.Checkbutton(frame_set, text="Copiar set a USB al final", variable=copy_usb_var).grid(row=3, column=1, sticky="w", padx=8, pady=4)

    btn_run = tk.Button(
        frame_set,
        text="Ejecutar",
        command=lambda: run_set_conversion(root, script_dir, m3u_var.get(), set_var.get(), ask_cover_var.get(), copy_usb_var.get())
    )
    btn_run.grid(row=4, column=1, sticky="w", padx=8, pady=10)

    frame_playlist = tk.LabelFrame(content, text="Compatibilidad M3U8")
    frame_playlist.pack(fill="x", padx=12, pady=8)

    tk.Button(
        frame_playlist,
        text="Revisar compatibilidad y convertir",
        command=lambda: check_m3u8_compatibility_gui(root, script_dir)
    ).pack(side="left", padx=8, pady=8)

    # Covers tools
    frame_tools = tk.LabelFrame(content, text="Herramientas de covers")
    frame_tools.pack(fill="x", padx=12, pady=8)

    tk.Button(frame_tools, text="Listar temas sin cover", command=lambda: list_tracks_without_cover_gui(root)).pack(side="left", padx=8, pady=8)
    tk.Button(frame_tools, text="Procesar imagenes a covers/", command=lambda: process_images_gui(root, script_dir)).pack(side="left", padx=8, pady=8)

    frame_ff = tk.LabelFrame(content, text="FFmpeg")
    frame_ff.pack(fill="x", padx=12, pady=8)

    tk.Button(frame_ff, text="Seleccionar ffmpeg.exe", command=lambda: set_ffmpeg_paths_gui(root)).pack(side="left", padx=8, pady=8)

    frame_check = tk.LabelFrame(content, text="Chequeo de integridad")
    frame_check.pack(fill="x", padx=12, pady=8)

    tk.Button(frame_check, text="Revisar carpeta de musica", command=lambda: scan_folder_integrity_gui(root)).pack(side="left", padx=8, pady=8)

    frame_usb = tk.LabelFrame(content, text="USB Serato")
    frame_usb.pack(fill="x", padx=12, pady=8)

    tk.Button(frame_usb, text="Clonar _Serato_ + Music", command=lambda: clone_serato_usb_gui(root)).pack(side="left", padx=8, pady=8)

    # Exports info
    exports_label = tk.Label(content, text=f"Exports: {script_dir / 'exports'}")
    exports_label.pack(pady=6)

    root.mainloop()


HOST = "127.0.0.1"
PORT = 8765


def _send_event(handler: BaseHTTPRequestHandler, event: str, data: dict):
    payload = json.dumps(data, ensure_ascii=True)
    handler.wfile.write(f"event: {event}\n".encode("utf-8"))
    handler.wfile.write(f"data: {payload}\n\n".encode("utf-8"))
    handler.wfile.flush()


def _send_json(handler: BaseHTTPRequestHandler, status: int, data: dict):
    payload = json.dumps(data, ensure_ascii=True).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json")
    handler.send_header("Access-Control-Allow-Origin", "*")
    handler.end_headers()
    handler.wfile.write(payload)


def _resolve_existing_initial_dir(initial_path: str = "") -> str:
    raw = str(initial_path or "").strip()
    if not raw:
        return ""

    candidate = Path(raw)
    try:
        if candidate.exists() and candidate.is_dir():
            return str(candidate)
        if candidate.exists() and candidate.is_file():
            return str(candidate.parent)
    except Exception:
        pass

    try:
        parent = candidate.parent
        if str(parent) not in {"", "."} and parent.exists() and parent.is_dir():
            return str(parent)
    except Exception:
        pass

    return ""


def _pick_folder_native(initial_dir: str = "") -> str | None:
    root = None
    try:
        resolved_initial_dir = _resolve_existing_initial_dir(initial_dir)
        root = tk.Tk()
        root.withdraw()
        try:
            root.attributes("-topmost", True)
        except Exception:
            pass
        selected = filedialog.askdirectory(
            title="Selecciona carpeta",
            initialdir=resolved_initial_dir or None,
            mustexist=True,
            parent=root,
        )
    except Exception:
        selected = ""
    finally:
        if root is not None:
            try:
                root.destroy()
            except Exception:
                pass

    value = str(selected or "").strip()
    return value or None


def _pick_file_native(initial_path: str = "") -> str | None:
    root = None
    try:
        root = tk.Tk()
        root.withdraw()
        try:
            root.attributes("-topmost", True)
        except Exception:
            pass

        initial_raw = str(initial_path or "").strip()
        initial_dir = _resolve_existing_initial_dir(initial_raw)
        initial_file = ""
        if initial_raw:
            candidate = Path(initial_raw)
            if candidate.exists() and candidate.is_file():
                initial_file = candidate.name
            else:
                initial_file = candidate.name if candidate.name else ""

        selected = filedialog.askopenfilename(
            title="Selecciona archivo",
            initialdir=initial_dir or None,
            initialfile=initial_file or None,
            filetypes=[
                ("Audio y playlists", "*.m3u *.m3u8 *.wav *.wave *.aif *.aiff *.flac *.mp3 *.m4a *.aac *.alac *.mp4"),
                ("Todos los archivos", "*.*"),
            ],
            parent=root,
        )
    except Exception:
        selected = ""
    finally:
        if root is not None:
            try:
                root.destroy()
            except Exception:
                pass

    value = str(selected or "").strip()
    return value or None


def _parse_bool(value: str | None) -> bool:
    if not value:
        return False
    return value.strip().lower() in {"1", "true", "yes", "y"}


def _find_artwork(artwork_dir: Path, track_path: Path):
    for ext in (".jpg", ".jpeg", ".png"):
        candidate = artwork_dir / f"{track_path.stem}{ext}"
        if candidate.exists():
            return candidate
    return None


def _write_cover_overrides(base_name: str, cover_overrides: dict, ffmpeg_cmd: str | None):
    if not isinstance(cover_overrides, dict) or not cover_overrides:
        return None

    target_dir = Path(__file__).parent / "exports" / "_temp" / "covers" / safe_filename(base_name)
    ensure_dir(target_dir)

    for track_path, data_url in cover_overrides.items():
        if not isinstance(track_path, str) or not isinstance(data_url, str):
            continue
        if not data_url.startswith("data:image/") or "," not in data_url:
            continue

        try:
            header, encoded = data_url.split(",", 1)
            if ";base64" not in header:
                continue
            image_bytes = base64.b64decode(encoded)
        except Exception:
            continue

        stem = Path(track_path).stem
        ext = ".jpg"
        if "image/png" in header.lower():
            ext = ".png"
        elif "image/webp" in header.lower():
            ext = ".webp"

        raw_path = target_dir / f"{stem}_upload{ext}"
        out_path = target_dir / f"{stem}.jpg"

        try:
            raw_path.write_bytes(image_bytes)
            if ffmpeg_cmd:
                cmd = [
                    ffmpeg_cmd,
                    "-v", "error",
                    "-y",
                    "-i", str(raw_path),
                    "-vf", f"scale={ARTWORK_SIZE}:{ARTWORK_SIZE}:force_original_aspect_ratio=decrease,pad={ARTWORK_SIZE}:{ARTWORK_SIZE}:(ow-iw)/2:(oh-ih)/2:black",
                    "-q:v", str(ARTWORK_JPEG_QUALITY),
                    str(out_path),
                ]
                proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="ignore")
                if proc.returncode != 0:
                    out_path.write_bytes(image_bytes)
            else:
                out_path.write_bytes(image_bytes)
        except Exception:
            continue
        finally:
            try:
                raw_path.unlink(missing_ok=True)
            except Exception:
                pass

    return target_dir


def _cleanup_stale_temp_artifacts(base_dir: Path, retention_hours: int = TEMP_ARTIFACT_RETENTION_HOURS):
    try:
        root = Path(base_dir)
        if not root.exists():
            return
        now_ts = time.time()
        max_age_sec = max(1, int(retention_hours)) * 3600

        stale_files = []
        stale_dirs = []

        for path in root.rglob("*"):
            try:
                age_sec = now_ts - path.stat().st_mtime
            except Exception:
                continue
            if age_sec <= max_age_sec:
                continue
            if path.is_file():
                stale_files.append(path)
            elif path.is_dir():
                stale_dirs.append(path)

        for file_path in stale_files:
            try:
                file_path.unlink(missing_ok=True)
            except Exception:
                pass

        for dir_path in sorted(stale_dirs, key=lambda item: len(item.parts), reverse=True):
            try:
                dir_path.rmdir()
            except Exception:
                pass
    except Exception:
        pass


def _prepare_cover_jpeg_bytes(data_url: str, ffmpeg_cmd: str | None):
    if not isinstance(data_url, str) or not data_url.startswith("data:image/") or "," not in data_url:
        return None
    try:
        header, encoded = data_url.split(",", 1)
        if ";base64" not in header:
            return None
        mime_hint = header.split(";", 1)[0].replace("data:", "").strip().lower()
        image_bytes = base64.b64decode(encoded)
    except Exception:
        return None

    if not ffmpeg_cmd:
        return image_bytes

    try:
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp = Path(tmp_dir)
            if mime_hint == "image/png":
                raw_ext = ".png"
            elif mime_hint == "image/webp":
                raw_ext = ".webp"
            else:
                raw_ext = ".jpg"

            raw_path = tmp / f"cover_upload{raw_ext}"
            out_path = tmp / "cover_512.jpg"
            raw_path.write_bytes(image_bytes)

            cmd = [
                ffmpeg_cmd,
                "-v", "error",
                "-y",
                "-i", str(raw_path),
                "-vf", f"scale={ARTWORK_SIZE}:{ARTWORK_SIZE}:force_original_aspect_ratio=decrease,pad={ARTWORK_SIZE}:{ARTWORK_SIZE}:(ow-iw)/2:(oh-ih)/2:black",
                "-q:v", str(ARTWORK_JPEG_QUALITY),
                str(out_path),
            ]
            proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="ignore")
            if proc.returncode == 0 and out_path.exists():
                return out_path.read_bytes()
    except Exception:
        pass

    return image_bytes


def _guess_image_mime(image_bytes: bytes | None):
    data = bytes(image_bytes or b"")
    if not data:
        return "image/jpeg"
    if data.startswith(b"\x89PNG"):
        return "image/png"
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if data.startswith(b"RIFF") and b"WEBP" in data[:16]:
        return "image/webp"
    return "image/jpeg"


def _probe_primary_audio_signature(audio_path: Path, ffprobe_cmd: str | None):
    if not ffprobe_cmd:
        return None
    try:
        cmd = [
            ffprobe_cmd,
            "-v", "error",
            "-select_streams", "a:0",
            "-show_entries", "stream=codec_name,sample_rate,channels,bits_per_sample,bits_per_raw_sample,sample_fmt,duration",
            "-show_entries", "format=duration,format_name",
            "-of", "json",
            str(audio_path),
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="ignore", timeout=20)
        if result.returncode != 0:
            return None
        payload = json.loads(result.stdout or "{}")
        streams = payload.get("streams") or []
        stream = streams[0] if streams else {}
        fmt = payload.get("format") or {}
        return {
            "codec": str(stream.get("codec_name") or "").lower(),
            "sample_rate": int(stream.get("sample_rate") or 0),
            "channels": int(stream.get("channels") or 0),
            "sample_fmt": str(stream.get("sample_fmt") or "").lower(),
            "bits_per_sample": int(stream.get("bits_per_sample") or 0),
            "bits_per_raw_sample": int(stream.get("bits_per_raw_sample") or 0),
            "stream_duration": float(stream.get("duration") or 0.0),
            "format_duration": float(fmt.get("duration") or 0.0),
            "format_name": str(fmt.get("format_name") or "").lower(),
        }
    except Exception:
        return None


def _audio_signature_compatible(original_sig: dict | None, updated_sig: dict | None):
    if not original_sig or not updated_sig:
        return True

    strict_fields = ("codec", "sample_rate", "channels")
    for field in strict_fields:
        base = original_sig.get(field)
        new = updated_sig.get(field)
        if base and new and base != new:
            return False

    original_duration = float(original_sig.get("format_duration") or original_sig.get("stream_duration") or 0.0)
    updated_duration = float(updated_sig.get("format_duration") or updated_sig.get("stream_duration") or 0.0)
    if original_duration > 0 and updated_duration > 0:
        tolerance = max(0.15, original_duration * 0.01)
        if abs(original_duration - updated_duration) > tolerance:
            return False

    return True


def _validate_cover_aware_aiff_integrity(audio_path: Path, ffprobe_cmd: str | None):
    header_err, _ = check_container_header_and_format(str(audio_path))
    if header_err:
        return False, header_err

    sig = _probe_primary_audio_signature(audio_path, ffprobe_cmd)
    if not sig:
        return False, "ffprobe no pudo leer stream de audio"

    sample_rate = int(sig.get("sample_rate") or 0)
    channels = int(sig.get("channels") or 0)
    if sample_rate <= 0 or channels <= 0:
        return False, "stream de audio invalido"

    duration = float(sig.get("format_duration") or sig.get("stream_duration") or 0.0)
    if duration <= 0:
        return False, "duracion invalida"

    return True, ""


def _audio_signature_wav_to_aiff_compatible(original_sig: dict | None, updated_sig: dict | None):
    if not original_sig or not updated_sig:
        return True

    for field in ("sample_rate", "channels"):
        base = int(original_sig.get(field) or 0)
        new = int(updated_sig.get(field) or 0)
        if base > 0 and new > 0 and base != new:
            return False

    base_bits = int(original_sig.get("bits_per_raw_sample") or original_sig.get("bits_per_sample") or 0)
    new_bits = int(updated_sig.get("bits_per_raw_sample") or updated_sig.get("bits_per_sample") or 0)
    if base_bits > 0 and new_bits > 0 and base_bits != new_bits:
        return False

    original_duration = float(original_sig.get("format_duration") or original_sig.get("stream_duration") or 0.0)
    updated_duration = float(updated_sig.get("format_duration") or updated_sig.get("stream_duration") or 0.0)
    if original_duration > 0 and updated_duration > 0:
        tolerance = max(0.15, original_duration * 0.01)
        if abs(original_duration - updated_duration) > tolerance:
            return False

    return True


def _pcm_codec_from_bit_depth(bit_depth: int | None):
    depth = int(bit_depth or 0)
    if depth <= 16:
        return "pcm_s16be"
    if depth <= 24:
        return "pcm_s24be"
    return "pcm_s32be"


def _convert_wav_to_aiff_preserving_audio(src_path: Path, dst_path: Path, ffmpeg_cmd: str | None, ffprobe_cmd: str | None):
    if not ffmpeg_cmd:
        raise RuntimeError("ffmpeg no encontrado")

    info = get_audio_metadata(str(src_path), ffprobe_cmd) if ffprobe_cmd else {}
    sample_rate = int(info.get("sample_rate") or 0)
    channels = int(info.get("channels") or 0)
    bit_depth = int(info.get("bits_per_raw_sample") or info.get("bits_per_sample") or 0)
    if bit_depth <= 0:
        bit_depth = get_mutagen_bit_depth(str(src_path)) or 24

    pcm_codec = _pcm_codec_from_bit_depth(bit_depth)

    cmd = [
        ffmpeg_cmd,
        "-v", "error",
        "-y",
        "-i", str(src_path),
        "-map", "0:a",
        "-map_metadata", "0",
        "-ar", str(sample_rate if sample_rate > 0 else SAFE_SAMPLE_RATE_HZ),
        "-ac", str(channels if channels > 0 else 2),
        "-c:a", pcm_codec,
        str(dst_path),
    ]

    proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="ignore", timeout=180)
    if proc.returncode != 0 or not dst_path.exists() or dst_path.stat().st_size <= 0:
        err = (proc.stderr or proc.stdout or "").strip()
        raise RuntimeError(f"No se pudo convertir WAV a AIFF: {err or 'ffmpeg error'}")


def update_track_metadata_file(track_path: Path, artist: str, title: str, bpm: str | None, musical_key: str | None, cover_data_url: str | None, ffmpeg_cmd: str | None, clone_output_dir: str | None = None):
    if not track_path.exists():
        return {"ok": False, "error": "Archivo no encontrado"}

    artist = (artist or "").strip() or "N-A"
    title = (title or "").strip() or track_path.stem
    bpm_text = None if bpm is None else (bpm or "").strip()
    key_text = None if musical_key is None else (musical_key or "").strip()
    cover_jpeg = _prepare_cover_jpeg_bytes(cover_data_url or "", ffmpeg_cmd)
    cover_mime = _guess_image_mime(cover_jpeg)
    should_update_cover = bool(cover_jpeg)
    previous_cover_data = extract_cover_base64(track_path, ffmpeg_cmd) if should_update_cover else None
    ext = track_path.suffix.lower()
    should_convert_wav_to_aiff = ext in {".wav", ".wave"}
    clone_dir_raw = str(clone_output_dir or "").strip()
    preserve_source_wav = bool(should_convert_wav_to_aiff and clone_dir_raw)
    if preserve_source_wav:
        clone_dir = Path(clone_dir_raw).expanduser()
        ensure_dir(clone_dir)
        final_path = clone_dir / f"{track_path.stem}.aiff"
    else:
        final_path = track_path.with_suffix(".aiff") if should_convert_wav_to_aiff else track_path

    def _apply_metadata(target_path: Path):
        target_ext = target_path.suffix.lower()

        if target_ext == ".mp3":
            from mutagen.id3 import ID3, ID3NoHeaderError, TIT2, TPE1, TBPM, TKEY, APIC
            try:
                tags = ID3(str(target_path))
            except ID3NoHeaderError:
                tags = ID3()

            tags.delall("TIT2")
            tags.delall("TPE1")
            tags.add(TIT2(encoding=3, text=title))
            tags.add(TPE1(encoding=3, text=artist))
            if bpm_text is not None:
                tags.delall("TBPM")
                if bpm_text:
                    tags.add(TBPM(encoding=3, text=bpm_text))
            if key_text is not None:
                tags.delall("TKEY")
                if key_text:
                    tags.add(TKEY(encoding=3, text=key_text))

            if cover_jpeg:
                tags.delall("APIC")
                tags.add(APIC(encoding=3, mime=cover_mime, type=3, desc="Cover", data=cover_jpeg))

            tags.save(str(target_path), v2_version=3)
            return

        if target_ext in {".aif", ".aiff"}:
            from mutagen.aiff import AIFF
            from mutagen.id3 import TIT2, TPE1, TBPM, TKEY, APIC

            audio = AIFF(str(target_path))
            if audio.tags is None:
                audio.add_tags()
            tags = audio.tags

            tags.delall("TIT2")
            tags.delall("TPE1")
            tags.add(TIT2(encoding=3, text=title))
            tags.add(TPE1(encoding=3, text=artist))
            if bpm_text is not None:
                tags.delall("TBPM")
                if bpm_text:
                    tags.add(TBPM(encoding=3, text=bpm_text))
            if key_text is not None:
                tags.delall("TKEY")
                if key_text:
                    tags.add(TKEY(encoding=3, text=key_text))

            if cover_jpeg:
                tags.delall("APIC")
                tags.add(APIC(encoding=3, mime=cover_mime, type=3, desc="Cover", data=cover_jpeg))

            audio.save()
            return

        if target_ext in {".wav", ".wave"}:
            from mutagen.wave import WAVE
            from mutagen.id3 import TIT2, TPE1, TBPM, TKEY, APIC

            audio = WAVE(str(target_path))
            if audio.tags is None:
                audio.add_tags()
            tags = audio.tags

            tags.delall("TIT2")
            tags.delall("TPE1")
            tags.add(TIT2(encoding=3, text=title))
            tags.add(TPE1(encoding=3, text=artist))
            if bpm_text is not None:
                tags.delall("TBPM")
                if bpm_text:
                    tags.add(TBPM(encoding=3, text=bpm_text))
            if key_text is not None:
                tags.delall("TKEY")
                if key_text:
                    tags.add(TKEY(encoding=3, text=key_text))

            if cover_jpeg:
                tags.delall("APIC")
                tags.add(APIC(encoding=3, mime=cover_mime, type=3, desc="Cover", data=cover_jpeg))

            audio.save()
            return

        if target_ext == ".flac":
            from mutagen.flac import FLAC, Picture
            audio = FLAC(str(target_path))
            audio["artist"] = [artist]
            audio["title"] = [title]
            if bpm_text is not None:
                if bpm_text:
                    audio["bpm"] = [bpm_text]
                else:
                    audio.pop("bpm", None)
            if key_text is not None:
                if key_text:
                    audio["initialkey"] = [key_text]
                else:
                    audio.pop("initialkey", None)
            if cover_jpeg:
                picture = Picture()
                picture.type = 3
                picture.mime = cover_mime
                picture.desc = "Cover"
                picture.data = cover_jpeg
                audio.clear_pictures()
                audio.add_picture(picture)
            audio.save()
            return

        if target_ext in {".m4a", ".aac", ".alac", ".mp4"}:
            from mutagen.mp4 import MP4, MP4Cover
            audio = MP4(str(target_path))
            tags = audio.tags or {}

            def _sanitize_tmpo_values(raw_value):
                values = raw_value if isinstance(raw_value, (list, tuple)) else [raw_value]
                sanitized = []
                for item in values:
                    try:
                        bpm_number = int(round(float(str(item).strip())))
                    except Exception:
                        continue
                    if bpm_number > 0:
                        sanitized.append(bpm_number)
                return sanitized

            existing_tmpo = tags.get("tmpo")
            if existing_tmpo is not None:
                sanitized_existing_tmpo = _sanitize_tmpo_values(existing_tmpo)
                if sanitized_existing_tmpo:
                    tags["tmpo"] = sanitized_existing_tmpo
                else:
                    tags.pop("tmpo", None)

            tags["\xa9ART"] = [artist]
            tags["\xa9nam"] = [title]
            if bpm_text is not None:
                if bpm_text:
                    sanitized_new_tmpo = _sanitize_tmpo_values(bpm_text)
                    if sanitized_new_tmpo:
                        tags["tmpo"] = sanitized_new_tmpo
                    else:
                        tags.pop("tmpo", None)
                elif "tmpo" in tags:
                    tags.pop("tmpo", None)
            key_freeform = "----:com.apple.iTunes:INITIALKEY"
            if key_text is not None:
                if key_text:
                    tags[key_freeform] = [key_text.encode("utf-8")]
                elif key_freeform in tags:
                    tags.pop(key_freeform, None)
            if cover_jpeg:
                mp4_cover_format = MP4Cover.FORMAT_PNG if cover_mime == "image/png" else MP4Cover.FORMAT_JPEG
                tags["covr"] = [MP4Cover(cover_jpeg, imageformat=mp4_cover_format)]
            audio.tags = tags
            audio.save()
            return

        from mutagen import File as MutagenFile
        audio = MutagenFile(str(target_path), easy=True)
        if audio is None:
            raise RuntimeError("Formato no soportado")
        audio["artist"] = [artist]
        audio["title"] = [title]
        if bpm_text is not None:
            if bpm_text:
                audio["bpm"] = [bpm_text]
            elif "bpm" in audio:
                audio.pop("bpm", None)
        if key_text is not None:
            if key_text:
                audio["initialkey"] = [key_text]
            elif "initialkey" in audio:
                audio.pop("initialkey", None)
        audio.save()

    ffprobe_cmd = get_ffprobe_cmd()
    original_sig = _probe_primary_audio_signature(track_path, ffprobe_cmd)

    temp_path = None
    backup_path = None
    target_existing_backup_path = None
    source_was_locked = False

    def _verify_bpm_key_applied(check_path: Path, label: str):
        if bpm_text is None and key_text is None:
            return
        meta = get_track_metadata(check_path, ffprobe_cmd=ffprobe_cmd)
        if bpm_text is not None:
            expected_bpm = _fmt_bpm_value(bpm_text) if bpm_text else "--"
            actual_bpm = str(meta.get("bpm") or "--").strip() or "--"
            if actual_bpm != expected_bpm:
                raise RuntimeError(f"BPM no se aplico correctamente ({label})")
        if key_text is not None:
            expected_key = (key_text or "--").strip() or "--"
            actual_key = str(meta.get("key") or "--").strip() or "--"
            if actual_key.lower() != expected_key.lower():
                raise RuntimeError(f"KEY no se aplico correctamente ({label})")

    try:
        with tempfile.NamedTemporaryFile(prefix=f"{track_path.stem}_meta_", suffix=final_path.suffix, dir=str(final_path.parent), delete=False) as tmp_file:
            temp_path = Path(tmp_file.name)

        if should_convert_wav_to_aiff:
            _convert_wav_to_aiff_preserving_audio(track_path, temp_path, ffmpeg_cmd, ffprobe_cmd)
        else:
            shutil.copy2(track_path, temp_path)

        _apply_metadata(temp_path)

        if not temp_path.exists() or temp_path.stat().st_size <= 0:
            raise RuntimeError("Archivo temporal invalido tras actualizar metadata")

        updated_sig = _probe_primary_audio_signature(temp_path, ffprobe_cmd)
        if should_convert_wav_to_aiff:
            if not _audio_signature_wav_to_aiff_compatible(original_sig, updated_sig):
                raise RuntimeError("Verificacion fallida: WAV->AIFF no conserva sample rate / bit depth / canales")
        else:
            if not _audio_signature_compatible(original_sig, updated_sig):
                raise RuntimeError("Verificacion fallida: el audio modificado no coincide con el original")

        is_aiff_target = final_path.suffix.lower() in {".aif", ".aiff"}
        if is_aiff_target:
            ok, err = _validate_cover_aware_aiff_integrity(temp_path, ffprobe_cmd)
            if not ok:
                raise RuntimeError(f"Verificacion AIFF fallida: {err or 'integridad invalida'}")
        elif ffmpeg_cmd and ffprobe_cmd:
            ok, err, _, _ = decode_integrity_check(str(temp_path), ffmpeg_cmd, ffprobe_cmd)
            if not ok:
                raise RuntimeError(f"Verificacion ffmpeg fallida: {err or 'decode error'}")

        _verify_bpm_key_applied(temp_path, "temporal")

        if should_update_cover:
            temp_cover_data = extract_cover_base64(temp_path, ffmpeg_cmd)
            if not temp_cover_data:
                raise RuntimeError("Cover no se aplico correctamente al archivo temporal")
            if previous_cover_data and temp_cover_data == previous_cover_data:
                raise RuntimeError("Cover no cambio en el archivo temporal")

        backup_path = temp_path.with_suffix(temp_path.suffix + ".bak")
        if backup_path.exists():
            backup_path.unlink(missing_ok=True)

        if should_convert_wav_to_aiff and final_path.exists():
            target_existing_backup_path = final_path.with_suffix(final_path.suffix + ".existing.bak")
            if target_existing_backup_path.exists():
                target_existing_backup_path.unlink(missing_ok=True)
            os.replace(str(final_path), str(target_existing_backup_path))

        replace_err = None
        if not preserve_source_wav:
            for _ in range(4):
                try:
                    os.replace(str(track_path), str(backup_path))
                    replace_err = None
                    break
                except PermissionError as exc:
                    replace_err = exc
                    if getattr(exc, "winerror", None) == 32:
                        time.sleep(0.2)
                        continue
                    raise

            if replace_err is not None:
                if should_convert_wav_to_aiff and getattr(replace_err, "winerror", None) == 32:
                    source_was_locked = True
                else:
                    raise replace_err

        os.replace(str(temp_path), str(final_path))
        temp_path = None

        final_sig = _probe_primary_audio_signature(final_path, ffprobe_cmd)
        if should_convert_wav_to_aiff:
            if not _audio_signature_wav_to_aiff_compatible(original_sig, final_sig):
                raise RuntimeError("Verificacion final fallida tras reemplazo WAV->AIFF")
        else:
            if not _audio_signature_compatible(original_sig, final_sig):
                raise RuntimeError("Verificacion final fallida tras reemplazo")

        if is_aiff_target:
            ok, err = _validate_cover_aware_aiff_integrity(final_path, ffprobe_cmd)
            if not ok:
                raise RuntimeError(f"Verificacion final AIFF fallida: {err or 'integridad invalida'}")

        _verify_bpm_key_applied(final_path, "final")

        if should_update_cover:
            final_cover_data = extract_cover_base64(final_path, ffmpeg_cmd)
            if not final_cover_data:
                raise RuntimeError("Cover no se aplico al archivo final")
            if previous_cover_data and final_cover_data == previous_cover_data:
                raise RuntimeError("Cover no cambio tras guardar")

        if backup_path.exists():
            backup_path.unlink(missing_ok=True)
        if target_existing_backup_path and target_existing_backup_path.exists():
            target_existing_backup_path.unlink(missing_ok=True)

        success_message = "Cambios guardados correctamente"
        if should_convert_wav_to_aiff:
            if preserve_source_wav:
                success_message = "Archivo clonado listo para reintegrarse: AIFF con cover guardado en ruta de clonado"
            elif source_was_locked:
                success_message = "WAV bloqueado por otro proceso: se guardo AIFF actualizado y se mantuvo el WAV original"
            else:
                success_message = "WAV convertido a AIFF y cambios guardados correctamente"

        return {
            "ok": True,
            "message": success_message,
            "newPath": str(final_path),
            "convertedToAiff": should_convert_wav_to_aiff,
            "clonedForReintegration": preserve_source_wav,
            "sourceLocked": source_was_locked,
        }
    except Exception as exc:
        rolled_back = False
        if should_convert_wav_to_aiff and final_path.exists() and (not target_existing_backup_path or not target_existing_backup_path.exists()):
            try:
                final_path.unlink(missing_ok=True)
            except Exception:
                pass

        if should_convert_wav_to_aiff and target_existing_backup_path and target_existing_backup_path.exists():
            try:
                if final_path.exists():
                    final_path.unlink(missing_ok=True)
                os.replace(str(target_existing_backup_path), str(final_path))
            except Exception:
                pass

        if backup_path and backup_path.exists():
            try:
                if track_path.exists():
                    track_path.unlink(missing_ok=True)
                os.replace(str(backup_path), str(track_path))
                rolled_back = True
            except Exception:
                pass
        if temp_path and temp_path.exists():
            temp_path.unlink(missing_ok=True)
        return {"ok": False, "error": str(exc), "rolledBack": rolled_back}


def _build_safe_cmd(src_path: Path, dst_path: Path, is_lossless: bool, artwork_path: Path | None, ffmpeg_cmd: str, ffprobe_cmd: str, normalize: bool, resample: bool, lossless_format: str, lossy_format: str, normalize_gain_db: float = 0.0, source_bit_depth: int | None = None, force_triangular_dither: bool = False):
    gain_db = max(0.0, float(normalize_gain_db or 0.0))

    cmd = [
        ffmpeg_cmd,
        "-y",
        "-i", str(src_path)
    ]

    if artwork_path:
        cmd += [
            "-i", str(artwork_path),
            "-map", "0:a",
            "-map", "1:v",
            "-c:v", "mjpeg",
            "-vf", f"scale={ARTWORK_SIZE}:{ARTWORK_SIZE}:force_original_aspect_ratio=decrease,pad={ARTWORK_SIZE}:{ARTWORK_SIZE}:(ow-iw)/2:(oh-ih)/2:black",
            "-q:v", str(ARTWORK_JPEG_QUALITY),
            "-disposition:v", "attached_pic"
        ]
    else:
        cmd += ["-map", "0:a", "-map", "0:v?"]

    cmd += ["-map_metadata", "0"]
    cmd += ["-ac", "2"]

    af_filters = []
    if is_lossless:
        if gain_db > 0:
            af_filters.append(f"volume={gain_db}dB")
        should_apply_dither = bool(
            force_triangular_dither
            or source_bit_depth is None
            or source_bit_depth > 16
        )
        if should_apply_dither:
            af_filters.append("aresample=44100:resampler=soxr:precision=28:dither_method=triangular")
        else:
            af_filters.append("aresample=44100:resampler=soxr:precision=28")
    else:
        af_filters.append("aformat=sample_fmts=flt")
        if normalize and gain_db > 0:
            af_filters.append("loudnorm=I=-12:LRA=11:TP=-1.0:linear=true")
        elif gain_db > 0:
            af_filters.append(f"volume={gain_db}dB")
        if resample:
            af_filters.append("aresample=44100:resampler=soxr:precision=28")

    if af_filters:
        cmd += ["-af", ",".join(af_filters)]

    if is_lossless:
        cmd += [
            "-ar", "44100",
            "-c:a", "pcm_s16be",
        ]
        cmd += [
            "-write_id3v2", "1",
            str(dst_path)
        ]
    else:
        cmd += [
            "-c:a", "libmp3lame",
            "-b:a", "320k",
            "-minrate", "320k",
            "-maxrate", "320k",
            "-bufsize", "640k",
            "-compression_level", "0",
            "-ar", "44100",
        ]
        cmd += [
            "-write_id3v2", "1",
            str(dst_path)
        ]

    return cmd


def preserve_metadata_and_cover_from_source(src_path: Path, dst_path: Path, ffmpeg_cmd: str | None, ffprobe_cmd: str | None = None):
    src_meta = get_track_metadata(src_path, ffprobe_cmd=ffprobe_cmd)
    artist = str(src_meta.get("artist") or "").strip() or "N-A"
    title = str(src_meta.get("title") or "").strip() or src_path.stem

    bpm_raw = str(src_meta.get("bpm") or "").strip()
    bpm_value = "" if bpm_raw in {"", "--", "N/A", "n/a"} else bpm_raw

    key_raw = str(src_meta.get("key") or "").strip()
    key_value = "" if key_raw in {"", "--", "N/A", "n/a"} else key_raw

    cover_data_url = extract_cover_base64(src_path, ffmpeg_cmd)
    if not cover_data_url:
        def _image_file_to_data_url(image_path: Path):
            try:
                image_bytes = image_path.read_bytes()
                if not image_bytes:
                    return None
                mime = "image/png" if image_bytes.startswith(b"\x89PNG") else "image/jpeg"
                encoded = base64.b64encode(image_bytes).decode("ascii")
                return f"data:{mime};base64,{encoded}"
            except Exception:
                return None

        sidecar_candidates = []
        for ext in (".jpg", ".jpeg", ".png"):
            sidecar_candidates.append(src_path.with_suffix(ext))
            sidecar_candidates.append(src_path.parent / f"{src_path.stem}{ext}")

        covers_dir = (Path(__file__).parent / LOCAL_COVERS_DIR)
        for ext in (".jpg", ".jpeg", ".png"):
            sidecar_candidates.append(covers_dir / f"{src_path.stem}{ext}")

        for candidate in sidecar_candidates:
            if candidate.exists() and candidate.is_file():
                cover_data_url = _image_file_to_data_url(candidate)
                if cover_data_url:
                    break
    result = update_track_metadata_file(
        track_path=dst_path,
        artist=artist,
        title=title,
        bpm=bpm_value,
        musical_key=key_value,
        cover_data_url=cover_data_url,
        ffmpeg_cmd=ffmpeg_cmd,
        clone_output_dir=None,
    )

    if not result.get("ok"):
        raise RuntimeError(result.get("error") or "No se pudo preservar metadata/cover")

    updated_path = str(result.get("newPath") or "").strip()
    return Path(updated_path) if updated_path else dst_path


class CrateGuardHandler(BaseHTTPRequestHandler):
    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def do_POST(self):
        parsed = urlparse(self.path)
        if parsed.path == "/api/app-config":
            content_length = int(self.headers.get('Content-Length', 0))
            post_data = self.rfile.read(content_length)
            try:
                data = json.loads(post_data.decode('utf-8')) if post_data else {}
                clone_output_dir = str(data.get("cloneOutputDir") or "").strip()
                set_clone_output_dir_config(clone_output_dir)
                _send_json(self, 200, {"ok": True, "cloneOutputDir": get_clone_output_dir_config()})
            except Exception as exc:
                _send_json(self, 500, {"ok": False, "error": str(exc)})
            return

        if parsed.path == "/api/update-track-metadata":
            content_length = int(self.headers.get('Content-Length', 0))
            post_data = self.rfile.read(content_length)
            try:
                data = json.loads(post_data.decode('utf-8'))
                path_raw = (data.get("path") or "").strip()
                artist = data.get("artist") or ""
                title = data.get("title") or ""
                bpm = data.get("bpm") if "bpm" in data else None
                musical_key = data.get("key") if "key" in data else None
                cover_data_url = data.get("coverDataUrl") if "coverDataUrl" in data else None
                clone_output_dir = data.get("cloneOutputDir") if "cloneOutputDir" in data else None
                playlist_path_raw = str(data.get("playlistPath") or "").strip()

                if not path_raw:
                    _send_json(self, 400, {"ok": False, "error": "Ruta faltante"})
                    return

                track_path = Path(path_raw)
                result = update_track_metadata_file(track_path, artist, title, bpm, musical_key, cover_data_url, get_ffmpeg_cmd(), clone_output_dir)

                if result.get("ok") and playlist_path_raw:
                    try:
                        updated_path = str(result.get("newPath") or "").strip()
                        if updated_path:
                            old_resolved = str(track_path.resolve())
                            new_resolved = str(Path(updated_path).resolve())
                            if old_resolved.lower() != new_resolved.lower():
                                playlist_update = update_m3u8_track_path(Path(playlist_path_raw), track_path, Path(updated_path))
                                if playlist_update.get("ok"):
                                    result["playlistUpdated"] = True
                                    result["playlistPath"] = playlist_path_raw
                                else:
                                    result["playlistUpdated"] = False
                                    result["playlistUpdateError"] = playlist_update.get("error") or "No se pudo actualizar playlist"
                    except Exception as playlist_exc:
                        result["playlistUpdated"] = False
                        result["playlistUpdateError"] = str(playlist_exc)

                status = 200 if result.get("ok") else 500
                _send_json(self, status, result)
            except Exception as exc:
                _send_json(self, 500, {"ok": False, "error": str(exc)})
            return

        if parsed.path == "/api/extract-cover-from-upload":
            content_length = int(self.headers.get('Content-Length', 0))
            post_data = self.rfile.read(content_length)
            try:
                data = json.loads(post_data.decode('utf-8'))
                file_name = str(data.get("fileName") or "audio.bin").strip()
                audio_base64 = str(data.get("audioBase64") or "").strip()

                if not audio_base64:
                    _send_json(self, 400, {"ok": False, "error": "Archivo faltante"})
                    return

                ffmpeg_cmd = get_ffmpeg_cmd()
                if not ffmpeg_cmd:
                    _send_json(self, 500, {"ok": False, "error": "ffmpeg no encontrado"})
                    return

                ext = Path(file_name).suffix.lower() or ".bin"

                try:
                    audio_bytes = base64.b64decode(audio_base64)
                except Exception:
                    _send_json(self, 400, {"ok": False, "error": "Base64 invalido"})
                    return

                with tempfile.TemporaryDirectory() as tmp_dir:
                    tmp_audio = Path(tmp_dir) / f"upload{ext}"
                    tmp_audio.write_bytes(audio_bytes)
                    image_data = extract_cover_base64(tmp_audio, ffmpeg_cmd)

                if not image_data:
                    _send_json(self, 200, {"ok": False, "image": None, "error": "El archivo no contiene cover"})
                    return

                _send_json(self, 200, {"ok": True, "image": image_data})
            except Exception as exc:
                _send_json(self, 500, {"ok": False, "error": str(exc)})
            return

        if parsed.path == "/api/create-temp-playlist":
            content_length = int(self.headers.get('Content-Length', 0))
            post_data = self.rfile.read(content_length)
            try:
                _cleanup_stale_temp_artifacts(Path(__file__).parent / "exports" / "_temp")
                data = json.loads(post_data.decode('utf-8'))
                tracks = data.get("tracks", [])
                base_name = data.get("name", "Temp_Set")
                cover_overrides = data.get("coverOverrides") or {}
                
                temp_m3u = Path(__file__).parent / "exports" / "_temp" / f"{safe_filename(base_name)}.m3u8"
                ensure_dir(temp_m3u.parent)
                
                with open(temp_m3u, "w", encoding="utf-8") as f:
                    for t in tracks:
                        f.write(f"{t}\n")

                artwork_dir = None
                if isinstance(cover_overrides, dict) and cover_overrides:
                    artwork_dir = _write_cover_overrides(base_name, cover_overrides, get_ffmpeg_cmd())

                _send_json(self, 200, {
                    "ok": True,
                    "path": str(temp_m3u),
                    "artworkDir": str(artwork_dir) if artwork_dir else "",
                })
            except Exception as e:
                _send_json(self, 500, {"error": str(e)})
            return
            
        self.send_response(404)
        self.end_headers()

    def do_GET(self):
        parsed = urlparse(self.path)
        if parsed.path == "/api/app-config":
            _send_json(self, 200, {"ok": True, "cloneOutputDir": get_clone_output_dir_config()})
            return

        if parsed.path == "/api/ping":
            _send_json(self, 200, {"ok": True})
            return

        params = parse_qs(parsed.query)

        if parsed.path == "/api/pick-file":
            initial = (params.get("initial") or [""])[0].strip()
            selected = _pick_file_native(initial)
            _send_json(self, 200, {
                "ok": True,
                "path": selected or "",
                "cancelled": not bool(selected),
            })
            return

        if parsed.path == "/api/pick-folder":
            initial = (params.get("initial") or [""])[0].strip()
            selected = _pick_folder_native(initial)
            _send_json(self, 200, {
                "ok": True,
                "path": selected or "",
                "cancelled": not bool(selected),
            })
            return

        if parsed.path == "/api/waveform":
            track_path = (params.get("path") or [""])[0].strip()
            if not track_path:
                _send_json(self, 400, {"error": "Ruta faltante"})
                return
            src_path = Path(track_path)
            if not src_path.exists():
                _send_json(self, 404, {"error": "Archivo no encontrado"})
                return
            ffmpeg_cmd = get_ffmpeg_cmd()
            if not ffmpeg_cmd:
                _send_json(self, 500, {"error": "ffmpeg no encontrado"})
                return
            try:
                cached_image = _waveform_cache_get(src_path)
                if cached_image:
                    _send_json(self, 200, {"image": cached_image})
                    return
                with tempfile.TemporaryDirectory() as tmp_dir:
                    out_path = Path(tmp_dir) / "waveform.png"
                    cmd_three_band_blend = [
                        ffmpeg_cmd,
                        "-v", "error",
                        "-nostdin",
                        "-y",
                        "-i", str(src_path),
                        "-filter_complex",
                        "[0:a]asplit=3[lo][mi][hi];"
                        f"[lo]lowpass=220,showwavespic=s=1280x320:colors=0x{WAVEFORM_COLOR_LOW_HEX}[loww];"
                        f"[mi]highpass=220,lowpass=2500,showwavespic=s=1280x320:colors=0x{WAVEFORM_COLOR_MAIN_HEX}[midw];"
                        f"[hi]highpass=2500,showwavespic=s=1280x320:colors=0x{WAVEFORM_COLOR_HIGH_HEX}[highw];"
                        "[loww][midw]blend=all_mode=screen:all_opacity=1.0[lm];"
                        "[lm][highw]blend=all_mode=screen:all_opacity=1.0[out]",
                        "-map", "[out]",
                        "-frames:v", "1",
                        str(out_path),
                    ]

                    result = subprocess.run(cmd_three_band_blend, capture_output=True, text=True, encoding="utf-8", errors="ignore")

                    if result.returncode != 0 or not out_path.exists():
                        cmd_three_band_overlay = [
                            ffmpeg_cmd,
                            "-v", "error",
                            "-nostdin",
                            "-y",
                            "-i", str(src_path),
                            "-filter_complex",
                            "[0:a]asplit=3[lo][mi][hi];"
                            f"[lo]lowpass=220,showwavespic=s=1280x320:colors=0x{WAVEFORM_COLOR_LOW_HEX}[loww];"
                            f"[mi]highpass=220,lowpass=2500,showwavespic=s=1280x320:colors=0x{WAVEFORM_COLOR_MAIN_HEX}[midw];"
                            f"[hi]highpass=2500,showwavespic=s=1280x320:colors=0x{WAVEFORM_COLOR_HIGH_HEX}[highw];"
                            "[midw]colorkey=0x000000:0.05:0[midk];"
                            "[highw]colorkey=0x000000:0.05:0[highk];"
                            "[loww][midk]overlay=format=auto[tmp];"
                            "[tmp][highk]overlay=format=auto[out]",
                            "-map", "[out]",
                            "-frames:v", "1",
                            str(out_path),
                        ]
                        result = subprocess.run(cmd_three_band_overlay, capture_output=True, text=True, encoding="utf-8", errors="ignore")

                    if result.returncode != 0 or not out_path.exists():
                        cmd_fallback = [
                            ffmpeg_cmd,
                            "-v", "error",
                            "-nostdin",
                            "-y",
                            "-i", str(src_path),
                            "-filter_complex", f"showwavespic=s=1280x320:mode=cline:scale=sqrt:colors=0x{WAVEFORM_COLOR_MAIN_HEX}",
                            "-frames:v", "1",
                            str(out_path),
                        ]
                        result = subprocess.run(cmd_fallback, capture_output=True, text=True, encoding="utf-8", errors="ignore")
                        if result.returncode != 0 or not out_path.exists():
                            _send_json(self, 500, {"error": "No se pudo generar la forma de onda"})
                            return
                    image_bytes = out_path.read_bytes()
                encoded = base64.b64encode(image_bytes).decode("ascii")
                image_data_url = f"data:image/png;base64,{encoded}"
                _waveform_cache_put(src_path, image_data_url)
                _send_json(self, 200, {"image": image_data_url})
            except Exception as exc:
                _send_json(self, 500, {"error": str(exc)})
            return

        if parsed.path == "/api/lufs":
            track_path = (params.get("path") or [""])[0].strip()
            if not track_path:
                _send_json(self, 400, {"error": "Ruta faltante"})
                return
            src_path = Path(track_path)
            if not src_path.exists():
                _send_json(self, 404, {"error": "Archivo no encontrado"})
                return
            ffmpeg_cmd = get_ffmpeg_cmd()
            if not ffmpeg_cmd:
                _send_json(self, 500, {"error": "ffmpeg no encontrado"})
                return
            try:
                cached_lufs = _lufs_cache_get(src_path)
                if cached_lufs is not None:
                    _send_json(self, 200, {"lufs": f"{cached_lufs:.1f}"})
                    return

                measured = get_lufs_integrated(str(src_path), ffmpeg_cmd, None)
                if measured is None:
                    _send_json(self, 200, {"lufs": "-"})
                    return

                _lufs_cache_put(src_path, measured)
                _send_json(self, 200, {"lufs": f"{measured:.1f}"})
            except Exception as exc:
                _send_json(self, 500, {"error": str(exc)})
            return

        if parsed.path == "/api/cover":
            track_path = (params.get("path") or [""])[0].strip()
            if not track_path:
                _send_json(self, 400, {"error": "Ruta faltante"})
                return
            src_path = Path(track_path)
            if not src_path.exists():
                _send_json(self, 404, {"error": "Archivo no encontrado"})
                return
            ffmpeg_cmd = get_ffmpeg_cmd()
            if not ffmpeg_cmd:
                _send_json(self, 500, {"error": "ffmpeg no encontrado"})
                return
            try:
                with tempfile.TemporaryDirectory() as tmp_dir:
                    out_path = Path(tmp_dir) / "cover.jpg"
                    cmd = [
                        ffmpeg_cmd,
                        "-v", "error",
                        "-y",
                        "-i", str(src_path),
                        "-an",
                        "-vcodec", "copy",
                        str(out_path)
                    ]
                    # Si no se puede copiar el stream de video directo, intentar transcodificar
                    res = subprocess.run(cmd, capture_output=True, timeout=10)
                    if res.returncode != 0 or not out_path.exists():
                        cmd_transcode = [
                            ffmpeg_cmd,
                            "-v", "error",
                            "-y",
                            "-i", str(src_path),
                            "-an",
                            "-frames:v", "1",
                            str(out_path)
                        ]
                        subprocess.run(cmd_transcode, capture_output=True, timeout=10)
                        
                    if out_path.exists():
                        image_bytes = out_path.read_bytes()
                        encoded = base64.b64encode(image_bytes).decode("ascii")
                        _send_json(self, 200, {"image": f"data:image/jpeg;base64,{encoded}"})
                    else:
                        _send_json(self, 200, {"image": None})
            except Exception as exc:
                _send_json(self, 500, {"error": str(exc)})
            return

        if parsed.path == "/api/covers-missing":
            folder = (params.get("folder") or [""])[0].strip()
            if not folder:
                _send_json(self, 400, {"error": "Missing folder"})
                return
            missing = list_tracks_without_cover(Path(folder))
            _send_json(self, 200, {"missing": missing, "count": len(missing)})
            return

        if parsed.path == "/api/serato-drives":
            drives = list_external_drives()
            _send_json(self, 200, {"drives": drives})
            return

        if parsed.path == "/api/audio-file":
            track_path = (params.get("path") or [""])[0].strip()
            preview_mode = _parse_bool((params.get("preview") or ["0"])[0])
            clip_seconds_raw = (params.get("clipSeconds") or params.get("clip") or [""])[0].strip()
            clip_seconds = None
            if clip_seconds_raw:
                try:
                    parsed_clip = int(clip_seconds_raw)
                    if parsed_clip > 0:
                        clip_seconds = max(1, min(120, parsed_clip))
                except Exception:
                    clip_seconds = None
            if not track_path:
                _send_json(self, 400, {"error": "Ruta faltante"})
                return

            src_path = Path(track_path)
            if not src_path.exists() or not src_path.is_file():
                _send_json(self, 404, {"error": "Archivo no encontrado"})
                return

            target_path = src_path
            if preview_mode:
                ffmpeg_cmd = get_ffmpeg_cmd()
                preview_path = _ensure_preview_audio_file(src_path, ffmpeg_cmd, clip_seconds=clip_seconds)
                if preview_path and preview_path.exists():
                    target_path = preview_path

            try:
                file_size = target_path.stat().st_size
                if file_size <= 0:
                    _send_json(self, 404, {"error": "Archivo vacio"})
                    return

                content_type = _resolve_audio_content_type(target_path)

                range_header = self.headers.get("Range")
                start = 0
                end = file_size - 1
                status = 200

                if range_header and range_header.startswith("bytes="):
                    status = 206
                    byte_range = range_header.replace("bytes=", "", 1).split(",", 1)[0].strip()
                    if "-" in byte_range:
                        start_str, end_str = byte_range.split("-", 1)
                        if start_str.strip() and end_str.strip():
                            start = int(start_str)
                            end = int(end_str)
                        elif start_str.strip():
                            start = int(start_str)
                        elif end_str.strip():
                            suffix_len = int(end_str)
                            if suffix_len > 0:
                                start = max(file_size - suffix_len, 0)
                                end = file_size - 1
                    start = max(0, min(start, file_size - 1))
                    end = max(start, min(end, file_size - 1))

                chunk_size = end - start + 1

                self.send_response(status)
                self.send_header("Content-Type", content_type)
                self.send_header("Accept-Ranges", "bytes")
                self.send_header("Content-Length", str(chunk_size))
                self.send_header("Access-Control-Allow-Origin", "*")
                self.send_header("Cache-Control", "public, max-age=3600")
                if status == 206:
                    self.send_header("Content-Range", f"bytes {start}-{end}/{file_size}")
                self.end_headers()

                with target_path.open("rb") as f:
                    f.seek(start)
                    remaining = chunk_size
                    while remaining > 0:
                        block = f.read(min(64 * 1024, remaining))
                        if not block:
                            break
                        self.wfile.write(block)
                        remaining -= len(block)
                return
            except Exception as exc:
                if isinstance(exc, (ConnectionAbortedError, ConnectionResetError, BrokenPipeError)):
                    return
                try:
                    _send_json(self, 500, {"error": str(exc)})
                except Exception:
                    pass
                return

        if parsed.path not in {
            "/api/scan",
            "/api/safe-set",
            "/api/convert-single",
            "/api/integrity-scan",
            "/api/covers-process",
            "/api/serato-clone",
        }:
            self.send_response(404)
            self.end_headers()
            return

        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "keep-alive")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()

        aborted = threading.Event()

        def progress_cb(idx, total, name):
            if aborted.is_set():
                return
            _send_event(self, "progress", {
                "current": idx,
                "total": total,
                "name": name,
            })

        scan_counts = {"broken": 0, "incompatible": 0, "warning": 0, "compatible": 0, "total": 0}

        def _bucket_from_status(status: str):
            value = str(status or "").lower()
            if "roto" in value:
                return "broken"
            if "incompatible" in value:
                return "incompatible"
            if "advertencia" in value or "warning" in value:
                return "warning"
            if "compatible" in value or "ok" in value:
                return "compatible"
            return "warning"

        def row_cb(row):
            if aborted.is_set():
                return
            values = row.get("values") or ("-", "-", "-", "-", "-", "-")
            bucket = _bucket_from_status(values[5])
            scan_counts[bucket] += 1
            scan_counts["total"] += 1
            payload = {
                "trackIndex": row.get("track_index") or 0,
                "track": values[0],
                "codec": values[1],
                "sampleRate": values[2],
                "bits": values[3],
                "bitrate": row.get("bitrate_kbps"),
                "lufs": values[4],
                "bpm": row.get("bpm") or "--",
                "bpmSource": row.get("bpm_source") or "",
                "key": row.get("key") or "--",
                "keySource": row.get("key_source") or "",
                "durationSeconds": row.get("duration_seconds"),
                "status": values[5],
                "compatibilityScore": row.get("compatibility_score"),
                "deviceSupport": row.get("device_support"),
                "deviceReasons": row.get("device_reasons"),
                "deviceGroups": _device_groups_from_support(row.get("device_support")),
                "device_groups": _device_groups_from_support(row.get("device_support")),
                "detailsText": row.get("details_text"),
                "qualityTier": row.get("quality_tier") or "STD",
                "path": str(row.get("path")) if row.get("path") else "",
                "coverImage": row.get("cover_image"),
            }
            _send_event(self, "row", payload)

        try:
            needs_ffmpeg = parsed.path in {
                "/api/scan",
                "/api/safe-set",
                "/api/convert-single",
                "/api/integrity-scan",
            }
            ffmpeg_cmd = get_ffmpeg_cmd() if needs_ffmpeg else None
            ffprobe_cmd = get_ffprobe_cmd() if needs_ffmpeg else None
            if needs_ffmpeg and (not ffmpeg_cmd or not ffprobe_cmd):
                _send_event(self, "error", {"error": "ffmpeg o ffprobe no encontrados"})
                return

            if parsed.path == "/api/scan":
                playlist_path = (params.get("path") or [""])[0].strip()
                is_demo = _parse_bool((params.get("demo") or ["0"])[0])
                no_lufs = _parse_bool((params.get("noLufs") or ["0"])[0])
                normalize_for_scan = _parse_bool((params.get("normalize") or ["1"])[0])

                if is_demo:
                    demo_folder_raw = (params.get("folder") or [r"C:\Users\rivit\Music\Temitas"])[0].strip()
                    demo_folder = Path(demo_folder_raw)
                    if not demo_folder.exists() or not demo_folder.is_dir():
                        _send_event(self, "error", {"error": f"Carpeta demo no encontrada: {demo_folder_raw}"})
                        return

                    demo_candidates = []
                    for root, _, names in os.walk(demo_folder):
                        for name in names:
                            p = Path(root) / name
                            if p.suffix.lower() in INPUT_EXTS:
                                demo_candidates.append(p)

                    if not demo_candidates:
                        _send_event(self, "error", {"error": "No hay archivos de audio en la carpeta demo"})
                        return

                    pick_count = min(5, len(demo_candidates))
                    chosen = random.sample(demo_candidates, pick_count)
                else:
                    if not playlist_path:
                        _send_event(self, "error", {"error": "Ruta faltante"})
                        return

                    resolved_input = resolve_scan_input_paths(playlist_path)
                    if not resolved_input.get("ok"):
                        _send_event(self, "error", {"error": resolved_input.get("error") or "Ruta inválida"})
                        return
                    m3u_path = resolved_input.get("m3u_path")
                    chosen = resolved_input.get("track_paths")

                report, _ = deep_scan_playlist(
                    m3u_path=None if is_demo else m3u_path,
                    ffprobe_cmd=ffprobe_cmd,
                    ffmpeg_cmd=ffmpeg_cmd,
                    fast_lufs=False,
                    enable_lufs=(not no_lufs) and normalize_for_scan,
                    enable_decode_check=True,
                    include_cover_image=is_demo,
                    track_paths=chosen,
                    collect_rows=False,
                    collect_report=False,
                    fast_scan_mode=no_lufs,
                    progress_cb=progress_cb,
                    row_cb=row_cb,
                )
                summary = {
                    "tracks": scan_counts["total"],
                    "broken": scan_counts["broken"],
                    "incompatible": scan_counts["incompatible"],
                    "warning": scan_counts["warning"],
                    "compatible": scan_counts["compatible"],
                }
                _send_event(self, "summary", summary)
                _send_event(self, "done", {"ok": True})
                return

            if parsed.path == "/api/safe-set":
                playlist_path = (params.get("path") or [""])[0].strip()
                output_dir_raw = (params.get("output") or [""])[0].strip()
                artwork_dir_raw = (params.get("artworkDir") or [""])[0].strip()
                embed_art = _parse_bool((params.get("embedArt") or [""])[0])
                overwrite = _parse_bool((params.get("overwrite") or [""])[0])
                normalize = _parse_bool((params.get("normalize") or ["1"])[0])
                resample = _parse_bool((params.get("resample") or ["1"])[0])
                lossless_format = (params.get("losslessFormat") or ["AIFF 24-bit"])[0].strip()
                lossy_format = (params.get("lossyFormat") or ["MP3 320k"])[0].strip()

                if not playlist_path:
                    _send_event(self, "error", {"error": "Ruta faltante"})
                    return

                m3u_path = Path(playlist_path)
                if not m3u_path.exists():
                    _send_event(self, "error", {"error": "Playlist no encontrada"})
                    return

                if output_dir_raw:
                    output_dir = Path(output_dir_raw)
                else:
                    base = Path(__file__).parent / "exports"
                    ensure_dir(base)
                    output_dir = base / f"Set_Ready_{safe_filename(m3u_path.stem)}"

                if output_dir.exists() and overwrite:
                    shutil.rmtree(output_dir, ignore_errors=True)
                ensure_dir(output_dir)

                artwork_dir = Path(artwork_dir_raw) if artwork_dir_raw else None
                lossless_exts = {".flac", ".wav", ".wave", ".aif", ".aiff", ".alac"}
                supported_union = set().union(*DEVICE_FORMATS.values())

                tracks_raw = parse_m3u(m3u_path)
                src_paths = [normalize_track_path(x, m3u_path.parent) for x in tracks_raw]
                ordered_existing = [p for p in src_paths if p.exists()]

                total = len(ordered_existing)
                ok = 0
                fail = 0
                final_files = []

                def _process_safe_track(idx: int, src: Path):
                    if aborted.is_set():
                        return {
                            "idx": idx,
                            "source": str(src),
                            "output": "-",
                            "status": "FAIL",
                            "message": "Proceso abortado",
                            "final_path": None,
                        }

                    ext = src.suffix.lower()
                    info = get_audio_metadata(str(src), ffprobe_cmd)
                    sr, bits, _ = get_audio_metrics(str(src), ffprobe_cmd)
                    header_err, is_float_32 = check_container_header_and_format(str(src))
                    duration_seconds = probe_duration_seconds(str(src), ffprobe_cmd)
                    has_audio_stream = bool(info)
                    is_broken = (duration_seconds is None) or (duration_seconds <= 0) or (not has_audio_stream)
                    score_raw = _classify_dj_smart_check(
                        {
                            "ext": ext,
                            "sample_rate": sr,
                            "bit_depth": bits,
                            "is_float_32": is_float_32,
                            "header_err": header_err,
                            "codec": info.get("codec_name", ""),
                        },
                        is_broken,
                    ).get("score")
                    score_value = int(score_raw) if score_raw is not None else 6

                    if is_broken or score_value <= 0:
                        return {
                            "idx": idx,
                            "source": str(src),
                            "output": "-",
                            "status": "FAIL",
                            "message": "ARCHIVO DAÑADO: SUSTITUIR FUENTE",
                            "final_path": None,
                        }

                    force_universal_mp3 = score_value < 10
                    is_lossless = (not force_universal_mp3) and (ext in lossless_exts)
                    out_ext = ".mp3" if force_universal_mp3 else (".aiff" if is_lossless else ".mp3")
                    artist, title = get_track_info(src)
                    final_name = f"{idx:02d}_{safe_filename(artist)}_{safe_filename(title)}{out_ext}"
                    final_path = output_dir / final_name

                    artwork_path = None
                    if embed_art:
                        if artwork_dir:
                            artwork_path = _find_artwork(artwork_dir, src)
                        elif has_artwork(src, ffprobe_cmd):
                            artwork_path = None

                    normalize_gain_db = 0.0
                    if normalize:
                        normalize_gain_db = compute_safe_normalize_gain_db(str(src), ffmpeg_cmd)

                    src_bit_depth = get_mutagen_bit_depth(str(src))
                    if not src_bit_depth:
                        src_info = get_audio_metadata(str(src), ffprobe_cmd)
                        src_bits_str = src_info.get("bits_per_raw_sample") or src_info.get("bits_per_sample")
                        try:
                            src_bit_depth = int(src_bits_str) if src_bits_str else None
                        except Exception:
                            src_bit_depth = None

                    cmd = _build_safe_cmd(
                        src_path=src,
                        dst_path=final_path,
                        is_lossless=is_lossless,
                        artwork_path=artwork_path,
                        ffmpeg_cmd=ffmpeg_cmd,
                        ffprobe_cmd=ffprobe_cmd,
                        normalize=normalize,
                        resample=(True if force_universal_mp3 else resample),
                        lossless_format=lossless_format,
                        lossy_format=("MP3 320k" if force_universal_mp3 else lossy_format),
                        normalize_gain_db=normalize_gain_db,
                        source_bit_depth=src_bit_depth,
                        force_triangular_dither=False,
                    )
                    try:
                        proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="ignore")
                        if proc.returncode == 0:
                            return {
                                "idx": idx,
                                "source": str(src),
                                "output": str(final_path),
                                "status": "OK",
                                "message": ("Universal MP3 320kbps 44.1kHz (LOSSY)" if force_universal_mp3 else "Converted"),
                                "final_path": final_path,
                            }
                        return {
                            "idx": idx,
                            "source": str(src),
                            "output": str(final_path),
                            "status": "FAIL",
                            "message": (proc.stderr or "").strip()[:300],
                            "final_path": None,
                        }
                    except Exception as exc:
                        return {
                            "idx": idx,
                            "source": str(src),
                            "output": str(final_path),
                            "status": "FAIL",
                            "message": str(exc),
                            "final_path": None,
                        }

                workers = _max_parallel_workers(total, io_heavy=True, profile="transcode")
                ordered_results = []
                if workers <= 1 or total <= 1:
                    for idx, src in enumerate(ordered_existing, start=1):
                        if aborted.is_set():
                            return
                        result_item = _process_safe_track(idx, src)
                        ordered_results.append(result_item)
                        progress_cb(len(ordered_results), total, src.name)
                        _send_event(self, "item", {
                            "source": result_item["source"],
                            "output": result_item["output"],
                            "status": result_item["status"],
                            "message": result_item["message"],
                        })
                else:
                    completed = 0
                    with ThreadPoolExecutor(max_workers=workers) as executor:
                        future_map = {
                            executor.submit(_process_safe_track, idx, src): (idx, src)
                            for idx, src in enumerate(ordered_existing, start=1)
                        }
                        for fut in as_completed(future_map):
                            idx, src = future_map[fut]
                            result_item = fut.result()
                            ordered_results.append(result_item)
                            completed += 1
                            progress_cb(completed, total, src.name)
                            _send_event(self, "item", {
                                "source": result_item["source"],
                                "output": result_item["output"],
                                "status": result_item["status"],
                                "message": result_item["message"],
                            })

                ordered_results.sort(key=lambda item: int(item.get("idx") or 0))
                for item in ordered_results:
                    if item.get("status") == "OK":
                        ok += 1
                        if item.get("final_path"):
                            final_files.append(item.get("final_path"))
                    else:
                        fail += 1

                out_m3u = output_dir / f"Set_Ready_{safe_filename(m3u_path.stem)}.m3u8"
                write_m3u(final_files, out_m3u)
                _send_event(self, "summary", {"ok": ok, "fail": fail, "output": str(output_dir), "m3u": str(out_m3u)})
                _send_event(self, "done", {"ok": True})
                return

            if parsed.path == "/api/convert-single":
                single_total_steps = 100

                def _single_progress(current: int, name: str):
                    current_clamped = max(0, min(single_total_steps, int(current)))
                    _send_event(self, "progress", {"current": current_clamped, "total": single_total_steps, "name": name})

                def _run_ffmpeg_with_progress(cmd: list[str], phase_name: str, start_pct: int, end_pct: int, duration_hint: float | None):
                    if len(cmd) < 2:
                        return 1, "ffmpeg command inválido"

                    progress_cmd = list(cmd)
                    output_arg = progress_cmd[-1]
                    progress_cmd = progress_cmd[:-1] + ["-progress", "pipe:1", "-nostats", output_arg]

                    try:
                        proc = subprocess.Popen(
                            progress_cmd,
                            stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE,
                            text=True,
                            encoding="utf-8",
                            errors="ignore",
                        )
                    except Exception as launch_exc:
                        return 1, str(launch_exc)

                    last_pct = start_pct
                    _single_progress(start_pct, phase_name)

                    if proc.stdout is not None:
                        for raw_line in proc.stdout:
                            line = str(raw_line or "").strip()
                            if not line.startswith("out_time_ms="):
                                continue
                            if not duration_hint or duration_hint <= 0:
                                continue
                            try:
                                out_ms = int(line.split("=", 1)[1].strip())
                            except Exception:
                                continue

                            ratio = max(0.0, min(1.0, (out_ms / 1_000_000.0) / duration_hint))
                            next_pct = int(start_pct + ratio * max(1, (end_pct - start_pct)))
                            if next_pct > last_pct:
                                last_pct = next_pct
                                _single_progress(next_pct, phase_name)

                    stderr_text = ""
                    if proc.stderr is not None:
                        stderr_text = proc.stderr.read() or ""

                    return_code = proc.wait()
                    if return_code == 0:
                        _single_progress(end_pct, phase_name)
                    return return_code, stderr_text

                src_path_raw = (params.get("path") or [""])[0].strip()
                output_raw = (params.get("output") or [""])[0].strip()
                artwork_raw = (params.get("artwork") or [""])[0].strip()
                force_aiff = _parse_bool((params.get("forceAiff") or [""])[0])
                smart_mode = _parse_bool((params.get("smart") or ["1"])[0])
                universal_target = _parse_bool((params.get("universalTarget") or ["0"])[0])
                normalize = _parse_bool((params.get("normalize") or ["0"])[0])
                resample = _parse_bool((params.get("resample") or ["0"])[0])
                lossless_format = (params.get("losslessFormat") or ["AIFF 24-bit"])[0].strip()
                lossy_format = (params.get("lossyFormat") or ["MP3 320k"])[0].strip()

                if not src_path_raw:
                    _send_event(self, "error", {"error": "Ruta faltante"})
                    return

                resolved_input = resolve_scan_input_paths(src_path_raw)
                if not resolved_input.get("ok"):
                    _send_event(self, "error", {"error": resolved_input.get("error") or "Archivo no encontrado"})
                    return

                resolved_tracks = list(resolved_input.get("track_paths") or [])
                if len(resolved_tracks) != 1:
                    _send_event(self, "error", {"error": "Selecciona un solo archivo de audio para conversión individual"})
                    return

                src_path = Path(resolved_tracks[0])

                _single_progress(5, "Preparando")
                duration_hint = probe_duration_seconds(str(src_path), ffprobe_cmd)

                artwork_path = Path(artwork_raw) if artwork_raw else None
                if force_aiff:
                    _single_progress(15, "Convirtiendo audio")
                    dst_path = Path(output_raw) if output_raw else src_path.with_name(f"{src_path.stem}_converted.aiff")
                    status, out_file, msg = convert_to_aiff_transparent(src_path, dst_path, ffmpeg_cmd, ffprobe_cmd, artwork_path)
                else:
                    src_ext = src_path.suffix.lower()
                    lossy_exts = {".mp3", ".aac", ".m4a", ".ogg"}
                    source_is_lossy = src_ext in lossy_exts
                    should_process_audio = bool(normalize or resample)

                    if universal_target:
                        _single_progress(15, "Analizando fuente")
                        decode_ok, decode_err, _, _ = decode_integrity_check(str(src_path), ffmpeg_cmd, ffprobe_cmd, max_probe_seconds=12)
                        if not decode_ok and (_is_fatal_decode_error(decode_err) or "truncado" in str(decode_err or "").lower()):
                            status, out_file, msg = "FAIL", str(src_path), "ARCHIVO DAÑADO: SUSTITUIR FUENTE"
                        else:
                            _single_progress(30, "Convirtiendo a MP3 320")
                            dst_path = Path(output_raw) if output_raw else src_path.with_name(f"{src_path.stem}_converted.mp3")

                            src_bit_depth = get_mutagen_bit_depth(str(src_path))
                            if not src_bit_depth:
                                src_info = get_audio_metadata(str(src_path), ffprobe_cmd)
                                src_bits_str = src_info.get("bits_per_raw_sample") or src_info.get("bits_per_sample")
                                try:
                                    src_bit_depth = int(src_bits_str) if src_bits_str else None
                                except Exception:
                                    src_bit_depth = None

                            cmd = _build_safe_cmd(
                                src_path=src_path,
                                dst_path=dst_path,
                                is_lossless=False,
                                artwork_path=artwork_path,
                                ffmpeg_cmd=ffmpeg_cmd,
                                ffprobe_cmd=ffprobe_cmd,
                                normalize=normalize,
                                resample=True,
                                lossless_format=lossless_format,
                                lossy_format="MP3 320k",
                                normalize_gain_db=(1.0 if normalize else 0.0),
                                source_bit_depth=src_bit_depth,
                                force_triangular_dither=False,
                            )
                            rc, stderr_text = _run_ffmpeg_with_progress(cmd, "Convirtiendo a MP3 320", 30, 80, duration_hint)
                            if rc == 0:
                                status, out_file, msg = "OK", str(dst_path), "Universal MP3 320kbps CBR 44.1kHz (LOSSY)"
                            else:
                                status, out_file, msg = "FAIL", str(dst_path), (stderr_text or "ffmpeg error").strip()[:300]
                    elif smart_mode and not should_process_audio:
                        _single_progress(35, "Copiando transparente")
                        dst_ext = src_ext or ".audio"
                        dst_path = Path(output_raw) if output_raw else src_path.with_name(f"{src_path.stem}_converted{dst_ext}")
                        status, out_file, msg = copy_audio(src_path, dst_path, ffmpeg_cmd)
                        _single_progress(80, "Copiando transparente")
                        if status == "OK":
                            msg = "Copiado transparente (sin reprocesar audio)"
                    elif smart_mode and should_process_audio:
                        _single_progress(15, "Analizando fuente")
                        if output_raw:
                            dst_path = Path(output_raw)
                            dst_is_lossless = dst_path.suffix.lower() != ".mp3"
                        else:
                            dst_path = src_path.with_name(
                                f"{src_path.stem}_converted{'.mp3' if source_is_lossy else '.aiff'}"
                            )
                            dst_is_lossless = not source_is_lossy

                        src_bit_depth = get_mutagen_bit_depth(str(src_path))
                        if not src_bit_depth:
                            src_info = get_audio_metadata(str(src_path), ffprobe_cmd)
                            src_bits_str = src_info.get("bits_per_raw_sample") or src_info.get("bits_per_sample")
                            try:
                                src_bit_depth = int(src_bits_str) if src_bits_str else None
                            except Exception:
                                src_bit_depth = None

                        _single_progress(30, "Procesando audio")
                        cmd = _build_safe_cmd(
                            src_path=src_path,
                            dst_path=dst_path,
                            is_lossless=dst_is_lossless,
                            artwork_path=artwork_path,
                            ffmpeg_cmd=ffmpeg_cmd,
                            ffprobe_cmd=ffprobe_cmd,
                            normalize=normalize,
                            resample=resample,
                            lossless_format=lossless_format,
                            lossy_format=lossy_format,
                            normalize_gain_db=(1.0 if normalize else 0.0),
                            source_bit_depth=src_bit_depth,
                            force_triangular_dither=not source_is_lossy,
                        )
                        rc, stderr_text = _run_ffmpeg_with_progress(cmd, "Procesando audio", 30, 80, duration_hint)
                        if rc == 0:
                            status, out_file, msg = "OK", str(dst_path), "Procesado con estrategia inteligente"
                        else:
                            status, out_file, msg = "FAIL", str(dst_path), (stderr_text or "ffmpeg error").strip()[:300]
                    else:
                        _single_progress(30, "Convirtiendo")
                        action, _ = classify_action(src_path, ffprobe_cmd)
                        dst_path = Path(output_raw) if output_raw else src_path.with_name(f"{src_path.stem}_converted.aiff")
                        if action == "COPY":
                            status, out_file, msg = copy_audio(src_path, dst_path, ffmpeg_cmd)
                            _single_progress(80, "Convirtiendo")
                        else:
                            status, out_file, msg = convert_to_aiff_transparent(src_path, dst_path, ffmpeg_cmd, ffprobe_cmd, artwork_path)
                            _single_progress(80, "Convirtiendo")

                if status == "OK":
                    _single_progress(90, "Preservando tags y cover")
                    try:
                        preserved_path = preserve_metadata_and_cover_from_source(
                            src_path=src_path,
                            dst_path=Path(out_file),
                            ffmpeg_cmd=ffmpeg_cmd,
                            ffprobe_cmd=ffprobe_cmd,
                        )
                        out_file = str(preserved_path)
                    except Exception as meta_exc:
                        msg = f"{msg} · Aviso metadata/cover: {meta_exc}"

                _single_progress(100, "Finalizando")

                _send_event(self, "done", {"status": status, "output": out_file, "message": msg})
                return

            if parsed.path == "/api/integrity-scan":
                folder_raw = (params.get("folder") or [""])[0].strip()
                if not folder_raw:
                    _send_event(self, "error", {"error": "Carpeta faltante"})
                    return

                folder = Path(folder_raw)
                files = []
                for root, _, names in os.walk(folder):
                    for name in names:
                        p = Path(root) / name
                        if p.suffix.lower() in INPUT_EXTS:
                            files.append(p)

                total = len(files)
                counts = {"OK": 0, "Warning": 0, "Error": 0}

                for idx, p in enumerate(files, start=1):
                    if aborted.is_set():
                        return
                    progress_cb(idx, total, p.name)
                    metrics = evaluate_hardware_compatibility(str(p), ffprobe_cmd)
                    ok, err, header_dur, _ = decode_integrity_check(str(p), ffmpeg_cmd, ffprobe_cmd)
                    if header_dur is None or header_dur <= 0:
                        metrics["issues"].append("Duracion invalida")
                    if not ok:
                        metrics["issues"].append(err)
                    metrics["severity"] = _derive_severity(metrics["issues"])
                    status = metrics["severity"]
                    counts[status] += 1

                    _send_event(self, "row", {
                        "file": str(p),
                        "status": status,
                        "sampleRate": metrics.get("sample_rate") or "-",
                        "bits": metrics.get("bit_depth") or "-",
                        "bitrate": metrics.get("bitrate_kbps") or "-",
                        "details": "; ".join(metrics.get("issues") or []),
                    })

                _send_event(self, "summary", {"ok": counts["OK"], "warning": counts["Warning"], "error": counts["Error"]})
                _send_event(self, "done", {"ok": True})
                return

            if parsed.path == "/api/covers-process":
                source_raw = (params.get("source") or [""])[0].strip()
                output_raw = (params.get("output") or [""])[0].strip()
                size_raw = (params.get("size") or [""])[0].strip()
                if not source_raw or not output_raw:
                    _send_event(self, "error", {"error": "Origen o salida faltante"})
                    return

                size = int(size_raw) if size_raw.isdigit() else ARTWORK_SIZE
                process_cover_images(Path(source_raw), Path(output_raw), size)
                _send_event(self, "done", {"ok": True, "output": output_raw})
                return

            if parsed.path == "/api/serato-clone":
                source_raw = (params.get("source") or [""])[0].strip()
                dest_raw = (params.get("dest") or [""])[0].strip()
                if not source_raw or not dest_raw:
                    _send_event(self, "error", {"error": "Origen o destino faltante"})
                    return

                source_root = Path(source_raw)
                dest_root = Path(dest_raw)
                folders = []
                for folder in SERATO_FOLDERS_TO_COPY:
                    src_folder = source_root / folder
                    if src_folder.exists():
                        folders.append((src_folder, dest_root / folder))

                if not folders:
                    _send_event(self, "error", {"error": "No hay _Serato_ o Music en origen"})
                    return

                total_bytes = 0
                for src_folder, _ in folders:
                    total_bytes += get_folder_size_bytes(src_folder)

                copied = 0
                for src_folder, dst_folder in folders:
                    for root, _, files in os.walk(src_folder):
                        for name in files:
                            src_file = Path(root) / name
                            rel = src_file.relative_to(src_folder)
                            dst_file = dst_folder / rel
                            ensure_dir(dst_file.parent)
                            try:
                                shutil.copy2(str(src_file), str(dst_file))
                                copied += src_file.stat().st_size
                            except Exception:
                                pass
                            _send_event(self, "progress", {
                                "currentBytes": copied,
                                "totalBytes": total_bytes,
                                "name": src_file.name,
                            })

                _send_event(self, "done", {"ok": True, "dest": str(dest_root)})
                return
        except BrokenPipeError:
            aborted.set()
        except Exception as exc:
            try:
                _send_event(self, "error", {"error": str(exc)})
            except Exception:
                pass

    def log_message(self, format, *args):
        return


def run_server(host: str = HOST, port: int = PORT):
    _cleanup_stale_temp_artifacts(Path(__file__).parent / "exports" / "_temp")

    try:
        warmup_root = tk.Tk()
        warmup_root.withdraw()
        warmup_root.update_idletasks()
        warmup_root.destroy()
    except Exception:
        pass

    server = ThreadingHTTPServer((host, port), CrateGuardHandler)
    print(f"CrateGuard server running on http://{host}:{port}")
    server.serve_forever()


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1].lower() in {"--server", "server"}:
        run_server()
    else:
        build_gui()
