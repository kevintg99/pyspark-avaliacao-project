"""Enquadramentos variados (o que evita cara de template).

Cada shot recebe um layout sorteado com seed própria, então a mesma seed
sempre gera o mesmo vídeo, independentemente da ordem de renderização.

Layouts de vídeo:  full · zoom_crop · push (zoom lento) · inset (fundo desfocado)
Layouts de imagem: kenburns (zoom/pan com direção e velocidade variadas) · inset
"""

from __future__ import annotations

import random
from typing import Any

from .models import MediaType

KENBURNS_MOVES = ("zoom_in", "zoom_out", "pan_left", "pan_right", "pan_up", "diagonal")


def _weighted(options: dict[str, float], rng: random.Random, avoid: str | None) -> str:
    items = [(k, w) for k, w in options.items() if w > 0 and k != avoid] or list(options.items())
    total = sum(w for _, w in items)
    pick = rng.uniform(0, total)
    for key, weight in items:
        pick -= weight
        if pick <= 0:
            return key
    return items[-1][0]


def choose_layout(media_type: MediaType, source_aspect: float, video_layouts: dict[str, float],
                  image_layouts: dict[str, float], rng: random.Random, previous: str | None) -> dict[str, Any]:
    """Sorteia o layout e seus parâmetros. Mídia vertical sempre vai de inset."""
    portrait = source_aspect < 1.0
    if media_type is MediaType.IMAGE:
        name = "inset" if portrait else _weighted(image_layouts, rng, previous)
    else:
        name = "inset" if portrait else _weighted(video_layouts, rng, previous)

    r3 = lambda a, b: round(rng.uniform(a, b), 3)  # noqa: E731
    layout: dict[str, Any] = {"name": name}
    if name == "full":
        pass
    elif name == "zoom_crop":
        layout.update(zoom=r3(1.08, 1.28), fx=r3(0.2, 0.8), fy=r3(0.25, 0.75))
    elif name == "push":
        z0 = r3(1.0, 1.06)
        layout.update(z0=z0, z1=round(z0 + rng.uniform(0.06, 0.16), 3), fx=r3(0.35, 0.65), fy=r3(0.35, 0.65))
    elif name == "kenburns":
        move = rng.choice(KENBURNS_MOVES)
        strength = rng.uniform(0.08, 0.22)
        z_hi = round(1.0 + strength + 0.05, 3)
        if move == "zoom_in":
            layout.update(z0=1.0, z1=z_hi, fx0=0.5, fx1=r3(0.4, 0.6), fy0=0.5, fy1=r3(0.4, 0.6))
        elif move == "zoom_out":
            layout.update(z0=z_hi, z1=1.0, fx0=r3(0.35, 0.65), fx1=0.5, fy0=r3(0.35, 0.65), fy1=0.5)
        elif move == "pan_left":
            layout.update(z0=z_hi, z1=z_hi, fx0=r3(0.75, 1.0), fx1=r3(0.0, 0.25), fy0=0.5, fy1=0.5)
        elif move == "pan_right":
            layout.update(z0=z_hi, z1=z_hi, fx0=r3(0.0, 0.25), fx1=r3(0.75, 1.0), fy0=0.5, fy1=0.5)
        elif move == "pan_up":
            layout.update(z0=z_hi, z1=z_hi, fx0=0.5, fx1=0.5, fy0=r3(0.75, 1.0), fy1=r3(0.0, 0.25))
        else:
            layout.update(z0=1.02, z1=z_hi, fx0=r3(0.0, 0.3), fx1=r3(0.7, 1.0), fy0=r3(0.0, 0.3), fy1=r3(0.7, 1.0))
        layout["move"] = move
    elif name == "inset":
        layout.update(scale=r3(0.72, 0.9), fx=r3(0.42, 0.58), fy=r3(0.44, 0.56), blur=rng.randint(18, 32),
                      dim=r3(-0.12, -0.04), drift=r3(0.0, 0.08))
    return layout


