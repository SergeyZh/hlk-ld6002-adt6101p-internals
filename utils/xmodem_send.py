#!/usr/bin/env python3
"""
Simple XMODEM-CRC sender over a serial port.

Usage examples:
  python utils/xmodem_send.py --port /dev/ttyUSB0 --baud 115200 --file firmware.bin

This implementation supports XMODEM with CRC (128-byte blocks, SOH) only.
It waits for the receiver to initiate by sending 'C' (0x43). If the receiver
never sends 'C' within the initial timeout, the transfer fails by default.

Exit codes:
  0 - success
  1 - argument/IO error
  2 - serial/handshake error
  3 - transfer failed (retries exceeded or receiver cancelled)
"""
from __future__ import annotations

import argparse
import os
import sys
import time
from typing import Optional

import serial  # pyserial

# Control bytes
SOH = 0x01  # Start of 128-byte data packet
EOT = 0x04  # End of transmission
ACK = 0x06  # Acknowledge
NAK = 0x15  # Not acknowledge
CAN = 0x18  # Cancel
CHAR_C = 0x43  # 'C' - request CRC mode

BLOCK_SIZE = 128  # Standard XMODEM block size
PAD_BYTE = 0x1A   # Substitution/pad when file shorter than block (CP/M EOF)


def crc16_ccitt(data: bytes, poly: int = 0x1021, init_crc: int = 0x0000) -> int:
    """Compute CRC-16/CCITT (XMODEM) over data.

    Parameters are chosen for XMODEM-CRC: polynomial 0x1021, initial value 0x0000.
    """
    crc = init_crc
    for b in data:
        crc ^= b << 8
        for _ in range(8):
            if crc & 0x8000:
                crc = ((crc << 1) & 0xFFFF) ^ poly
            else:
                crc = (crc << 1) & 0xFFFF
    return crc & 0xFFFF


