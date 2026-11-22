import threading
import sounddevice as sd
import soundfile as sf
import numpy as np
from loguru import logger
import os
import time

# Global lock to synchronize sounddevice access, especially on Windows
# to prevent "Invalid stream pointer" or "PortAudio" crashes during concurrent init.
_sd_lock = threading.Lock()
_FILE_READY_TIMEOUT_SEC = 1.0
_FILE_READY_POLL_INTERVAL_SEC = 0.025

def play_audio_to_device(file_path: str, device_names, remove_after: bool = False):
    """
    Plays an audio file to one or more output devices in separate threads.
    Does so asynchronously to avoid blocking the main thread.
    """
    # Note: We NO LONGER check for file existence here synchronously.
    # We move it to the background thread to handle disk write latency.

    if not device_names:
        if remove_after and os.path.exists(file_path):
            try: os.remove(file_path)
            except: pass
        return

    # Convert single string to list
    if isinstance(device_names, str):
        valid_device_names = [device_names]
    else:
        valid_device_names = device_names

    # Start a single background thread that will handle file reading and device resolution
    # daemon=True ensures threads don't block application exit
    thread = threading.Thread(
        target=_background_play_handler, 
        args=(file_path, valid_device_names, remove_after), 
        daemon=True
    )
    thread.start()

def _background_play_handler(file_path: str, device_names: list[str], remove_after: bool = False):
    """Background handler to read file and query devices without blocking main loop"""
    try:
        data, fs = _read_audio_file_when_ready(file_path)
    except RuntimeError as exc:
        logger.error(f"Audio playback failed: {exc}")
        return

    try:
        devices = sd.query_devices()
    except Exception as e:
        logger.error(f"Failed to query audio devices: {e}")
        return

    valid_configs = []
    
    for device_name in device_names:
        device_index = None
        for i, dev in enumerate(devices):
            if device_name.lower() in dev['name'].lower() and dev['max_output_channels'] > 0:
                device_index = i
                break
        
        if device_index is not None:
            valid_configs.append((device_name, device_index))
        else:
            logger.warning(f"Audio device '{device_name}' not found. Skipping mirroring.")

    # Start playback on each valid device
    try:
        for name, index in valid_configs:
            try:
                logger.debug(f"Starting playback chunk for device {name} (index {index})")
                _play_from_memory(data, fs, index, name)
            except Exception as e:
                logger.error(f"Playback failed for {name}: {e}")
    finally:
        if remove_after and os.path.exists(file_path):
            try:
                os.remove(file_path)
                logger.debug(f"Audio cache file cleaned after local playback: {file_path}")
            except Exception as e:
                logger.warning(f"Failed to clean audio cache file {file_path}: {e}")

def _play_from_memory(data: np.ndarray, samplerate: int, device_index: int, name: str):
    """Playback implementation using data already in memory"""
    try:
        # Keep the complete PortAudio operation serialized on Windows. sd.wait()
        # uses global stream state, so another sd.play() must not replace it midway.
        with _sd_lock:
            sd.play(data, samplerate, device=device_index)
            sd.wait()
    except Exception as e:
        error_msg = str(e)
        if "-9988" in error_msg:
            logger.warning(
                f"Audio error -9988 (Invalid stream pointer) on device {name}. "
                "This usually happens if the device is busy or just became available. Skipping."
            )
        else:
            logger.error(f"Error playing on device {name} (index {device_index}): {e}")


def _read_audio_file_when_ready(
    file_path: str,
    timeout_sec: float = _FILE_READY_TIMEOUT_SEC,
    poll_interval_sec: float = _FILE_READY_POLL_INTERVAL_SEC,
):
    """
    Poll until the file becomes readable instead of sleeping a fixed delay.
    This removes avoidable startup latency while still tolerating Windows write lag.
    """
    deadline = time.perf_counter() + timeout_sec
    last_error = None
    attempt = 0

    while time.perf_counter() < deadline:
        attempt += 1
        if os.path.exists(file_path):
            try:
                return sf.read(file_path)
            except Exception as exc:
                last_error = exc
                logger.debug(f"Audio file exists but is not ready (attempt {attempt}): {exc}")
        time.sleep(poll_interval_sec)

    error_detail = f" Last error: {last_error}" if last_error else ""
    raise RuntimeError(f"File not found or unreadable at {file_path}.{error_detail}")

def list_output_devices():
    """Returns a list of output device names"""
    try:
        return [d['name'] for d in sd.query_devices() if d['max_output_channels'] > 0]
    except Exception:
        return []
