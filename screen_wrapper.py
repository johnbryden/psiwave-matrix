"""
Screen backend for psiwave-matrix: drop-in matrix/canvas implementation
using a pygame window. Use this on Windows, WSL2, or Linux desktop when
the LED hardware is not available.
"""

import os
import time

import numpy as np

# Enable with PSIWAVE_DEBUG_SCREEN=1
_DEBUG = os.environ.get("PSIWAVE_DEBUG_SCREEN", "").strip() not in ("", "0", "false", "False", "no", "NO")


def _dbg(msg: str) -> None:
    if _DEBUG:
        print(f"[screen] {msg}", flush=True)


def _configure_wsl_display() -> None:
    """Set DISPLAY and SDL_VIDEODRIVER so a pygame window can appear in WSL2."""
    # Detect WSL2 (Microsoft in /proc/version or WSL_DISTRO_NAME set)
    try:
        with open("/proc/version", "r") as f:
            is_wsl = "microsoft" in f.read().lower()
    except OSError:
        is_wsl = bool(os.environ.get("WSL_DISTRO_NAME"))
    if not is_wsl:
        return

    # If you see a taskbar icon but the window is off-screen, centering helps a lot.
    os.environ.setdefault("SDL_VIDEO_CENTERED", "1")

    # WSLg (Windows 11) and X servers (VcXsrv etc.) need DISPLAY set.
    if not os.environ.get("DISPLAY"):
        # WSLg (Windows 11) uses :0. For VcXsrv on Windows host use: export DISPLAY=$(grep nameserver /etc/resolv.conf | awk '{print $2}'):0
        os.environ["DISPLAY"] = ":0"

    # Pick an SDL video backend unless the user already forced one.
    if "SDL_VIDEODRIVER" not in os.environ:
        # Default to X11 on WSL. It's the most universally available backend across:
        # - WSLg (via XWayland)
        # - external X servers (VcXsrv/X410/etc.)
        # Wayland support depends on how SDL/pygame was built; users can opt-in by
        # exporting SDL_VIDEODRIVER=wayland explicitly.
        os.environ["SDL_VIDEODRIVER"] = "x11"


class ScreenClosed(Exception):
    """Raised when the user closes the display window or presses q/Escape."""


class ScreenCanvas:
    """Canvas that draws into a numpy RGB buffer. API matches rgbmatrix FrameCanvas."""

    def __init__(self, buffer: np.ndarray, width: int, height: int):
        self._buffer = buffer  # shape (height, width, 3), uint8
        self._width = width
        self._height = height

    @property
    def width(self) -> int:
        return self._width

    @property
    def height(self) -> int:
        return self._height

    def SetPixel(self, x: int, y: int, r: int, g: int, b: int) -> None:
        if 0 <= x < self._width and 0 <= y < self._height:
            self._buffer[y, x, 0] = min(255, max(0, r))
            self._buffer[y, x, 1] = min(255, max(0, g))
            self._buffer[y, x, 2] = min(255, max(0, b))

    def Clear(self) -> None:
        self._buffer.fill(0)


