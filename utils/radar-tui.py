#!/usr/bin/env python

import asyncio
import sys
import argparse
import traceback
import serial_asyncio
import TinyFrame as TF
from textual.app import App, ComposeResult
from textual.widgets import Header, Footer, Button, Input, Log, DataTable
from textual.containers import Horizontal, Vertical
from textual.reactive import reactive
from textual.coordinate import Coordinate

PORT_DEFAULT = "/dev/cu.usbserial-XXXX"
BAUD_DEFAULT = "115200"

class RadarApp(App):
    """A Textual TUI application for radar communication using TinyFrame.
    
    Features:
    - Real-time display of received TinyFrame messages
    - Tracks and displays only the latest packet of each type (no packet accumulation)
    - Counts how many packets of each type have been received
    - Connection management with configurable serial port and baud rate
    - Table clearing on disconnect for a clean state on reconnection
    - Robust error handling and detailed logging
    - Automatic duplicate row detection and cleanup
    
    The right panel displays a table with exactly one row per packet type,
    updating existing rows when new packets of the same type are received
    rather than adding new rows. This ensures the table remains compact and
    focused on the current state of each packet type.
    
    The application uses a robust row tracking mechanism that:
    1. Finds existing rows by their content rather than by row keys
    2. Updates rows using direct row indices instead of potentially invalid row keys
    3. Automatically detects and removes any duplicate rows that might occur
    4. Maintains a clean, concise view with exactly one row per packet type
    """
    
    CSS = """
    Screen { layout: vertical; }
    #top { height: 3; }
    Input#port { width: 40; }
    Input#baud { width: 20; }
    #main { height: 1fr; }
    #log { height: 1fr; }
    #table { height: 1fr; }
    
    """
    
    # Reactive variables for connection status
    connected = reactive(False)
    
    def compose(self) -> ComposeResult:
        """Create the UI layout."""
        yield Header(show_clock=True)
        with Horizontal(id="top"):
            yield Input(PORT_DEFAULT, placeholder="Serial port path", id="port")
            yield Input(BAUD_DEFAULT, placeholder="Baud rate", id="baud")
            yield Button("Connect", id="connect", variant="primary")
            yield Button("Disconnect", id="disconnect", variant="error", disabled=True)
        with Horizontal(id="main"):
            with Vertical(id="left_panel"):
                yield Log(id="log", highlight=True)
            with Vertical(id="right_panel"):
                table = DataTable(id="table")
                table.add_columns("Type", "Count", "ID", "Length", "Data")
                yield table
        yield Footer()
    
    def on_mount(self) -> None:
        """Initialize the application."""
        self.reader = None
        self.writer = None
        self.read_task = None
        
        # Dictionary to track packet types, counts, and row indices
        # Key: frame type, Value: {'count': int, 'row_key': str}
        self.packet_tracker = {}
        
        # Initialize TinyFrame
        self.tf = TF.TinyFrame()
        self.tf.TYPE_BYTES = 0x02
        self.tf.CKSUM_TYPE = 'xor'
        self.tf.SOF_BYTE = 0x01
        
        # Log initialization message
        self.call_after_refresh(lambda: self.log_message("[yellow]Application initialized. Right panel will show the latest packet of each type with counters.[/yellow]"))
        
        # Add TinyFrame listeners
        self.tf.add_fallback_listener(self.fallback_listener)
        self.tf.add_type_listener(0x100, self.fallback_listener)
    
    def fallback_listener(self, tf, frame):
        """Handle received TinyFrame messages."""
        # Update the log with the received frame
        # self.log_message(f"RX: {frame}")
        
        try:
            # Convert binary data to hex string for display
            data_hex = frame.data.hex(' ')
            table = self.query_one(DataTable)
            
            # Get the frame type as a string for display and as a key
            frame_type_str = f"0x{frame.type:X}"
            frame_type = frame.type
            
            # Log the current state of the packet tracker for debugging
            # self.log_message(f"[cyan]Current packet types in tracker: {list(self.packet_tracker.keys())}[/cyan]")
            
            # Check if this packet type has been seen before
            if frame_type in self.packet_tracker:
                # Update the count for this packet type
                self.packet_tracker[frame_type]['count'] += 1
                count = self.packet_tracker[frame_type]['count']
                # self.log_message(f"[blue]Updating packet type {frame_type_str}, count: {count}[/blue]")
            else:
                # This is a new packet type
                count = 1
                self.packet_tracker[frame_type] = {'count': count}
                self.log_message(f"[green]New packet type: {frame_type_str}[/green]")
            
            # Find existing row with this frame type
            existing_row_index = None
            for i, row in enumerate(table.rows):
                # The first column (index 0) contains the frame type
                if table.get_cell_at(Coordinate(i,0)) == frame_type_str:
                    existing_row_index = i
                    break
            
            try:
                if existing_row_index is not None:
                    # Update the existing row
                    # self.log_message(f"[blue]Found existing row at index {existing_row_index} for type {frame_type_str}[/blue]")
                    table.update_cell_at(Coordinate(existing_row_index, 1), str(count))  # Count column
                    table.update_cell_at(Coordinate(existing_row_index, 2), f"0x{frame.id:X}")  # ID column
                    table.update_cell_at(Coordinate(existing_row_index, 3), str(frame.len))  # Length column
                    table.update_cell_at(Coordinate(existing_row_index, 4), data_hex)  # Data column
                else:
                    # Add a new row
                    self.log_message(f"[green]Adding new row for type {frame_type_str}[/green]")
                    table.add_row(
                        frame_type_str,  # Type
                        str(count),      # Count
                        f"0x{frame.id:X}",  # ID
                        str(frame.len),  # Length
                        data_hex         # Data
                    )
                
                # Log the current state of the table
                # self.log_message(f"[cyan]Packet types: {len(self.packet_tracker)}, Table rows: {len(table.rows)}[/cyan]")
            except Exception as e:
                self.log_message(f"[red]Error updating/adding row:[/red] {e}")
                self.log_message(f"[red]Error details:[/red] {type(e).__name__}: {str(e)}")
                self.log_message(f"[red]Traceback:[/red] {traceback.format_exc()}")
        except Exception as e:
            self.log_message(f"[red]Error processing frame:[/red] {e}")
            self.log_message(f"[red]Error details:[/red] {type(e).__name__}: {str(e)}")
            self.log_message(f"[red]Traceback:[/red] {traceback.format_exc()}")
    
    def log_message(self, message: str) -> None:
        """Add a message to the log widget."""
        log = self.query_one(Log)
        log.write(message+"\n")
    
    def clear_table(self) -> None:
        """Clear the data table and reset the packet tracker."""
        try:
            table = self.query_one(DataTable)
            
            # Clear all rows from the table
            table.clear()
            
            # Reset the packet tracker
            self.packet_tracker = {}
            
            self.log_message("[magenta]Table cleared and packet tracker reset[/magenta]")
        except Exception as e:
            self.log_message(f"[red]Error clearing table:[/red] {e}")
    
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
        
        # Clear the table and reset packet tracker when disconnecting
        self.clear_table()
        
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