def build_filter(layout: dict[str, Any], w: int, h: int, fps: int, frames: int, speed: float = 1.0,
                 src_aspect: float = 16 / 9) -> str:
    """Filtergraph de ``[0:v]`` até ``[v]`` para um shot já normalizado em WxH@fps."""
    head = f"[0:v]setpts=(PTS-STARTPTS)*{speed:.4f},fps={fps},setsar=1"
    tail = f"setsar=1,format=yuv420p,tpad=stop_mode=clone:stop_duration=4[v]"
    n = max(1, frames - 1)
    name = layout.get("name", "full")

    def cover(scale: float = 1.0) -> str:
        sw, sh = _even(w * scale), _even(h * scale)
        return f"scale={sw}:{sh}:force_original_aspect_ratio=increase:flags=lanczos"

    if name == "zoom_crop":
        z = layout["zoom"]
        return f"{head},{cover(z)},crop={w}:{h}:(iw-{w})*{layout['fx']}:(ih-{h})*{layout['fy']},{tail}"

    if name in ("push", "kenburns"):
        z0, z1 = layout["z0"], layout["z1"]
        fx0, fx1 = layout.get("fx0", layout.get("fx", 0.5)), layout.get("fx1", layout.get("fx", 0.5))
        fy0, fy1 = layout.get("fy0", layout.get("fy", 0.5)), layout.get("fy1", layout.get("fy", 0.5))
        # pré-escala 2x reduz o "tremido" do zoompan (que arredonda x/y para inteiros)
        pre = 2.0 if name == "kenburns" else 1.5
        pw, ph = _even(w * pre), _even(h * pre)
        # suavização (ease in-out) para o movimento parecer de câmera, não linear
        p = f"(0.5-0.5*cos(PI*on/{n}))"
        zexpr = f"{z0}+({z1}-{z0})*{p}"
        xexpr = f"(iw-iw/zoom)*({fx0}+({fx1}-{fx0})*{p})"
        yexpr = f"(ih-ih/zoom)*({fy0}+({fy1}-{fy0})*{p})"
        return (f"{head},scale={pw}:{ph}:force_original_aspect_ratio=increase:flags=lanczos,crop={pw}:{ph},"
                f"zoompan=z='{zexpr}':x='{xexpr}':y='{yexpr}':d=1:s={w}x{h}:fps={fps},{tail}")

    if name == "inset":
        box_w, box_h = w * layout["scale"], h * layout["scale"]
        if src_aspect >= box_w / box_h:
            fw, fh = _even(box_w), _even(box_w / src_aspect)
        else:
            fw, fh = _even(box_h * src_aspect), _even(box_h)
        drift = layout.get("drift", 0.0)
        # fundo: a própria mídia ampliada, desfocada e escurecida; frente: mídia inteira
        # com um zoom bem leve (drift) para não ficar estática
        return (
            f"{head},split=2[bgsrc][fgsrc];"
            f"[bgsrc]{cover(1.0)},crop={w}:{h},gblur=sigma={layout['blur']},eq=brightness={layout['dim']}[bg];"
            f"[fgsrc]scale={_even(fw * 1.5)}:{_even(fh * 1.5)}:flags=lanczos,"
            f"zoompan=z='1+{drift}*(0.5-0.5*cos(PI*on/{n}))':x='(iw-iw/zoom)/2':y='(ih-ih/zoom)/2'"
            f":d=1:s={fw}x{fh}:fps={fps}[fg];"
            f"[bg][fg]overlay=x=(W-w)*{layout['fx']}:y=(H-h)*{layout['fy']}:shortest=1,{tail}"
        )

    # full
    return f"{head},{cover(1.0)},crop={w}:{h},{tail}"


def _even(value: float) -> int:
    v = int(round(value))
    return v if v % 2 == 0 else v + 1
