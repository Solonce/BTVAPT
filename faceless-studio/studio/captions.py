"""Burned-in captions as an ASS subtitle file (word-by-word highlight or subtle)."""
from __future__ import annotations

from pathlib import Path

from .settings import Settings


def _ass_color(hex_rgb: str) -> str:
    h = hex_rgb.lstrip("#")
    if len(h) != 6:
        h = "FFFFFF"
    return f"&H00{h[4:6]}{h[2:4]}{h[0:2]}".upper()


def _ts(t: float) -> str:
    t = max(0.0, t)
    h, rem = divmod(t, 3600)
    m, sec = divmod(rem, 60)
    return f"{int(h)}:{int(m):02d}:{sec:05.2f}"


def _clean(text: str) -> str:
    return text.replace("\\", "").replace("{", "(").replace("}", ")").replace("\n", " ")


def _chunks(words: list[dict], size: int) -> list[list[dict]]:
    out, cur = [], []
    for w in words:
        cur.append(w)
        if len(cur) >= size or w["text"].rstrip()[-1:] in ".!?,;:":
            out.append(cur)
            cur = []
    if cur:
        out.append(cur)
    return out


def build(s: Settings, words: list[dict], width: int, height: int, dest: Path) -> Path:
    vertical = height > width
    pop = s.caption_style == "pop"
    size = int(height * (0.048 if vertical else 0.07)) if pop else int(height * (0.035 if vertical else 0.05))
    margin_v = int(height * (0.26 if vertical else 0.09))
    outline = max(2, size // 12) if pop else max(1, size // 18)
    primary, hi = _ass_color(s.caption_color), _ass_color(s.caption_highlight)

    lines = [
        "[Script Info]", "ScriptType: v4.00+", f"PlayResX: {width}", f"PlayResY: {height}",
        "WrapStyle: 0", "ScaledBorderAndShadow: yes", "",
        "[V4+ Styles]",
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, "
        "Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, "
        "Shadow, Alignment, MarginL, MarginR, MarginV, Encoding",
        f"Style: Main,{s.caption_font},{size},{primary},{primary},&H00000000,&H80000000,"
        f"{-1 if pop else 0},0,0,0,100,100,0,0,1,{outline},{2 if pop else 1},2,"
        f"{int(width * 0.08)},{int(width * 0.08)},{margin_v},1",
        "", "[Events]",
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text",
    ]

    groups = _chunks(words, s.caption_words if pop else max(5, s.caption_words * 2))
    for gi, group in enumerate(groups):
        next_start = groups[gi + 1][0]["start"] if gi + 1 < len(groups) else group[-1]["end"] + 0.4
        group_end = min(next_start, group[-1]["end"] + 0.6)
        texts = [_clean(w["text"]).rstrip(".,;:").upper() if pop else _clean(w["text"]) for w in group]
        if not pop:
            lines.append(f"Dialogue: 0,{_ts(group[0]['start'])},{_ts(group_end)},Main,,0,0,0,,{' '.join(texts)}")
            continue
        for wi, w in enumerate(group):
            end = group[wi + 1]["start"] if wi + 1 < len(group) else group_end
            parts = []
            for k, t in enumerate(texts):
                parts.append("{\\c" + hi + "&\\fscx112\\fscy112}" + t + "{\\c" + primary + "&\\fscx100\\fscy100}"
                             if k == wi else t)
            pop_in = "{\\fad(40,0)}" if wi == 0 else ""
            lines.append(f"Dialogue: 0,{_ts(w['start'])},{_ts(end)},Main,,0,0,0,,{pop_in}{' '.join(parts)}")

    dest.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return dest
