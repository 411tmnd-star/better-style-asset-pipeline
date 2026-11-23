"""Editorial hero: the approved ringer tee behind chenille yarn hanging from bleached branches (Veilance F26 direction,
KKK444/selected_frames/10_090 and 10_093).

The garment is NOT rebuilt or re-shaded here. Step 1 uses the pipeline's own renderer, unchanged, to prepare the
calibrated garment scene; this script adds props, light and camera around it. Nothing in the pipeline is written.

  # 1. prepared garment scene (pipeline renderer, unchanged; scratch output dir)
  python engine/render_sku.py --manifest configs/skus/BS-RINGER-001.json --only white-black --views front \
      --res 480x600 --samples 8 --out-dir <tmp> --keep-blend
  # 2. hero
  python storefront/hero/build_hero.py --scene <tmp>/_scene_last.blend --out storefront/hero/renders/ringer_yarn_hero.png

Yarn strands are hanging ropes solved with position-based dynamics (gravity, inextensible segments, pinned ends,
push-out from the garment); the props are not merchandise.
"""
import argparse
import hashlib
import json
import math
import random
import time
from pathlib import Path

import bpy
import numpy as np
from mathutils import Vector

ROOT = Path(__file__).resolve().parents[2]  # better_style_catalog/
SEG = 0.024  # rope simulation segment length (m); render geometry is resampled finer by densify()


def lin(hex_):
    c = [int(hex_.lstrip("#")[i:i + 2], 16) / 255 for i in (0, 2, 4)]
    return tuple(x / 12.92 if x <= 0.04045 else ((x + 0.055) / 1.055) ** 2.4 for x in c) + (1.0,)


# ---------------------------------------------------------------- geometry helpers
def garment_bounds():
    g = [o for o in bpy.data.collections["Garment"].objects if o.type == "MESH"]
    pts = [o.matrix_world @ Vector(c) for o in g for c in o.bound_box]
    lo = np.array([min(p[i] for p in pts) for i in range(3)]); hi = np.array([max(p[i] for p in pts) for i in range(3)])
    return lo, hi, g


def densify(pts, rad, step=0.005):
    """Resample a polyline to ~step spacing so surface displacement has geometry to work with."""
    d = np.r_[0, np.cumsum(np.linalg.norm(np.diff(pts, axis=0), axis=1))]
    t = np.linspace(0, d[-1], max(2, int(d[-1] / step)))
    return np.stack([np.interp(t, d, pts[:, k]) for k in range(3)], 1), np.interp(t, d, rad)


def curve_object(name, polylines, radii, mat, coll, resolution=3):
    """One Curve object holding many poly splines; radii = per-point bevel radius multipliers."""
    cu = bpy.data.curves.new(name, "CURVE"); cu.dimensions = "3D"; cu.bevel_depth = 1.0; cu.bevel_resolution = resolution
    cu.use_fill_caps = True
    for pts, rad in zip(polylines, radii):
        sp = cu.splines.new("POLY"); sp.points.add(len(pts) - 1)
        for p, co, r in zip(sp.points, pts, rad):
            p.co = (co[0], co[1], co[2], 1.0); p.radius = r
    ob = bpy.data.objects.new(name, cu); coll.objects.link(ob); cu.materials.append(mat)
    return ob


def branch(rnd, start, end, n=24, wander=0.035):
    """Gnarled branch from start to end: a jittered path with low-frequency bends."""
    start, end = np.array(start), np.array(end)
    t = np.linspace(0, 1, n)[:, None]
    path = start + (end - start) * t
    for k in (1, 2, 3):
        amp = wander / k; ph = rnd.uniform(0, 6.28)
        path[:, 1] += amp * np.sin(t[:, 0] * math.pi * (k + 1) + ph)
        path[:, 2] += amp * np.cos(t[:, 0] * math.pi * (k + 1.5) + ph * 0.7)
    path += np.array([[rnd.gauss(0, wander * 0.15) for _ in range(3)] for _ in range(n)])
    path[0], path[-1] = start, end
    return path


