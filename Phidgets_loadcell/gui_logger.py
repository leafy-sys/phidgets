import os
os.environ["GPIOZERO_PIN_FACTORY"] = "lgpio"

import csv
import threading
import time
import tkinter as tk
from collections import deque
from datetime import datetime

from gpiozero import Button, LED
from Phidget22.Phidget import *
from Phidget22.Devices.VoltageRatioInput import *
from Phidget22.BridgeGain import BridgeGain

# --- Configuration ---
HUB_PORT = 0
CHANNEL = 0
DATA_INTERVAL_MS = 50
BUTTON_PIN = 17          # BCM numbering (GPIO17)
LED_PIN = 27             # BCM numbering (GPIO27)
BRIDGE_GAIN = BridgeGain.BRIDGE_GAIN_128

# Calibration: change in voltage ratio per pound of load.
# Negative because more weight gives a lower reading on your cell.
# This came from the 1/3/5 lb test run. Replace it with your own fit.
RATIO_PER_LB = -1.832e-6
KG_PER_LB = 0.45359237

TARE_SECONDS = 1.0       # tare averages this much recent data
GRAPH_SECONDS = 30       # width of the live graph's time window
GUI_REFRESH_MS = 100

# --- Look ---
BG = "#F2F4F5"
INK = "#1B2A33"
MUTED = "#5B6B75"
TRACE = "#0B7285"
REC_RED = "#C92A2A"
BTN_BG = "#DDE3E7"

# --- Shared state (touched by the Phidget thread, the GPIO thread, and the GUI) ---
lock = threading.Lock()
history = deque(maxlen=int(GRAPH_SECONDS * 1000 / DATA_INTERVAL_MS) + 20)  # (time, ratio)
zero_ratio = None        # voltage ratio that counts as 0 weight (set by tare)
recording = False
samples = []             # (elapsed_seconds, voltage_ratio) while recording
start_time = None
error_text = ""
error_time = 0.0


def ratio_to_lb(ratio, zero):
    """Convert a raw voltage ratio to pounds, relative to the tare point."""
    return (ratio - zero) / RATIO_PER_LB


# --- Phidget callbacks (run on the Phidget thread) ---
def on_voltage_ratio_change(ch, ratio):
    now = time.monotonic()
    with lock:
        history.append((now, ratio))
        if recording:
            samples.append((now - start_time, ratio))


def on_error(ch, code, description):
    global error_text, error_time
    with lock:
        error_text = description
        error_time = time.monotonic()


# --- Tare ---
def tare():
    """Set zero to the average of the last TARE_SECONDS of readings.
    Returns False if it can't tare right now (recording, or not enough data yet)."""
    global zero_ratio
    min_needed = int(0.8 * TARE_SECONDS * 1000 / DATA_INTERVAL_MS)
    with lock:
        if recording:
            return False
        cutoff = time.monotonic() - TARE_SECONDS
        recent = [r for t, r in history if t >= cutoff]
        if len(recent) < min_needed:
            return False
        zero_ratio = sum(recent) / len(recent)
        return True


# --- Recording ---
def save_samples(data, zero):
    filename = datetime.now().strftime("loadcell_%Y%m%d_%H%M%S.csv")
    with open(filename, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["elapsed_s", "voltage_ratio", "weight_lb", "weight_kg"])
        for t, ratio in data:
            lb = ratio_to_lb(ratio, zero)
            writer.writerow([f"{t:.4f}", ratio, f"{lb:.4f}", f"{lb * KG_PER_LB:.4f}"])
    print(f"Saved {len(data)} samples to {filename}")


def toggle_recording():
    """Called by the physical button (GPIO thread) and the on-screen button (GUI thread).
    It never touches a tkinter widget, so it is safe to call from either."""
    global recording, samples, start_time
    data_to_save = None
    zero = None
    with lock:
        if not recording:
            if zero_ratio is None:
                print("Not ready yet: the scale hasn't been zeroed.")
                return
            samples = []
            start_time = time.monotonic()
            recording = True
        else:
            recording = False
            data_to_save = samples
            zero = zero_ratio
            samples = []

    if data_to_save is None:
        led.on()
        print("Recording started...")
    else:
        led.off()
        print("Recording stopped.")
        save_samples(data_to_save, zero)