class ScreenMatrix:
    """
    Matrix-like display that uses a pygame window. Same interface as rgbmatrix
    for CreateFrameCanvas(), SwapOnVSync(), Clear(), and .height / .width.
    """

    def __init__(self, width: int = 80, height: int = 40, scale: int = 8):
        self._width = width
        self._height = height
        self._scale = max(1, scale)
        self._win_w = self._width * self._scale
        self._win_h = self._height * self._scale
        # Double-buffer: two buffers, two canvases; SwapOnVSync returns the other canvas.
        self._buf0 = np.zeros((height, width, 3), dtype=np.uint8)
        self._buf1 = np.zeros((height, width, 3), dtype=np.uint8)
        self._canvas0 = ScreenCanvas(self._buf0, width, height)
        self._canvas1 = ScreenCanvas(self._buf1, width, height)
        self._front = 0  # which canvas to return next from CreateFrameCanvas
        self._pygame = None
        self._screen = None
        self._frame_surface = None
        self._closed = False
        self._fullscreen = False
        self._windowed_size = (self._win_w, self._win_h)
        self._frame_count = 0
        self._last_dbg_t = -1.0
        self._font = None
        self._last_frame_t = time.monotonic()
        self._fps_display = 0.0
        self._render_ms_display = 0.0
        if _DEBUG:
            _dbg("Debug enabled (PSIWAVE_DEBUG_SCREEN=1)")
        self._init_display()

    def _init_display(self) -> None:
        _configure_wsl_display()
        # Prevent fullscreen window from minimizing when clicking another window (e.g. on extended display).
        os.environ.setdefault("SDL_VIDEO_MINIMIZE_ON_FOCUS_LOSS", "0")
        try:
            import pygame
        except ImportError as e:
            raise RuntimeError(
                "pygame is required for screen mode. Install with: pip install pygame"
            ) from e
        self._pygame = pygame
        self._pygame.init()
        try:
            self._screen = self._pygame.display.set_mode((self._win_w, self._win_h))
        except self._pygame.error as e:
            # Some environments (notably certain WSL/SDL builds) expose WAYLAND_DISPLAY but
            # SDL is not built with Wayland support. If that happens, automatically retry
            # using X11 rather than crashing.
            driver = os.environ.get("SDL_VIDEODRIVER", "").lower()
            msg = str(e).lower()
            if driver == "wayland" and ("wayland not available" in msg or "no available video device" in msg):
                if _DEBUG:
                    _dbg(f"Wayland init failed ({e}); retrying with SDL_VIDEODRIVER=x11")
                try:
                    self._pygame.quit()
                except Exception:
                    pass
                os.environ["SDL_VIDEODRIVER"] = "x11"
                self._pygame.init()
                try:
                    self._screen = self._pygame.display.set_mode((self._win_w, self._win_h))
                except self._pygame.error as e2:
                    disp = os.environ.get("DISPLAY", "(not set)")
                    raise RuntimeError(
                        f"Cannot open display: {e2}. "
                        f"DISPLAY={disp}. "
                        "On WSL2: install VcXsrv on Windows, start it, then run "
                        "export DISPLAY=$(grep nameserver /etc/resolv.conf | awk '{print $2}'):0 ; "
                        "or use Windows 11 with 'wsl --update' for WSLg."
                    ) from e2
            else:
                disp = os.environ.get("DISPLAY", "(not set)")
                raise RuntimeError(
                    f"Cannot open display: {e}. "
                    f"DISPLAY={disp}. "
                    "On WSL2: install VcXsrv on Windows, start it, then run "
                    "export DISPLAY=$(grep nameserver /etc/resolv.conf | awk '{print $2}'):0 ; "
                    "or use Windows 11 with 'wsl --update' for WSLg."
                ) from e
        self._pygame.display.set_caption("psiwave-matrix (screen)")

        # A stable intermediate surface avoids some driver/compositor quirks (notably WSLg/XWayland).
        # We blit the numpy RGB buffer into this, then scale/blit to the display.
        self._frame_surface = self._pygame.Surface((self._width, self._height))
        try:
            self._font = self._pygame.font.SysFont("Arial", 18)
        except Exception:
            self._font = self._pygame.font.Font(None, 18)

        if _DEBUG:
            _dbg("--- display init ---")
            _dbg(f"DISPLAY={os.environ.get('DISPLAY', '(unset)')}")
            _dbg(f"WAYLAND_DISPLAY={os.environ.get('WAYLAND_DISPLAY', '(unset)')}")
            _dbg(f"SDL_VIDEODRIVER={os.environ.get('SDL_VIDEODRIVER', '(unset)')}")
            try:
                _dbg(f"pygame display driver={self._pygame.display.get_driver()}")
            except Exception as ex:
                _dbg(f"pygame display get_driver error: {ex}")
            _dbg(f"window size={self._win_w}x{self._win_h}")
            try:
                _dbg(f"screen size={self._screen.get_size()}")
            except Exception as ex:
                _dbg(f"screen get_size: {ex}")
            for attr in ("get_bitsize", "get_pitch", "get_masks"):
                if hasattr(self._screen, attr):
                    try:
                        _dbg(f"  {attr}={getattr(self._screen, attr)()}")
                    except Exception as ex:
                        _dbg(f"  {attr} error: {ex}")
            _dbg("-------------------")

    @property
    def width(self) -> int:
        return self._width

    @property
    def height(self) -> int:
        return self._height

    def CreateFrameCanvas(self) -> ScreenCanvas:
        if self._front == 0:
            self._front = 1
            return self._canvas0
        self._front = 0
        return self._canvas1

    def SwapOnVSync(self, canvas: ScreenCanvas) -> ScreenCanvas:
        if self._closed or self._screen is None:
            return self._canvas1 if canvas is self._canvas0 else self._canvas0
        # Process events (quit, etc.)
        for event in self._pygame.event.get():
            if event.type == self._pygame.QUIT:
                self._closed = True
            if event.type == self._pygame.KEYDOWN and event.key in (self._pygame.K_ESCAPE, self._pygame.K_q):
                self._closed = True
            if event.type == self._pygame.KEYDOWN and event.key == self._pygame.K_f:
                self._fullscreen = not self._fullscreen
                if self._fullscreen:
                    self._screen = self._pygame.display.set_mode((0, 0), self._pygame.FULLSCREEN)
                    self._win_w, self._win_h = self._screen.get_size()
                else:
                    self._screen = self._pygame.display.set_mode(self._windowed_size)
                    self._win_w, self._win_h = self._windowed_size
        if self._closed:
            raise ScreenClosed("Display window was closed")

        # Blit buffer to window (scaled). Pygame expects (width, height) and RGB.
        buf = canvas._buffer  # (height, width, 3)
        arr = buf.transpose(1, 0, 2)  # (width, height, 3)

        render_start = time.perf_counter()

        # Primary path: write pixels into a stable intermediate surface, then scale.
        # This avoids a class of "blank/transparent window" issues seen with make_surface()
        # under some WSLg/XWayland + SDL combinations.
        surf = None
        try:
            if self._frame_surface is not None:
                self._pygame.surfarray.blit_array(self._frame_surface, arr)
                if self._scale != 1:
                    # Scale directly into the display surface for fewer intermediate allocations.
                    self._pygame.transform.scale(self._frame_surface, (self._win_w, self._win_h), self._screen)
                else:
                    self._screen.blit(self._frame_surface, (0, 0))
            else:
                raise RuntimeError("frame surface not initialized")
        except Exception:
            # Fallback: allocate a surface from the array each frame.
            surf = self._pygame.surfarray.make_surface(arr)
            if self._scale != 1:
                surf = self._pygame.transform.scale(surf, (self._win_w, self._win_h))
            self._screen.blit(surf, (0, 0))

        render_ms = (time.perf_counter() - render_start) * 1000.0
        self._render_ms_display = (
            render_ms if self._render_ms_display <= 0.0 else (self._render_ms_display * 0.9 + render_ms * 0.1)
        )

        # FPS and render-time overlay in a simple clean font at the top-right.
        if self._font is not None:
            now = time.monotonic()
            dt = max(now - self._last_frame_t, 1e-6)
            self._last_frame_t = now
            current_fps = 1.0 / dt
            self._fps_display = current_fps if self._fps_display <= 0.0 else (self._fps_display * 0.9 + current_fps * 0.1)
            fps_text = self._font.render(f"{self._fps_display:5.1f} FPS", True, (230, 230, 230))
            text_rect = fps_text.get_rect(topright=(self._win_w - 8, 8))
            self._screen.blit(fps_text, text_rect)
            ms_text = self._font.render(f"{self._render_ms_display:5.2f} ms", True, (230, 230, 230))
            ms_rect = ms_text.get_rect(topright=(self._win_w - 8, text_rect.bottom + 4))
            self._screen.blit(ms_text, ms_rect)

        self._pygame.display.flip()

        if _DEBUG:
            self._frame_count += 1
            now = time.monotonic()
            if now - self._last_dbg_t >= 1.0:
                self._last_dbg_t = now
                non_zero = int(np.count_nonzero(buf))
                total = buf.size
                _dbg(f"frame={self._frame_count} buffer non_zero_pixels={non_zero}/{total} "
                     f"min={buf.min()} max={buf.max()} mean={buf.mean():.2f}")
                if buf.size > 0:
                    cy, cx = self._height // 2, self._width // 2
                    _dbg(f"  center pixel ({cx},{cy}) rgb={tuple(buf[cy, cx])} "
                         f"top-left (0,0) rgb={tuple(buf[0, 0])}")
                try:
                    if surf is not None:
                        _dbg(f"  surf size={surf.get_size()} screen size={self._screen.get_size()}")
                    else:
                        _dbg(f"  frame_surface size={self._frame_surface.get_size() if self._frame_surface else None} "
                             f"screen size={self._screen.get_size()}")
                except Exception as ex:
                    _dbg(f"  surf/screen get_size: {ex}")

        return self._canvas1 if canvas is self._canvas0 else self._canvas0

    def Clear(self) -> None:
        if self._screen is not None and not self._closed:
            self._screen.fill((0, 0, 0))
            if self._pygame is not None:
                self._pygame.display.flip()
