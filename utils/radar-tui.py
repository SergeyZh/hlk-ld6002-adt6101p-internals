#!/usr/bin/env python

import asyncio
import sys
import argparse
import traceback
import struct
from dataclasses import dataclass
import serial_asyncio

from vendor.PonyFrame.TinyFrame import TinyFrame as TF
from textual import work
from textual.app import App, ComposeResult
from textual.screen import Screen
from textual.screen import ModalScreen
from textual.widgets import Header, Footer, Button, Input, Log, DataTable, Static, Switch, Checkbox
from textual.containers import Horizontal, Vertical, Grid
from textual.reactive import reactive
from textual.coordinate import Coordinate
from textual_hires_canvas import Canvas, HiResMode, TextAlign

# --- Radar type naming (easy to override) ---
# Map radar_type_value (1..9) to human-readable names.
# To override names, edit this dict in code or replace via configuration in your fork.
RADAR_TYPE_NAMES: dict[int, str] = {
    1: "Infant Monitoring, HLK-6002H",
    3: "Fall Detection v2, HLK-LD6002C",
    4: "Breath/Heart Monitoring, HLK-LD6002",
    6: "3D Human Detection, HLK-LD6002B",
    8: "Fall Detection v4, HLK-LD6002C",
}

def get_radar_type_name(radar_type_value: int) -> str:
    """Return human-readable radar type name for given integer value.
    Easy to override by changing RADAR_TYPE_NAMES.
    """
    return RADAR_TYPE_NAMES.get(radar_type_value, f"Unknown ({radar_type_value})")

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

@dataclass
class CloudPoint:
    """Structure to store data for a single point in the point cloud."""
    idx: float   # Cloud index (float per protocol)
    x: float     # X coordinate (meters)
    y: float     # Y coordinate (meters)
    z: float     # Z coordinate (meters)
    speed: float # Speed (m/s)

    @classmethod
    def from_bytes(cls, data: bytes, offset: int = 0) -> 'CloudPoint':
        """Create a PointCloudPoint from bytes starting at offset.
        Data layout: 5 floats little-endian: idx, x, y, z, speed.
        """
        if len(data) < offset + 20:
            raise ValueError(f"Not enough data to create a PointCloudPoint: need 20 bytes, got {len(data) - offset}")
        idx, x, y, z, speed = struct.unpack('<fffff', data[offset:offset+20])
        return cls(idx, x, y, z, speed)

PORT_DEFAULT = "/dev/cu.usbserial-XXXX"
BAUD_DEFAULT = "115200"

class ConfirmDialog(ModalScreen[bool]):
    BINDINGS = [
        ("escape", "app.pop_screen", "Close"),
    ]
    def __init__(self, message: str) -> None:
        super().__init__()
        self.message = message
    def compose(self) -> ComposeResult:
        yield Grid(
            Static(self.message, id="question"),
            Button("Yes", id="confirm_yes", variant="warning"),
            Button("No", id="confirm_no", variant="primary"),
            id="dialog",
        )
    def on_mount(self) -> None:
        try:
            self.set_focus(self.query_one("#confirm_no", Button))
        except Exception:
            pass
    async def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "confirm_yes":
            self.dismiss(True)
        elif event.button.id == "confirm_no":
            self.dismiss(False)

