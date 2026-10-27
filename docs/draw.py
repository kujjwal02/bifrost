"""Draws the README images: python3 docs/draw.py. Writes docs/*.svg."""

from html import escape
from pathlib import Path

DOCS = Path(__file__).resolve().parent
MONO = "ui-monospace, SFMono-Regular, Menlo, monospace"
SANS = "system-ui, -apple-system, Segoe UI, sans-serif"
# Catppuccin Mocha (terminal) and Latte (browser), herdr's default theme family.
BASE, MANTLE, SURFACE, OVERLAY, TEXT = "#1e1e2e", "#181825", "#313244", "#6c7086", "#cdd6f4"
GREEN, BLUE, MAUVE, YELLOW, PEACH = "#a6e3a1", "#89b4fa", "#cba6f7", "#f9e2af", "#fab387"
LATTE_BASE, LATTE_CRUST, LATTE_TEXT, LATTE_SUB, LATTE_BLUE = "#eff1f5", "#dce0e8", "#4c4f69", "#6c6f85", "#1e66f5"

LANE_W, LANE_H = 920, 118
TERMINAL_W, BROWSER_X, BROWSER_W = 390, 510, 410


def text(x, y, content, fill, size=14, family=MONO, weight=400, anchor="start"):
    return (f'<text x="{x}" y="{y}" fill="{fill}" font-size="{size}" font-family="{family}" '
            f'font-weight="{weight}" text-anchor="{anchor}" xml:space="preserve">{content}</text>')


def terminal(lines, title="afk"):
    parts = [f'<rect width="{TERMINAL_W}" height="{LANE_H - 18}" rx="10" fill="{MANTLE}" stroke="{SURFACE}"/>',
             text(16, 22, escape(title), OVERLAY, 11)]
    for index, line in enumerate(lines):
        parts.append(text(16, 50 + index * 22, line, TEXT, 14))
    return "".join(parts)


def browser(url, body):
    return (
        f'<g transform="translate({BROWSER_X} 0)">'
        f'<rect width="{BROWSER_W}" height="{LANE_H - 18}" rx="10" fill="{LATTE_BASE}" stroke="{SURFACE}"/>'
        f'<path d="M0 10a10 10 0 0 1 10-10h{BROWSER_W - 20}a10 10 0 0 1 10 10v20H0z" fill="{LATTE_CRUST}"/>'
        f'<rect x="12" y="6" width="{BROWSER_W - 24}" height="18" rx="9" fill="#fff"/>'
        f'{text(24, 19, escape(url), LATTE_TEXT, 11)}{body}</g>'
    )


def popup(lines):
    width = BROWSER_W
    parts = [f'<g transform="translate({BROWSER_X} 0)">',
             f'<rect width="{width}" height="{LANE_H - 18}" rx="10" fill="{BASE}" stroke="{MAUVE}" stroke-width="1.5"/>',
             f'<rect x="1" y="1" width="{width - 2}" height="26" rx="9" fill="{SURFACE}"/>',
             text(14, 19, "Ferry: open this page?", MAUVE, 12, weight=700)]
    for index, line in enumerate(lines):
        parts.append(text(14, 50 + index * 22, line, TEXT, 13))
    parts.append("</g>")
    return "".join(parts)


def arrow(caption, note):
    middle = TERMINAL_W + (BROWSER_X - TERMINAL_W) / 2
    y = (LANE_H - 18) / 2
    return (f'<path d="M{TERMINAL_W + 14} {y} H{BROWSER_X - 22}" stroke="{MAUVE}" stroke-width="2.5" stroke-dasharray="5 5"/>'
            f'<path d="M{BROWSER_X - 22} {y - 6} L{BROWSER_X - 12} {y} L{BROWSER_X - 22} {y + 6} Z" fill="{MAUVE}"/>'
            + text(middle - 4, y - 12, caption, MAUVE, 12, weight=700, anchor="middle")
            + text(middle - 4, y + 22, note, OVERLAY, 11, SANS, anchor="middle"))


def page_lines(rows):
    return "".join(text(16, 50 + index * 20, content, fill, 12, family=family)
                   for index, (content, fill, family) in enumerate(rows))


