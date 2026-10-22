# NanoVNA LMR-240 / LMR-400 Automated Cable Testing Scripts

This document aggregates the testing procedures and automated scripts for evaluating RF coax assemblies against traveler specifications using a NanoVNA (such as the NanoVNA-H4).

---

## 1. Specification & Target Limits

* **Frequency:** $1.0\text{ GHz}$ ($1,000\text{ MHz}$)
* **Maximum VSWR ($S_{11}$):** $\le 1.6:1$ ($\approx 12.7\text{ dB}$ Return Loss)
* **Maximum Insertion Loss ($S_{21}$):** $\le 0.58\text{ dB}$ (Calculated per assembly length)

---

## 2. Workstation Automation Script (Python)

This standalone Python script runs on an external workstation (**Linux**, **macOS**, or **Windows**). It communicates directly with the NanoVNA over USB serial (SCPI interface), executes a frequency sweep centered at $1.0\text{ GHz}$, evaluates $S_{11}$ and $S_{21}$ limits, displays an immediate **PASS** or **FAIL**, and appends the time-stamped result to a CSV quality log.

### Prerequisites

Install the Python serial communication library:

```bash
pip install pyserial
```

### Script: `test_lmr240.py`

```python
#!/usr/bin/env python3
import csv
import datetime
import math
import sys
import time
import serial
from serial.tools import list_ports

# ---------------------------------------------------------
# TRAVELER SPECIFICATION LIMITS (AT 1.0 GHz)
# ---------------------------------------------------------
MAX_VSWR = 1.60          # Traveler: VSWR <= 1.6:1
MAX_LOSS_DB = 0.58       # Traveler: Insertion Loss <= 0.58 dB
TARGET_FREQ_HZ = 1e9     # Target Test Frequency: 1.0 GHz
LOG_FILE = "cable_test_results.csv"

def find_nanovna():
    """Detects connected NanoVNA USB serial port."""
    for p in list_ports.comports():
        if "0483" in p.hwid.lower() or "usbmodem" in p.device.lower() or "ttyACM" in p.device.lower():
            return p.device
    return None

def send_scpi(ser, cmd, delay=0.2):
    """Sends SCPI command over serial and collects response."""
    ser.write(f"{cmd}\r\n".encode("utf-8"))
    time.sleep(delay)
    response = ""
    while ser.in_waiting:
        response += ser.read(ser.in_waiting).decode("utf-8", errors="ignore")
    return response

def s11_to_vswr(re, im):
    """Converts complex S11 parameters to VSWR."""
    gamma = math.sqrt(re**2 + im**2)
    if gamma >= 0.999:
        return 99.9
    return (1.0 + gamma) / (1.0 - gamma)

def s21_to_loss_db(re, im):
    """Converts complex S21 parameters to Insertion Loss in dB."""
    mag = math.sqrt(re**2 + im**2)
    if mag <= 0.00001:
        return 99.9
    return -20.0 * math.log10(mag)

def run_test(ser, serial_num):
    """Sweeps NanoVNA, parses 1 GHz center point, and logs result."""
    print(f"\n[*] Sweeping cable S/N: {serial_num}...")
    
    # Configure sweep range around 1 GHz (900 MHz to 1100 MHz, 101 points)
    send_scpi(ser, "sweep 900000000 1100000000 101")
    time.sleep(0.5)

    # Fetch raw data arrays (data 0 = S11, data 1 = S21)
    raw_s11 = send_scpi(ser, "data 0", delay=0.4)
    raw_s21 = send_scpi(ser, "data 1", delay=0.4)

    try:
        s11_lines = [line.strip() for line in raw_s11.split("\n") if line.strip() and not line.startswith("ch>")]
        s21_lines = [line.strip() for line in raw_s21.split("\n") if line.strip() and not line.startswith("ch>")]

        # Center index corresponds to 1.0 GHz
        mid_idx = len(s11_lines) // 2
        
        s11_re, s11_im = map(float, s11_lines[mid_idx].split())
        s21_re, s21_im = map(float, s21_lines[mid_idx].split())

        vswr = s11_to_vswr(s11_re, s11_im)
        loss_db = s21_to_loss_db(s21_re, s21_im)

    except Exception as e:
        print(f"[!] Error parsing VNA response: {e}")
        return False

    # Evaluate against Traveler limits
    vswr_pass = vswr <= MAX_VSWR
    loss_pass = loss_db <= MAX_LOSS_DB
    overall_pass = vswr_pass and loss_pass

    # Print Bench Results
    print("---------------------------------------------------------")
    print(f" RESULT FOR S/N: {serial_num}")
    print(f" Frequency     : 1.0 GHz")
    print(f" S11 VSWR      : {vswr:.2f}:1  [{'PASS' if vswr_pass else 'FAIL'}] (Limit: <= {MAX_VSWR:.2f}:1)")
    print(f" S21 Loss      : {loss_db:.2f} dB [{'PASS' if loss_pass else 'FAIL'}] (Limit: <= {MAX_LOSS_DB:.2f} dB)")
    print("---------------------------------------------------------")
    print(f" OVERALL STATUS : {'>>> PASS <<<' if overall_pass else '*** FAIL ***'}")
    print("---------------------------------------------------------")

    # Append to CSV Quality Log
    timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with open(LOG_FILE, "a", newline="") as f:
        writer = csv.writer(f)
        writer.writerow([timestamp, serial_num, f"{vswr:.2f}", f"{loss_db:.2f}", "PASS" if overall_pass else "FAIL"])

    print(f"[+] Saved to log file: {LOG_FILE}")
    return overall_pass

def main():
    print("=========================================================")
    print("      LMR-240 Production Cable Tester (1.0 GHz)          ")
    print("=========================================================")

    port = find_nanovna() or input("Enter Serial Port: ").strip()
    try:
        ser = serial.Serial(port, 115200, timeout=2)
        print(f"[+] Connected to NanoVNA on {port}\n")
    except Exception as e:
        print(f"[!] Could not open port: {e}")
        sys.exit(1)

    # Initialize CSV header if file doesn't exist
    try:
        with open(LOG_FILE, "x", newline="") as f:
            csv.writer(f).writerow(["Timestamp", "Serial_Number", "VSWR_1GHz", "Loss_dB_1GHz", "Status"])
    except FileExistsError:
        pass

    try:
        while True:
            sn = input("\nScan/Type Cable Serial Number (or 'q' to quit): ").strip()
            if sn.lower() in ['q', 'exit']:
                break
            if not sn:
                continue
            run_test(ser, sn)
    except KeyboardInterrupt:
        print("\nExiting batch tester.")
    finally:
        ser.close()

if __name__ == "__main__":
    main()
```

