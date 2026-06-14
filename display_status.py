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

    def consume_end_request(self) -> bool:
        return False

    def request_number(self, prompt: str, digits: int = 5) -> str | None:
        return None

    def supports_actions(self) -> bool:
        return False

    def is_real_display(self) -> bool:
        return False

    def stop(self) -> None:
        return


class StatusDisplay:
    def __init__(self, width: int = 1024, height: int = 600, fps: int = 20) -> None:
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
        self._number_results: queue.Queue[str | None] = queue.Queue()
        self._end_requested = False
        self._numpad_active = False
        self._numpad_prompt = ""
        self._numpad_digits = ""
        self._numpad_required_digits = 5
        self._running = False
        self._thread: threading.Thread | None = None

    def _end_button_rect(self):
        return (804, 24, 180, 82)

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

    def consume_end_request(self) -> bool:
        with self._lock:
            if not self._end_requested:
                return False
            self._end_requested = False
            return True

    def request_number(self, prompt: str, digits: int = 5) -> str | None:
        while not self._number_results.empty():
            try:
                self._number_results.get_nowait()
            except queue.Empty:
                break

        with self._lock:
            self._numpad_active = True
            self._numpad_prompt = prompt
            self._numpad_digits = ""
            self._numpad_required_digits = digits
            self.state["status"] = "Register User"
            self.state["last_event"] = prompt

        while self._running:
            try:
                result = self._number_results.get(timeout=0.1)
                with self._lock:
                    self._numpad_active = False
                    self._numpad_digits = ""
                return result
            except queue.Empty:
                continue

        with self._lock:
            self._numpad_active = False
            self._numpad_digits = ""
        return None

    def supports_actions(self) -> bool:
        return self._running

    def is_real_display(self) -> bool:
        return True

    def _push_action(self, action: str, message: str) -> None:
        if action == "end":
            with self._lock:
                self._end_requested = True
                self.state["last_event"] = message
            return
        elif not self._actions.empty():
            return
        self._actions.put(action)
        with self._lock:
            self.state["last_event"] = message

    def _handle_touch(self, pos) -> None:
        x, y = pos
        bx, by, bw, bh = self._end_button_rect()
        if bx <= x <= bx + bw and by <= y <= by + bh:
            self._push_action("end", f"Touch END pressed at ({x}, {y}).")

    def _numpad_buttons(self):
        button_w = 150
        button_h = 68
        gap = 18
        start_x = 286
        start_y = 190
        labels = [
            ("1", 0, 0), ("2", 1, 0), ("3", 2, 0),
            ("4", 0, 1), ("5", 1, 1), ("6", 2, 1),
            ("7", 0, 2), ("8", 1, 2), ("9", 2, 2),
            ("DEL", 0, 3), ("0", 1, 3), ("OK", 2, 3),
            ("CANCEL", 0, 4),
        ]
        buttons = []
        for label, col, row in labels:
            width = button_w
            if label == "CANCEL":
                width = button_w * 3 + gap * 2
            rect = (
                start_x + col * (button_w + gap),
                start_y + row * (button_h + gap),
                width,
                button_h,
            )
            buttons.append((label, rect))
        return buttons

    def _handle_numpad_touch(self, pos) -> None:
        x, y = pos
        for label, rect in self._numpad_buttons():
            bx, by, bw, bh = rect
            if not (bx <= x <= bx + bw and by <= y <= by + bh):
                continue

            result: str | None
            with self._lock:
                if label.isdigit():
                    if len(self._numpad_digits) < self._numpad_required_digits:
                        self._numpad_digits += label
                        self.state["last_event"] = f"Student ID: {self._numpad_digits}"
                    return
                if label == "DEL":
                    self._numpad_digits = self._numpad_digits[:-1]
                    self.state["last_event"] = f"Student ID: {self._numpad_digits or '-'}"
                    return
                if label == "OK":
                    if len(self._numpad_digits) != self._numpad_required_digits:
                        self.state["last_event"] = f"Enter exactly {self._numpad_required_digits} digits."
                        return
                    result = self._numpad_digits
                    self.state["last_event"] = f"Student ID submitted: {result}"
                elif label == "CANCEL":
                    result = None
                    self.state["last_event"] = "Student ID entry cancelled."
                else:
                    return

            self._number_results.put(result)
            return

    def stop(self) -> None:
        self._running = False
        if self._thread is not None:
            self._thread.join(timeout=2)

    def _snapshot(self) -> dict[str, Any]:
        with self._lock:
            snapshot = dict(self.state)
            snapshot["_numpad_active"] = self._numpad_active
            snapshot["_numpad_prompt"] = self._numpad_prompt
            snapshot["_numpad_digits"] = self._numpad_digits
            snapshot["_numpad_required_digits"] = self._numpad_required_digits
            return snapshot

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
            self.width, self.height = screen.get_size()
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
                    elif event.type == pygame.MOUSEBUTTONDOWN:
                        if self._snapshot().get("_numpad_active"):
                            self._handle_numpad_touch(event.pos)
                        else:
                            self._handle_touch(event.pos)
                    elif event.type == pygame.FINGERDOWN:
                        surface_w, surface_h = screen.get_size()
                        pos = (int(event.x * surface_w), int(event.y * surface_h))
                        if self._snapshot().get("_numpad_active"):
                            self._handle_numpad_touch(pos)
                        else:
                            self._handle_touch(pos)

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

        if state.get("_numpad_active"):
            self._draw_numpad(screen, state, font_big, font_title, font_label, font_value, font_small)
            return

        screen.fill((8, 12, 22))
        pygame.draw.circle(screen, (18, 91, 111), (0, 20), 180)
        pygame.draw.circle(screen, (75, 32, 116), (1010, 585), 260)
        pygame.draw.circle(screen, (20, 100, 62), (520, 650), 240)

        self._text(screen, "SMART BOTTLE MACHINE", font_title, (205, 242, 255), (34, 24))
        self._text(screen, datetime.now().strftime("%H:%M:%S"), font_big, (255, 255, 255), (512, 68), center=True)
        self._button(screen, self._end_button_rect(), "END", (190, 72, 72))

        self._card(screen, (40, 132, 440, 150), "Session", (112, 214, 255))
        self._line(screen, "Status", str(state["status"]), font_label, font_value, 68, 192)
        self._line(screen, "Student", str(state["student"]), font_label, font_value, 68, 234)

        self._card(screen, (544, 132, 440, 150), "Detection", (255, 218, 128))
        labels = str(state["labels"])
        if len(labels) > 26:
            labels = labels[:25] + "..."
        self._line(screen, "Labels", labels, font_label, font_value, 572, 192)
        self._line(screen, "Bottle", f"{float(state['confidence']):.2f}", font_label, font_value, 572, 234)

        self._card(screen, (40, 330, 280, 150), "Accepted", (127, 237, 174))
        self._text(screen, str(state["bottles"]), font_big, (245, 255, 250), (180, 420), center=True)

        self._card(screen, (372, 330, 280, 150), "Points", (170, 206, 255))
        self._text(screen, f"{int(state['points'])}", font_big, (245, 250, 255), (512, 420), center=True)

        self._card(screen, (704, 330, 280, 150), "ESP32", (255, 144, 144))
        self._text(screen, str(state["esp32"]), font_value, (245, 250, 255), (732, 392))
        self._text(screen, f"Rejected {int(state['rejected'])}", font_label, (215, 225, 238), (732, 434))

        pygame.draw.rect(screen, (20, 28, 44), (40, 532, 944, 38), border_radius=19)
        event = str(state["last_event"])
        if len(event) > 104:
            event = event[:103] + "..."
        self._text(screen, event, font_small, (196, 214, 232), (60, 542))
        self._text(screen, "Touch END to finish session", font_small, (118, 134, 154), (732, 542))

    def _draw_numpad(self, screen, state, font_big, font_title, font_label, font_value, font_small) -> None:
        import pygame

        screen.fill((8, 12, 22))
        pygame.draw.circle(screen, (18, 91, 111), (0, 20), 180)
        pygame.draw.circle(screen, (75, 32, 116), (1010, 585), 260)

        prompt = str(state.get("_numpad_prompt", "Enter Student ID"))
        digits = str(state.get("_numpad_digits", ""))
        required = int(state.get("_numpad_required_digits", 5))

        self._text(screen, "REGISTER NEW RFID", font_title, (205, 242, 255), (512, 42), center=True)
        self._text(screen, prompt, font_label, (210, 224, 242), (512, 92), center=True)

        box_text = digits + "_" * max(0, required - len(digits))
        pygame.draw.rect(screen, (20, 28, 44), (286, 120, 486, 52), border_radius=18)
        pygame.draw.rect(screen, (58, 78, 110), (286, 120, 486, 52), width=2, border_radius=18)
        self._text(screen, box_text, font_value, (255, 255, 255), (529, 146), center=True)

        for label, rect in self._numpad_buttons():
            color = (42, 111, 158)
            if label == "OK":
                color = (39, 156, 106)
            elif label in ("DEL", "CANCEL"):
                color = (155, 88, 70)
            self._button(screen, rect, label, color)

        event = str(state["last_event"])
        if len(event) > 104:
            event = event[:103] + "..."
        self._text(screen, event, font_small, (196, 214, 232), (60, 562))

    def _button(self, screen, rect, label: str, color) -> None:
        import pygame

        pygame.draw.rect(screen, color, rect, border_radius=20)
        pygame.draw.rect(screen, (255, 220, 220), rect, width=2, border_radius=20)
        self._text(
            screen,
            label,
            pygame.font.Font("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 26),
            (255, 255, 255),
            (rect[0] + rect[2] // 2, rect[1] + rect[3] // 2),
            center=True,
        )

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
