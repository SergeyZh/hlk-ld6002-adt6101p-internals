#!/usr/bin/env python

import asyncio
import sys
import argparse
import serial_asyncio
import TinyFrame as TF
from textual.app import App, ComposeResult
from textual.widgets import Header, Footer, Button, Input, Log, DataTable
from textual.containers import Horizontal, Vertical
from textual.reactive import reactive

PORT_DEFAULT = "/dev/cu.usbserial-XXXX"
BAUD_DEFAULT = "115200"

class RadarApp(App):
    """A Textual TUI application for radar communication using TinyFrame."""
    
    CSS = """
    Screen { layout: vertical; }
    #top { height: 3; }
    Input#port { width: 40; }
    Input#baud { width: 20; }
    #main { height: 1fr; }
    #log { height: 1fr; }
    #table { height: 1fr; }
    Button { min-width: 10; max-height: 3; }
    """
    
    # Reactive variables for connection status
    connected = reactive(False)
    
    def compose(self) -> ComposeResult:
        """Create the UI layout."""
        yield Header(show_clock=True)
        with Horizontal(id="top"):
            yield Input(PORT_DEFAULT, placeholder="Serial port path", id="port")
            yield Input(BAUD_DEFAULT, placeholder="Baud rate", id="baud")
        with Horizontal():
            yield Button("Connect", id="connect", variant="primary")
            yield Button("Disconnect", id="disconnect", variant="error", disabled=True)
        with Horizontal(id="main"):
            with Vertical(id="left_panel"):
                yield Log(id="log", highlight=True)
            with Vertical(id="right_panel"):
                table = DataTable(id="table")
                table.add_columns("ID", "Type", "Length", "Data")
                yield table
        yield Footer()
    
    def on_mount(self) -> None:
        """Initialize the application."""
        self.reader = None
        self.writer = None
        self.read_task = None
        
        # Initialize TinyFrame
        self.tf = TF.TinyFrame()
        self.tf.TYPE_BYTES = 0x02
        self.tf.CKSUM_TYPE = 'xor'
        self.tf.SOF_BYTE = 0x01
        
        # Add TinyFrame listeners
        self.tf.add_fallback_listener(self.fallback_listener)
        self.tf.add_type_listener(0x100, self.fallback_listener)
    
    def fallback_listener(self, tf, frame):
        """Handle received TinyFrame messages."""
        # Update the log with the received frame
        self.log_message(f"RX: {frame}")
        
        # Add the frame to the data table
        try:
            # Convert binary data to hex string for display
            data_hex = frame.data.hex(' ')
            self.query_one(DataTable).add_row(
                f"0x{frame.id:X}", 
                f"0x{frame.type:X}", 
                str(frame.len), 
                data_hex
            )
        except Exception as e:
            self.log_message(f"[red]Error processing frame:[/red] {e}")
    
    def log_message(self, message: str) -> None:
        """Add a message to the log widget."""
        log = self.query_one(Log)
        log.write(message)
    
    def watch_connected(self, connected: bool) -> None:
        """React to changes in the connection status."""
        connect_button = self.query_one("#connect", Button)
        disconnect_button = self.query_one("#disconnect", Button)
        
        connect_button.disabled = connected
        disconnect_button.disabled = not connected
    
    async def on_button_pressed(self, event: Button.Pressed) -> None:
        """Handle button press events."""
        button_id = event.button.id
        
        if button_id == "connect":
            await self.connect_serial()
        elif button_id == "disconnect":
            await self.disconnect_serial()
    
    async def connect_serial(self) -> None:
        """Connect to the serial port."""
        port = self.query_one("#port", Input).value.strip()
        
        try:
            baud = int(self.query_one("#baud", Input).value or BAUD_DEFAULT)
        except ValueError:
            self.log_message(f"[red]Invalid baud rate. Using default: {BAUD_DEFAULT}[/red]")
            baud = int(BAUD_DEFAULT)
        
        self.log_message(f"Connecting to {port} at {baud} baud...")
        
        try:
            self.reader, self.writer = await serial_asyncio.open_serial_connection(
                url=port, baudrate=baud, bytesize=8, parity="N", stopbits=1
            )
            
            # Set up TinyFrame to use our serial writer
            self.tf.write = self.serial_write
            
            # Start the read loop
            self.read_task = asyncio.create_task(self.read_loop())
            
            self.connected = True
            self.log_message(f"[green]Connected to {port} at {baud} baud[/green]")
            
        except Exception as e:
            self.log_message(f"[red]Serial connection error:[/red] {e}")
    
    async def disconnect_serial(self) -> None:
        """Disconnect from the serial port."""
        if self.read_task:
            self.read_task.cancel()
            try:
                await self.read_task
            except asyncio.CancelledError:
                pass
            self.read_task = None
        
        if self.writer:
            self.writer.close()
            self.writer = None
        
        self.reader = None
        self.connected = False
        self.log_message("[yellow]Disconnected from serial port[/yellow]")
    
    def serial_write(self, data: bytes) -> None:
        """Write data to the serial port (used by TinyFrame)."""
        if self.writer:
            self.writer.write(data)
            # Note: We can't await writer.drain() here because this is called
            # from a synchronous context. This is a limitation of using TinyFrame
            # with asyncio.
    
    async def read_loop(self) -> None:
        """Read data from the serial port and process it with TinyFrame."""
        try:
            while True:
                # Read a chunk of data
                data = await self.reader.read(1000)
                if not data:
                    self.log_message("[red]Serial connection closed[/red]")
                    break
                
                # Process the data with TinyFrame
                self.tf.accept(data)
                
                # Small delay to prevent CPU hogging
                await asyncio.sleep(0.01)
                
        except asyncio.CancelledError:
            # Task was cancelled, exit gracefully
            raise
        except Exception as e:
            self.log_message(f"[red]Error in read loop:[/red] {e}")
        finally:
            # Ensure we update the connection status if the loop exits
            self.connected = False
            self.query_one("#connect", Button).disabled = False
            self.query_one("#disconnect", Button).disabled = True

def main(arguments):
    """Run the Radar TUI application."""
    # Set up argument parser
    parser = argparse.ArgumentParser(
        description="Radar TUI Application - A Textual interface for radar communication using TinyFrame",
        epilog="Example: python radar-tui.py /dev/ttyUSB0 115200"
    )
    
    # Add arguments
    parser.add_argument(
        "port", 
        nargs="?", 
        default=PORT_DEFAULT,
        help=f"Serial port path (default: {PORT_DEFAULT})"
    )
    
    parser.add_argument(
        "baud", 
        nargs="?", 
        default=BAUD_DEFAULT,
        help=f"Baud rate (default: {BAUD_DEFAULT})"
    )
    
    parser.add_argument(
        "--no-auto-connect", 
        action="store_true",
        help="Don't automatically connect even if port is specified"
    )
    
    # Parse arguments
    args = parser.parse_args(arguments)
    
    # Create and run the app
    app = RadarApp()
    
    # Set the port and baud rate from command-line arguments
    async def set_connection_params():
        app.query_one("#port", Input).value = args.port
        app.query_one("#baud", Input).value = args.baud
        
        # Auto-connect if valid port is provided (not the default) and auto-connect is not disabled
        if args.port != PORT_DEFAULT and not args.no_auto_connect:
            await app.connect_serial()
    
    # Schedule the setup to run after the app is mounted
    app.call_after_refresh(set_connection_params)
    
    # Run the app
    app.run()
    return 0

if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))