# Radar TUI Application

A Text-based User Interface (TUI) application for radar communication using the TinyFrame protocol.

## Features

- Modern, responsive terminal interface using the Textual framework
- Real-time display of received [TinyFrame](https://github.com/MightyPork/PonyFrame) messages
- Connection management with configurable serial port and baud rate
- Command-line arguments for easy configuration
- Detailed logging of all communication
- Tabular display of received frames

## Requirements

- Python 3.7+
- Required Python packages:
  - textual
  - pyserial
  - pyserial-asyncio

## Installation

1. Ensure you have Python installed
2. Install the required packages:

```bash
pip install -r requirements.txt
```

## Usage

### Basic Usage

Run the application with default settings:

```bash
python radar-tui.py
```

Specify a serial port:

```bash
python radar-tui.py /dev/ttyUSB0
```

Specify both serial port and baud rate:

```bash
python radar-tui.py /dev/ttyUSB0 115200
```

### Command-line Arguments

The application accepts the following command-line arguments:

- `port` (optional): Serial port path (default: "/dev/cu.usbserial-XXXX")
- `baud` (optional): Baud rate (default: 115200)
- `--no-auto-connect`: Don't automatically connect even if port is specified

### Help

To view the help message and available options:

```bash
python radar-tui.py --help
```

## Interface Guide

The application interface consists of the following elements:

### Top Bar

- **Serial Port Input**: Enter the path to your serial port
- **Baud Rate Input**: Enter the desired baud rate
- **Connect Button**: Click to connect to the specified serial port
- **Disconnect Button**: Click to disconnect from the current serial port

### Main Area

- **Log Panel**: Displays connection status and received messages
- **Data Table**: Shows received TinyFrame messages in a structured format with columns for:
  - ID: The frame ID
  - Type: The message type
  - Length: Number of data bytes in the frame
  - Data: The frame data in hexadecimal format

### Keyboard Shortcuts

- **Ctrl+C**: Exit the application
- **Tab**: Navigate between input fields and buttons

## Comparison with radar-client.py

The `radar-tui.py` application is an enhanced version of `radar-client.py` with the following improvements:

1. **User Interface**: Full TUI interface instead of command-line output
2. **Visualization**: Structured display of received frames in a table
3. **Usability**: Interactive connection management with buttons
4. **Feedback**: Color-coded status messages and detailed logging
5. **Configuration**: More flexible command-line arguments

Both applications use the TinyFrame protocol for communication but `radar-tui.py` provides a more user-friendly experience.

## Troubleshooting

### Connection Issues

If you encounter connection issues:

1. Verify the serial port path is correct
2. Ensure you have the necessary permissions to access the port
3. Check that the baud rate matches your device's configuration
4. Make sure no other application is using the serial port

### Display Issues

If the interface doesn't display correctly:

1. Ensure your terminal supports colors and Unicode characters
2. Try resizing your terminal window
3. Check that you're using a compatible terminal emulator

## License

This application is distributed under the same license as the parent project.