def read_with_timeout(ser: serial.Serial, timeout: float) -> Optional[int]:
    """Read one byte, return int(0..255) or None on timeout."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        b = ser.read(1)
        if b:
            return b[0]
    return None


def wait_for_receiver_crc_request(ser: serial.Serial, timeout: float) -> bool:
    """Wait until receiver sends 'C' to request CRC mode. Return True if received."""
    b = read_with_timeout(ser, timeout)
    if b is None:
        return False
    # Some receivers may send noise first; scan a bit of stream within timeout window
    end = time.time() + max(0.0, timeout - 0.0)
    while True:
        if b == CHAR_C:
            return True
        if time.time() >= end:
            return False
        nxt = ser.read(1)
        if nxt:
            b = nxt[0]
        else:
            # small sleep to avoid busy loop
            time.sleep(0.01)
    

def send_block(ser: serial.Serial, block_no: int, chunk: bytes, per_try_timeout: float) -> bool:
    """Send a single XMODEM-CRC 128-byte block and wait for ACK. Return True if ACKed.

    chunk must be <= BLOCK_SIZE; will be padded with PAD_BYTE.
    """
    if len(chunk) < BLOCK_SIZE:
        chunk += bytes([PAD_BYTE]) * (BLOCK_SIZE - len(chunk))
    elif len(chunk) > BLOCK_SIZE:
        raise ValueError("Chunk larger than 128 bytes")

    pkt = bytearray()
    pkt.append(SOH)
    pkt.append(block_no & 0xFF)
    pkt.append(0xFF - (block_no & 0xFF))
    pkt.extend(chunk)
    crc = crc16_ccitt(chunk)
    pkt.append((crc >> 8) & 0xFF)
    pkt.append(crc & 0xFF)

    ser.write(pkt)
    ser.flush()

    # Wait for ACK/NAK/CAN
    while True:
        r = read_with_timeout(ser, per_try_timeout)
        if r is None:
            return False
        if r == ACK:
            return True
        if r == NAK:
            return False
        if r == CAN:
            raise RuntimeError("Transfer cancelled by receiver (CAN)")
        # Ignore any other bytes (like spurious 'C' or noise)


def send_eot(ser: serial.Serial, per_try_timeout: float) -> bool:
    """Send EOT sequence and get final ACK. Some receivers respond NAK then ACK."""
    # First EOT
    ser.write(bytes([EOT]))
    ser.flush()
    r = read_with_timeout(ser, per_try_timeout)
    if r == ACK:
        return True
    if r == NAK:
        # Send EOT again, expect ACK
        ser.write(bytes([EOT]))
        ser.flush()
        r2 = read_with_timeout(ser, per_try_timeout)
        return r2 == ACK
    if r == CAN:
        raise RuntimeError("Transfer cancelled by receiver (CAN during EOT)")
    # Unexpected or timeout
    return False


def xmodem_crc_send(ser: serial.Serial, data: bytes, *, initial_timeout: float, per_try_timeout: float, max_retries: int, progress: bool = True) -> bool:
    """Send data via XMODEM-CRC over an opened serial.Serial.

    Returns True on success.
    """
    # Wait for 'C' from the receiver to initiate CRC mode
    if not wait_for_receiver_crc_request(ser, initial_timeout):
        print("[ERROR] Receiver did not request CRC mode ('C') within timeout.", file=sys.stderr)
        return False

    total = len(data)
    sent = 0
    block_no = 1

    while sent < total:
        end = min(sent + BLOCK_SIZE, total)
        chunk = data[sent:end]
        # Try to send with retries on NAK/timeout
        attempt = 0
        while True:
            try:
                ok = send_block(ser, block_no, chunk, per_try_timeout)
            except RuntimeError as e:
                print(f"[ERROR] {e}", file=sys.stderr)
                return False
            if ok:
                break
            attempt += 1
            print(f"Attempt: {attempt}")
            if attempt > max_retries:
                print(f"[ERROR] Block {block_no}: retries exceeded.", file=sys.stderr)
                return False
            # On retry, just resend the same block
        sent = end
        if progress:
            pct = (sent / total) * 100 if total else 100.0
            print(f"Sent block {block_no} ({sent}/{total} bytes, {pct:.1f}%)")
        block_no = (block_no + 1) % 256
        #if block_no == 0:
        #    block_no = 1  # XMODEM block numbers roll over from 255 to 0? Keep 1..255; 0 is allowed but uncommon. Use 1..255.

    # Send EOT and expect final ACK
    if not send_eot(ser, per_try_timeout):
        print("[ERROR] Did not receive final ACK for EOT.", file=sys.stderr)
        return False

    return True


def main() -> int:
    p = argparse.ArgumentParser(description="Send a file via XMODEM-CRC over a serial port")
    p.add_argument("--port", required=True, help="Serial port path, e.g., /dev/ttyUSB0 or COM3")
    p.add_argument("--baud", type=int, default=115200, help="Baud rate (default: 115200)")
    p.add_argument("--file", required=True, help="Path to file to send")
    p.add_argument("--initial-timeout", type=float, default=30.0, help="Seconds to wait for receiver to request CRC ('C') (default: 30)")
    p.add_argument("--per-try-timeout", type=float, default=10.0, help="Seconds to wait for ACK/NAK per block (default: 10)")
    p.add_argument("--retries", type=int, default=10, help="Max retries per block (default: 10)")
    p.add_argument("--rtscts", action="store_true", help="Enable RTS/CTS hardware flow control")
    p.add_argument("--xonxoff", action="store_true", help="Enable software flow control (XON/XOFF)")
    p.add_argument("--no-progress", action="store_true", help="Disable progress output")

    args = p.parse_args()

    if not os.path.isfile(args.file):
        print(f"[ERROR] File not found: {args.file}", file=sys.stderr)
        return 1

    try:
        with open(args.file, "rb") as f:
            data = f.read()
    except Exception as e:
        print(f"[ERROR] Failed to read file: {e}", file=sys.stderr)
        return 1

    try:
        ser = serial.Serial(
            port=args.port,
            baudrate=args.baud,
            bytesize=serial.EIGHTBITS,
            parity=serial.PARITY_NONE,
            stopbits=serial.STOPBITS_ONE,
            timeout=0.1,  # small read timeout; we handle overall timeouts manually
            xonxoff=args.xonxoff,
            rtscts=args.rtscts,
            dsrdtr=False,
            write_timeout=5,
        )
    except Exception as e:
        print(f"[ERROR] Failed to open serial port: {e}", file=sys.stderr)
        return 2

    print(f"Opening {args.port} @ {args.baud} baud; sending {len(data)} bytes via XMODEM-CRC...")

    try:
        ok = True
        ok = xmodem_crc_send(
            ser,
            data,
            initial_timeout=args.initial_timeout,
            per_try_timeout=args.per_try_timeout,
            max_retries=args.retries,
            progress=not args.no_progress,
        )
        if ok:
            print("Transfer complete.")
            print("Wait some time until Radar writes app into the Flash, then press Ctrl+C to exit.")
            while True:
                print(ser.read(1).decode("ascii", errors="ignore"), end="")
    finally:
        ser.close()

    if ok:
        return 0
    else:
        return 3


if __name__ == "__main__":
    sys.exit(main())