# --- GUI ---
class App:
    def __init__(self, root):
        self.root = root
        self.units = "lb"

        root.title("Load cell")
        root.configure(bg=BG)
        root.attributes("-fullscreen", True)
        root.bind("<Escape>", lambda e: self.quit())
        root.protocol("WM_DELETE_WINDOW", self.quit)
        root.columnconfigure(0, weight=1)
        root.rowconfigure(1, weight=1)   # the graph takes any spare space

        self.readout = tk.Label(root, text="--", font=("Helvetica", 54, "bold"),
                                bg=BG, fg=INK)
        self.readout.grid(row=0, column=0, sticky="ew", pady=(6, 0))

        self.canvas = tk.Canvas(root, bg=BG, highlightthickness=0)
        self.canvas.grid(row=1, column=0, sticky="nsew", padx=8)

        self.status = tk.Label(root, text="Zeroing...", font=("Helvetica", 13),
                               bg=BG, fg=MUTED)
        self.status.grid(row=2, column=0, sticky="ew")

        bar = tk.Frame(root, bg=BG)
        bar.grid(row=3, column=0, sticky="ew", padx=8, pady=8)
        for i in range(4):
            bar.columnconfigure(i, weight=1, uniform="buttons")

        def make_button(col, text, command):
            b = tk.Button(bar, text=text, command=command, font=("Helvetica", 16, "bold"),
                          bg=BTN_BG, fg=INK, activebackground=BTN_BG, relief="flat",
                          height=2)
            b.grid(row=0, column=col, sticky="ew", padx=3)
            return b

        self.tare_btn = make_button(0, "Tare", tare)
        self.units_btn = make_button(1, "Units: lb", self.toggle_units)
        self.rec_btn = make_button(2, "Start", toggle_recording)
        make_button(3, "Quit", self.quit)

    def quit(self):
        shutdown(self.root)

    def toggle_units(self):
        self.units = "kg" if self.units == "lb" else "lb"
        self.units_btn.config(text=f"Units: {self.units}")

    def refresh(self):
        """Runs every GUI_REFRESH_MS on the GUI thread; reads shared state, updates widgets."""
        now = time.monotonic()
        with lock:
            zero = zero_ratio
            rec = recording
            n_rec = len(samples)
            points = [(t, r) for t, r in history if now - t <= GRAPH_SECONDS]
            err = error_text if now - error_time < 3 else ""

        if zero is None:
            tare()   # zero automatically once a second of data has arrived
            self.readout.config(text="--", fg=INK)
        elif points:
            # average the last few samples so the digits don't flicker
            recent = [r for _, r in points[-4:]]
            lb = ratio_to_lb(sum(recent) / len(recent), zero)
            value = lb if self.units == "lb" else lb * KG_PER_LB
            self.readout.config(text=f"{value:.2f} {self.units}",
                                fg=REC_RED if rec else INK)

        if err:
            self.status.config(text=f"Sensor error: {err}", fg=REC_RED)
        elif rec:
            self.status.config(text=f"Recording: {n_rec} samples", fg=REC_RED)
        elif zero is None:
            self.status.config(text="Zeroing... keep the cell unloaded", fg=MUTED)
        else:
            self.status.config(text="Ready", fg=MUTED)

        self.rec_btn.config(text="Stop" if rec else "Start")
        self.tare_btn.config(state="disabled" if rec else "normal")

        self.draw_graph(points, zero, now)
        self.root.after(GUI_REFRESH_MS, self.refresh)

    def draw_graph(self, points, zero, now):
        c = self.canvas
        c.delete("all")
        w, h = c.winfo_width(), c.winfo_height()
        if zero is None or len(points) < 2 or w < 100 or h < 60:
            return

        k = 1.0 if self.units == "lb" else KG_PER_LB
        values = [ratio_to_lb(r, zero) * k for _, r in points]
        lo, hi = min(values), max(values)
        if hi - lo < 1.0:                       # keep a sensible minimum scale
            mid = (hi + lo) / 2
            lo, hi = mid - 0.5, mid + 0.5
        else:
            pad = (hi - lo) * 0.1
            lo, hi = lo - pad, hi + pad

        left, right, top, bottom = 58, 8, 8, 8

        def x_of(t):
            return left + (t - (now - GRAPH_SECONDS)) / GRAPH_SECONDS * (w - left - right)

        def y_of(v):
            return h - bottom - (v - lo) / (hi - lo) * (h - top - bottom)

        # zero line and the top/bottom scale labels
        if lo < 0 < hi:
            c.create_line(left, y_of(0), w - right, y_of(0), fill=MUTED, dash=(4, 4))
            c.create_text(left - 6, y_of(0), text="0", anchor="e", fill=MUTED,
                          font=("Helvetica", 11))
        c.create_text(left - 6, top + 6, text=f"{hi:.1f}", anchor="e", fill=MUTED,
                      font=("Helvetica", 11))
        c.create_text(left - 6, h - bottom - 6, text=f"{lo:.1f}", anchor="e", fill=MUTED,
                      font=("Helvetica", 11))
        c.create_line(left, top, left, h - bottom, fill=MUTED)

        coords = []
        for (t, _), v in zip(points, values):
            coords += [x_of(t), y_of(v)]
        c.create_line(*coords, fill=TRACE, width=2)


# --- Hardware setup ---
led = LED(LED_PIN)
button = Button(BUTTON_PIN, bounce_time=0.05)   # button between GPIO17 and GND
button.when_pressed = toggle_recording

ch = VoltageRatioInput()
ch.setHubPort(HUB_PORT)
ch.setChannel(CHANNEL)
ch.setOnVoltageRatioChangeHandler(on_voltage_ratio_change)
ch.setOnErrorHandler(on_error)
ch.openWaitForAttachment(5000)

ch.setBridgeEnabled(True)
ch.setBridgeGain(BRIDGE_GAIN)
ch.setDataInterval(DATA_INTERVAL_MS)
ch.setVoltageRatioChangeTrigger(0)


# --- Shutdown (wired to the Quit button, Esc, and the window close) ---
def shutdown(root):
    global recording
    with lock:
        leftover = samples if recording else None
        zero = zero_ratio
        recording = False
    if leftover:
        save_samples(leftover, zero)
    led.off()
    ch.close()
    root.destroy()


root = tk.Tk()
app = App(root)
app.refresh()
root.mainloop()