def twigs(rnd, main, count, length, up_bias=0.6):
    out = []
    for _ in range(count):
        i = rnd.randint(2, len(main) - 3); base = main[i]
        tangent = main[i + 1] - main[i - 1]; tangent /= np.linalg.norm(tangent)
        d = tangent * rnd.uniform(0.2, 0.7) + np.array([rnd.uniform(-0.3, 0.3), rnd.uniform(-0.5, 0.3), rnd.uniform(-0.2, 1.0) * up_bias])
        d /= np.linalg.norm(d)
        out.append((branch(rnd, base, base + d * length * rnd.uniform(0.5, 1.2), n=10, wander=0.012), i))
    return out


# ---------------------------------------------------------------- rope solver
def solve_ropes(ropes, garment_box, steps=200, iters=16, seg=SEG, gravity=-9.81, dt=1 / 120, seed=0):
    """ropes: list of dicts {a: anchor, b: optional second anchor, n: points, lateral: initial sideways drift}.
    Position-based dynamics on all ropes at once (vectorised Jacobi constraints)."""
    rng = np.random.default_rng(seed)
    P, pins, segs = [], [], []
    off = 0
    for r in ropes:
        n = r["n"]; a = np.asarray(r["a"], float)
        if r.get("b") is not None:                       # loop between two anchors: start as a straight sagging chord
            b = np.asarray(r["b"], float); t = np.linspace(0, 1, n)[:, None]
            pts = a + (b - a) * t; pts[:, 2] -= 0.02
            pins += [off, off + n - 1]
        else:                                            # free tail: start stretched out sideways so it swings down
            d = np.array([r["lateral"][0], r["lateral"][1], -1.0]); d /= np.linalg.norm(d)
            pts = a + d * seg * np.arange(n)[:, None]
            pins.append(off)
        P.append(pts)
        segs += [(off + i, off + i + 1) for i in range(n - 1)]
        off += n
    P = np.concatenate(P); prev = P - rng.normal(0, 0.0004, P.shape)
    pins = np.array(pins); pin_pos = P[pins].copy()
    S = np.array(segs); rest = np.linalg.norm(P[S[:, 1]] - P[S[:, 0]], axis=1)
    g = np.array([0, 0, gravity]) * dt * dt
    inv_cnt = (1.0 / np.maximum(np.bincount(S.ravel(), minlength=len(P)), 1))[:, None]
    lo, hi = garment_box
    for step in range(steps):
        vel = (P - prev) * 0.985
        prev = P.copy(); P = P + vel + g
        for _ in range(iters):
            d = P[S[:, 1]] - P[S[:, 0]]; L = np.linalg.norm(d, axis=1) + 1e-9
            corr = (d * ((L - rest) / L)[:, None]) * 0.5
            acc = np.stack([np.bincount(S[:, 0], corr[:, k], len(P)) - np.bincount(S[:, 1], corr[:, k], len(P)) for k in range(3)], 1)
            P += acc * inv_cnt * 0.9
            P[pins] = pin_pos
            inside = np.all((P > lo) & (P < hi), axis=1)          # keep yarn out of the garment: push to the front face
            if inside.any():
                P[inside, 1] = lo[1]
    out, k = [], 0
    for r in ropes:
        out.append(P[k:k + r["n"]]); k += r["n"]
    return out


def kink(pts, rnd, amp):
    """Yarn is never a perfect catenary: low-amplitude kinks perpendicular to the rope."""
    n = len(pts); t = np.linspace(0, 1, n)
    off = np.zeros_like(pts)
    for ax in (0, 1):
        for k in (3, 7, 13):
            off[:, ax] += amp / k * 3 * np.sin(t * k * math.pi + rnd.uniform(0, 6.28))
    off[0] = 0
    return pts + off * np.minimum(1, t * 6)[:, None]


