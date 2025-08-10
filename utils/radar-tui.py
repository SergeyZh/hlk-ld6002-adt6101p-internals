#!/usr/bin/env python

import asyncio
import sys
import argparse
import traceback
import struct
from dataclasses import dataclass
import serial_asyncio
import TinyFrame as TF
from textual.app import App, ComposeResult
from textual.widgets import Header, Footer, Button, Input, Log, DataTable
from textual.containers import Horizontal, Vertical
from textual.reactive import reactive
from textual.coordinate import Coordinate
from textual_hires_canvas import Canvas, HiResMode, TextAlign

@dataclass
class Target:
    """Structure to store data for a single radar target."""
    flag: int  # Target number/flag
    x: float   # X coordinate
    y: float   # Y coordinate
    z: float   # Z coordinate
    dop_idx: int  # Doppler index
    cluster_id: int  # Cluster identifier
    
    @classmethod
    def from_bytes(cls, data: bytes, offset: int = 0) -> 'Target':
        """Create a Target object from bytes data starting at the given offset.
        
        Args:
            data: The binary data containing target information
            offset: The starting offset in the data
            
        Returns:
            A new Target object
        """
        if len(data) < offset + 24:
            raise ValueError(f"Not enough data to create a Target: need 24 bytes, got {len(data) - offset}")
            
        flag = int.from_bytes(data[offset:offset+4], byteorder='little', signed=True)
        x = struct.unpack('<f', data[offset+4:offset+8])[0]
        y = struct.unpack('<f', data[offset+8:offset+12])[0]
        z = struct.unpack('<f', data[offset+12:offset+16])[0]
        dop_idx = int.from_bytes(data[offset+16:offset+20], byteorder='little', signed=True)
        cluster_id = int.from_bytes(data[offset+20:offset+24], byteorder='little')

        return cls(flag, x, y, z, dop_idx, cluster_id)

    def format(self) -> str:
        """Format the target data as a colored string for display.
        
        Returns:
            A formatted string with color highlighting
        """
        return (
            f"[bold white]Target {self.flag}:[/bold white] "
            f"[bold blue]X:[/bold blue] {self.x:.4f}, "
            f"[bold blue]Y:[/bold blue] {self.y:.4f}, "
            f"[bold blue]Z:[/bold blue] {self.z:.4f}, "
            f"[bold green]dop_idx:[/bold green] {self.dop_idx}, "
            f"[bold red]cluster_id:[/bold red] {self.cluster_id}"
        )

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
    - Custom data formatting for specific packet types (0xA0A, 0xA04)
    - Support for multiple targets (up to 4) in 0xA04 packets
    - Visual display of Target coordinates on a Cartesian plane
    
    The right panel displays a table with exactly one row per packet type,
    updating existing rows when new packets of the same type are received
    rather than adding new rows. This ensures the table remains compact and
    focused on the current state of each packet type.
    
    The left panel displays a Canvas with a Cartesian coordinate system showing
    the positions of detected targets in real-time. The X and Y coordinates from
    the Target objects are plotted on the plane, with labels showing their values.
    
    The application uses a robust row tracking mechanism that:
    1. Finds existing rows by their content rather than by row keys
    2. Updates rows using direct row indices instead of potentially invalid row keys
    3. Automatically detects and removes any duplicate rows that might occur
    4. Maintains a clean, concise view with exactly one row per packet type
    
    Data formatting is customized for specific packet types:
    - Type 0xA0A: Formatted as structured data with human-readable fields
    - Type 0xA04: Formatted as structured data with human-readable fields for up to 4 targets
      Each target includes coordinates (x, y, z), doppler index, and cluster ID
    - Other types: Displayed as hex strings
    """
    
    # World coordinate bounds for the radar data
    XMIN, XMAX = -4, 4  # X-axis bounds in meters
    YMIN, YMAX = -4, 4  # Y-axis bounds in meters
    
    CSS = """
    Screen { layout: vertical; }
    #top { height: 3; }
    Input#port { width: 40; }
    Input#baud { width: 20; }
    #main { height: 1fr; }
    #left_panel { width: 3fr; }
    #right_panel { width: 2fr; }
    #log { height: 1fr; }
    #table { height: 5fr; }
    Canvas { 
        border: round green;
        height: 5fr;
    }
    """
    
    # Reactive variables for connection status
    connected = reactive(False)
    
    def compose(self) -> ComposeResult:
        """Create the UI layout."""
        yield Header(show_clock=True)
        with Horizontal(id="top"):
            yield Input(PORT_DEFAULT, placeholder="Serial port path", id="port")
            yield Input(BAUD_DEFAULT, placeholder="Baud rate", id="baud")
            yield Button("Connect", id="connect", variant="primary", compact=True)
            yield Button("Disconnect", id="disconnect", variant="error", disabled=True)
        with Horizontal(id="main"):
            with Vertical(id="left_panel"):
                yield Canvas(id="radar_canvas", width=200, height=100)
            with Vertical(id="right_panel"):
                table = DataTable(id="table")
                table.add_columns("Type", "Count", "ID", "Length", "Data")
                yield table
                yield Log(id="log", highlight=True)
        yield Footer()
    
    def on_mount(self) -> None:
        """Initialize the application."""
        self.reader = None
        self.writer = None
        self.read_task = None
        
        # Dictionary to track packet types, counts, and row indices
        # Key: frame type, Value: {'count': int, 'row_key': str}
        self.packet_tracker = {}
        
        # List to store current targets for display
        self.current_targets = []
        
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
        
        # Initialize the radar plot after the canvas has been properly sized
        self.call_after_refresh(self.draw_radar_plot)
    
    def on_resize(self) -> None:
        """Handle resize events to redraw the radar plot."""
        self.draw_radar_plot()
    
    def world_to_canvas(self, x: float, y: float, width: int, height: int) -> tuple[float, float]:
        """Convert world coordinates to canvas coordinates.
        
        Args:
            x: X coordinate in world space
            y: Y coordinate in world space
            width: Canvas width in pixels
            height: Canvas height in pixels
            
        Returns:
            A tuple of (canvas_x, canvas_y) coordinates
        """
        # Map x from world space to canvas space
        canvas_x = (x - self.XMIN) / (self.XMAX - self.XMIN) * (width - 1)
        # Map y from world space to canvas space (with Y-axis inversion)
        canvas_y = (self.YMAX - y) / (self.YMAX - self.YMIN) * (height - 1)
        return canvas_x, canvas_y
    
    def draw_radar_plot(self) -> None:
        """Draw the radar plot on the canvas, showing coordinate axes, axis labels, and targets.
        
        This method renders a complete radar visualization including:
        - X and Y axes with tick marks
        - Numeric labels for tick marks
        - Axis labels with units (meters)
        - Target points and labels (if targets are present)
        - A title for the display
        """
        try:
            # Get the canvas widget
            canvas = self.query_one("#radar_canvas", Canvas)
            
            # Get canvas dimensions
            width, height = canvas.size.width, canvas.size.height

            # Reset the canvas
            canvas.reset()
            
            # Skip drawing if canvas has no size yet
            if width == 0 or height == 0:
                self.log_message("[yellow]Canvas has zero size, skipping drawing[/yellow]")
                return
            
            # Draw X-axis
            if self.YMIN <= 0 <= self.YMAX:
                x0, y0 = self.world_to_canvas(self.XMIN, 0, width, height)
                x1, y1 = self.world_to_canvas(self.XMAX, 0, width, height)
                canvas.draw_hires_line(x0, y0, x1, y1, HiResMode.BRAILLE, style="grey50")
            
            # Draw Y-axis
            if self.XMIN <= 0 <= self.XMAX:
                x0, y0 = self.world_to_canvas(0, self.YMIN, width, height)
                x1, y1 = self.world_to_canvas(0, self.YMAX, width, height)
                canvas.draw_hires_line(x0, y0, x1, y1, HiResMode.BRAILLE, style="grey50")
            
            # Draw X-axis ticks and labels
            for xt in range(int(self.XMIN), int(self.XMAX) + 1):
                if xt == 0:  # Skip zero as it's the origin
                    continue
                x, y = self.world_to_canvas(xt, 0, width, height)
                canvas.set_hires_pixels([(x, y - 0.5), (x, y + 0.5)], HiResMode.BRAILLE)
                canvas.write_text(int(round(x)), int(min(height - 1, y + 1)), str(xt), TextAlign.CENTER)
            

            # Draw Y-axis ticks and labels
            for yt in range(int(self.YMIN), int(self.YMAX) + 1):
                if yt == 0:  # Skip zero as it's the origin
                    continue
                x, y = self.world_to_canvas(0, yt, width, height)
                canvas.set_hires_pixels([(x - 0.5, y), (x + 0.5, y)], HiResMode.BRAILLE)
                if x >= 2:
                    canvas.write_text(int(x - 2), int(round(y)), str(yt))
            
            # Draw origin label
            origin_x, origin_y = self.world_to_canvas(0, 0, width, height)
            canvas.write_text(int(origin_x) + 1, int(origin_y) - 1, "0", TextAlign.CENTER)
            
            # Draw X-axis label
            x_label_x = width - 1  # Center of the canvas horizontally
            x_label_y = (height // 2) + 1  # Bottom of the canvas
            canvas.write_text(x_label_x, x_label_y, "X,m", TextAlign.RIGHT)
            
            # Draw Y-axis label
            # Position the Y-axis label to the left of the Y-axis with some padding
            # to avoid overlapping with tick labels
            # y_axis_x, _ = self.world_to_canvas(0, 0, width, height)
            y_label_x = width // 2 + 1 # max(0, int(y_axis_x) - 12)  # 12 characters to the left of Y-axis
            y_label_y = 0  # Center of the canvas vertically
            canvas.write_text(y_label_x, y_label_y, "Y,m", TextAlign.LEFT)
            
            # Draw targets
            if self.current_targets:
                # Convert target coordinates to canvas coordinates
                canvas_points = []
                for target in self.current_targets:
                    cx, cy = self.world_to_canvas(target.x, target.y, width, height)
                    canvas_points.append((cx, cy))
                
                # Draw target points
                canvas.set_hires_pixels(canvas_points, HiResMode.BRAILLE, style="yellow")
                
                # Draw target labels
                for target, (cx, cy) in zip(self.current_targets, canvas_points):
                    label = f"T {target.flag}, X={target.x:.2f}, Y={target.y:.2f}"
                    canvas.write_text(int(cx) + 1, int(cy), label)
            
            # Draw a title
            canvas.write_text(2, 0, "Radar Target Display", TextAlign.LEFT)
            
        except Exception as e:
            self.log_message(f"[red]Error drawing radar plot:[/red] {e}")
            self.log_message(f"[red]Error details:[/red] {type(e).__name__}: {str(e)}")
            self.log_message(f"[red]Traceback:[/red] {traceback.format_exc()}")
    
    def fallback_listener(self, tf, frame):
        """Handle received TinyFrame messages."""
        # Update the log with the received frame
        # self.log_message(f"RX: {frame}")
        
        try:
            # Format the data based on packet type
            formatted_data = self.format_packet_data(frame.type, frame.data)
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
                    table.update_cell_at(Coordinate(existing_row_index, 4), formatted_data)  # Data column
                else:
                    # Add a new row
                    self.log_message(f"[green]Adding new row for type {frame_type_str}[/green]")
                    table.add_row(
                        frame_type_str,  # Type
                        str(count),      # Count
                        f"0x{frame.id:X}",  # ID
                        str(frame.len),  # Length
                        formatted_data   # Data
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
    
    def format_packet_data(self, frame_type: int, data: bytes) -> str:
        """Format packet data based on packet type.
        
        Args:
            frame_type: The type of the frame (e.g., 0xA0A, 0xA04)
            data: The binary data from the frame
            
        Returns:
            A formatted string representation of the data
        """
        try:
            # Format based on packet type
            if frame_type == 0xA0A:  # 0xA0A packet
                # Assuming 0xA0A packet has a specific structure
                # For example: 4 bytes for status, 4 bytes for value1, 4 bytes for value2, etc.
                if len(data) >= 16:  # Ensure we have enough data
                    # Extract values from the binary data
                    area0 = int.from_bytes(data[0:4], byteorder='little')
                    area1 = int.from_bytes(data[4:8], byteorder='little')
                    area2 = int.from_bytes(data[8:12], byteorder='little')
                    area3 = int.from_bytes(data[12:16], byteorder='little')
                    
                    # Format as a structured string with color highlighting
                    return (
                        f"[bold cyan]Area0:[/bold cyan] {area0}, "
                        f"[bold green]Area1:[/bold green] {area1}, "
                        f"[bold yellow]Area2:[/bold yellow] {area2}, "
                        f"[bold magenta]Area3:[/bold magenta] {area3}"
                    )
                
            elif frame_type == 0xA04:  # 0xA04 packet - Target coordinates
                # Each target requires 24 bytes of data
                # There can be up to 4 targets in a single packet
                if len(data) >= 24:  # Ensure we have enough data for at least one target
                    # Calculate how many complete targets we can parse
                    target_count = min(4, len(data) // 24)  # Maximum 4 targets
                    
                    targets = []
                    # Parse each target
                    for i in range(target_count):
                        try:
                            # Create a Target object from the data at the appropriate offset
                            target = Target.from_bytes(data, i * 24)
                            targets.append(target)
                        except Exception as e:
                            self.log_message(f"[red]Error parsing target {i}:[/red] {e}")
                    
                    if not targets:
                        return f"[red]No valid targets found in data of length {len(data)}[/red]"
                    
                    # Update the current_targets list for the radar plot
                    self.current_targets = targets
                    
                    # Schedule a redraw of the radar plot
                    self.call_after_refresh(self.draw_radar_plot)
                    
                    # Format all targets for display in the table
                    result = f"[bold yellow]{len(targets)} target(s) detected:[/bold yellow]\n"
                    for i, target in enumerate(targets):
                        result += f"{target.format()}"
                        if i < len(targets) - 1:
                            result += "\n"
                    
                    return result
            
            # Default formatting for other packet types
            return data.hex(' ')
            
        except Exception as e:
            # If formatting fails, fall back to hex display
            self.log_message(f"[red]Error formatting packet type 0x{frame_type:X}:[/red] {e}")
            return data.hex(' ')  # Fallback to hex format
    
    def log_message(self, message: str) -> None:
        """Add a message to the log widget."""
        log = self.query_one(Log)
        log.write_line(message)
    
    def clear_table(self) -> None:
        """Clear the data table and reset the packet tracker."""
        try:
            table = self.query_one(DataTable)
            
            # Clear all rows from the table
            table.clear()
            
            # Reset the packet tracker
            self.packet_tracker = {}
            
            # Clear the current targets list
            self.current_targets = []
            
            # Redraw the radar plot to clear the targets
            self.call_after_refresh(self.draw_radar_plot)
            
            self.log_message("[magenta]Table cleared, packet tracker reset, and radar display cleared[/magenta]")
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
        
        # Auto-connect if a valid port is provided (not the default) and auto-connect is not disabled
        if args.port != PORT_DEFAULT and not args.no_auto_connect:
            await app.connect_serial()
    
    # Schedule the setup to run after the app is mounted
    app.call_after_refresh(set_connection_params)
    
    # Run the app
    app.run()
    return 0

if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))