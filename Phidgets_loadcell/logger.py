import os
os.environ["GPIOZERO_PIN_FACTORY"] = "lgpio"

import csv
import time
from datetime import datetime
from gpiozero import Button, LED
from Phidget22.Phidget import *
from Phidget22.Devices.VoltageRatioInput import *

# --- Configuration ---
HUB_PORT = 0               # VINT Hub port (0-5)
DATA_INTERVAL_MS = 50      # 50 ms = 20 Hz sampling rate
BUTTON_PIN = 17            # GPIO 17 (Pin 11)
LED_PIN = 27               # GPIO 27 (Pin 13)

# --- State ---
logging_active = False
csv_file = None
csv_writer = None

led = LED(LED_PIN)
button = Button(BUTTON_PIN, bounce_time=0.2)

def toggle_logging():
    global logging_active, csv_file, csv_writer

    if not logging_active:
        filename = f"load_cell_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
        csv_file = open(filename, mode="w", newline="")
        csv_writer = csv.writer(csv_file)
        csv_writer.writerow(["timestamp_iso", "elapsed_s", "voltage_ratio_v_v"])
        toggle_logging.start_time = time.monotonic()
        logging_active = True
        led.on()
        print(f"[STARTED] Recording to {filename}")
    else:
        logging_active = False
        led.off()
        if csv_file:
            csv_file.flush()
            csv_file.close()
            csv_file = None
            csv_writer = None
        print("[STOPPED] File saved and closed.")

toggle_logging.start_time = 0.0
button.when_pressed = toggle_logging

def on_voltage_ratio_change(self, voltage_ratio):
    if logging_active and csv_writer is not None:
        now_iso = datetime.now().isoformat()
        elapsed = round(time.monotonic() - toggle_logging.start_time, 4)
        csv_writer.writerow([now_iso, elapsed, f"{voltage_ratio:.8f}"])

def main():
    ch = VoltageRatioInput()
    ch.setHubPort(HUB_PORT)
    ch.setIsHubPortDevice(False)
    ch.setOnVoltageRatioChangeHandler(on_voltage_ratio_change)

    print("Waiting for Phidget Wheatstone Bridge...")
    ch.openWaitForAttachment(5000)

    ch.setBridgeGain(VoltageRatioInputBridgeGain.GAIN_128X)
    ch.setDataInterval(DATA_INTERVAL_MS)

    print("Connected! Press the button to start/stop logging. Press Ctrl+C to exit.")
    try:
        while True:
            time.sleep(0.5)
    except KeyboardInterrupt:
        print("\nShutting down...")
    finally:
        if logging_active and csv_file:
            csv_file.flush()
            csv_file.close()
        led.off()
        ch.close()

if __name__ == "__main__":
    main()