# ---------------------------------------------------------------- materials
def chenille_material():
    m = bpy.data.materials.new("Chenille"); m.use_nodes = True
    nt = m.node_tree; N, L = nt.nodes, nt.links
    b = N["Principled BSDF"]
    co = N.new("ShaderNodeTexCoord"); info = N.new("ShaderNodeObjectInfo")
    # per-strand tone variation around deep navy (Random is per object, so vary along position instead)
    tone = N.new("ShaderNodeTexNoise"); tone.inputs["Scale"].default_value = 3.0
    L.new(co.outputs["Object"], tone.inputs["Vector"])
    ramp = N.new("ShaderNodeValToRGB"); e = ramp.color_ramp.elements
    e[0].position = 0.3; e[0].color = lin("#0a1120"); e[1].position = 0.75; e[1].color = lin("#0e1729")
    L.new(tone.outputs["Fac"], ramp.inputs["Fac"])
    # chenille pile: fine voronoi bumps + noise fuzz
    vor = N.new("ShaderNodeTexVoronoi"); vor.inputs["Scale"].default_value = 500
    fuzz = N.new("ShaderNodeTexNoise"); fuzz.inputs["Scale"].default_value = 300; fuzz.inputs["Detail"].default_value = 12; fuzz.inputs["Roughness"].default_value = 0.85
    L.new(co.outputs["Object"], vor.inputs["Vector"]); L.new(co.outputs["Object"], fuzz.inputs["Vector"])
    hmix = N.new("ShaderNodeMath"); hmix.operation = "ADD"
    L.new(vor.outputs["Distance"], hmix.inputs[0]); L.new(fuzz.outputs["Fac"], hmix.inputs[1])
    bump = N.new("ShaderNodeBump"); bump.inputs["Strength"].default_value = 0.55; bump.inputs["Distance"].default_value = 0.003
    L.new(hmix.outputs[0], bump.inputs["Height"]); L.new(bump.outputs["Normal"], b.inputs["Normal"])
    dark = N.new("ShaderNodeMix"); dark.data_type = "RGBA"; dark.blend_type = "MULTIPLY"; dark.inputs["Factor"].default_value = 0.5
    L.new(ramp.outputs["Color"], dark.inputs["A"]); L.new(vor.outputs["Color"], dark.inputs["B"])
    L.new(ramp.outputs["Color"], b.inputs["Base Color"])
    b.inputs["Roughness"].default_value = 0.95
    b.inputs["Sheen Weight"].default_value = 1.0; b.inputs["Sheen Roughness"].default_value = 0.3
    b.inputs["Specular IOR Level"].default_value = 0.12          # rough dielectric spec greys a near-black base
    b.inputs["Sheen Tint"].default_value = lin("#2b3f66")
    return m


def wood_material():
    m = bpy.data.materials.new("BleachedWood"); m.use_nodes = True
    nt = m.node_tree; N, L = nt.nodes, nt.links; b = N["Principled BSDF"]
    co = N.new("ShaderNodeTexCoord"); n1 = N.new("ShaderNodeTexNoise"); n1.inputs["Scale"].default_value = 60; n1.inputs["Detail"].default_value = 8
    L.new(co.outputs["Object"], n1.inputs["Vector"])
    ramp = N.new("ShaderNodeValToRGB"); e = ramp.color_ramp.elements
    e[0].position = 0.35; e[0].color = lin("#6e5f45"); e[1].position = 0.7; e[1].color = lin("#b8a582")
    L.new(n1.outputs["Fac"], ramp.inputs["Fac"]); L.new(ramp.outputs["Color"], b.inputs["Base Color"])
    bump = N.new("ShaderNodeBump"); bump.inputs["Strength"].default_value = 0.6; bump.inputs["Distance"].default_value = 0.002
    L.new(n1.outputs["Fac"], bump.inputs["Height"]); L.new(bump.outputs["Normal"], b.inputs["Normal"])
    b.inputs["Roughness"].default_value = 0.8
    return m


def flat_material(name, hex_, rough, metallic=0.0, aniso=0.0):
    m = bpy.data.materials.new(name); m.use_nodes = True
    b = m.node_tree.nodes["Principled BSDF"]; b.inputs["Base Color"].default_value = lin(hex_)
    b.inputs["Roughness"].default_value = rough; b.inputs["Metallic"].default_value = metallic
    if aniso: b.inputs["Anisotropic"].default_value = aniso
    return m