class FirmwareScreen(Screen):
    """Simple Firmware screen with title and Esc to close."""

    BINDINGS = [
        ("escape", "app.pop_screen", "Close"),
    ]

    def __init__(self) -> None:
        super().__init__()
        self._prev_title: str | None = None

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        yield Static("Firmware screen", id="firmware_info")
        yield Footer()

    def on_mount(self) -> None:
        # Save and temporarily set the app title
        self._prev_title = self.app.title
        self.app.title = "Firmware"

    def on_unmount(self) -> None:
        # Restore previous title
        if self._prev_title is not None:
            self.app.title = self._prev_title


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
    - Radar version detection and display in the footer (type 0xFFFF)
    - Functions for sending various packet types to the radar:
      - request_radar_version(): Sends a packet with type 0xFFFF to request version information
      - request_area_data(): Sends a packet with type 0xA0A to request area data
      - request_target_coordinates(): Sends a packet with type 0xA04 to request target coordinates
      - request_type_100(): Sends a packet with type 0x100
      - send_packet(): Generic function for sending any packet type with optional data
    
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

    async def on_unmount(self) -> None:
        """Best-effort cleanup when screen unmounts (covers some exit paths)."""
        try:
            await self.disconnect_serial()
            # Schedule async cleanup on the app loop; ignore errors if already closed
            # self.call_after_refresh(lambda: asyncio.create_task(self.disconnect_serial()))
        except Exception:
            pass
    
    # World coordinate bounds for the radar data
    XMIN, XMAX = -4, 4  # X-axis bounds in meters
    YMIN, YMAX = -4, 4  # Y-axis bounds in meters
    
    CSS = """
    Screen { layout: vertical; }
    #top { height: 3; }
    Input#port { width: 40; }
    Input#baud { width: 20; }
    Static#point_cloud_label { width: 20; }
    #main { height: 1fr; }
    #left_panel { width: 3fr; }
    #right_panel { width: 2fr; }
    #log { height: 1fr; }
    #table { height: 5fr; }
    
    ConfirmDialog {
        align: center middle;
        border: round $warning;
    }
    #dialog {
        grid-size: 2;
        grid-gutter: 1 2;
        grid-rows: 1fr 3;
        padding: 0 1;
        width: 60;
        height: 11;
        border: thick $background 80%;
        background: darkred;
        color: yellow;
    }

    #question {
        column-span: 2;
        height: 1fr;
        width: 1fr;
        content-align: center middle;
    }
    ConfirmDialog>Grid>Button {
        width: 100%;
    }
    Canvas { 
        border: round green;
        height: 5fr;
    }
    #radar_controls {
        height: 3fr;
        padding: 0;
        border: tab $warning;
        margin-bottom: 1;
    }
    #radar_status {
        height: auto;
        padding: 1;
        border: tab $primary;
        background: $surface;
        margin-bottom: 1;
    }
    """
    
    BINDINGS = [
#        ("ctrl+f", "open_firmware", "Firmware"),
#        ("ctrl+q", "quit_app", "Quit"),
    ]
    
    # Reactive variables for connection status and radar information
    connected = reactive(False)
    radar_version = reactive("Unknown")
    radar_type = reactive("Unknown")
    
    def compose(self) -> ComposeResult:
        """Create the UI layout."""
        yield Header(show_clock=True)
        with Horizontal(id="top"):
            yield Input(PORT_DEFAULT, placeholder="Serial port path", id="port")
            yield Input(BAUD_DEFAULT, placeholder="Baud rate", id="baud")
            yield Button("Connect", id="connect", variant="primary", compact=True)
            yield Button("Disconnect", id="disconnect", variant="error", disabled=True, compact=True)
        with Horizontal(id="main"):
            with Vertical(id="left_panel"):
                yield Canvas(id="radar_canvas", width=200, height=100)
            with Vertical(id="right_panel"):
                # Radar status panel
                yield Static("Radar Status: Loading...", id="radar_status")
                
                # Radar control panel
                with Vertical(id="radar_controls"):
                    with Horizontal():
                        yield Checkbox("Point Cloud", id="on_point_cloud", value=False, compact=False)
                        yield Checkbox("Targets", id="on_targets", value=False, compact=False)
                    yield Button("Trigger Firmware update", id="request_fw_update", variant="warning", compact=False)

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
        # List to store current point cloud for display (list of PointCloudPoint)
        self.current_point_cloud: list[CloudPoint] = []
        
        # Initialize TinyFrame
        self.tf = TF()
        self.tf.TYPE_BYTES = 0x02
        self.tf.CKSUM_TYPE = 'xor'
        self.tf.SOF_BYTE = 0x01
        
        # Log initialization message
        self.call_after_refresh(lambda: self.log_message("[yellow]Application initialized. Right panel will show the latest packet of each type with counters.[/yellow]"))
        
        # Internal flags to control UI event side-effects
        self._suppress_on_targets_event = False
        self._suppress_on_point_cloud_event = False

        # Device state derived from incoming messages (source of truth)
        self._targets_active = False
        self._point_cloud_active = False
        # Monotonic timestamps of last packets to infer feature OFF by timeout
        self._last_a04_time = 0.0
        self._last_a08_time = 0.0
        # Timeout window (seconds) without data to consider feature OFF
        self._feature_timeout_sec = 1.0
        # Grace period after user CLOSE request to ignore transient incoming data
        self._command_grace_sec = 1.5
        self._targets_close_grace_until = 0.0
        self._point_cloud_close_grace_until = 0.0
        
        # Add TinyFrame listeners
        # Each packet type has its own listener function that processes the specific packet type
        # and updates the DataTable using the common update_data_table function.
        # The fallback_listener handles any packet types that don't have a specific listener.
        self.tf.add_fallback_listener(self.fallback_listener)
        self.tf.add_type_listener(0xA0A, self.area_data_listener)  # Area data packets
        self.tf.add_type_listener(0xA04, self.target_coordinates_listener)  # Target coordinates packets
        self.tf.add_type_listener(0xA08, self.point_cloud_listener)  # Point cloud packets
        self.tf.add_type_listener(0xFFFF, self.version_listener)  # Radar version information packets
        
        # Initialize the radar status widget
        self.call_after_refresh(self.update_radar_status)
        
        # Initialize the radar plot after the canvas has been properly sized
        self.call_after_refresh(self.draw_radar_plot)

        radar_control_window = self.query_one("#radar_controls", Vertical)
        radar_control_window.border_title = "[bold]Radar Controls[/bold]"
    
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
            try:
                on_targets = self.query_one("#on_targets", Checkbox).value
            except Exception:
                on_targets = False
            if on_targets and self.current_targets:
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
            
            # Draw point cloud (if enabled)
            try:
                on_point_cloud = self.query_one("#on_point_cloud", Checkbox).value
            except Exception:
                on_point_cloud = False
            if on_point_cloud and self.current_point_cloud:
                pc_canvas_points = []
                for pt in self.current_point_cloud:
                    # pt is PointCloudPoint
                    cx, cy = self.world_to_canvas(pt.x, pt.y, width, height)
                    pc_canvas_points.append((cx, cy))
                if pc_canvas_points:
                    canvas.set_hires_pixels(pc_canvas_points, HiResMode.BRAILLE, style="red")
            
            # Draw a title
            canvas.write_text(2, 0, "Radar Target Display", TextAlign.LEFT)
            
        except Exception as e:
            self.log_message(f"[red]Error drawing radar plot:[/red] {e}")
            self.log_message(f"[red]Error details:[/red] {type(e).__name__}: {str(e)}")
            self.log_message(f"[red]Traceback:[/red] {traceback.format_exc()}")
        
    def update_data_table(self, frame, formatted_data):
        """Update the DataTable with frame information.
        
        This is a base function that handles the common logic for updating the DataTable
        with frame information, regardless of the packet type. It's used by all specific
        packet type listener functions to avoid code duplication.
        
        The function:
        1. Updates the packet counter for the frame type
        2. Finds the existing row in the table for this frame type (if any)
        3. Either updates the existing row or adds a new row to the table
        
        Args:
            frame: The TinyFrame frame object containing type, id, length, and data
            formatted_data: The formatted data string to display in the table
        """
        try:
            table = self.query_one(DataTable)
            
            # Get the frame type as a string for display and as a key
            frame_type_str = f"0x{frame.type:X}"
            frame_type = frame.type
            
            # Check if this packet type has been seen before
            if frame_type in self.packet_tracker:
                # Update the count for this packet type
                self.packet_tracker[frame_type]['count'] += 1
                count = self.packet_tracker[frame_type]['count']
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
            except Exception as e:
                self.log_message(f"[red]Error updating/adding row:[/red] {e}")
                self.log_message(f"[red]Error details:[/red] {type(e).__name__}: {str(e)}")
                self.log_message(f"[red]Traceback:[/red] {traceback.format_exc()}")
        except Exception as e:
            self.log_message(f"[red]Error processing frame for data table:[/red] {e}")
            self.log_message(f"[red]Error details:[/red] {type(e).__name__}: {str(e)}")
            self.log_message(f"[red]Traceback:[/red] {traceback.format_exc()}")
    
    def fallback_listener(self, tf, frame):
        """Handle received TinyFrame messages that don't have a specific listener.
        
        This function is registered as the fallback listener and will be called for any
        packet type that doesn't have a specific listener registered. It formats the data
        using the format_packet_data function and updates the DataTable using the
        update_data_table function.
        
        Args:
            tf: The TinyFrame instance
            frame: The received frame object
        """
        try:
            # Format the data based on packet type
            formatted_data = self.format_packet_data(frame.type, frame.data)
            
            # Update the data table
            self.update_data_table(frame, formatted_data)
        except Exception as e:
            self.log_message(f"[red]Error in fallback listener:[/red] {e}")
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
                    
            elif frame_type == 0xFFFF:  # 0xFFFF packet - Radar version information
                if len(data) >= 4:  # Ensure we have enough data
                    # Extract radar type and version from the data
                    radar_type_value = data[0]
                    major = data[1]
                    minor = data[2]
                    patch = data[3]
                    
                    # Format as a structured string with color highlighting and textual type
                    radar_type_name = get_radar_type_name(radar_type_value)
                    return (
                        f"[bold cyan]Radar Type:[/bold cyan] {radar_type_value} ({radar_type_name}), "
                        f"[bold green]Version:[/bold green] {major}.{minor}.{patch}"
                    )
            
            elif frame_type == 0xA08:  # 0xA08 packet - Point cloud
                # Expect at least 4 bytes for count
                # if there is only 4 zero bytes - ignore this message
                if len(data) >= 4:
                    count = struct.unpack('<I', data[0:4])[0]
                    # Calculate how many complete points we actually have in the payload
                    if count > 0:
                        max_points = (len(data) - 4) // 20  # each point: 5 floats = 20 bytes
                        actual = min(count, max_points)
                        points: list[CloudPoint] = []
                        offset = 4
                        for i in range(actual):
                            try:
                                pt = CloudPoint.from_bytes(data, offset)
                                points.append(pt)
                            except Exception as e:
                                self.log_message(f"[red]Error parsing point {i}:[/red] {e}")
                                break
                            offset += 20
                        # Update state and schedule redraw
                        self.current_point_cloud = points
                        self.call_after_refresh(self.draw_radar_plot)
                    return f"[bold red]PointCloud:[/bold red] {len(self.current_point_cloud)} point(s)"
            
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
            # Clear the current point cloud list
            self.current_point_cloud = []
            
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
        
        # Optionally disable firmware update button when disconnected
        try:
            fw_btn = self.query_one("#request_fw_update", Button)
            fw_btn.disabled = not connected
        except Exception:
            pass

        # Update the UI with connection status
        self.update_radar_status()
        
    def watch_radar_type(self, radar_type: str) -> None:
        """React to changes in the radar type."""

        self.update_radar_status()
        
    def watch_radar_version(self, radar_version: str) -> None:
        """React to changes in the radar version."""

        self.update_radar_status()
        
    def action_open_firmware(self) -> None:
        """Open the Firmware screen."""
        self.push_screen(FirmwareScreen())
        
    async def action_quit_app(self) -> None:
        """Quit the app gracefully: perform Disconnect actions, then exit."""
        try:
            await self.disconnect_serial()
        except Exception:
            pass
        # Exit the Textual application
        try:
            self.exit()
        except Exception:
            # Fallback for older Textual: action_quit if available
            try:
                self.app.action_quit()
            except Exception:
                pass
        
    def update_radar_status(self) -> None:
        """Update the radar status widget with radar information."""
        try:
            radar_status = self.query_one("#radar_status", Static)
            radar_status.border_title = "[bold]Radar Status[/bold]"
            # Create radar status content based on connection status and radar information
            if self.connected:
                status_text = (
                    f"[bold]Connection:[/bold] [green]Connected[/green]\n"
                    f"[bold]Radar Type:[/bold] ({self.radar_type}) [{get_radar_type_name(self.radar_type)}]\n"
                    f"[bold]Radar Version:[/bold] {self.radar_version}"
                )
            else:
                status_text = (
                    f"[bold]Connection:[/bold] [red]Disconnected[/red]\n"
                    f"[bold]Radar Type:[/bold] Unknown\n"
                    f"[bold]Radar Version:[/bold] Unknown"
                )
                
            # Update the radar status content
            radar_status.update(status_text)
            
        except Exception as e:
            self.log_message(f"[red]Error updating radar status:[/red] {e}")
            self.log_message(f"[red]Error details:[/red] {type(e).__name__}: {str(e)}")
            self.log_message(f"[red]Traceback:[/red] {traceback.format_exc()}")
    @work
    async def on_button_pressed(self, event: Button.Pressed) -> None:
        """Handle button press events."""
        button_id = event.button.id
        
        if button_id == "connect":
            await self.connect_serial()
        elif button_id == "disconnect":
            await self.disconnect_serial()
        elif button_id == "request_fw_update":
            # Show confirmation dialog before requesting firmware update
            message = (
                "You are switching the radar to firmware update mode. You cannot make the radar work again until you install the correct firmware, even if you turn it off and on. Continue?"
            )
            try:
                result = await self.push_screen_wait(ConfirmDialog(message))
            except Exception as e:
                # Fallback: if modal fails for some reason, default to not proceeding
                self.log_message(f"Exception: {e}")
                result = False
            self.log_message(f"Modal closed, result: {result}")
            if result:
                self.send_request_firmware_update()

    async def on_checkbox_changed(self, event: Checkbox.Changed) -> None:
        checkbox_id =event.checkbox.id

        if checkbox_id == "on_point_cloud":
            # If this change is triggered by an incoming message, do not send commands
            if getattr(self, "_suppress_on_point_cloud_event", False):
                self.call_after_refresh(self.draw_radar_plot)
                return
            # Send user's desired command. Do not force checkbox to device state immediately.
            import asyncio as _asyncio
            now = _asyncio.get_event_loop().time()
            if event.checkbox.value:
                # User requests OPEN: clear any close-grace window
                self._point_cloud_close_grace_until = 0.0
                self.send_open_point_cloud_display()
            else:
                # User requests CLOSE: start grace window to ignore transient incoming data
                self._point_cloud_close_grace_until = now + self._command_grace_sec
                self.send_close_point_cloud_display()
                # Ensure a timeout is scheduled based on the last known packet time
                self._schedule_feature_timeout("point_cloud")
            # Redraw
            self.call_after_refresh(self.draw_radar_plot)
        elif checkbox_id == "on_targets":
            # If this change is triggered by an incoming message, do not send commands
            if getattr(self, "_suppress_on_targets_event", False):
                self.call_after_refresh(self.draw_radar_plot)
                return
            # Send user's desired command. Do not force checkbox to device state immediately.
            import asyncio as _asyncio
            now = _asyncio.get_event_loop().time()
            if event.checkbox.value:
                # User requests OPEN: clear any close-grace window
                self._targets_close_grace_until = 0.0
                self.send_open_target_display()
            else:
                # User requests CLOSE: start grace window to ignore transient incoming data
                self._targets_close_grace_until = now + self._command_grace_sec
                self.send_close_target_display()
                # Ensure a timeout is scheduled based on the last known packet time
                self._schedule_feature_timeout("targets")
            # Redraw
            self.call_after_refresh(self.draw_radar_plot)

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
            
            # Request radar version information
            self.request_radar_version()
            
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
        
        # Reset radar version information
        self.radar_type = "Unknown"
        self.radar_version = "Unknown"
        
        # Reset device-derived feature states and reflect in UI
        self._targets_active = False
        self._point_cloud_active = False
        self._targets_close_grace_until = 0.0
        self._point_cloud_close_grace_until = 0.0
        try:
            cb_t = self.query_one("#on_targets", Checkbox)
            if cb_t.value is not False:
                self._suppress_on_targets_event = True
                try:
                    cb_t.value = False
                finally:
                    self._suppress_on_targets_event = False
        except Exception:
            pass
        try:
            cb_pc = self.query_one("#on_point_cloud", Checkbox)
            if cb_pc.value is not False:
                self._suppress_on_point_cloud_event = True
                try:
                    cb_pc.value = False
                finally:
                    self._suppress_on_point_cloud_event = False
        except Exception:
            pass

        self.log_message("[yellow]Disconnected from serial port[/yellow]")
    
    def request_radar_version(self) -> None:
        """Send a request for radar version information.
        
        This sends an empty packet with type 0xFFFF to the radar.
        The radar will respond with a packet of the same type containing
        4 bytes of data: radar type (1 byte) and version (3 bytes).
        """
        try:
            self.log_message("[cyan]Requesting radar version information...[/cyan]")
            # Send an empty packet with type 0xFFFF
            self.tf.send(0xFFFF, b"")
        except Exception as e:
            self.log_message(f"[red]Error requesting radar version:[/red] {e}")
            self.log_message(f"[red]Error details:[/red] {type(e).__name__}: {str(e)}")
            self.log_message(f"[red]Traceback:[/red] {traceback.format_exc()}")

    # 0x0201 message type functions
    
    def send_generate_interference_zone(self) -> None:
        """Send a command to automatically generate the interference zone.
        
        This sends a packet with type 0x0201 and command value 0x1.
        Message type 0x0201 is used for control instructions to the radar.
        """
        try:
            self.log_message("[cyan]Sending command to automatically generate the interference zone...[/cyan]")
            # Pack the command value (0x1) as a 4-byte uint32
            command_data = struct.pack("<I", 0x1)
            # Send the packet with type 0x0201 and the command data
            self.tf.send(0x0201, command_data)
        except Exception as e:
            self.log_message(f"[red]Error sending generate interference zone command:[/red] {e}")
            self.log_message(f"[red]Error details:[/red] {type(e).__name__}: {str(e)}")
            self.log_message(f"[red]Traceback:[/red] {traceback.format_exc()}")
            
    def send_acquire_areas(self) -> None:
        """Send a command to acquire the interference area and the detection area.
        
        This sends a packet with type 0x0201 and command value 0x2.
        Message type 0x0201 is used for control instructions to the radar.
        """
        try:
            self.log_message("[cyan]Sending command to acquire the interference area and detection area...[/cyan]")
            # Pack the command value (0x2) as a 4-byte uint32
            command_data = struct.pack("<I", 0x2)
            # Send the packet with type 0x0201 and the command data
            self.tf.send(0x0201, command_data)
        except Exception as e:
            self.log_message(f"[red]Error sending acquire areas command:[/red] {e}")
            self.log_message(f"[red]Error details:[/red] {type(e).__name__}: {str(e)}")
            self.log_message(f"[red]Traceback:[/red] {traceback.format_exc()}")
            
    def send_clear_interference_areas(self) -> None:
        """Send a command to clear the interference areas.
        
        This sends a packet with type 0x0201 and command value 0x3.
        Message type 0x0201 is used for control instructions to the radar.
        """
        try:
            self.log_message("[cyan]Sending command to clear the interference areas...[/cyan]")
            # Pack the command value (0x3) as a 4-byte uint32
            command_data = struct.pack("<I", 0x3)
            # Send the packet with type 0x0201 and the command data
            self.tf.send(0x0201, command_data)
        except Exception as e:
            self.log_message(f"[red]Error sending clear interference areas command:[/red] {e}")
            self.log_message(f"[red]Error details:[/red] {type(e).__name__}: {str(e)}")
            self.log_message(f"[red]Traceback:[/red] {traceback.format_exc()}")
            
    def send_reset_detection_area(self) -> None:
        """Send a command to reset the detection area.
        
        This sends a packet with type 0x0201 and command value 0x4.
        Message type 0x0201 is used for control instructions to the radar.
        """
        try:
            self.log_message("[cyan]Sending command to reset the detection area...[/cyan]")
            # Pack the command value (0x4) as a 4-byte uint32
            command_data = struct.pack("<I", 0x4)
            # Send the packet with type 0x0201 and the command data
            self.tf.send(0x0201, command_data)
        except Exception as e:
            self.log_message(f"[red]Error sending reset detection area command:[/red] {e}")
            self.log_message(f"[red]Error details:[/red] {type(e).__name__}: {str(e)}")
            self.log_message(f"[red]Traceback:[/red] {traceback.format_exc()}")
            
    def send_get_delay_time(self) -> None:
        """Send a command to get the delay time.
        
        This sends a packet with type 0x0201 and command value 0x5.
        Message type 0x0201 is used for control instructions to the radar.
        """
        try:
            self.log_message("[cyan]Sending command to get the delay time...[/cyan]")
            # Pack the command value (0x5) as a 4-byte uint32
            command_data = struct.pack("<I", 0x5)
            # Send the packet with type 0x0201 and the command data
            self.tf.send(0x0201, command_data)
        except Exception as e:
            self.log_message(f"[red]Error sending get delay time command:[/red] {e}")
            self.log_message(f"[red]Error details:[/red] {type(e).__name__}: {str(e)}")
            self.log_message(f"[red]Traceback:[/red] {traceback.format_exc()}")
            
    def send_open_point_cloud_display(self) -> None:
        """Send a command to open the point cloud display.
        
        This sends a packet with type 0x0201 and command value 0x6.
        Message type 0x0201 is used for control instructions to the radar.
        """
        try:
            self.log_message("[cyan]Sending command to open the point cloud display...[/cyan]")
            # Pack the command value (0x6) as a 4-byte uint32
            command_data = struct.pack("<I", 0x6)
            # Send the packet with type 0x0201 and the command data
            self.tf.send(0x0201, command_data)
        except Exception as e:
            self.log_message(f"[red]Error sending open point cloud display command:[/red] {e}")
            self.log_message(f"[red]Error details:[/red] {type(e).__name__}: {str(e)}")
            self.log_message(f"[red]Traceback:[/red] {traceback.format_exc()}")
            
    def send_close_point_cloud_display(self) -> None:
        """Send a command to close the point cloud display.
        
        This sends a packet with type 0x0201 and command value 0x7.
        Message type 0x0201 is used for control instructions to the radar.
        """
        try:
            self.log_message("[cyan]Sending command to close the point cloud display...[/cyan]")
            # Pack the command value (0x7) as a 4-byte uint32
            command_data = struct.pack("<I", 0x7)
            # Send the packet with type 0x0201 and the command data
            self.tf.send(0x0201, command_data)
        except Exception as e:
            self.log_message(f"[red]Error sending close point cloud display command:[/red] {e}")
            self.log_message(f"[red]Error details:[/red] {type(e).__name__}: {str(e)}")
            self.log_message(f"[red]Traceback:[/red] {traceback.format_exc()}")
            
    def send_open_target_display(self) -> None:
        """Send a command to open the target display.
        
        This sends a packet with type 0x0201 and command value 0x8.
        Message type 0x0201 is used for control instructions to the radar.
        """
        try:
            self.log_message("[cyan]Sending command to open the target display...[/cyan]")
            # Pack the command value (0x8) as a 4-byte uint32
            command_data = struct.pack("<I", 0x8)
            # Send the packet with type 0x0201 and the command data
            self.tf.send(0x0201, command_data)
        except Exception as e:
            self.log_message(f"[red]Error sending open target display command:[/red] {e}")
            self.log_message(f"[red]Error details:[/red] {type(e).__name__}: {str(e)}")
            self.log_message(f"[red]Traceback:[/red] {traceback.format_exc()}")
            
    def send_close_target_display(self) -> None:
        """Send a command to close the target display.
        
        This sends a packet with type 0x0201 and command value 0x9.
        Message type 0x0201 is used for control instructions to the radar.
        """
        try:
            self.log_message("[cyan]Sending command to close the target display...[/cyan]")
            # Pack the command value (0x9) as a 4-byte uint32
            command_data = struct.pack("<I", 0x9)
            # Send the packet with type 0x0201 and the command data
            self.tf.send(0x0201, command_data)
        except Exception as e:
            self.log_message(f"[red]Error sending close target display command:[/red] {e}")
            self.log_message(f"[red]Error details:[/red] {type(e).__name__}: {str(e)}")
            self.log_message(f"[red]Traceback:[/red] {traceback.format_exc()}")
            
    def send_set_detection_sensitivity_low(self) -> None:
        """Send a command to set the detection sensitivity to low.
        
        This sends a packet with type 0x0201 and command value 0xA.
        Message type 0x0201 is used for control instructions to the radar.
        """
        try:
            self.log_message("[cyan]Sending command to set the detection sensitivity to low...[/cyan]")
            # Pack the command value (0xA) as a 4-byte uint32
            command_data = struct.pack("<I", 0xA)
            # Send the packet with type 0x0201 and the command data
            self.tf.send(0x0201, command_data)
        except Exception as e:
            self.log_message(f"[red]Error sending set detection sensitivity to low command:[/red] {e}")
            self.log_message(f"[red]Error details:[/red] {type(e).__name__}: {str(e)}")
            self.log_message(f"[red]Traceback:[/red] {traceback.format_exc()}")
            
    def send_set_detection_sensitivity_medium(self) -> None:
        """Send a command to set the detection sensitivity to medium.
        
        This sends a packet with type 0x0201 and command value 0xB.
        Message type 0x0201 is used for control instructions to the radar.
        """
        try:
            self.log_message("[cyan]Sending command to set the detection sensitivity to medium...[/cyan]")
            # Pack the command value (0xB) as a 4-byte uint32
            command_data = struct.pack("<I", 0xB)
            # Send the packet with type 0x0201 and the command data
            self.tf.send(0x0201, command_data)
        except Exception as e:
            self.log_message(f"[red]Error sending set detection sensitivity to medium command:[/red] {e}")
            self.log_message(f"[red]Error details:[/red] {type(e).__name__}: {str(e)}")
            self.log_message(f"[red]Traceback:[/red] {traceback.format_exc()}")
            
    def send_set_detection_sensitivity_high(self) -> None:
        """Send a command to set the detection sensitivity to high.
        
        This sends a packet with type 0x0201 and command value 0xC.
        Message type 0x0201 is used for control instructions to the radar.
        """
        try:
            self.log_message("[cyan]Sending command to set the detection sensitivity to high...[/cyan]")
            # Pack the command value (0xC) as a 4-byte uint32
            command_data = struct.pack("<I", 0xC)
            # Send the packet with type 0x0201 and the command data
            self.tf.send(0x0201, command_data)
        except Exception as e:
            self.log_message(f"[red]Error sending set detection sensitivity to high command:[/red] {e}")
            self.log_message(f"[red]Error details:[/red] {type(e).__name__}: {str(e)}")
            self.log_message(f"[red]Traceback:[/red] {traceback.format_exc()}")
            
    def send_get_detection_sensitivity_status(self) -> None:
        """Send a command to get the detection sensitivity status.
        
        This sends a packet with type 0x0201 and command value 0xD.
        Message type 0x0201 is used for control instructions to the radar.
        """
        try:
            self.log_message("[cyan]Sending command to get the detection sensitivity status...[/cyan]")
            # Pack the command value (0xD) as a 4-byte uint32
            command_data = struct.pack("<I", 0xD)
            # Send the packet with type 0x0201 and the command data
            self.tf.send(0x0201, command_data)
        except Exception as e:
            self.log_message(f"[red]Error sending get detection sensitivity status command:[/red] {e}")
            self.log_message(f"[red]Error details:[/red] {type(e).__name__}: {str(e)}")
            self.log_message(f"[red]Traceback:[/red] {traceback.format_exc()}")
            
    def send_set_trigger_speed_slow(self) -> None:
        """Send a command to set the trigger speed to slow.
        
        This sends a packet with type 0x0201 and command value 0xE.
        Message type 0x0201 is used for control instructions to the radar.
        """
        try:
            self.log_message("[cyan]Sending command to set the trigger speed to slow...[/cyan]")
            # Pack the command value (0xE) as a 4-byte uint32
            command_data = struct.pack("<I", 0xE)
            # Send the packet with type 0x0201 and the command data
            self.tf.send(0x0201, command_data)
        except Exception as e:
            self.log_message(f"[red]Error sending set trigger speed to slow command:[/red] {e}")
            self.log_message(f"[red]Error details:[/red] {type(e).__name__}: {str(e)}")
            self.log_message(f"[red]Traceback:[/red] {traceback.format_exc()}")
            
    def send_set_trigger_speed_medium(self) -> None:
        """Send a command to set the trigger speed to medium.
        
        This sends a packet with type 0x0201 and command value 0xF.
        Message type 0x0201 is used for control instructions to the radar.
        """
        try:
            self.log_message("[cyan]Sending command to set the trigger speed to medium...[/cyan]")
            # Pack the command value (0xF) as a 4-byte uint32
            command_data = struct.pack("<I", 0xF)
            # Send the packet with type 0x0201 and the command data
            self.tf.send(0x0201, command_data)
        except Exception as e:
            self.log_message(f"[red]Error sending set trigger speed to medium command:[/red] {e}")
            self.log_message(f"[red]Error details:[/red] {type(e).__name__}: {str(e)}")
            self.log_message(f"[red]Traceback:[/red] {traceback.format_exc()}")
            
    def send_set_trigger_speed_fast(self) -> None:
        """Send a command to set the trigger speed to fast.
        
        This sends a packet with type 0x0201 and command value 0x10.
        Message type 0x0201 is used for control instructions to the radar.
        """
        try:
            self.log_message("[cyan]Sending command to set the trigger speed to fast...[/cyan]")
            # Pack the command value (0x10) as a 4-byte uint32
            command_data = struct.pack("<I", 0x10)
            # Send the packet with type 0x0201 and the command data
            self.tf.send(0x0201, command_data)
        except Exception as e:
            self.log_message(f"[red]Error sending set trigger speed to fast command:[/red] {e}")
            self.log_message(f"[red]Error details:[/red] {type(e).__name__}: {str(e)}")
            self.log_message(f"[red]Traceback:[/red] {traceback.format_exc()}")
            
    def send_get_trigger_speed_status(self) -> None:
        """Send a command to get the trigger speed status.
        
        This sends a packet with type 0x0201 and command value 0x11.
        Message type 0x0201 is used for control instructions to the radar.
        """
        try:
            self.log_message("[cyan]Sending command to get the trigger speed status...[/cyan]")
            # Pack the command value (0x11) as a 4-byte uint32
            command_data = struct.pack("<I", 0x11)
            # Send the packet with type 0x0201 and the command data
            self.tf.send(0x0201, command_data)
        except Exception as e:
            self.log_message(f"[red]Error sending get trigger speed status command:[/red] {e}")
            self.log_message(f"[red]Error details:[/red] {type(e).__name__}: {str(e)}")
            self.log_message(f"[red]Traceback:[/red] {traceback.format_exc()}")
            
    def send_get_z_axis_range(self) -> None:
        """Send a command to get the Z-axis range.
        
        This sends a packet with type 0x0201 and command value 0x12.
        Message type 0x0201 is used for control instructions to the radar.
        Note: This command applies to 3D radar only.
        """
        try:
            self.log_message("[cyan]Sending command to get the Z-axis range...[/cyan]")
            # Pack the command value (0x12) as a 4-byte uint32
            command_data = struct.pack("<I", 0x12)
            # Send the packet with type 0x0201 and the command data
            self.tf.send(0x0201, command_data)
        except Exception as e:
            self.log_message(f"[red]Error sending get Z-axis range command:[/red] {e}")
            self.log_message(f"[red]Error details:[/red] {type(e).__name__}: {str(e)}")
            self.log_message(f"[red]Traceback:[/red] {traceback.format_exc()}")
            
    def send_set_installation_top_mounted(self) -> None:
        """Send a command to set the installation as top mounted.
        
        This sends a packet with type 0x0201 and command value 0x13.
        Message type 0x0201 is used for control instructions to the radar.
        Note: This command applies to 3D radar only.
        """
        try:
            self.log_message("[cyan]Sending command to set the installation as top mounted...[/cyan]")
            # Pack the command value (0x13) as a 4-byte uint32
            command_data = struct.pack("<I", 0x13)
            # Send the packet with type 0x0201 and the command data
            self.tf.send(0x0201, command_data)
        except Exception as e:
            self.log_message(f"[red]Error sending set installation as top mounted command:[/red] {e}")
            self.log_message(f"[red]Error details:[/red] {type(e).__name__}: {str(e)}")
            self.log_message(f"[red]Traceback:[/red] {traceback.format_exc()}")
            
    def send_set_installation_side_mounted(self) -> None:
        """Send a command to set the installation as side mounted.
        
        This sends a packet with type 0x0201 and command value 0x14.
        Message type 0x0201 is used for control instructions to the radar.
        Note: This command applies to 3D radar only.
        """
        try:
            self.log_message("[cyan]Sending command to set the installation as side mounted...[/cyan]")
            # Pack the command value (0x14) as a 4-byte uint32
            command_data = struct.pack("<I", 0x14)
            # Send the packet with type 0x0201 and the command data
            self.tf.send(0x0201, command_data)
        except Exception as e:
            self.log_message(f"[red]Error sending set installation as side mounted command:[/red] {e}")
            self.log_message(f"[red]Error details:[/red] {type(e).__name__}: {str(e)}")
            self.log_message(f"[red]Traceback:[/red] {traceback.format_exc()}")
            
    def send_get_installation_method(self) -> None:
        """Send a command to get the installation method.
        
        This sends a packet with type 0x0201 and command value 0x15.
        Message type 0x0201 is used for control instructions to the radar.
        """
        try:
            self.log_message("[cyan]Sending command to set the installation method...[/cyan]")
            # Pack the command value (0x15) as a 4-byte uint32
            command_data = struct.pack("<I", 0x15)
            # Send the packet with type 0x0201 and the command data
            self.tf.send(0x0201, command_data)
        except Exception as e:
            self.log_message(f"[red]Error sending set installation method command:[/red] {e}")
            self.log_message(f"[red]Error details:[/red] {type(e).__name__}: {str(e)}")
            self.log_message(f"[red]Traceback:[/red] {traceback.format_exc()}")
            
    def send_turn_on_low_power_mode(self) -> None:
        """Send a command to turn on the low power mode when unattended.
        
        This sends a packet with type 0x0201 and command value 0x16.
        Message type 0x0201 is used for control instructions to the radar.
        """
        try:
            self.log_message("[cyan]Sending command to turn on the low power mode when unattended...[/cyan]")
            # Pack the command value (0x16) as a 4-byte uint32
            command_data = struct.pack("<I", 0x16)
            # Send the packet with type 0x0201 and the command data
            self.tf.send(0x0201, command_data)
        except Exception as e:
            self.log_message(f"[red]Error sending turn on low power mode command:[/red] {e}")
            self.log_message(f"[red]Error details:[/red] {type(e).__name__}: {str(e)}")
            self.log_message(f"[red]Traceback:[/red] {traceback.format_exc()}")
            
    def send_turn_off_low_power_mode(self) -> None:
        """Send a command to turn off the low power mode when unattended.
        
        This sends a packet with type 0x0201 and command value 0x17.
        Message type 0x0201 is used for control instructions to the radar.
        """
        try:
            self.log_message("[cyan]Sending command to turn off the low power mode when unattended...[/cyan]")
            # Pack the command value (0x17) as a 4-byte uint32
            command_data = struct.pack("<I", 0x17)
            # Send the packet with type 0x0201 and the command data
            self.tf.send(0x0201, command_data)
        except Exception as e:
            self.log_message(f"[red]Error sending turn off low power mode command:[/red] {e}")
            self.log_message(f"[red]Error details:[/red] {type(e).__name__}: {str(e)}")
            self.log_message(f"[red]Traceback:[/red] {traceback.format_exc()}")
            
    def send_get_low_power_mode_status(self) -> None:
        """Send a command to obtain whether the low power mode is turned on when unattended.
        
        This sends a packet with type 0x0201 and command value 0x18.
        Message type 0x0201 is used for control instructions to the radar.
        """
        try:
            self.log_message("[cyan]Sending command to get the low power mode status...[/cyan]")
            # Pack the command value (0x18) as a 4-byte uint32
            command_data = struct.pack("<I", 0x18)
            # Send the packet with type 0x0201 and the command data
            self.tf.send(0x0201, command_data)
        except Exception as e:
            self.log_message(f"[red]Error sending get low power mode status command:[/red] {e}")
            self.log_message(f"[red]Error details:[/red] {type(e).__name__}: {str(e)}")
            self.log_message(f"[red]Traceback:[/red] {traceback.format_exc()}")
            
    def send_get_sleeping_time(self) -> None:
        """Send a command to obtain the sleeping time in the low power mode when unattended.
        
        This sends a packet with type 0x0201 and command value 0x19.
        Message type 0x0201 is used for control instructions to the radar.
        """
        try:
            self.log_message("[cyan]Sending command to get the sleeping time in low power mode...[/cyan]")
            # Pack the command value (0x19) as a 4-byte uint32
            command_data = struct.pack("<I", 0x19)
            # Send the packet with type 0x0201 and the command data
            self.tf.send(0x0201, command_data)
        except Exception as e:
            self.log_message(f"[red]Error sending get sleeping time command:[/red] {e}")
            self.log_message(f"[red]Error details:[/red] {type(e).__name__}: {str(e)}")
            self.log_message(f"[red]Traceback:[/red] {traceback.format_exc()}")
            
    def send_reset_unattended_status(self) -> None:
        """Send a command to reset the unattended status.
        
        This sends a packet with type 0x0201 and command value 0x1A.
        Message type 0x0201 is used for control instructions to the radar.
        """
        try:
            self.log_message("[cyan]Sending command to reset the unattended status...[/cyan]")
            # Pack the command value (0x1A) as a 4-byte uint32
            command_data = struct.pack("<I", 0x1A)
            # Send the packet with type 0x0201 and the command data
            self.tf.send(0x0201, command_data)
        except Exception as e:
            self.log_message(f"[red]Error sending reset unattended status command:[/red] {e}")
            self.log_message(f"[red]Error details:[/red] {type(e).__name__}: {str(e)}")
            self.log_message(f"[red]Traceback:[/red] {traceback.format_exc()}")
            
    def send_request_firmware_update(self) -> None:
        """Request the radar to enter Firmware Update mode by sending type 0x3000."""
        try:
            self.log_message("[cyan]Requesting Firmware Update mode (type 0x3000)...[/cyan]")
            # Send an empty packet with type 0x3000 to trigger firmware update mode
            self.tf.send(0x3000, b"")
            self.log_message("[green]Firmware Update request sent.[/green]")
        except Exception as e:
            self.log_message(f"[red]Error sending Firmware Update request:[/red] {e}")
            self.log_message(f"[red]Error details:[/red] {type(e).__name__}: {str(e)}")
            self.log_message(f"[red]Traceback:[/red] {traceback.format_exc()}")
            
    def send_packet(self, packet_type: int, data: bytes = b"") -> None:
        """Send a packet with the specified type and data to the radar.
        
        This is a generic function for sending any packet type to the radar.
        
        Args:
            packet_type: The type of the packet to send (e.g., 0xA0A, 0xA04, 0xFFFF)
            data: The binary data to include in the packet (default: empty)
        """
        try:
            self.log_message(f"[cyan]Sending packet type 0x{packet_type:X} with {len(data)} bytes of data...[/cyan]")
            # Send the packet with the specified type and data
            self.tf.send(packet_type, data)
        except Exception as e:
            self.log_message(f"[red]Error sending packet type 0x{packet_type:X}:[/red] {e}")
            self.log_message(f"[red]Error details:[/red] {type(e).__name__}: {str(e)}")
            self.log_message(f"[red]Traceback:[/red] {traceback.format_exc()}")
    
    def area_data_listener(self, tf, frame):
        """Handle the area data packet (type 0xA0A).
        
        This function is registered as a specific listener for packet type 0xA0A (area data).
        It formats the data using the format_packet_data function and updates the DataTable
        using the update_data_table function.
        
        The packet contains area data information with 4 areas (area0, area1, area2, area3).
        
        Args:
            tf: The TinyFrame instance
            frame: The received frame object with type 0xA0A
        """
        try:
            # Format the data using the existing format_packet_data function
            formatted_data = self.format_packet_data(frame.type, frame.data)
            
            # Update the data table
            self.update_data_table(frame, formatted_data)
        except Exception as e:
            self.log_message(f"[red]Error in area data listener:[/red] {e}")
            self.log_message(f"[red]Error details:[/red] {type(e).__name__}: {str(e)}")
            self.log_message(f"[red]Traceback:[/red] {traceback.format_exc()}")
    
    def target_coordinates_listener(self, tf, frame):
        """Handle the target coordinates packet (type 0xA04).
        
        This function is registered as a specific listener for packet type 0xA04 (target coordinates).
        It formats the data using the format_packet_data function and updates the DataTable
        using the update_data_table function.
        
        The packet contains target coordinate information for up to 4 targets, including
        position, velocity, and other target attributes.
        
        Additionally, upon receiving this message, we set the UI checkbox "Targets" to True
        to reflect that the radar has this feature enabled already. We avoid sending any
        control command in response to this packet.
        
        Args:
            tf: The TinyFrame instance
            frame: The received frame object with type 0xA04
        """
        try:
            # Format the data using the existing format_packet_data function
            formatted_data = self.format_packet_data(frame.type, frame.data)
            
            # Update the data table
            self.update_data_table(frame, formatted_data)
            
            # Update state and ensure the Targets checkbox reflects that targets are enabled when 0xA04 arrives
            import asyncio as _asyncio
            loop = _asyncio.get_event_loop()
            now = loop.time()
            # Respect close-grace window: ignore enabling while grace is active
            if now < getattr(self, "_targets_close_grace_until", 0.0):
                # Do not update last time or active state to allow timeout to turn it off
                pass
            else:
                self._last_a04_time = now
                if not self._targets_active:
                    self._targets_active = True
                    try:
                        checkbox = self.query_one("#on_targets", Checkbox)
                        if checkbox.value is not True:
                            # Suppress event side-effects while updating programmatically
                            self._suppress_on_targets_event = True
                            try:
                                checkbox.value = True
                            finally:
                                self._suppress_on_targets_event = False
                            # Redraw to reflect UI state immediately
                            self.call_after_refresh(self.draw_radar_plot)
                    except Exception:
                        # If UI not ready or checkbox not found, ignore silently
                        pass
                # Schedule timeout to turn it off if no packets arrive for a while
                self._schedule_feature_timeout("targets")
        except Exception as e:
            self.log_message(f"[red]Error in target coordinates listener:[/red] {e}")
            self.log_message(f"[red]Error details:[/red] {type(e).__name__}: {str(e)}")
            self.log_message(f"[red]Traceback:[/red] {traceback.format_exc()}")
    
    def point_cloud_listener(self, tf, frame):
        """Handle the point cloud packet (type 0xA08).
        Parses points, updates state, and updates the DataTable.
        Additionally, upon receiving this message, set the "Point Cloud" checkbox to True
        to reflect that the radar has this feature enabled already, without sending any
        control commands in response.
        """
        try:
            formatted_data = self.format_packet_data(frame.type, frame.data)
            self.update_data_table(frame, formatted_data)

            # Update state and ensure the Point Cloud checkbox reflects that PC is enabled when 0xA08 arrives
            import asyncio as _asyncio
            loop = _asyncio.get_event_loop()
            now = loop.time()
            # Respect close-grace window: ignore enabling while grace is active
            if now < getattr(self, "_point_cloud_close_grace_until", 0.0):
                # Do not update last time or active state to allow timeout to turn it off
                pass
            else:
                self._last_a08_time = now
                if not self._point_cloud_active:
                    self._point_cloud_active = True
                    try:
                        checkbox = self.query_one("#on_point_cloud", Checkbox)
                        if checkbox.value is not True:
                            # Suppress event side-effects while updating programmatically
                            self._suppress_on_point_cloud_event = True
                            try:
                                checkbox.value = True
                            finally:
                                self._suppress_on_point_cloud_event = False
                            # Redraw to reflect UI state immediately
                            self.call_after_refresh(self.draw_radar_plot)
                    except Exception:
                        # If UI not ready or checkbox not found, ignore silently
                        pass
                # Schedule timeout to turn it off if no packets arrive for a while
                self._schedule_feature_timeout("point_cloud")
        except Exception as e:
            self.log_message(f"[red]Error in point cloud listener:[/red] {e}")
            self.log_message(f"[red]Error details:[/red] {type(e).__name__}: {str(e)}")
            self.log_message(f"[red]Traceback:[/red] {traceback.format_exc()}")
    
    def _schedule_feature_timeout(self, feature: str) -> None:
        """Schedule a timeout check for a feature ('targets' or 'point_cloud').
        If no new packets are received within the timeout window, mark the feature as inactive
        and update the checkbox to False programmatically.
        """
        try:
            import asyncio as _asyncio
            loop = _asyncio.get_event_loop()
            if feature == "targets":
                expected = self._last_a04_time
            elif feature == "point_cloud":
                expected = self._last_a08_time
            else:
                return
            _asyncio.create_task(self._feature_timeout_task(feature, expected))
        except Exception:
            pass

    async def _feature_timeout_task(self, feature: str, expected_time: float) -> None:
        """Async task to wait for timeout and set feature to False if stale."""
        try:
            import asyncio as _asyncio
            await _asyncio.sleep(self._feature_timeout_sec)
            # Check if a newer packet arrived since scheduling
            if feature == "targets":
                if expected_time != self._last_a04_time:
                    return
                if self._targets_active:
                    self._targets_active = False
                    try:
                        checkbox = self.query_one("#on_targets", Checkbox)
                        if checkbox.value is not False:
                            self._suppress_on_targets_event = True
                            try:
                                checkbox.value = False
                            finally:
                                self._suppress_on_targets_event = False
                            self.call_after_refresh(self.draw_radar_plot)
                    except Exception:
                        pass
            elif feature == "point_cloud":
                if expected_time != self._last_a08_time:
                    return
                if self._point_cloud_active:
                    self._point_cloud_active = False
                    try:
                        checkbox = self.query_one("#on_point_cloud", Checkbox)
                        if checkbox.value is not False:
                            self._suppress_on_point_cloud_event = True
                            try:
                                checkbox.value = False
                            finally:
                                self._suppress_on_point_cloud_event = False
                            self.call_after_refresh(self.draw_radar_plot)
                    except Exception:
                        pass
        except Exception as e:
            self.log_message(f"[red]Error in feature timeout task ({feature}):[/red] {e}")
    
    def version_listener(self, tf, frame):
        """Handle the radar version information packet (type 0xFFFF).
        
        This function is registered as a specific listener for packet type 0xFFFF (radar version).
        It extracts radar type and version information from the packet, updates the application's
        reactive variables, and also updates the DataTable using the update_data_table function.
        
        The packet contains 4 bytes of data:
        - First byte: radar type
        - Next 3 bytes: version in format 1.2.3
        
        Args:
            tf: The TinyFrame instance
            frame: The received frame object with type 0xFFFF
        """
        try:
            if frame.len >= 4:  # Ensure we have enough data
                # Extract radar type and version from the data
                radar_type_value = frame.data[0]
                major = frame.data[1]
                minor = frame.data[2]
                patch = frame.data[3]
                
                # Format the version string
                version_str = f"{major}.{minor}.{patch}"
                
                # Update the reactive variables
                self.radar_type = radar_type_value
                self.radar_version = version_str
                radar_type_name = get_radar_type_name(radar_type_value)

                self.log_message(f"[green]Radar version information received: Type {radar_type_value} ({radar_type_name}), Version {version_str}[/green]")
                
                # Format the data using the existing format_packet_data function
                formatted_data = self.format_packet_data(frame.type, frame.data)
                
                # Update the data table
                self.update_data_table(frame, formatted_data)
            else:
                self.log_message(f"[yellow]Received version packet with insufficient data: {frame.len} bytes[/yellow]")
        except Exception as e:
            self.log_message(f"[red]Error processing version information:[/red] {e}")
            self.log_message(f"[red]Error details:[/red] {type(e).__name__}: {str(e)}")
            self.log_message(f"[red]Traceback:[/red] {traceback.format_exc()}")
    
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
