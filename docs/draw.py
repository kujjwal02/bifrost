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
             text(14, 19, "Bifrost: open this page?", MAUVE, 12, weight=700)]
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
               "What bifrost does: remote ports, links, files and Ctrl+clicked links open in this computer's browser")


def box(x, y, width, height, title, subtitle, fill=MANTLE, stroke=SURFACE, title_fill=TEXT, sub_fill=OVERLAY):
    return (f'<rect x="{x}" y="{y}" width="{width}" height="{height}" rx="8" fill="{fill}" stroke="{stroke}"/>'
            + text(x + width / 2, y + height / 2 - 3, escape(title), title_fill, 13, SANS, 600, "middle")
            + text(x + width / 2, y + height / 2 + 14, escape(subtitle), sub_fill, 10.5, MONO, anchor="middle"))


def link(x1, y1, x2, y2, color, both_ways=False, dashed=False):
    dash = ' stroke-dasharray="5 4"' if dashed else ""
    heads = f' marker-end="url(#head-{color[1:]})"' + (f' marker-start="url(#tail-{color[1:]})"' if both_ways else "")
    return f'<path d="M{x1} {y1} L{x2} {y2}" stroke="{color}" stroke-width="2"{dash}{heads}/>'


def markers(colors):
    return "<defs>" + "".join(
        f'<marker id="head-{c[1:]}" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto">'
        f'<path d="M0 0 L10 5 L0 10 z" fill="{c}"/></marker>'
        f'<marker id="tail-{c[1:]}" viewBox="0 0 10 10" refX="1" refY="5" markerWidth="7" markerHeight="7" orient="auto">'
        f'<path d="M10 0 L0 5 L10 10 z" fill="{c}"/></marker>'
        for c in colors) + "</defs>"


def architecture():
    """One SSH master per machine carries three kinds of traffic; each gets a lane."""
    lanes = {"ports": (110, BLUE), "files": (200, GREEN), "watch": (290, MAUVE)}
    body = [markers([BLUE, GREEN, MAUVE, OVERLAY])]
    # Zones.
    body.append(f'<rect x="196" y="40" width="228" height="306" rx="12" fill="none" stroke="{SURFACE}" stroke-dasharray="4 4"/>')
    body.append(text(310, 62, "bifrost daemon", TEXT, 13, SANS, 700, "middle"))
    body.append(f'<rect x="604" y="40" width="336" height="392" rx="12" fill="none" stroke="{SURFACE}" stroke-dasharray="4 4"/>')
    body.append(text(772, 62, "remote machine (afk)", TEXT, 13, SANS, 700, "middle"))
    body.append(text(100, 30, "your computer", OVERLAY, 12, SANS, 600, "middle"))
    # The SSH pipe.
    body.append(f'<rect x="424" y="78" width="180" height="244" rx="22" fill="{MANTLE}" stroke="{SURFACE}"/>')
    body.append(text(514, 344, "one SSH master per machine", OVERLAY, 11, SANS, 600, "middle"))
    lane_labels = {"ports": "port forwards", "files": "file reads", "watch": "ticks ⇄ ports, opens"}
    for name, (y, color) in lanes.items():
        both = name == "watch"
        body.append(link(405, y, 620, y, color, both_ways=both))
        body.append(text(514, y - 9, lane_labels[name], color, 11, SANS, 600, "middle"))
    # Left column.
    body.append(f'<rect x="30" y="78" width="140" height="154" rx="8" fill="{LATTE_BASE}" stroke="{SURFACE}"/>')
    body.append(text(100, 100, "Browser", LATTE_TEXT, 13, SANS, 600, "middle"))
    body.append(text(100, 114, "5173.afk.localhost", LATTE_BLUE, 10.5, anchor="middle"))
    body.append(text(100, 204, "files.afk.localhost", LATTE_BLUE, 10.5, anchor="middle"))
    body.append(box(30, 262, 140, 56, "herdr", "machines, popups"))
    body.append(link(170, 110, 213, 110, BLUE))
    body.append(link(170, 200, 213, 200, GREEN))
    body.append(link(170, 290, 213, 290, OVERLAY, both_ways=True))
    # Daemon rows.
    body.append(box(215, 85, 190, 50, "router: port route", "Host → unix socket", stroke=BLUE))
    body.append(box(215, 175, 190, 50, "router: file link", "HMAC check, stream", stroke=GREEN))
    body.append(box(215, 265, 190, 50, "machine thread", "forwards, open rules", stroke=MAUVE))
    # Remote rows.
    body.append(box(622, 85, 300, 50, "dev server :5173", "any listener of your user, port ≥ 1024", stroke=BLUE))
    body.append(box(622, 175, 300, 50, "sh: realpath -e, cat", "refuses files outside the folder", stroke=GREEN))
    body.append(box(622, 265, 300, 50, "watch.sh", "reads /proc/net/tcp every tick", stroke=MAUVE))
    # The opener feeding watch.sh.
    body.append(box(622, 362, 70, 50, "agent", "xdg-open"))
    body.append(box(710, 362, 96, 50, "opener", "as xdg-open"))
    body.append(box(824, 362, 98, 50, "queue", "~/.cache/bifrost"))
    body.append(link(692, 387, 708, 387, OVERLAY))
    body.append(link(806, 387, 822, 387, OVERLAY))
    body.append(link(873, 362, 873, 317, OVERLAY))
    return svg(960, 452, "".join(body), "bifrost architecture: one SSH master per machine carries port forwards, file reads and the watch protocol")


def main():
    (DOCS / "bifrost.svg").write_text(hero())
    (DOCS / "architecture.svg").write_text(architecture())
    for name, draw in LANES.items():
        body = f'<g transform="translate(20 20)">{draw()}</g>'
        (DOCS / f"{name}.svg").write_text(svg(LANE_W + 40, LANE_H + 22, body, f"bifrost: {name}"))


if __name__ == "__main__":
    main()