def box(name, loc, size, mat, coll):
    bpy.ops.mesh.primitive_cube_add(size=1, location=loc); o = bpy.context.active_object; o.name = name
    o.scale = size; o.data.materials.append(mat)
    for c in o.users_collection: c.objects.unlink(o)
    coll.objects.link(o); return o


def wall_material(hex_):
    m = bpy.data.materials.new("Wall"); m.use_nodes = True
    b = m.node_tree.nodes["Principled BSDF"]; b.inputs["Base Color"].default_value = lin(hex_); b.inputs["Roughness"].default_value = 0.95
    return m


def area(name, loc, target, energy, size, colour=(1, 1, 1), receivers=None):
    d = bpy.data.lights.new(name, "AREA"); d.energy = energy; d.size = size; d.color = colour
    o = bpy.data.objects.new(name, d); bpy.context.scene.collection.objects.link(o); o.location = loc
    o.rotation_euler = (Vector(target) - Vector(loc)).to_track_quat("-Z", "Y").to_euler()
    if receivers is not None:                       # Cycles light linking: light only these objects
        o.light_linking.receiver_collection = receivers
    return o


# ---------------------------------------------------------------- scene
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--scene", required=True, help="_scene_last.blend from engine/render_sku.py --keep-blend")
    ap.add_argument("--out", required=True)
    ap.add_argument("--res", default="2400x1350")
    ap.add_argument("--samples", type=int, default=160)
    ap.add_argument("--seed", type=int, default=184)
    ap.add_argument("--wall", default="#948e84", help="backdrop albedo")
    ap.add_argument("--keep-blend", action="store_true")
    a = ap.parse_args()
    t0 = time.time(); rnd = random.Random(a.seed)

    bpy.ops.wm.open_mainfile(filepath=a.scene)
    sc = bpy.context.scene
    for o in list(bpy.data.objects):
        if o.type in ("LIGHT", "CAMERA", "EMPTY"):
            bpy.data.objects.remove(o, do_unlink=True)
    bpy.data.collections["Mannequin"].hide_render = True
    lo, hi, garment = garment_bounds(); ctr = (lo + hi) / 2; H = hi[2] - lo[2]

    # camera: 75 mm, f/3.2 focused on the chest print; tee fills 65 % of the height, +0.08 frame widths right of centre
    RX, RY = [int(v) for v in a.res.split("x")]
    cam = bpy.data.objects.new("cam", bpy.data.cameras.new("cam")); sc.collection.objects.link(cam); sc.camera = cam
    cam.data.lens = 75; cam.data.sensor_fit = "HORIZONTAL"; cam.data.sensor_width = 36
    vh = H / 0.65; aspect = RY / RX
    dist = vh * cam.data.lens / (36 * aspect)
    cam.location = (ctr[0], ctr[1] - dist, ctr[2] + 0.01); cam.rotation_euler = (math.radians(90), 0, 0)
    shift = -0.08; cam.data.shift_x = shift
    focus = bpy.data.objects.new("focus", None); sc.collection.objects.link(focus)
    focus.location = (ctr[0], lo[1] + 0.03, hi[2] - 0.13)                      # front surface at the chest script
    cam.data.dof.use_dof = True; cam.data.dof.focus_object = focus; cam.data.dof.aperture_fstop = 3.2

    def at(u, v, y):
        """World point that lands at frame coords (u, v) in [-0.5, 0.5] at world depth y."""
        D = y - cam.location[1]; W = D * 36 / cam.data.lens
        return np.array([cam.location[0] + (shift + u) * W, y, cam.location[2] + v * W * aspect])

    def frame_u(p):
        D = p[1] - cam.location[1]; W = D * 36 / cam.data.lens
        return (p[0] - cam.location[0]) / W - shift

    props = bpy.data.collections.new("Props"); sc.collection.children.link(props)
    wood, yarn = wood_material(), chenille_material()

    # grounding: matte-black neck form in the collar + brushed-steel rod from the hem (no mannequin)
    top = [o.matrix_world @ v.co for o in garment for v in o.data.vertices if (o.matrix_world @ v.co).z > hi[2] - 0.02]
    xs = [p.x for p in top]; ys = [p.y for p in top]
    nx, ny = (min(xs) + max(xs)) / 2, (min(ys) + max(ys)) / 2
    rx, ry = (max(xs) - min(xs)) / 2 * 0.72, (max(ys) - min(ys)) / 2 * 0.72
    bpy.ops.mesh.primitive_cylinder_add(vertices=48, radius=1, depth=1, location=(nx, ny, hi[2] - 0.02))
    neck = bpy.context.active_object; neck.name = "NeckForm"; neck.scale = (rx, ry, 0.19)
    bev = neck.modifiers.new("Bevel", "BEVEL"); bev.width = 0.03; bev.segments = 8; bev.affect = "EDGES"
    nm = flat_material("NeckForm", "#0d0d0f", 0.85); nm.node_tree.nodes["Principled BSDF"].inputs["Specular IOR Level"].default_value = 0.15
    neck.data.materials.append(nm)
    bpy.ops.mesh.primitive_cylinder_add(vertices=24, radius=0.012, depth=1, location=(ctr[0], ctr[1], 0))
    rod = bpy.context.active_object; rod.name = "StandRod"
    rod_top, rod_bot = lo[2] + 0.10, ctr[2] - vh                                 # from inside the hem to below the frame
    rod.location = (ctr[0], ctr[1], (rod_top + rod_bot) / 2); rod.scale = (1, 1, rod_top - rod_bot)
    rod.data.materials.append(flat_material("BrushedSteel", "#9a9ca0", 0.3, metallic=0.9, aniso=0.6))
    for o in (neck, rod):
        for c in o.users_collection: c.objects.unlink(o)
        props.objects.link(o)

    # three planes of props, placed in frame coordinates
    y_fg, y_mid, y_back = lo[1] - 0.85, lo[1] - 0.22, hi[1] + 0.18
    A = branch(rnd, at(-0.60, 0.40, y_fg), at(-0.10, 0.27, y_fg + 0.05), n=26, wander=0.04)        # foreground left, crossing the lens
    B = branch(rnd, at(-0.30, -0.40, y_mid), at(0.62, -0.34, y_mid - 0.05), n=30, wander=0.035)    # low across the foreground
    Cf = branch(rnd, at(-0.60, 0.60, y_mid), at(0.62, 0.57, y_mid + 0.03), n=30, wander=0.03)      # canopy, just above the frame
    Cb = branch(rnd, at(-0.55, 0.62, y_back), at(0.62, 0.60, y_back), n=26, wander=0.03)           # canopy behind the tee
    mains = [A, B, Cf, Cb]
    tw = [t for t, _ in twigs(rnd, A, 10, 0.16)] + [t for t, _ in twigs(rnd, B, 14, 0.14, up_bias=0.9)]
    wood_lines = mains + tw
    wood_radii = [np.linspace(0.010, 0.0045, len(p)) for p in mains] + [np.linspace(0.004, 0.001, len(p)) for p in tw]
    curve_object("Branches", wood_lines, wood_radii, wood, props, resolution=4)

    # tee in frame coords: spans tee_u0 .. tee_u1; the chest script and rider sit right of tee_u0 + 0.07
    tee_half_u = (hi[0] - lo[0]) / 2 / ((ctr[1] - cam.location[1]) * 36 / cam.data.lens)
    tee_u0, tee_u1 = -shift - tee_half_u, -shift + tee_half_u
    ropes = []

    def strand(anchor, max_len, fine=False):
        n = max(8, int(rnd.uniform(0.25, 1.0) * max_len / SEG))
        base = rnd.uniform(0.0035, 0.007) * 0.65                                  # 35 % thinner than the first pass
        ropes.append(dict(a=anchor, n=n, lateral=(rnd.uniform(-0.4, 0.4), rnd.uniform(-0.3, 0.3)),
                          r=base * (0.25 / 0.65 if fine else 1.0), fine=fine))    # fine threads: 0.25x the original radius

    def clump(path, count, strands, max_len, loops, keep=None):
        for _ in range(count):
            i = rnd.randint(1, len(path) - 2); anchor = path[i]
            if keep and not keep(anchor):
                continue
            for _ in range(rnd.randint(*strands)):
                j = anchor + np.array([rnd.gauss(0, 0.012), rnd.gauss(0, 0.012), rnd.uniform(-0.004, 0.01)])
                strand(j, max_len, fine=rnd.random() < 0.30)
            for _ in range(loops):
                k = min(len(path) - 1, i + rnd.randint(2, 6)); b = path[k] + np.array([0, rnd.gauss(0, 0.01), 0])
                span = np.linalg.norm(b - anchor); n = max(8, int(span * rnd.uniform(1.15, 1.6) / SEG))
                ropes.append(dict(a=anchor, b=b, n=n, r=rnd.uniform(0.005, 0.008) * 0.65, fine=False))

    # foreground: dense curtain at the left; only strands left of the print zone may cross the tee
    clump(A, 16, (8, 18), 1.4, 4, keep=lambda p: frame_u(p) < tee_u0 + 0.07)
    clump(B, 30, (8, 20), 0.42, 7)
    # canopy: mostly fine threads into the top of the frame; over the tee they stop above the collar
    for path, cnt in ((Cf, 34), (Cb, 26)):
        for _ in range(cnt):
            i = rnd.randint(1, len(path) - 2); anchor = path[i]
            over = path is Cf and tee_u0 - 0.02 < frame_u(anchor) < tee_u1 + 0.02
            reach = (0.10 if over else rnd.uniform(0.18, 0.42)) * vh
            for _ in range(rnd.randint(5, 14)):
                j = anchor + np.array([rnd.gauss(0, 0.015), rnd.gauss(0, 0.01), 0])
                strand(j, reach + 0.08 * vh, fine=rnd.random() < 0.55)
    solved = solve_ropes(ropes, (lo - 0.01, hi + 0.01), seed=a.seed)
    lines, radii = [], []
    for r, pts in zip(ropes, solved):
        pts = kink(pts, rnd, 0.022 if r["fine"] else 0.013)
        lines.append(pts)
        taper = np.ones(len(pts)); taper[-2:] = (0.85, 0.6) if r.get("b") is None else (1, 1)
        radii.append(r["r"] * taper * np.array([rnd.uniform(0.85, 1.15) for _ in pts]))
    dense = [densify(p, r) for p, r in zip(lines, radii)]
    yob = curve_object("Yarn", [d[0] for d in dense], [d[1] for d in dense], yarn, props, resolution=2)
    with bpy.context.temp_override(selected_objects=[yob], selected_editable_objects=[yob], active_object=yob, object=yob):
        bpy.ops.object.convert(target="MESH")
    tex = bpy.data.textures.new("pile", "CLOUDS"); tex.noise_scale = 0.0035; tex.noise_depth = 2
    dm = yob.modifiers.new("Pile", "DISPLACE"); dm.texture = tex; dm.strength = 0.0012; dm.mid_level = 0.5; dm.texture_coords = "GLOBAL"
    sim_s = round(time.time() - t0, 1)

    # gallery wall: modular panels with 4 mm seams over a dark cavity (seams placed away from the tee)
    wy = hi[1] + 0.9
    cavity = flat_material("Cavity", "#1a1917", 0.9); panel = wall_material(a.wall)
    walls = bpy.data.collections.new("Wall"); sc.collection.children.link(walls)
    box("Cavity", (ctr[0], wy + 0.03, ctr[2]), (12, 0.01, 8), cavity, walls)
    seams = sorted(at(u, 0, wy)[0] for u in (-0.31, 0.47))
    edges = [ctr[0] - 6] + seams + [ctr[0] + 6]
    for k in range(len(edges) - 1):
        x0, x1 = edges[k] + 0.002, edges[k + 1] - 0.002
        box(f"Panel{k}", ((x0 + x1) / 2, wy, ctr[2]), (x1 - x0, 0.03, 8), panel, walls)
    w = bpy.data.worlds.new("w"); sc.world = w; w.use_nodes = True
    w.node_tree.nodes["Background"].inputs[0].default_value = lin("#d8d3ca"); w.node_tree.nodes["Background"].inputs[1].default_value = 0.05

    # light: two high gallery lights at about 55 degrees, weak frontal fill, linked rim, top-down wall washes
    tgt = tuple(ctr)

    def high(name, side, energy, size):
        horiz = 1.45
        loc = (ctr[0] + side * 0.85, ctr[1] - horiz, ctr[2] + horiz * math.tan(math.radians(55)))
        area(name, loc, tgt, energy, size, (1.0, 0.97, 0.92))
    high("gallery_l", -1, 330, 0.7); high("gallery_r", 1, 170, 0.7)
    area("fill", (ctr[0] + 0.3, ctr[1] - 2.6, ctr[2] - 0.1), tgt, 45, 3.0, (0.95, 0.97, 1.0))
    lit = bpy.data.collections.new("RimReceivers")
    for o in list(garment) + list(props.objects):
        lit.objects.link(o)
    area("rim", (ctr[0] + 0.5, ctr[1] + 0.7, ctr[2] + 0.9), tgt, 140, 1.4, (1.0, 0.96, 0.9), receivers=lit)
    for u in (-0.45, 0.15):
        p = at(u, 0.5, wy)
        area(f"wash{u}", (p[0], wy - 0.35, p[2] + 0.9), (p[0], wy, p[2] - 0.6), 90, 0.5, (1.0, 0.95, 0.88))

    r = sc.render; r.engine = "CYCLES"; r.resolution_x, r.resolution_y = RX, RY; r.resolution_percentage = 100
    r.film_transparent = False; r.image_settings.file_format = "PNG"; r.image_settings.color_mode = "RGB"; r.image_settings.color_depth = "8"
    c = sc.cycles; c.device = "CPU"; c.samples = a.samples; c.use_denoising = True; c.max_bounces = 6
    out = Path(a.out).resolve(); out.parent.mkdir(parents=True, exist_ok=True); r.filepath = str(out)
    t1 = time.time(); bpy.ops.render.render(write_still=True); render_s = round(time.time() - t1, 1)
    if a.keep_blend:
        bpy.ops.wm.save_as_mainfile(filepath=str(out.with_suffix(".blend")))

    master = ROOT / "assets/archetypes/ringer_tee_master.blend"
    out.with_suffix(".json").write_text(json.dumps({
        "what": "editorial hero: approved garment + simulated chenille yarn on bleached branches (props are not merchandise)",
        "reference": "Veilance F26 at HUES: KKK444/selected_frames/10_090, 10_093; KKK444 WhatsApp 7.46.54 PM, 7.46.55 PM (1)",
        "garment": {"sku": "BS-RINGER-001", "colourway": "white-black", "master": master.relative_to(ROOT).as_posix(),
                    "master_sha256": hashlib.sha256(master.read_bytes()).hexdigest(),
                    "shading": "engine/render_sku.py --keep-blend (calibrated), unchanged"},
        "props": {"yarn_strands": len(ropes), "fine_threads": sum(r["fine"] for r in ropes), "branches": len(mains), "twigs": len(tw),
                  "grounding": "matte-black neck form + brushed-steel rod (not a mannequin)"},
        "camera": {"lens_mm": 75, "fstop": 3.2, "focus": "chest script", "garment_frame_height": 0.65},
        "params": {k: getattr(a, k) for k in ("res", "samples", "seed", "wall")},
        "view": {"transform": sc.view_settings.view_transform, "look": sc.view_settings.look, "exposure": sc.view_settings.exposure},
        "seconds": {"simulation": sim_s, "render": render_s},
    }, indent=1))
    print(f"HERO {out.name}: {len(ropes)} strands, sim {sim_s}s, render {render_s}s")


if __name__ == "__main__":
    main()
