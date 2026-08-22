"""Terminal widget for Fleet TUI - an interactive shell embedded in Textual."""

import asyncio
import fcntl
import os
import pty
import signal
import struct
import sys
import termios
from typing import Optional

from rich.text import Text
from textual import events
from textual.app import ComposeResult
from textual.widget import Widget
from textual.widgets import Static
import pyte


class TerminalPane(Widget):
    """An interactive PTY-backed terminal embedded in the TUI."""

    # Enable keyboard focus
    can_focus = True

    def __init__(
        self,
        *,
        name: Optional[str] = None,
        id: Optional[str] = None,
        classes: Optional[str] = None,
    ):
        super().__init__(name=name, id=id, classes=classes)
        self._fd: Optional[int] = None
        self._pid: Optional[int] = None
        self._screen: Optional[pyte.Screen] = None
        self._stream: Optional[pyte.ByteStream] = None
        self._reader: Optional[asyncio.AbstractEventLoop] = None

    # NOTE: NO compose() — this widget draws itself via render() (the pyte screen). A child widget
    # would take over display and render() would never be called (→ an empty terminal).

    async def on_mount(self) -> None:
        """Initialize the terminal when mounted."""
        # Fork a PTY
        pid, fd = pty.fork()

        if pid == 0:
            # Child process: exec the shell. If exec fails, exit HARD — never run app code as the child.
            try:
                shell = os.environ.get("SHELL", "/bin/bash")
                os.execvp(shell, [shell])
            except Exception:
                os._exit(1)
        else:
            # Parent process
            self._fd = fd
            self._pid = pid
            
            # Set PTY window size to default 80x20
            rows, cols = 20, 80
            self._screen = pyte.Screen(cols, rows)
            self._stream = pyte.ByteStream(self._screen)
            
            # Set the PTY window size
            try:
                fcntl.ioctl(
                    self._fd,
                    termios.TIOCSWINSZ,
                    struct.pack("HHHH", rows, cols, 0, 0)
                )
            except Exception:
                pass
            
            # Register the fd with the RUNNING event loop for non-blocking reads
            loop = asyncio.get_running_loop()
            self._reader = loop
            loop.add_reader(self._fd, self._on_readable)

    async def on_resize(self, event: events.Resize) -> None:
        """Handle resize events."""
        if not self._fd or not self._screen:
            return
            
        # Get new size from widget
        width, height = self.size
        
        # Convert to rows and cols (assuming 80x24 default for now)
        rows = max(1, height)
        cols = max(1, width)
        
        # Resize the screen
        self._screen.resize(rows, cols)
        
        # Update PTY window size
        try:
            fcntl.ioctl(
                self._fd,
                termios.TIOCSWINSZ,
                struct.pack("HHHH", rows, cols, 0, 0)
            )
        except Exception:
            pass
        
        # Force redraw
        self.refresh()

    async def on_show(self) -> None:
        """Handle when the widget is shown."""
        if not self._fd or not self._screen:
            return
            
        # Get new size from widget
        width, height = self.size
        
        # Convert to rows and cols (assuming 80x24 default for now)
        rows = max(1, height)
        cols = max(1, width)
        
        # Resize the screen
        self._screen.resize(rows, cols)
        
        # Update PTY window size
        try:
            fcntl.ioctl(
                self._fd,
                termios.TIOCSWINSZ,
                struct.pack("HHHH", rows, cols, 0, 0)
            )
        except Exception:
            pass

    def _on_readable(self) -> None:
        """Handle readable data from the PTY."""
        if not self._fd or not self._stream:
            return
            
        try:
            data = os.read(self._fd, 65536)
            if not data:  # EOF
                # Shell has exited
                if self._reader:
                    try:
                        self._reader.remove_reader(self._fd)
                    except Exception:
                        pass
                self._cleanup()
                return
                
            self._stream.feed(data)
            self.refresh()
            
        except (OSError, Exception):
            # Handle read errors or EOF
            if self._fd is not None:
                try:
                    if self._reader:
                        self._reader.remove_reader(self._fd)
                except Exception:
                    pass
                self._cleanup()

    def render(self) -> Text:
        """Render the terminal content."""
        if not self._screen:
            return Text("Terminal not initialized")
            
        # Build text from screen lines
        lines = []
        for line in self._screen.display:
            # For now, just return plain text; colors/attributes can be added later
            lines.append(line)
            
        return Text("\n".join(lines))

    async def on_key(self, event: events.Key) -> None:
        """Handle key presses."""
        if not self._fd:
            return
            
        # Translate the key to bytes
        data = b""
        
        if event.character:
            # Printable character
            try:
                data = event.character.encode("utf-8")
            except Exception:
                pass
        elif event.key == "enter":
            data = b"\r"
        elif event.key == "backspace":
            data = b"\x7f"
        elif event.key == "tab":
            data = b"\t"
        elif event.key == "up":
            data = b"\x1b[A"
        elif event.key == "down":
            data = b"\x1b[B"
        elif event.key == "right":
            data = b"\x1b[C"
        elif event.key == "left":
            data = b"\x1b[D"
        elif event.key == "ctrl_c":
            data = b"\x03"
        elif event.key == "ctrl_d":
            data = b"\x04"
        # Add more keys as needed
            
        if data:
            try:
                os.write(self._fd, data)
            except Exception:
                pass  # Ignore write errors
            finally:
                event.stop()

    def _cleanup(self) -> None:
        """Clean up the PTY when closing."""
        if self._fd is not None:
            try:
                if self._reader:
                    try:
                        self._reader.remove_reader(self._fd)
                    except Exception:
                        pass
                os.close(self._fd)
            except Exception:
                pass
                
        if self._pid is not None:
            try:
                # Never block Textual's shutdown loop waiting for a child shell.  A hidden terminal
                # may have descendants, so a blocking waitpid can prevent the entire app/test from
                # closing even after the terminal itself is no longer usable.
                os.kill(self._pid, signal.SIGTERM)
                pid, _status = os.waitpid(self._pid, os.WNOHANG)
                if pid == 0:
                    os.kill(self._pid, signal.SIGKILL)
            except Exception:
                # If it already died, just ignore
                pass
        self._fd = None
        self._pid = None

    async def on_unmount(self) -> None:
        """Clean up when widget is unmounted."""
        self._cleanup()
