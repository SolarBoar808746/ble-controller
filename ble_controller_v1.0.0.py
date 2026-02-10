import tkinter as tk
from tkinter import colorchooser, ttk
import asyncio
import threading
import numpy as np
import mss
import time
from bleak import BleakClient
import sys
import os

# --- CONFIGURATION ---
DEVICE_ADDRESS = "BE:27:5F:00:13:87"
WRITE_UUID = "0000fff3-0000-1000-8000-00805f9b34fb"

def resource_path(relative_path):
    """ Get absolute path to resource, works for dev and for PyInstaller """
    try:
        # PyInstaller creates a temp folder and stores path in _MEIPASS
        base_path = sys._MEIPASS
    except Exception:
        base_path = os.path.abspath(".")
    return os.path.join(base_path, relative_path)

class LEDControllerPro:
    def __init__(self, root):
        self.root = root
        self.root.title("ELK-BLEDOM Color Engine")
        self.root.geometry("400x850")

        try:
            # We call the function here to find the absolute path of led.ico
            icon_path = resource_path("led.ico")
            self.root.iconbitmap(icon_path)
        except Exception as e:
            print(f"Icon error: {e}")

        # --- STATE ---
        self.client = None
        self.is_on = True
        self.screen_sync_active = False
        self.dark_mode = True
        self.boost_mode = False 
        self.sync_strategy = "Average" # "Average" or "Dominant"
        
        self.manual_rgb = (255, 255, 255)
        self.manual_brightness = 100
        self.current_displayed_rgb = [0.0, 0.0, 0.0]

        self.loop = asyncio.new_event_loop()
        threading.Thread(target=self._run_async_loop, daemon=True).start()
        
        self.setup_ui()
        self.apply_theme()

    def setup_ui(self):
        # Top Bar
        self.top_frame = tk.Frame(self.root)
        self.top_frame.pack(fill="x", padx=10, pady=5)
        self.theme_btn = tk.Button(self.top_frame, text="🌙 Dark Mode", command=self.toggle_theme, font=("Arial", 8))
        self.theme_btn.pack(side="right")

        self.pwr_btn = tk.Button(self.root, text="POWER ON", bg="#2ecc71", fg="white", font=("Arial", 12, "bold"), command=self.toggle_power)
        self.pwr_btn.pack(pady=15, fill="x", padx=50)

        # --- SYNC SETTINGS ---
        sync_frame = tk.LabelFrame(self.root, text="Sync Strategy & Color Engine", font=("Arial", 10, "bold"))
        sync_frame.pack(pady=10, fill="x", padx=20)
        
        self.sync_btn = tk.Button(sync_frame, text="START LEFT SYNC", bg="#3498db", fg="white", font=("Arial", 10, "bold"), command=self.toggle_screen_sync)
        self.sync_btn.pack(pady=10, fill="x", padx=30)

        # STRATEGY TOGGLE
        self.strat_btn = tk.Button(sync_frame, text="Strategy: Average (Balanced)", bg="#9b59b6", fg="white", command=self.toggle_strategy)
        self.strat_btn.pack(pady=5, fill="x", padx=30)
        
        self.boost_btn = tk.Button(sync_frame, text="Brightness: Natural", command=self.toggle_boost_mode, bg="#95a5a6", fg="white")
        self.boost_btn.pack(pady=5, fill="x", padx=30)

        # VIBRANCY (Saturation Boost)
        tk.Label(sync_frame, text="Vibrancy (Saturation Boost)").pack(pady=(10, 0))
        self.vib_slider = ttk.Scale(sync_frame, from_=1.0, to=3.0, orient="horizontal")
        self.vib_slider.set(1.2)
        self.vib_slider.pack(pady=5, fill="x", padx=30)

        # FLOW & TEMP
        tk.Label(sync_frame, text="Flow / Smoothness").pack(pady=(5, 0))
        self.smooth_slider = ttk.Scale(sync_frame, from_=0.05, to=1.0, orient="horizontal")
        self.smooth_slider.set(0.15)
        self.smooth_slider.pack(pady=5, fill="x", padx=30)

        tk.Label(sync_frame, text="Warm ← Temperature → Cool").pack(pady=(5, 0))
        self.temp_slider = ttk.Scale(sync_frame, from_=-0.5, to=0.5, orient="horizontal")
        self.temp_slider.set(0.0)
        self.temp_slider.pack(pady=5, fill="x", padx=30)

        # --- PREVIEW & MANUAL ---
        self.color_preview = tk.Canvas(self.root, width=60, height=60, highlightthickness=2, bg="white")
        self.color_preview.pack(pady=10)
        
        tk.Button(self.root, text="Manual Color", command=self.pick_color).pack(pady=5)
        self.bright_slider = ttk.Scale(self.root, from_=0, to=100, orient="horizontal", command=self.on_brightness_change)
        self.bright_slider.set(100); self.bright_slider.pack(pady=5, fill="x", padx=50)

        self.status = tk.Label(self.root, text="Disconnected", bd=1, relief="sunken", anchor="w")
        self.status.pack(side="bottom", fill="x")

    def toggle_strategy(self):
        if self.sync_strategy == "Average":
            self.sync_strategy = "Dominant"
            self.strat_btn.config(text="Strategy: Dominant (Vivid)", bg="#e91e63")
        else:
            self.sync_strategy = "Average"
            self.strat_btn.config(text="Strategy: Average (Balanced)", bg="#9b59b6")

    def apply_vibrancy(self, r, g, b):
        """Boosts saturation by pushing colors away from the average brightness."""
        factor = self.vib_slider.get()
        avg = (r + g + b) / 3
        r = avg + (r - avg) * factor
        g = avg + (g - avg) * factor
        b = avg + (b - avg) * factor
        return np.clip([r, g, b], 0, 255)

    def screen_sync_loop(self):
        with mss.mss() as sct:
            mon = sct.monitors[1]
            capture_area = {"top": mon["top"], "left": mon["left"], "width": mon["width"] // 2, "height": mon["height"]}
            
            while self.screen_sync_active:
                sct_img = sct.grab(capture_area)
                img = np.array(sct_img)[:,:,:3] # Get RGB, ignore Alpha
                pixels = img[::20, ::20].reshape(-1, 3) # Heavy downsample for speed

                if self.sync_strategy == "Dominant":
                    # Use Median for 'Winning' color instead of 'Mixed' color
                    raw_color = np.median(pixels, axis=0)
                else:
                    # Traditional Blend
                    raw_color = np.mean(pixels, axis=0)
                
                b, g, r = raw_color[0], raw_color[1], raw_color[2]

                # 1. Temperature Bias
                bias = self.temp_slider.get()
                if bias > 0: # Cool
                    r *= (1.0 - bias); b *= (1.0 + bias)
                else: # Warm
                    r *= (1.0 + abs(bias)); b *= (1.0 - abs(bias))

                # 2. Vibrancy Boost
                r, g, b = self.apply_vibrancy(r, g, b)

                # 3. Brightness Boost
                mult = 2.5 if self.boost_mode else 1.0
                r, g, b = np.clip([r*mult, g*mult, b*mult], 0, 255)

                # 4. Smoothing
                f = self.smooth_slider.get()
                self.current_displayed_rgb[0] += (r - self.current_displayed_rgb[0]) * f
                self.current_displayed_rgb[1] += (g - self.current_displayed_rgb[1]) * f
                self.current_displayed_rgb[2] += (tb - self.current_displayed_rgb[2]) * f if 'tb' in locals() else 0 # fix below
                # Fixing variable name typo in smoothing math:
                self.current_displayed_rgb[2] += (b - self.current_displayed_rgb[2]) * f

                self.send_rgb_packet(int(self.current_displayed_rgb[0]), int(self.current_displayed_rgb[1]), int(self.current_displayed_rgb[2]))
                self.root.after(0, lambda: self.color_preview.config(bg=f'#{int(self.current_displayed_rgb[0]):02x}{int(self.current_displayed_rgb[1]):02x}{int(self.current_displayed_rgb[2]):02x}'))
                time.sleep(0.05)

    # --- REUSE PREVIOUS HELPER METHODS ---
    def toggle_boost_mode(self):
        self.boost_mode = not self.boost_mode
        self.boost_btn.config(text="Brightness: Boosted" if self.boost_mode else "Brightness: Natural", bg="#e67e22" if self.boost_mode else "#95a5a6")
    def toggle_screen_sync(self):
        self.screen_sync_active = not self.screen_sync_active
        if self.screen_sync_active:
            self.sync_btn.config(text="STOP SYNC", bg="#f39c12")
            threading.Thread(target=self.screen_sync_loop, daemon=True).start()
        else:
            self.sync_btn.config(text="START LEFT SYNC", bg="#3498db"); self.restore_manual_settings()
    def restore_manual_settings(self):
        if not self.is_on: return
        r, g, b = self.manual_rgb
        self.send_brightness_packet(self.manual_brightness); time.sleep(0.05); self.send_rgb_packet(r, g, b)
    def pick_color(self):
        color = colorchooser.askcolor(); 
        if color[0]: self.manual_rgb = [int(x) for x in color[0]]; self.send_rgb_packet(*self.manual_rgb)
    def on_brightness_change(self, _=None):
        self.manual_brightness = int(self.bright_slider.get()); self.send_brightness_packet(self.manual_brightness)
    def toggle_power(self):
        self.is_on = not self.is_on
        packet = bytearray([0x7e, 0x00, 0x04, 0x01 if self.is_on else 0x00, 0x00, 0x00, 0x00, 0x00, 0xef])
        self.pwr_btn.config(bg="#2ecc71" if self.is_on else "#e74c3c"); asyncio.run_coroutine_threadsafe(self._send_command(packet), self.loop)
    def send_rgb_packet(self, r, g, b):
        packet = bytearray([0x7e, 0x00, 0x05, 0x03, r, g, b, 0x00, 0xef]); asyncio.run_coroutine_threadsafe(self._send_command(packet), self.loop)
    def send_brightness_packet(self, val):
        packet = bytearray([0x7e, 0x00, 0x01, val, 0x00, 0x00, 0x00, 0x00, 0xef]); asyncio.run_coroutine_threadsafe(self._send_command(packet), self.loop)
    def _run_async_loop(self):
        asyncio.set_event_loop(self.loop); self.loop.run_forever()
    async def _send_command(self, packet):
        try:
            if not self.client or not self.client.is_connected:
                self.client = BleakClient(DEVICE_ADDRESS); await self.client.connect()
                self.root.after(0, lambda: self.status.config(text="Connected", fg="#2ecc71"))
            await self.client.write_gatt_char(WRITE_UUID, packet)
        except: pass
    def toggle_theme(self):
        self.dark_mode = not self.dark_mode; self.apply_theme()
    def apply_theme(self):
        bg = "#1e1e1e" if self.dark_mode else "#f0f0f0"; fg = "#ffffff" if self.dark_mode else "#000000"
        self.root.config(bg=bg); self.top_frame.config(bg=bg); self.status.config(bg=bg, fg=fg)

if __name__ == "__main__":
    root = tk.Tk(); app = LEDControllerPro(root); root.mainloop()