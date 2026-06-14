from __future__ import annotations

import os
import time


WIDTH = 1024
HEIGHT = 600
FPS = 30


def draw_text(surface, text, font, color, pos, center=False):
    rendered = font.render(text, True, color)
    rect = rendered.get_rect()
    if center:
        rect.center = pos
    else:
        rect.topleft = pos
    surface.blit(rendered, rect)


def draw_button(pygame, screen, rect, label, color, font):
    pygame.draw.rect(screen, color, rect, border_radius=22)
    pygame.draw.rect(screen, (235, 245, 255), rect, width=2, border_radius=22)
    draw_text(screen, label, font, (255, 255, 255), rect.center, center=True)


def inside(rect, pos):
    x, y = pos
    return rect.left <= x <= rect.right and rect.top <= y <= rect.bottom


def handle_tap(pos, item_button, end_button, quit_button):
    if inside(item_button, pos):
        return "ITEM", f"ITEM clicked at {pos}"
    if inside(end_button, pos):
        return "END", f"END clicked at {pos}"
    if inside(quit_button, pos):
        return "QUIT", f"QUIT clicked at {pos}"
    return None, f"Screen touched at {pos}"


def main() -> int:
    if "DISPLAY" not in os.environ:
        os.environ.setdefault("SDL_VIDEODRIVER", "kmsdrm")
        os.environ.setdefault("SDL_AUDIODRIVER", "dummy")

    import pygame

    pygame.init()
    pygame.mouse.set_visible(True)
    screen = pygame.display.set_mode((WIDTH, HEIGHT), pygame.FULLSCREEN)
    pygame.display.set_caption("Touchscreen Test")
    clock = pygame.time.Clock()

    font_title = pygame.font.Font("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 34)
    font_button = pygame.font.Font("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 30)
    font_label = pygame.font.Font("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 22)
    font_small = pygame.font.Font("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 18)

    item_button = pygame.Rect(90, 210, 360, 180)
    end_button = pygame.Rect(574, 210, 360, 180)
    quit_button = pygame.Rect(337, 465, 350, 80)

    last_message = "Touch a button. Tap coordinates will show here."
    tap_count = 0
    running = True

    print("Touchscreen test running.")
    print("Tap ITEM, END, or QUIT on the touchscreen.")

    while running:
        for event in pygame.event.get():
            if event.type == pygame.QUIT:
                running = False
            elif event.type == pygame.KEYDOWN and event.key in (pygame.K_q, pygame.K_ESCAPE):
                running = False
            elif event.type == pygame.MOUSEBUTTONDOWN:
                tap_count += 1
                pos = event.pos
                action, last_message = handle_tap(pos, item_button, end_button, quit_button)
                print(last_message, flush=True)
                if action == "QUIT":
                    running = False
            elif event.type == pygame.FINGERDOWN:
                tap_count += 1
                surface_w, surface_h = screen.get_size()
                pos = (int(event.x * surface_w), int(event.y * surface_h))
                action, last_message = handle_tap(pos, item_button, end_button, quit_button)
                print(last_message, flush=True)
                if action == "QUIT":
                    running = False

        screen.fill((8, 12, 22))
        pygame.draw.circle(screen, (18, 91, 111), (0, 20), 180)
        pygame.draw.circle(screen, (75, 32, 116), (1010, 585), 260)

        draw_text(screen, "TOUCHSCREEN TEST", font_title, (215, 242, 255), (WIDTH // 2, 64), center=True)
        draw_text(
            screen,
            "Tap the big buttons. Press Q/Esc only if using a keyboard.",
            font_label,
            (180, 198, 220),
            (WIDTH // 2, 124),
            center=True,
        )

        draw_button(pygame, screen, item_button, "ITEM", (39, 156, 106), font_button)
        draw_button(pygame, screen, end_button, "END", (190, 92, 74), font_button)
        draw_button(pygame, screen, quit_button, "QUIT", (64, 76, 102), font_label)

        draw_text(screen, f"Taps: {tap_count}", font_label, (235, 245, 255), (90, 420))
        draw_text(screen, last_message, font_small, (205, 220, 238), (90, 562))

        pygame.display.flip()
        clock.tick(FPS)

    pygame.display.quit()
    pygame.quit()
    time.sleep(0.2)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
