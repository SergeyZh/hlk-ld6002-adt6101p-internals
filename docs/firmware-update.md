# Firmware Update for HLK‑LD6002/ADT6101P (via XMODEM‑CRC)

[![en](https://img.shields.io/badge/lang-en-blue.svg)](firmware-update.md)
[![ru](https://img.shields.io/badge/lang-ru-green.svg)](firmware-update.ru.md)


This guide explains how to switch the radar into bootloader mode and upload new firmware using the utilities in the `utils/` folder.

In short: run `radar-tui.py` and press the “Trigger Firmware update” button. The radar will enter bootloader mode. Then quit `radar-tui.py` and send the firmware file over XMODEM‑CRC using `xmodem_send.py`.

## Requirements
- Python 3.7+ installed.
- Dependencies from `utils/requirements.txt`:
  - textual, pyserial, pyserial-asyncio (and others if added later)
- Access to the radar’s serial port (USB‑UART adapter, etc.).
- Firmware file (`.bin`). Prepared examples are located in `utils/`.

Install dependencies:

```bash
pip install -r utils/requirements.txt
```

## Step 1. Switch the radar to firmware update mode
1. Connect the radar to your computer and identify its COM/tty port.
   - Linux: usually `/dev/ttyUSB0`, `/dev/ttyACM0`
   - macOS: usually `/dev/tty.usbserial‑XXXX`, `/dev/tty.usbmodemXXXX`
   - Windows: `COM3`, `COM5`, etc.
2. Start the TUI application:
   - Default (you can set port and baud rate in the UI):
     ```bash
     python utils/radar-tui.py
     ```
   - With explicit port and baud (example):
     ```bash
     python utils/radar-tui.py /dev/ttyUSB0 115200
     ```
3. Connect to the radar (if it did not auto‑connect).
4. Press the “Trigger Firmware update” button in the right panel.
   - Internally, this sends a service frame (type 0x3000) that switches the device to bootloader mode.
   - After that, the radar will stop responding as a normal app and will wait for a file upload via XMODEM‑CRC.
5. Close `radar-tui.py` (Ctrl+Q or the exit button) to release the serial port.

## Step 2. Upload firmware via XMODEM‑CRC
Use `utils/xmodem_send.py` to send the file. The receiver (the radar bootloader) initiates XMODEM‑CRC — it periodically sends the `C` character. Sending will start once the script sees this character.

Examples:

- Linux/macOS:
  ```bash
  python utils/xmodem_send.py --port /dev/ttyUSB0 --baud 115200 --file utils/firmware.bin
  ```
  Replace the port path and file name with yours.

- Windows:
  ```bash
  python utils/xmodem_send.py --port COM5 --baud 115200 --file utils/firmware.bin
  ```

Useful `xmodem_send.py` options:
- `--initial-timeout` — how many seconds to wait for the CRC request (`C`) from the bootloader (default 30).
- `--per-try-timeout` — timeout waiting for ACK/NAK for each block (default 10).
- `--retries` — maximum number of retries for one block (default 10).
- `--rtscts` — enable hardware flow control.
- `--xonxoff` — enable software flow control.
- `--no-progress` — disable progress output.

Example with a longer initial timeout:
```bash
python utils/xmodem_send.py --port /dev/ttyUSB0 --baud 115200 --file utils/hlk-ld6002-3D.bin --initial-timeout 60
```

## Verify the result
- After the transfer completes, the script will report successful sending of all blocks.
- Usually the device restarts automatically. If needed, power‑cycle the radar.
- Run `radar-tui.py` again to check the device responds and reports the new firmware version.

## Common issues and fixes
- `xmodem_send.py` “hangs” while waiting for `C`:
  - Make sure you actually switched the radar to bootloader mode (repeat Step 1 and close the TUI).
  - Check the selected port and UART baud rate.
  - Try increasing `--initial-timeout`.
- Transfer errors (many NAK/retries):
  - Check cable and connections quality.
  - If needed, enable `--rtscts` or `--xonxoff` depending on your adapter.
- Port is busy/unavailable:
  - Ensure `radar-tui.py` is closed and no other program keeps the port open.

## Notes
- Command to switch to firmware update mode: frame type `0x3000` (see `utils/radar-tui.py`, the “Trigger Firmware update” button). Also documented in `docs/undocumented-commands-hlk-ld6002.md`.
- The file is sent in 128‑byte blocks with CP/M padding (0x1A) in the last block. Mode — XMODEM‑CRC.
- Firmware list is here: [firmware-list-hlk-ld6002.md](firmware-list-hlk-ld6002.md)
