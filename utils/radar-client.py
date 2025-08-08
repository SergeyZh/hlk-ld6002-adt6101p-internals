#!/usr/bin/env python

import serial
import TinyFrame as TF
import os
import sys
import time

from TinyFrame import (TinyFrame)

tf: TinyFrame = TF.TinyFrame()

def fallback_listener(tf, frame):
    # print("Fallback listener")
    print(frame)

def main(arguments):
    print("TinyFrame test sender")
    
    # Check if serial port path is provided
    if len(arguments) < 1:
        print("Error: Serial port path is required")
        print("Usage: python radar-client.py <serial_port_path> [baud_rate]")
        return 1
    
    # Get serial port path from arguments
    serial_port = arguments[0]
    
    # Get baud rate from arguments or use default
    baud_rate = 115200  # Default baud rate
    if len(arguments) >= 2:
        try:
            baud_rate = int(arguments[1])
        except ValueError:
            print(f"Error: Invalid baud rate '{arguments[1]}'. Using default: {baud_rate}")
    
    print(f"Connecting to {serial_port} at {baud_rate} baud")
    
    try:
        with serial.Serial(serial_port, baud_rate, timeout=1) as ser:
            tf.TYPE_BYTES = 0x02
            tf.CKSUM_TYPE = 'xor'
            tf.SOF_BYTE = 0x01
            tf.write = ser.write
            # Add listeners
            tf.add_fallback_listener(fallback_listener)
            tf.add_type_listener(0x100, fallback_listener)

            # send a frame
            # tf.send(TYPE_CENTRAL, b"Hi Central")
            # time.sleep(1)
            # tf.send(TYPE_PERIPHERAL, b"Hi Peripheral")
            # time.sleep(1)
            # tf.send(TYPE_UART, b"Hi UART\n")
            # time.sleep(1)
            #time.sleep(3600)
            while True:
                #line = ser.readline()   # read a '\n' terminated line
                print("qwe")
                tf.accept(ser.read(1000))
    except serial.SerialException as e:
        print(f"Error: {e}")
        return 1


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