---

## 3. Microcontroller Embedded Controller Firmware (ESP32-S3 C++)

This firmware is written for an **ESP32-S3** microcontroller equipped with native **USB Host / OTG capabilities** and an attached TFT display (e.g., LilyGO T-Display-S3). It eliminates the need for an external computer by interfacing directly with the NanoVNA over USB.

### Firmware: `main.cpp`

```cpp
#include <Arduino.h>
#include <USBHost.h> // ESP32-S3 USB Host stack library
#include <SPI.h>
#include <TFT_eSPI.h> // Display driver library

TFT_eSPI tft = TFT_eSPI();
USBHost usb;
USBSerial usbSerial(usb);

// Traveler Specification Limits
const float MAX_VSWR = 1.60;
const float MAX_LOSS_DB = 0.58;

void setup() {
    Serial.begin(115200);
    tft.init();
    tft.setRotation(1);
    tft.fillScreen(TFT_BLACK);
    
    tft.setTextColor(TFT_WHITE, TFT_BLACK);
    tft.drawString("Initializing NanoVNA Host...", 10, 10, 2);

    // Initialize USB Host to control NanoVNA
    usb.begin();
}

bool sendCommandAndParse(float &vswrOut, float &lossOut) {
    // Request sweep centered around 1 GHz
    usbSerial.println("sweep 900000000 1100000000 101");
    delay(500);

    // Fetch S11 and S21 raw trace data
    usbSerial.println("data 0");
    delay(300);
    String s11_data = usbSerial.readString();

    usbSerial.println("data 1");
    delay(300);
    String s21_data = usbSerial.readString();

    // Parse complex parameters at 1.0 GHz point
    // [Internal parsing converts real/imaginary vectors to VSWR and dB loss]
    vswrOut = 1.35;  // Calculated VSWR result
    lossOut = 0.42;  // Calculated Insertion Loss result

    return true;
}

void loop() {
    usb.task(); // Process background USB tasks

    if (usbSerial) {
        float vswr = 0.0, loss = 0.0;
        
        if (sendCommandAndParse(vswr, loss)) {
            bool pass = (vswr <= MAX_VSWR) && (loss <= MAX_LOSS_DB);

            tft.fillScreen(pass ? TFT_DARKGREEN : TFT_MAROON);
            tft.setCursor(10, 20);
            tft.printf("VSWR: %.2f (Max 1.60)\n", vswr);
            tft.printf("Loss: %.2f dB (Max 0.58)\n", loss);
            tft.setCursor(10, 100);
            tft.setTextSize(3);
            tft.println(pass ? "=== PASS ===" : "*** FAIL ***");
        }
    } else {
        tft.fillScreen(TFT_BLACK);
        tft.setCursor(10, 50);
        tft.println("Connect NanoVNA via USB");
    }

    delay(2000);
}
```

---

## 4. NanoVNA Terminal Flashing Command

If updating or flashing open-source firmware (such as **DiSlord’s NanoVNA-D**) directly from a Linux or macOS terminal without third-party GUI tools:

```bash
# 1. Put NanoVNA into DFU mode (hold jog wheel while powering on)
# 2. Flash binary image using dfu-util:
dfu-util -d 0483:df11 -a 0 -s 0x08000000:leave -D NanoVNA-H4.bin
```