def ports_lane():
    return (terminal([f'<tspan fill="{GREEN}">$</tspan> npm run dev',
                      f'  ➜ Local: <tspan fill="{BLUE}">http://localhost:5173/</tspan>'])
            + arrow("port", "routed")
            + browser("5173.afk.localhost", page_lines([
                ("Your Vite app, served from afk", LATTE_TEXT, SANS),
                ("Hot reload works. No port clashes with this", LATTE_SUB, SANS),
                ("computer or another machine's 5173.", LATTE_SUB, SANS)])))


def link_lane():
    return (terminal([f'<tspan fill="{GREEN}">$</tspan> gh auth login --web',
                      f'  Open <tspan fill="{BLUE}">github.com/login/device</tspan>'])
            + arrow("link", "asks first")
            + popup([f'afk wants to open  <tspan fill="{BLUE}">github.com/login/device</tspan>',
                     f'<tspan fill="{GREEN}">Enter</tspan> open   <tspan fill="{YELLOW}">n</tspan> skip   <tspan fill="{PEACH}">q</tspan> skip all']))


def file_lane():
    bars = "".join(f'<rect x="{250 + i * 26}" y="{88 - h}" width="16" height="{h}" rx="2" fill="{LATTE_BLUE}" opacity="{0.45 + i * 0.1}"/>'
                   for i, h in enumerate([18, 30, 24, 44, 38, 52]))
    return (terminal([f'<tspan fill="{GREEN}">$</tspan> xdg-open report.html',
                      f'<tspan fill="{OVERLAY}">  agent wrote ~/proj/report.html</tspan>'])
            + arrow("file", "opens here")
            + browser("files.afk.localhost/…/report.html", page_lines([
                ("Benchmark report", LATTE_TEXT, SANS),
                ("The file from afk, with its", LATTE_SUB, SANS),
                ("CSS, images and charts", LATTE_SUB, SANS)]) + bars))


def click_lane():
    cursor = (f'<g transform="translate(300 64)"><path d="M0 0 L0 17 L4.5 13 L8 20 L11 18.5 L7.5 12 L13 12 Z" '
              f'fill="#fff" stroke="{BASE}" stroke-width="1.2"/></g>')
    return (terminal([f'<tspan fill="{GREEN}">$</tspan> npm run dev',
                      f'  ➜ Local: <tspan fill="{BLUE}" text-decoration="underline">http://localhost:5173/</tspan>'],
                     title="herdr pane on afk") + cursor
            + arrow("Ctrl+click", "opens afk's")
            + browser("5173.afk.localhost", page_lines([
                ("afk's server on port 5173", LATTE_TEXT, SANS),
                ("not whatever this computer", LATTE_SUB, SANS),
                ("runs on its own port 5173", LATTE_SUB, SANS)])))


LANES = {"ports": ports_lane, "links": link_lane, "files": file_lane, "click": click_lane}


def svg(width, height, body, description):
    return (f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}" role="img">'
            f'<title>{escape(description)}</title><rect width="{width}" height="{height}" rx="16" fill="{BASE}"/>{body}</svg>\n')


def hero():
    margin_x, header_h = 20, 52
    body = [text(margin_x + TERMINAL_W / 2, 32, "on the remote machine", OVERLAY, 13, SANS, 600, "middle"),
            text(margin_x + BROWSER_X + BROWSER_W / 2, 32, "on your computer", OVERLAY, 13, SANS, 600, "middle")]
    for index, draw in enumerate(LANES.values()):
        body.append(f'<g transform="translate({margin_x} {header_h + index * LANE_H})">{draw()}</g>')
    height = header_h + len(LANES) * LANE_H + 4
    return svg(LANE_W + 2 * margin_x, height, "".join(body),
               "What ferry does: remote ports, links, files and Ctrl+clicked links open in this computer's browser")


def main():
    (DOCS / "ferry.svg").write_text(hero())
    for name, draw in LANES.items():
        body = f'<g transform="translate(20 20)">{draw()}</g>'
        (DOCS / f"{name}.svg").write_text(svg(LANE_W + 40, LANE_H + 22, body, f"ferry: {name}"))


if __name__ == "__main__":
    main()
