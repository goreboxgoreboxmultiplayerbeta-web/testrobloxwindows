"""Consent-based, Roblox-only screen sender + five-button controller for Windows.

This intentionally does NOT capture or control the desktop when Roblox is not
in the foreground. F12 immediately stops it and releases any held buttons.
"""
import base64
import json
import os
import sys
import time
import threading
from pathlib import Path

import mss
import psutil
import requests
from PIL import Image
import pydirectinput
import win32api
import win32con
import win32gui
import win32process

WIDTH, HEIGHT = 96, 54
KEYS = {'up': 'w', 'down': 's', 'left': 'a', 'right': 'd', 'jump': 'space'}
POLL_INTERVAL = 0.18
FRAME_INTERVAL = 0.4       # 2.5 frames/second max
ALLOW_EXES = {'robloxplayerbeta.exe'}

pydirectinput.PAUSE = 0


def load_settings():
    path = Path(__file__).with_name('config.json')
    if not path.exists():
        print('Copy config.example.json to config.json and add URL and PC_TOKEN.')
        sys.exit(1)
    conf = json.loads(path.read_text(encoding='utf-8'))
    url = str(conf['server_url']).rstrip('/')
    token = str(conf['pc_token'])
    if not url.startswith('https://') or len(token) < 20 or 'YOUR-SERVICE' in url:
        print('Config error. Use HTTPS Render URL and PC token (20+ chars).')
        sys.exit(1)
    return url, token


def active_roblox_rectangle():
    """Restrict capture and key injection to an actual, foreground Roblox Player process."""
    hwnd = win32gui.GetForegroundWindow()
    if not hwnd or win32gui.IsIconic(hwnd):
        return None
    try:
        _, pid = win32process.GetWindowThreadProcessId(hwnd)
        exe = psutil.Process(pid).name().lower()
        if exe not in ALLOW_EXES:
            return None
        left, top = win32gui.ClientToScreen(hwnd, (0, 0))
        rc = win32gui.GetClientRect(hwnd)
        width, height = rc[2] - rc[0], rc[3] - rc[1]
        if width < 100 or height < 100:
            return None
        return {'left': left, 'top': top, 'width': width, 'height': height}
    except (psutil.Error, OSError, Exception):
        return None


def rgb332_frame(image):
    """96x54 RGB -> 5,184 palette-index bytes -> base64 (6,912 characters)."""
    image = image.convert('RGB').resize((WIDTH, HEIGHT), Image.Resampling.BILINEAR)
    rgb = image.tobytes()
    pixels = bytearray(WIDTH * HEIGHT)
    for i in range(WIDTH * HEIGHT):
        r, g, b = rgb[i * 3], rgb[i * 3 + 1], rgb[i * 3 + 2]
        pixels[i] = (r & 0xE0) | ((g & 0xE0) >> 3) | (b >> 6)
    return base64.b64encode(pixels).decode('ascii')


class Keyboard:
    def __init__(self):
        self.pressed = set()

    def change(self, wants):
        desired = {name for name in KEYS if wants.get(name) is True}
        for name in list(self.pressed - desired):
            try:
                pydirectinput.keyUp(KEYS[name])
            except Exception as exc:
                print('Release failed:', exc)
            self.pressed.remove(name)
        for name in list(desired - self.pressed):
            try:
                pydirectinput.keyDown(KEYS[name])
                self.pressed.add(name)
            except Exception as exc:
                print('Input failed:', exc)

    def release(self):
        self.change({})


def keyboard_stop_requested():
    # Hold F12 to stop. F12 is checked locally and is never sent over the network.
    return bool(win32api.GetAsyncKeyState(win32con.VK_F12) & 0x8000)


def main():
    url, token = load_settings()
    session = requests.Session()
    keyboard = Keyboard()
    started = time.monotonic()
    last_frame = 0.0
    last_report = 0.0
    print('Roblox-to-Roblox PC sender / Version 1')
    print('ONLY captures RobloxPlayerBeta.exe when Roblox is the foreground window.')
    print('ONLY receives W/A/S/D/SPACE commands; no mouse, no desktop access.')
    print('Press F12 or Ctrl+C to stop. Do not share your private PC token.')
    print('If Roblox rejects simulated keyboard input, video can work while controls do not.')
    try:
        with mss.mss() as capturer:
            while True:
                if keyboard_stop_requested():
                    print('\nStopped using F12.')
                    break
                rect = active_roblox_rectangle()
                now = time.monotonic()
                try:
                    response = session.post(
                        url + '/api/pc/controls',
                        json={'token': token}, timeout=(2, 2.5),
                    )
                    response.raise_for_status()
                    state = response.json().get('controls', {})
                except (requests.RequestException, ValueError) as exc:
                    state = {}
                    if now - last_report > 6:
                        print('Cannot fetch controls:', str(exc)[:130])
                        last_report = now
                # If Roblox loses foreground focus, ALL keys are released immediately.
                keyboard.change(state if rect else {})
                if rect and now - last_frame >= FRAME_INTERVAL:
                    try:
                        snap = capturer.grab(rect)
                        image = Image.frombytes('RGB', snap.size, snap.rgb)
                        encoded = rgb332_frame(image)
                        response = session.post(
                            url + '/api/pc/frame',
                            json={'token': token, 'frame': encoded},
                            timeout=(2, 3),
                        )
                        response.raise_for_status()
                        last_frame = now
                        if now - last_report > 8:
                            print('Screen sending. Runtime:', round(now - started), 'sec')
                            last_report = now
                    except (mss.exception.ScreenShotError, requests.RequestException, ValueError) as exc:
                        if now - last_report > 6:
                            print('Capture/upload error:', str(exc)[:130])
                            last_report = now
                elif not rect and now - last_report > 8:
                    print('Open Roblox Player and keep its window in the foreground.')
                    last_report = now
                time.sleep(POLL_INTERVAL)
    except KeyboardInterrupt:
        print('\nStopped by Ctrl+C.')
    finally:
        keyboard.release()
        session.close()
        print('All simulated keys released.')


if __name__ == '__main__':
    if os.name != 'nt':
        sys.exit('This sender supports Windows only.')
    main()
