from __future__ import annotations

import os
import queue
import threading
import time
from datetime import datetime
from typing import Any


class NullDisplay:
    def start(self) -> None:
        return

    def update(self, **kwargs: Any) -> None:
        return

    def get_action(self) -> str | None:
        return None

    def wait_for_action(self, timeout_sec: float = 0.1) -> str | None:
        return None

    def supports_actions(self) -> bool:
        return False

    def is_real_display(self) -> bool:
        return False

    def stop(self) -> None:
        return


class StatusDisplay:
    def __init__(self, width: int = 800, height: int = 480, fps: int = 20) -> None:
        self.width = width
        self.height = height
        self.fps = fps
        self.state: dict[str, Any] = {
            "status": "Booting",
            "rfid": "-",
            "student": "-",
            "esp32": "Unknown",
            "labels": "-",
            "confidence": 0.0,
            "bottles": 0,
            "rejected": 0,
            "weight": 0.0,
            "points": 0,
            "last_event": "Starting up",
        }
        self._lock = threading.Lock()
        self._actions: queue.Queue[str] = queue.Queue()
        self._running = False
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        self._running = True
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def update(self, **kwargs: Any) -> None:
        with self._lock:
            self.state.update(kwargs)

    def get_action(self) -> str | None:
        try:
            return self._actions.get_nowait()
        except queue.Empty:
            return None

    def wait_for_action(self, timeout_sec: float = 0.1) -> str | None:
        try:
            return self._actions.get(timeout=timeout_sec)
        except queue.Empty:
            return None

    def supports_actions(self) -> bool:
        return self._running

    def is_real_display(self) -> bool:
        return True

    def _push_action(self, action: str, message: str) -> None:
        if not self._actions.empty():
            return
        self._actions.put(action)
        with self._lock:
            self.state["last_event"] = message

    def stop(self) -> None:
        self._running = False
        if self._thread is not None:
            self._thread.join(timeout=2)

    def _snapshot(self) -> dict[str, Any]:
        with self._lock:
            return dict(self.state)

    def _run(self) -> None:
        pygame = None
        try:
            if "DISPLAY" not in os.environ:
                os.environ.setdefault("SDL_VIDEODRIVER", "kmsdrm")
                os.environ.setdefault("SDL_AUDIODRIVER", "dummy")

            import pygame as pygame_module

            pygame = pygame_module
            pygame.init()
            screen = pygame.display.set_mode((self.width, self.height), pygame.FULLSCREEN)
            pygame.mouse.set_visible(False)
            pygame.display.set_caption("Bottle Machine")
            clock = pygame.time.Clock()

            font_big = pygame.font.Font("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 38)
            font_title = pygame.font.Font("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 28)
            font_label = pygame.font.Font("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 20)
            font_value = pygame.font.Font("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 24)
            font_small = pygame.font.Font("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 16)

            while self._running and pygame.get_init() and pygame.display.get_init():
                for event in pygame.event.get():
                    if event.type == pygame.QUIT:
                        self._running = False
                    elif event.type == pygame.KEYDOWN and event.key in (pygame.K_q, pygame.K_ESCAPE):
                        self._running = False
                    elif event.type == pygame.KEYDOWN and event.key in (pygame.K_RETURN, pygame.K_KP_ENTER):
                        self._push_action("item", "Display Enter pressed: simulate item detection.")
                    elif event.type == pygame.KEYDOWN and event.key == pygame.K_e:
                        self._push_action("end", "Display E pressed: end mock session.")

                state = self._snapshot()
                self._draw(screen, state, font_big, font_title, font_label, font_value, font_small)
                pygame.display.flip()
                clock.tick(self.fps)
        except Exception as exc:
            self._running = False
            print(f"[DISPLAY] Disabled after runtime error: {exc}", flush=True)
        finally:
            if pygame is not None:
                try:
                    if pygame.display.get_init():
                        pygame.display.quit()
                except Exception:
                    pass

    def _draw(self, screen, state, font_big, font_title, font_label, font_value, font_small) -> None:
        import pygame

        screen.fill((8, 12, 22))
        pygame.draw.circle(screen, (18, 91, 111), (0, 20), 180)
        pygame.draw.circle(screen, (75, 32, 116), (790, 465), 230)
        pygame.draw.circle(screen, (20, 100, 62), (420, 520), 210)

        self._text(screen, "SMART BOTTLE MACHINE", font_title, (205, 242, 255), (34, 24))
        self._text(screen, datetime.now().strftime("%H:%M:%S"), font_big, (255, 255, 255), (400, 64), center=True)

        self._card(screen, (34, 122, 350, 132), "Session", (112, 214, 255))
        self._line(screen, "Status", str(state["status"]), font_label, font_value, 58, 172)
        self._line(screen, "Student", str(state["student"]), font_label, font_value, 58, 210)

        self._card(screen, (416, 122, 350, 132), "Detection", (255, 218, 128))
        labels = str(state["labels"])
        if len(labels) > 22:
            labels = labels[:21] + "..."
        self._line(screen, "Labels", labels, font_label, font_value, 440, 172)
        self._line(screen, "Bottle", f"{float(state['confidence']):.2f}", font_label, font_value, 440, 210)

        self._card(screen, (34, 284, 222, 126), "Accepted", (127, 237, 174))
        self._text(screen, str(state["bottles"]), font_big, (245, 255, 250), (145, 360), center=True)

        self._card(screen, (288, 284, 222, 126), "Points", (170, 206, 255))
        self._text(screen, f"{int(state['points'])}", font_big, (245, 250, 255), (400, 354), center=True)

        self._card(screen, (542, 284, 224, 126), "ESP32", (255, 144, 144))
        self._text(screen, str(state["esp32"]), font_value, (245, 250, 255), (565, 337))
        self._text(screen, f"Rejected {int(state['rejected'])}", font_label, (215, 225, 238), (565, 374))

        pygame.draw.rect(screen, (20, 28, 44), (34, 430, 732, 34), border_radius=17)
        event = str(state["last_event"])
        if len(event) > 80:
            event = event[:79] + "..."
        self._text(screen, event, font_small, (196, 214, 232), (54, 438))
        self._text(screen, "ENTER item  |  E end  |  Q/ESC close screen", font_small, (118, 134, 154), (456, 438))

    def _card(self, screen, rect, title: str, color) -> None:
        import pygame

        pygame.draw.rect(screen, (18, 26, 41), rect, border_radius=22)
        pygame.draw.rect(screen, (38, 52, 76), rect, width=1, border_radius=22)
        self._text(screen, title, pygame.font.Font("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 20), color, (rect[0] + 24, rect[1] + 18))

    def _line(self, screen, label: str, value: str, font_label, font_value, x: int, y: int) -> None:
        self._text(screen, label, font_label, (155, 171, 192), (x, y))
        self._text(screen, value, font_value, (241, 246, 255), (x + 112, y - 3))

    def _text(self, screen, text: str, font, color, pos, center: bool = False) -> None:
        rendered = font.render(text, True, color)
        rect = rendered.get_rect()
        if center:
            rect.center = pos
        else:
            rect.topleft = pos
        screen.blit(rendered, rect)

def create_display(enabled: bool, width: int, height: int, fps: int = 20):
    if not enabled:
        return NullDisplay()
    try:
        display = StatusDisplay(width=width, height=height, fps=fps)
        display.start()
        return display
    except Exception as exc:
        print(f"[DISPLAY] Disabled after startup error: {exc}", flush=True)
        return NullDisplay()
