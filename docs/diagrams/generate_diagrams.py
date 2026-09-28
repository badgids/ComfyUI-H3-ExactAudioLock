#!/usr/bin/env python3
"""Pure-SVG flowchart generator (lumen dark-professional / Style 2 Dark Terminal).
Grounded in the actual ComfyUI-H3-ExactAudioLock workflow wiring.

Hard rules enforced by design + audited at render time:
- every segment is strictly horizontal or vertical (no diagonals)
- outputs only leave the RIGHT or BOTTOM of a node
- inputs only enter the LEFT or TOP of a node
- no two same-color lines overlap or share a collinear segment
- no line pierces a node (audited per shape, diamond-aware)
- node text always fits inside the node boundary (width auto-check)
"""
import html, sys, os

# ── palette (fireworks-tech-graph · Style 2 Dark Terminal) ──────
BG      = "#0f0f1a"
BG2     = "#1a1a2e"
PANEL   = "#0f172a"
GRIDC   = "#1d2338"
TEXT    = "#e2e8f0"
SUBT    = "#94a3b8"

TONES = {
    "cyan":   "#3b82f6",
    "green":  "#10b981",
    "purple": "#a855f7",
    "amber":  "#eab308",
    "rose":   "#ef4444",
    "orange": "#f97316",
    "slate":  "#334155",
}

EDGE = {
    "data":    "#a855f7",   # purple — audio / latent payloads
    "control": "#3b82f6",   # blue — INT frames / scene indices
    "guide":   "#f97316",   # orange — conditioning
    "write":   "#10b981",   # green — durable file writes
    "loop":    "#eab308",   # gold — recursive loop / overlaps
    "dim":     "#475569",   # slate — optional / legacy / annotations
}

MONO = "'JetBrains Mono','SFMono-Regular',Consolas,'Liberation Mono',Menlo,monospace"
CH_T, CH_S = 8.06, 6.82

def esc(s):
    return html.escape(s, quote=True)

def T(*lines): return [(t, "t") for t in lines]
def S(*lines): return [(t, "s") for t in lines]

class Node:
    def __init__(self, nid, x, y, w, lines, tone, shape="rect", dash=False, min_h=None):
        self.id, self.x, self.y = nid, x, y
        self.lines, self.tone = lines, TONES[tone]
        self.shape, self.dash = shape, dash
        nt = sum(1 for _, k in lines if k == "t")
        ns = sum(1 for _, k in lines if k == "s")
        self.h = max(min_h or 0, 18 + 14 * nt + 13 * ns)
        need = max((len(t) * (CH_T if k == "t" else CH_S) for t, k in lines), default=0)
        pad = 78 if shape == "diamond" else (30 if shape == "file" else 28)
        self.w = max(w, need + pad)
        if self.w != w:
            print(f"  ! width grown {nid}: {w} -> {self.w:.1f}")

    def cx(self): return self.x + self.w / 2
    def cy(self): return self.y + self.h / 2

    def on_boundary(self, p, tol=1e-4):
        x, y = p
        if self.shape == "hex":
            c = min(self.w * 0.16, 26)
            hx = [(self.x+c, self.y), (self.x+self.w-c, self.y), (self.x+self.w, self.y+self.h/2),
                  (self.x+self.w-c, self.y+self.h), (self.x+c, self.y+self.h), (self.x, self.y+self.h/2)]
            for i in range(6):
                ax, ay = hx[i]; bx, by = hx[(i+1) % 6]
                dx, dy = bx-ax, by-ay
                if dx == 0 and dy == 0: continue
                t = max(0.0, min(1.0, ((x-ax)*dx + (y-ay)*dy) / (dx*dx + dy*dy)))
                if (x-(ax+t*dx))**2 + (y-(ay+t*dy))**2 < tol*tol:
                    return True
            return False
        box = (abs(x-self.x) < tol or abs(x-(self.x+self.w)) < tol or
               abs(y-self.y) < tol or abs(y-(self.y+self.h)) < tol)
        if box:
            inside = (self.x - tol <= x <= self.x + self.w + tol and
                      self.y - tol <= y <= self.y + self.h + tol)
            if inside:
                return True
        if self.shape == "diamond":
            dx = abs(x - self.cx()) / (self.w / 2)
            dy = abs(y - self.cy()) / (self.h / 2)
            return abs(dx + dy - 1) < 0.01
        return False

    def interior(self, p, eps=1e-4):
        x, y = p
        if self.shape == "diamond":
            return (abs(x - self.cx()) / (self.w / 2) +
                    abs(y - self.cy()) / (self.h / 2)) < 1 - eps
        return (self.x + eps < x < self.x + self.w - eps and
                self.y + eps < y < self.y + self.h - eps)

def snap_edges(d, tol=16):
    """Nudge near-boundary endpoints exactly onto the boundary (no-op when exact)."""
    def slab(p0, p1, n):
        x0, y0 = p0
        dx, dy = p1[0] - x0, p1[1] - y0
        t1, t2, ok = 0.0, 1.0, True
        for lo, hi, dd, c in ((n.x, n.x + n.w, dx, x0), (n.y, n.y + n.h, dy, y0)):
            if abs(dd) < 1e-9:
                if c < lo or c > hi: ok = False
                continue
            a, b = (lo - c) / dd, (hi - c) / dd
            if a > b: a, b = b, a
            t1, t2 = max(t1, a), min(t2, b)
            if t1 > t2: ok = False
        if not ok or t1 > t2: return None
        return (t1, t2)

    for e in d.edges:
        pts = e["pts"]
        if len(pts) < 2: continue
        p0, p1 = pts[0], pts[1]
        for n in d.nodes:
            if n.on_boundary(p0, tol):
                s = slab(p0, p1, n)
                if s is None: continue
                t = s[0] if s[0] > 1e-6 else 0.0
                e["pts"][0] = (p0[0] + (p1[0] - p0[0]) * t, p0[1] + (p1[1] - p0[1]) * t)
                break
        pL, pP = pts[-1], pts[-2]
        for n in d.nodes:
            if n.on_boundary(pL, tol):
                s = slab(pL, pP, n)
                if s is None: continue
                t = s[0] if s[0] > 1e-6 else 0.0
                e["pts"][-1] = (pL[0] + (pP[0] - pL[0]) * t, pL[1] + (pP[1] - pL[1]) * t)
                break

def audit(d):
    """Verify: orthogonal segments, port-side rules, no node piercing, no same-color overlap."""
    probs = []
    def segs(e):
        out = []
        p = e["pts"]
        for a, b in zip(p, p[1:]):
            if abs(a[0]-b[0]) > 1e-6 and abs(a[1]-b[1]) > 1e-6:
                probs.append(f"diagonal {a} -> {b}")
            if abs(a[0]-b[0]) < 1e-6 and abs(a[1]-b[1]) < 1e-6:
                probs.append(f"zero-length {a}")
                continue
            out.append((a, b))
        return out
    for e in d.edges:
        if e.get("anno"): continue
        ss = segs(e)
        if not ss: continue
        (a0, b0) = ss[0]; (aL, bL) = ss[-1]
        src = next((n for n in d.nodes if n.on_boundary(a0)), None)
        if src is None:
            probs.append(f"edge start {a0} not on any node boundary")
        else:
            # strict: outputs only leave the RIGHT or BOTTOM of a node
            cx, cy = src.cx(), src.cy()
            ok = False
            if b0[0] > a0[0] + 1e-9 and (a0[0] >= cx - 1e-6 or abs(a0[1] - (src.y + src.h)) < 1e-6):
                ok = True   # leaves to the right, from the right half (or along the bottom edge)
            if b0[1] > a0[1] + 1e-9 and (a0[1] >= cy - 1e-6 or abs(a0[0] - (src.x + src.w)) < 1e-6):
                ok = True   # leaves downward, from the bottom half (or along the right edge)
            if not ok:
                probs.append(f"OUTPUT leaves {src.id} not from right/bottom at {a0}")
        tgt = next((n for n in d.nodes if n.on_boundary(bL)), None)
        if tgt is None:
            probs.append(f"edge end {bL} not on any node boundary")
        else:
            # strict: inputs only enter the LEFT or TOP of a node
            cx, cy = tgt.cx(), tgt.cy()
            ok = False
            if aL[0] < bL[0] - 1e-9 and (bL[0] <= cx + 1e-6 or abs(bL[1] - tgt.y) < 1e-6):
                ok = True   # enters from the left, into the left half (or along the top edge)
            if aL[1] < bL[1] - 1e-9 and (bL[1] <= cy + 1e-6 or abs(bL[0] - tgt.x) < 1e-6):
                ok = True   # enters from above, into the top half (or along the left edge)
            if not ok:
                probs.append(f"INPUT enters {tgt.id} not from left/top at {bL}")
    # node piercing (rigorous: positive-length intersection with node interior)
    for e in d.edges:
        if e.get("anno"): continue
        p = e["pts"]
        for a, b in zip(p, p[1:]):
            for n in d.nodes:
                def diamond_hit():
                    cx, cy, hw, hh = n.cx(), n.cy(), n.w/2, n.h/2
                    def f(t):
                        x = a[0] + (b[0]-a[0])*t; y = a[1] + (b[1]-a[1])*t
                        return abs(x-cx)/hw + abs(y-cy)/hh
                    cands = [0.0, 1.0]
                    for P, Q, C in ((a[0], b[0], cx), (a[1], b[1], cy)):
                        if abs(Q-P) > 1e-9:
                            t = (C-P)/(Q-P)
                            if 0.0 < t < 1.0: cands.append(t)
                    return min(f(t) for t in cands) < 1 - 1e-7
                if n.shape == "diamond":
                    hit = diamond_hit()
                elif abs(a[1]-b[1]) < 1e-9 and n.y < a[1] < n.y + n.h:
                    lo = max(min(a[0], b[0]), n.x); hi = min(max(a[0], b[0]), n.x + n.w)
                    hit = hi - lo > 1e-6
                elif abs(a[0]-b[0]) < 1e-9 and n.x < a[0] < n.x + n.w:
                    lo = max(min(a[1], b[1]), n.y); hi = min(max(a[1], b[1]), n.y + n.h)
                    hit = hi - lo > 1e-6
                else:
                    hit = diamond_hit()  # boundary-hugging box segment: L1 test is safe (f >= 1 on the boundary)
                if hit:
                    probs.append(f"segment {a}->{b} pierces {n.id}")
    # same-color collinear overlap
    allsegs = []
    for e in d.edges:
        for a, b in zip(e["pts"], e["pts"][1:]):
            allsegs.append((e["sem"], a, b))
    for i in range(len(allsegs)):
        s1, a1, b1 = allsegs[i]
        for j in range(i+1, len(allsegs)):
            s2, a2, b2 = allsegs[j]
            if abs(a1[1]-b1[1]) < 1e-6 and abs(a2[1]-b2[1]) < 1e-6 and abs(a1[1]-a2[1]) < 1e-6:
                lo = max(min(a1[0],b1[0]), min(a2[0],b2[0]))
                hi = min(max(a1[0],b1[0]), max(a2[0],b2[0]))
                if hi - lo > 0.5:
                    probs.append(f"overlap {s1}/{s2} y={a1[1]} x {lo:.0f}..{hi:.0f}")
            if abs(a1[0]-b1[0]) < 1e-6 and abs(a2[0]-b2[0]) < 1e-6 and abs(a1[0]-a2[0]) < 1e-6:
                lo = max(min(a1[1],b1[1]), min(a2[1],b2[1]))
                hi = min(max(a1[1],b1[1]), max(a2[1],b2[1]))
                if hi - lo > 0.5:
                    probs.append(f"overlap {s1}/{s2} x={a1[0]} y {lo:.0f}..{hi:.0f}")
    return probs

def blend(tone_hex, frac=0.13):
    def hx(c): return int(c[1:3], 16), int(c[3:5], 16), int(c[5:7], 16)
    a, b = hx(PANEL), hx(tone_hex)
    m = [round(x + (y - x) * frac) for x, y in zip(a, b)]
    return f"rgb({m[0]},{m[1]},{m[2]})"

class Diagram:
    def __init__(self, W, H, title, subtitle):
        self.W, self.H, self.title, self.subtitle = W, H, title, subtitle
        self.nodes, self.edges, self.legend, self.notes = [], [], [], []
        self.texts = []

    def text(self, x, y, s, size=11, color=SUBT, anchor="start", weight=400):
        self.texts.append((x, y, s, size, color, anchor, weight))

    def node(self, nid, x, y, w, lines, tone, shape="rect", dash=False, min_h=None):
        n = Node(nid, x, y, w, lines, tone, shape, dash, min_h)
        self.nodes.append(n)
        return n

    def pts(self, points, sem, thick=False, dash=None, arrow=True, anno=False):
        self.edges.append(dict(pts=points, sem=sem, thick=thick, dash=dash, arrow=arrow, anno=anno))
        return self

    def render(self, fname):
        snap_edges(self)
        probs = audit(self)
        for p in probs:
            print(f"  AUDIT {fname}: {p}")
        if not probs:
            print(f"  audit clean: {fname}")
        W, H = self.W, self.H
        o = []
        o.append(f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" viewBox="0 0 {W} {H}">')
        o.append('<defs>')
        o.append(f'<linearGradient id="bg-grad" gradientUnits="userSpaceOnUse" x1="0" y1="0" x2="{W}" y2="{H}">'
                 f'<stop offset="0%" stop-color="{BG}"/><stop offset="100%" stop-color="{BG2}"/></linearGradient>')
        for sem, col in EDGE.items():
            o.append(f'<marker id="arr_{sem}" viewBox="0 0 10 10" refX="8.5" refY="5" markerWidth="7" markerHeight="7" orient="auto"><path d="M0,0 L10,5 L0,10 z" fill="{col}"/></marker>')
        o.append("</defs>")
        o.append(f'<rect width="{W}" height="{H}" fill="url(#bg-grad)"/>')
        o.append(f'<text x="28" y="40" font-family="{MONO}" font-size="16" font-weight="700" fill="{TEXT}">{esc(self.title)}</text>')
        o.append(f'<text x="28" y="58" font-family="{MONO}" font-size="11" fill="{SUBT}">{esc(self.subtitle)}</text>')
        for e in self.edges:
            if len(e["pts"]) < 2: continue
            col = EDGE[e["sem"]]
            sw = 2.6 if e["thick"] else 1.6
            d = e.get("dash")
            if d is None:
                d = "6 4" if e["sem"] in ("write", "dim") else None
            pts = " ".join(f"{round(x,1)},{round(y,1)}" for x, y in e["pts"])
            extra = f' stroke-dasharray="{d}"' if d else ""
            mk = f' marker-end="url(#arr_{e["sem"]})"' if e.get("arrow", True) else ""
            o.append(f'<polyline points="{pts}" fill="none" stroke="{col}" stroke-width="{sw}"{extra}{mk} stroke-linejoin="round"/>')
        for n in self.nodes:
            x, y, w, h = n.x, n.y, n.w, n.h
            tone, fill, dash = n.tone, blend(n.tone), (' stroke-dasharray="5 4"' if n.dash else "")
            o.append("<g>")
            if n.shape in ("rect", "pill"):
                rx = h / 2 if n.shape == "pill" else 10
                o.append(f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="{rx}" fill="{fill}" stroke="{tone}" stroke-width="1.5"{dash}/>')
            elif n.shape in ("hex", "diamond"):
                c = min(w * 0.16, 26) if n.shape == "hex" else min(w * 0.30, 54)
                p = [(x + c, y), (x + w - c, y), (x + w, y + h / 2), (x + w - c, y + h), (x + c, y + h), (x, y + h / 2)]
                o.append('<polygon points="%s" fill="%s" stroke="%s" stroke-width="1.5"%s/>' % (
                    " ".join(f"{a},{b}" for a, b in p), fill, tone, dash))
            elif n.shape == "file":
                f = 14
                o.append(f'<path d="M {x} {y} h {w-f} l {f} {f} v {h-f} h -{w} z" fill="{fill}" stroke="{tone}" stroke-width="1.5"{dash}/>')
                o.append(f'<path d="M {x+w-f} {y} v {f} h {f}" fill="none" stroke="{tone}" stroke-width="1.2" opacity="0.8"/>')
            nt = sum(1 for _, k in n.lines if k == "t")
            ns = sum(1 for _, k in n.lines if k == "s")
            ty = y + (h - (14 * nt + 13 * ns)) / 2
            for text, kind in n.lines:
                if kind == "t":
                    o.append(f'<text x="{n.cx()}" y="{ty+11}" text-anchor="middle" font-family="{MONO}" font-size="13" font-weight="600" fill="{TEXT}">{esc(text)}</text>')
                    ty += 14
                else:
                    o.append(f'<text x="{n.cx()}" y="{ty+10}" text-anchor="middle" font-family="{MONO}" font-size="11" fill="{SUBT}">{esc(text)}</text>')
                    ty += 13
            o.append("</g>")
        for (tx, ty, ts, tsize, tcol, tanchor, tweight) in self.texts:
            o.append(f'<text x="{tx}" y="{ty}" text-anchor="{tanchor}" font-family="{MONO}" font-size="{tsize}" font-weight="{tweight}" fill="{tcol}">{esc(ts)}</text>')
        ly, lx = H - 24, 28
        for sem, label in self.legend:
            col = EDGE[sem]
            d = ' stroke-dasharray="6 4"' if sem in ("write", "dim") else ""
            o.append(f'<line x1="{lx}" y1="{ly-4}" x2="{lx+26}" y2="{ly-4}" stroke="{col}" stroke-width="1.6"{d} marker-end="url(#arr_{sem})"/>')
            o.append(f'<text x="{lx+34}" y="{ly}" font-family="{MONO}" font-size="11" fill="{SUBT}">{esc(label)}</text>')
            lx += 34 + 6.8 * len(label) + 26
        ny = H - 24
        for note in reversed(self.notes):
            o.append(f'<text x="{W-28}" y="{ny}" text-anchor="end" font-family="{MONO}" font-size="11" fill="{SUBT}">{esc(note)}</text>')
            ny -= 15
        o.append("</svg>")
        path = os.path.join(outdir, fname)
        with open(path, "w") as f:
            f.write("\n".join(o))
        print(f"wrote {path}  ({W}x{H})")

outdir = sys.argv[1] if len(sys.argv) > 1 else "docs/diagrams"
os.makedirs(outdir, exist_ok=True)

# ═══════════════════════════════════════════════════════════════════
# D1/D2 — standalone full exact lock
# la 60-230 y200-245 | ta 300-490 y200-272 | ag 300-490 y300-372 (cy336)
# i2v 540-730 y100-162 | av 60-250 y430-475 | lk 820-1033 y100-162 | right 1080-1252
# ═══════════════════════════════════════════════════════════════════
def exact_lock(fname, t1, subtitle):
    d = Diagram(1280, 680, "MiniMax H3 Exact Audio Lock — standalone wiring", subtitle)
    d.node("la",  60, 200, 170, T("Load Audio") + S("approved AUDIO"), "amber", "file")
    d.node("li",  60, 520, 170, T("Load Image") + S("first / last frame" if t1 == "Image" else "reference image"), "amber", "file")
    d.node("ta",  300, 200, 190, T("MiniMax H3", "Timed Audio") + S("start_frame · gain_db", "label"), "green")
    d.node("ag",  300, 300, 190, T("Add Guide for", "MiniMax H3") + S("positive · latent", "audio_vae · frame_idx"), "cyan", "hex")
    d.node("i2v", 540, 100, 190, T("MiniMax H3 " + t1, "to Video") + S("LATENT + positive"), "cyan", "hex", min_h=62)
    d.node("bg",  560, 300, 140, T("Basic Guider"), "slate", min_h=45)
    d.node("av",  60, 430, 190, T("H3 VAE") + S("video → I2V/decode") + S("audio → lock/guide"), "purple", min_h=45)
    d.node("lk",  820, 100, 213, T("MiniMax H3 Exact", "Audio Lock") + S("mix · encode · freeze audio"), "green", min_h=62)
    d.node("sm", 1080, 100, 172, T("H3 sampler") + S("SamplerCustomAdvanced"), "cyan", "hex", min_h=45)
    d.node("dc", 1080, 250, 172, T("VAE Decode") + S("video"), "cyan")
    d.node("cv", 1080, 430, 172, T("Create Video") + S("images + exact audio"), "slate")
    d.node("sv", 1080, 560, 172, T("Save Video"), "slate", "file", min_h=45)

    d.pts([(230,222.5),(265,222.5),(265,236),(300,236)], "data")                      # la -> ta AUDIO
    d.pts([(145,245),(145,290),(390,290),(390,300)], "data")                          # la -> ag same AUDIO
    d.pts([(230,542.5),(500,542.5),(500,84),(585,84),(585,100)], "data")              # li -> i2v image input
    d.pts([(365,272),(365,300)], "control", thick=True)                              # ta -> ag start_frame
    d.pts([(490,256),(515,256),(515,390),(740,390),(740,111),(820,111)], "data", thick=True)  # ta -> lk timed_audio
    d.pts([(700,162),(700,420),(805,420),(805,131),(820,131)], "data")                 # i2v -> lk LATENT
    d.pts([(585,162),(585,272),(410,272),(410,300)], "guide")                        # i2v -> ag positive
    d.pts([(670,162),(670,285),(455,285),(455,300)], "data")                          # i2v -> ag LATENT
    d.pts([(250,452.5),(270,452.5),(270,336),(300,336)], "data")                     # av -> ag audio_vae
    d.pts([(250,460.5),(250,520),(815,520),(815,151),(820,151)], "data")             # av -> lk audio_vae
    d.pts([(490,336),(525,336),(525,322.5),(560,322.5)], "guide")                    # ag -> bg conditioning
    d.pts([(700,322.5),(755,322.5),(755,84),(1110,84),(1110,100)], "guide")          # bg -> sm guider
    d.pts([(1033,131),(1056,131),(1056,122.5),(1080,122.5)], "data", thick=True)     # lk -> sm locked_av_latent
    d.pts([(926.5,162),(926.5,452.5),(1080,452.5)], "data", thick=True)             # lk -> cv exact_audio
    d.pts([(1166,145),(1166,250)], "data")                                          # sm -> dc latent
    d.pts([(1166,295),(1166,430)], "data")                                          # dc -> cv images
    d.pts([(1166,475),(1166,560)], "write")                                         # cv -> sv final video
    d.legend = [("data","audio / latent"), ("control","frame / INT"), ("guide","conditioning"), ("write","file save")]
    d.notes = ["thick = critical path · legacy single-AUDIO input available for v1 workflows",
               "model stack omitted: UNET/CLIP/noise/scheduler · Add Guide.positive → Basic Guider → sampler.guider"]
    d.render(fname)

exact_lock("exact-lock-basic.svg", "Image",
           "LoadAudio → Timed Audio → Exact Audio Lock → sampler, with same-frame native Add Guide (workflows standalone/01–03, 10)")
exact_lock("exact-lock-ref2v.svg", "Reference",
           "Ref2VA path — native H3 reference conditioning (workflow standalone/04_ref2v_exact_lock_load_audio)")

# ═══════════════════════════════════════════════════════════════════
# D3 — Add Guide same-frame wiring
# la 40-210 y200-245 | ta 280-470 y200-272 | ag 280-470 y420-492 (cy456)
# i2v 560-750 y120-182 | av 40-230 y420-465 | bg 560-700 y420-465
# sm 840-1020 y420-465 | lk 840-1040 y200-262
# ═══════════════════════════════════════════════════════════════════
def add_guide():
    d = Diagram(1240, 600, "Add Guide for MiniMax H3 — one frame source of truth",
                "Timed Audio start_frame drives Add Guide.frame_idx; the same AUDIO feeds both (workflows standalone/01, 10, 06)")
    d.node("la",  40, 200, 170, T("Approved AUDIO") + S("one source of truth"), "amber", "file")
    d.node("ta",  280, 200, 190, T("MiniMax H3", "Timed Audio") + S("start_frame · gain_db", "label"), "green")
    d.node("ag",  280, 420, 190, T("Add Guide for", "MiniMax H3") + S("positive · latent", "audio_vae · frame_idx"), "cyan", "hex")
    d.node("i2v", 560, 120, 190, T("MiniMax H3 Image", "to Video") + S("positive + LATENT"), "cyan", "hex", min_h=62)
    d.node("av",  40, 420, 190, T("MiniMax H3 audio VAE"), "purple", min_h=45)
    d.node("bg",  560, 420, 140, T("Basic Guider"), "slate", min_h=45)
    d.node("sm",  840, 420, 180, T("H3 sampler"), "cyan", "hex", min_h=45)
    d.node("lk",  840, 200, 200, T("Exact / Dialogue", "Audio Lock") + S("timed_audio input"), "green", min_h=62)

    d.pts([(210,222.5),(245,222.5),(245,236),(280,236)], "data")      # la -> ta AUDIO
    d.pts([(125,245),(125,400),(260,400),(260,456),(280,456)], "data")  # la -> ag same AUDIO
    d.pts([(230,442.5),(250,442.5),(250,410),(316,410),(316,420)], "data")  # av -> ag audio_vae
    d.pts([(375,272),(375,420)], "control", thick=True)            # ta -> ag start_frame
    d.pts([(470,236),(740,236),(740,205),(840,205)], "data", thick=True)  # ta -> lk timed_audio
    d.pts([(750,151),(900,151),(900,200)], "data")                 # i2v -> lk LATENT
    d.pts([(655,182),(655,350),(390,350),(390,420)], "guide")       # i2v -> ag positive
    d.pts([(600,182),(600,362),(340,362),(340,420)], "data")        # i2v -> ag LATENT
    d.pts([(230,450.5),(230,520),(825,520),(825,231),(840,231)], "data")   # av -> lk audio_vae
    d.pts([(470,456),(515,456),(515,442.5),(560,442.5)], "guide")   # ag -> bg conditioning
    d.pts([(630,465),(630,500),(810,500),(810,442.5),(840,442.5)], "guide")  # bg -> sm guider
    d.legend = [("data","audio / latent"), ("control","frame / INT"), ("guide","conditioning")]
    d.notes = ["do not type one frame into Timed Audio and a different frame into Add Guide"]
    d.render("add-guide.svg")
add_guide()

# ═══════════════════════════════════════════════════════════════════
# D4 — multi-track (WF 06)
# la 40-190 | ta 250-422 | ag 520-690 | i2v 520-710 y110-172 | lk 760-960 y260-322
# bg 760-910 y560-605 | right column 1010-1182
# corridors (verticals): purple 716/726/734/742 · orange 750/758
# ═══════════════════════════════════════════════════════════════════
def multi_track():
    d = Diagram(1240, 720, "Large timed audio input sets — multi-track",
                "Up to 100 Timed Audio events via Autogrow; each track keeps its own Add Guide (workflow standalone/06_t2v_exact_lock_multi_track)")
    rows = [(120, 110, 200), (300, 290, 380), (480, 470, 560)]
    for i, (yla, yta, yag) in enumerate(rows):
        d.node(f"la{i+1}", 40, yla, 150, T(f"Load Audio {i+1}") + S(f"speaker {i+1} AUDIO"), "amber", "file", min_h=45)
        d.node(f"ta{i+1}", 250, yta, 172, T("MiniMax H3", "Timed Audio") + S(f"event {i+1} · start_frame"), "green", min_h=72)
        d.node(f"ag{i+1}", 520, yag, 170, T(f"Add Guide {i+1}") + S("audio · frame_idx"), "cyan", "hex")
    d.node("i2v", 520, 110, 190, T("MiniMax H3 Image", "to Video") + S("positive + LATENT"), "cyan", "hex", min_h=62)
    d.node("lk",  760, 260, 200, T("MiniMax H3 Exact", "Audio Lock") + S("up to 100 timed inputs"), "green", min_h=62)
    d.node("bg",  760, 560, 150, T("Basic Guider"), "slate", min_h=45)
    d.node("av",  40, 610, 170, T("H3 audio VAE"), "purple", min_h=45)
    d.node("sm",  1010, 90, 172, T("H3 sampler"), "cyan", "hex", min_h=45)
    d.node("dc",  1010, 270, 172, T("VAE Decode") + S("video"), "cyan")
    d.node("cv",  1010, 450, 172, T("Create Video") + S("images + exact audio"), "slate")
    d.node("sv",  1010, 600, 172, T("Save Video"), "slate", "file", min_h=45)

    for i in range(3):
        yla, yta, yag = rows[i]
        cl, ct, cg = yla + 22.5, yta + 36, yag + 22.5
        d.pts([(190,cl),(220,cl),(220,ct),(250,ct)], "data")                        # la -> ta
        d.pts([(115,yla+45),(115,cg),(520,cg)], "data")                             # la -> ag same AUDIO
        d.pts([(321,yta+72),(321,yta+81),(565,yta+81),(565,yag)], "control")        # ta -> ag start_frame
    # timed audio -> lock (corridors x=726 / 734 / 742)
    d.pts([(422,146),(440,146),(440,90),(726,90),(726,281),(760,281)], "data", thick=True)
    d.pts([(422,326),(734,326),(734,291),(760,291)], "data")
    d.pts([(422,506),(742,506),(742,311),(760,311)], "data")
    # i2v -> AG1 (LATENT + positive), i2v -> AG2/AG3 LATENT, i2v -> lk LATENT
    d.pts([(595,172),(595,186),(585,186),(585,200)], "guide")
    d.pts([(635,172),(635,186),(625,186),(625,200)], "data")
    d.pts([(655,172),(655,186),(704,186),(704,360),(660,360),(660,380)], "data")
    d.pts([(672,172),(672,192),(710,192),(710,540),(660,540),(660,560)], "data")
    d.pts([(710,141),(716,141),(716,261),(760,261)], "data", thick=True)
    # audio VAE -> every Add Guide + lock
    d.pts([(210,632.5),(230,632.5),(230,186),(560,186),(560,200)], "data")  # av -> ag1 audio_vae
    d.pts([(210,618),(504,618),(504,368),(600,368),(600,380)], "data")       # av -> ag2 audio_vae
    d.pts([(150,655),(150,660),(494,660),(494,548),(600,548),(600,560)], "data")  # av -> ag3 audio_vae
    d.pts([(210,640),(746,640),(746,300),(760,300)], "data")                # av -> lk audio_vae
    # positive chain AG1 -> AG2 -> AG3 -> Basic Guider (enter TOP, avoiding la->ag entries)
    d.pts([(690,222.5),(750,222.5),(750,446),(488,446),(488,364),(556,364),(556,380)], "guide")
    d.pts([(690,402.5),(758,402.5),(758,626),(488,626),(488,544),(556,544),(556,560)], "guide")
    d.pts([(690,582.5),(760,582.5)], "guide")
    # right column
    d.pts([(910,582.5),(970,582.5),(970,112.5),(1010,112.5)], "guide")              # bg -> sm
    d.pts([(960,291),(985,291),(985,292.5),(1010,292.5)], "data")                   # lk -> dc
    d.pts([(960,303),(978,303),(978,472.5),(1010,472.5)], "data", thick=True)       # lk -> cv exact audio
    d.pts([(1096,135),(1096,270)], "data")                                         # sm -> dc
    d.pts([(1096,315),(1096,450)], "data")                                         # dc -> cv
    d.pts([(1096,495),(1096,600)], "write")                                        # cv -> sv
    d.legend = [("data","audio / latent"), ("control","frame / INT"), ("guide","conditioning"), ("write","file save")]
    d.notes = ["shared inputs: i2v LATENT + audio VAE feed every Add Guide and the lock",
               "positive chain: i2v → AG1 → AG2 → AG3 → Basic Guider"]
    d.render("multi-track.svg")
multi_track()

# ═══════════════════════════════════════════════════════════════════
# D5 — timeline overlap (24 fps)
# ═══════════════════════════════════════════════════════════════════
def timeline():
    d = Diagram(1100, 540, "Layering and overlapping audio — 24 fps target timeline",
                "start_frame is exact on the 24 fps video timeline · overlaps allowed under mix_policy=sum")
    x0, px = 190, 14
    d.pts([(x0,110),(862,110)], "dim", anno=True)
    for k in range(7):
        fx = x0 + 8*k*px
        d.pts([(fx,104),(fx,116)], "dim", arrow=False, anno=True)
        d.text(fx, 132, f"f{8*k}", 11, SUBT, "middle")
    d.text(872, 114, "frames @ 24 fps", 11, SUBT)
    bars = [
        ("Pippa",  "cyan",  150, 2, 16,  "dialogue A"),
        ("Magnus", "green", 220, 10, 26, "dialogue B"),
        ("Cricket","orange",290, 22, 34, "dialogue C"),
    ]
    for name, tone, y, f1, f2, lbl in bars:
        d.node(name.lower(), x0 + f1*px, y, (f2-f1)*px, T(lbl), tone)
        d.text(28, y + 20, name, 11, TEXT)
    d.pts([(372,182),(372,220)], "loop")
    d.pts([(526,252),(526,290)], "loop")
    d.text(382, 208, "overlap A+B", 11, EDGE["loop"])
    d.text(536, 278, "overlap B+C", 11, EDGE["loop"])
    d.node("bus", x0+2*px, 400, 32*px, T("mixed bus: A + overlap(A,B) + overlap(B,C) + C"), "purple")
    d.text(28, 420, "Mixed bus", 11, TEXT)
    d.notes = ["one exact waveform — silence is real waveform-domain zeros, not zero-valued latents"]
    d.legend = [("data","audio"), ("loop","overlap region")]
    d.render("timeline-overlap.svg")
timeline()

# ═══════════════════════════════════════════════════════════════════
# D6 — waveform pipeline (internal lock mechanics) — rounded box nodes only
# ta 84-129 | mx 200-245 | wv 320-365 | va 430-475 | lt 530-592 | sm 660-722
# ═══════════════════════════════════════════════════════════════════
def waveform():
    d = Diagram(900, 780, "How timing works — deterministic waveform pipeline",
                "Every start_frame converts to an exact waveform sample via integer arithmetic; sources are resampled to the H3 audio VAE rate")
    d.node("ta", 320, 84, 260, T("MiniMax H3 Timed Audio × N") + S("N events · up to 100"), "green")
    d.node("mx", 320, 200, 260, T("Deterministic waveform mixer") + S("mix_policy · overflow_policy"), "green", "hex")
    d.node("wv", 313, 320, 274, T("one exact waveform") + S("full target length · zeros = silence"), "purple", "pill")
    d.node("va", 320, 430, 260, T("H3 Audio VAE"), "purple", min_h=45)
    d.node("lt", 320, 530, 260, T("target audio latent") + S("audio protected (mask = 0)"), "purple", min_h=62)
    d.node("sm", 320, 660, 260, T("MiniMax H3 sampling") + S("video mask = 1"), "cyan", "hex", min_h=62)
    d.pts([(450,129),(450,200)], "data")   # N timed events
    d.pts([(450,245),(450,320)], "data")   # mixed samples
    d.pts([(450,365),(450,430)], "data")   # encoded
    d.pts([(450,475),(450,530)], "data")   # audio latent
    d.pts([(450,592),(450,660)], "data", thick=True)  # audio mask 0 · video mask 1
    d.text(462, 168, "N timed events", 11, SUBT)
    d.text(462, 284, "mixed samples", 11, SUBT)
    d.text(462, 398, "encoded", 11, SUBT)
    d.text(462, 508, "audio latent", 11, SUBT)
    d.text(462, 636, "audio mask 0 · video mask = 1", 11, SUBT)
    d.legend = [("data","payload")]
    d.notes = ["Timed Audio does not prepend silence — the lock builds the full target-length waveform"]
    d.render("waveform-pipeline.svg")
waveform()

# ═══════════════════════════════════════════════════════════════════
# D7 — audio review gate, connected / managed-file candidates
# takes x40-230 y84..399 | gt 300-563 y150-260 | al 300-530 y440-502
# ta 620-826 y110-182 | ag 620-810 y230-302 (cy266) | lk 620-810 y360-422
# bg 460-600 y360-405 | av 880-1070 y305-350 | right 880-1072
# i2v 40-225 y560-622
# ═══════════════════════════════════════════════════════════════════
def review_gate(fname, subtitle, takes, gate_sub):
    d = Diagram(1240, 800, "Audio Review / Accept Gate — before Timed Audio / ExactAudioLock", subtitle)
    take_y = [84, 174, 264, 354]
    for (tid, ttl, sub, shape), y in zip(takes, take_y):
        d.node(tid, 40, y, 190, T(ttl) + (S(sub) if sub else []), "amber", shape, min_h=45)
    d.node("gt", 300, 150, 263, T("Audio Review /", "Accept Gate") + S(gate_sub), "rose", "diamond", min_h=110)
    d.node("al", 300, 440, 230, T("Saved alternates") + S("output/.../alternates/", "<review-token>/"), "green", "file", min_h=62)
    d.node("ta", 620, 110, 206, T("MiniMax H3", "Timed Audio") + S("start_frame · gain · label"), "green", "hex", min_h=72)
    d.node("ag", 620, 230, 190, T("Add Guide for", "MiniMax H3") + S("audio · frame_idx", "positive · latent"), "cyan", "hex")
    d.node("lk", 620, 360, 190, T("MiniMax H3 Exact", "Audio Lock"), "green", min_h=62)
    d.node("bg", 460, 360, 140, T("Basic Guider"), "slate", min_h=45)
    d.node("av",  880, 305, 190, T("MiniMax H3 audio VAE"), "purple", min_h=45)
    d.node("sm", 880, 84, 172, T("H3 sampler"), "cyan", "hex", min_h=45)
    d.node("dc", 880, 240, 172, T("VAE Decode") + S("video"), "cyan")
    d.node("i2v", 40, 560, 185, T("MiniMax H3 Image", "to Video") + S("native H3 target latent"), "cyan", "hex", min_h=62)
    d.node("cv", 880, 620, 172, T("Create Video") + S("images + exact audio"), "slate")
    d.node("sv", 880, 700, 172, T("Save Video"), "slate", "file", min_h=45)

    # takes fan into the gate diamond's faces / left vertex
    d.pts([(230,106.5),(252,106.5),(252,189.94),(336,189.94)], "data")     # tA -> gate left face
    d.pts([(230,196.5),(265,196.5),(265,205),(300,205)], "data")       # tB -> gate left vertex
    d.pts([(230,286.5),(258,286.5),(258,220.06),(336,220.06)], "data")     # tC -> gate left face
    d.pts([(230,376.5),(272,376.5),(272,236.79),(376,236.79)], "data") # tD -> gate left face
    # gate -> timed audio / add guide / saved alternates
    d.pts([(536.7,194),(596,194),(596,146),(620,146)], "data", thick=True)   # gate -> ta accepted_audio
    d.pts([(563,205),(592,205),(592,266),(620,266)], "data")           # gate -> ag accepted_audio
    d.pts([(431.5,260),(431.5,340),(415,340),(415,440)], "write")     # gate -> alternates
    # timed audio -> add guide (frame idx) + lock (timed audio)
    d.pts([(826,146),(856,146),(856,200),(700,200),(700,230)], "control", thick=True)  # ta -> ag start_frame
    d.pts([(784,182),(784,186),(840,186),(840,345),(700,345),(700,360)], "data", thick=True)  # ta -> lk timed_audio
    # i2v -> add guide (positive / LATENT) + lock (LATENT)
    d.pts([(195,560),(260,560),(260,140),(600,140),(600,208),(646,208),(646,230)], "guide")   # i2v -> ag positive
    d.pts([(175,622),(175,648),(240,648),(240,132),(580,132),(580,215),(670,215),(670,230)], "data")  # i2v -> ag LATENT
    d.pts([(225,591),(608,591),(608,399),(620,399)], "data", thick=True)        # i2v -> lk LATENT
    # audio VAE -> add guide / lock / decode
    d.pts([(1070,327.5),(1106,327.5),(1106,222),(660,222),(660,230)], "data")   # av -> ag audio_vae
    d.pts([(940,350),(940,354),(608,354),(608,381),(620,381)], "data")          # av -> lk audio_vae
    d.pts([(1070,335),(1118,335),(1118,215),(1030,215),(1030,240)], "data")     # av -> dc vae
    # add guide -> basic guider -> sampler
    d.pts([(710,302),(710,330),(530,330),(530,360)], "guide")         # ag -> bg conditioning
    d.pts([(600,382.5),(612,382.5),(612,70),(940,70),(940,84)], "guide")       # bg -> sm guider
    # lock -> sampler / decode / video column
    d.pts([(810,381),(838,381),(838,106.5),(880,106.5)], "data", thick=True)  # lk -> sm locked_av_latent
    d.pts([(810,399),(830,399),(830,262.5),(880,262.5)], "data")       # lk -> dc (audio-side vae not wired)
    d.pts([(690,422),(690,642.5),(880,642.5)], "data", thick=True)    # lk -> cv exact_audio
    d.pts([(966,129),(966,240)], "data")                              # sm -> dc latent
    d.pts([(1052,270),(1084,270),(1084,600),(1000,600),(1000,620)], "data")    # dc -> cv images
    d.pts([(966,665),(966,700)], "write")                             # cv -> sv final video
    d.legend = [("data","audio / latent"), ("control","frame / INT"), ("guide","conditioning"), ("write","durable alternate")]
    d.notes = ["gate is an execution barrier — only accepted_audio continues downstream · Add Guide.positive → Basic Guider → sampler.guider",
               "i2v LATENT + audio VAE feed Add Guide and the lock"]
    d.render(fname)

review_gate(
    "audio-review-gate.svg",
    "Connected candidates: TTS/music takes or batched AUDIO via audio + audio_candidates (workflow standalone/07)",
    [("tA", "Qwen3-TTS take A", "", "hex"), ("tB", "Qwen3-TTS take B", "", "hex"),
     ("tC", "YuE2 / ACE-Step", "take C", "hex"), ("tD", "Loaded audio", "take D", "file")],
    "one accepted take · barrier",
)
review_gate(
    "audio-review-gate-files.svg",
    "Managed-file candidates: source_mode = audio_file (workflow standalone/08_t2v_audio_review_gate_managed_files)",
    [("tA", "take_01.mp3", "", "file"), ("tB", "take_02.flac", "", "file"),
     ("tC", "take_03.ogg", "", "file"), ("tD", "take_04.wav", "optional", "file")],
    "source_mode = audio_file",
)

# ═══════════════════════════════════════════════════════════════════
# D9 — Context Loop dialogue chain
# pl 40-259 y80-125 | la 40-240 y200/280/360 | tl 320-520 y200-272
# bd 320-603 y520-620 | cx 620-820 y84-146 | cc 620-820 y200-262
# cd 620-820 y380-442 | av 900-1090 y280-325 | ld 900-1130 y380-442
# le 900-1130 y560-622 | sm 1180-1352 y380-425
# ═══════════════════════════════════════════════════════════════════
def context_chain():
    d = Diagram(1390, 760, "Context Loop — production-wide dialogue review and scene routing",
                "Dialogue Timeline → Approval Board → Current Scene Dialogue → scene lock (workflows context_loop/04, 05)")
    d.node("pl",  40, 80, 219, T("Context Loop Plan") + S("prompt_prefix + shot prompts"), "orange")
    for i, y in enumerate([200, 280, 360]):
        d.node(f"la{i+1}", 40, y, 200, T(f"dialogue AUDIO {i+1}"), "amber", "file", min_h=45)
    d.node("tl",  320, 200, 200, T("MiniMax H3", "Dialogue Timeline") + S("scene-local start frames"), "green", min_h=62)
    d.node("bd",  320, 520, 283, T("Dialogue Review /", "Approval Board") + S("every event approved · barrier"), "rose", "diamond", min_h=100)
    d.node("cx",  620, 84, 200, T("MiniMax H3 Chain", "Context") + S("continuation latent"), "orange", min_h=62)
    d.node("cc",  620, 200, 200, T("MiniMax H3 Chain", "Current") + S("clip_index (one-based)"), "orange", min_h=62)
    d.node("cd",  620, 380, 200, T("MiniMax H3 Current", "Scene Dialogue") + S("filters to current scene"), "green", min_h=62)
    d.node("av",  900, 280, 190, T("MiniMax H3 audio VAE"), "purple", min_h=45)
    d.node("ld",  900, 380, 230, T("MiniMax H3 Scene Dialogue", "Audio Lock") + S("dialogue_event_set input"), "green", min_h=62)
    d.node("le",  900, 560, 230, T("MiniMax H3 Scene Exact", "Audio Lock") + S("alternate: full scene lock"), "green", "rect", True, 62)
    d.node("sm",  1180, 380, 172, T("H3 sampler"), "cyan", "hex", min_h=45)

    d.pts([(259,107.5),(289,107.5),(289,221),(320,221)], "data")        # plan -> timeline
    d.pts([(240,222.5),(264,222.5),(264,211),(320,211)], "data")       # audio_0
    d.pts([(240,302.5),(280,302.5),(280,231),(320,231)], "data")       # audio_1
    d.pts([(240,382.5),(296,382.5),(296,242.5),(320,242.5)], "data")   # audio_2
    d.pts([(420,262),(420,500),(461.5,500),(461.5,520)], "data")       # timeline -> board
    d.pts([(461.5,620),(461.5,648),(612,648),(612,411),(620,411)], "data")  # board -> scene dialogue
    d.pts([(700,262),(700,282),(592,282),(592,401),(620,401)], "control")  # clip_index
    d.pts([(820,215),(880,215),(880,70),(700,70),(700,84)], "data")    # Chain Current.state -> Chain Context
    d.pts([(820,115),(860,115),(860,391),(900,391)], "data")          # context -> lock
    d.pts([(820,231),(872,231),(872,411),(900,411)], "control")       # clip_index -> lock
    d.pts([(820,411),(846,411),(846,431),(900,431)], "data")          # event set
    d.pts([(995,325),(995,380)], "data")                             # audio_vae
    d.pts([(1015,442),(1015,560)], "dim")                            # or
    d.pts([(1130,411),(1156,411),(1156,402.5),(1180,402.5)], "data")  # locked latent
    d.pts([(1130,591),(1162,591),(1162,362),(1230,362),(1230,380)], "data")  # locked_av_latent
    d.legend = [("data","audio / latent / events"), ("control","scene index"), ("dim","optional path")]
    d.notes = ["Chain Current.state feeds Chain Context · scene lock sits between Chain Context.latent and the sampler",
               "only current_scene events are consumed"]
    d.render("context-loop-chain.svg")
context_chain()

# ═══════════════════════════════════════════════════════════════════
# D10 — standalone dialogue partial lock
# la 40-190 y200-245 | ta 250-440 y200-272 | ag 250-440 y420-492 (cy456)
# i2v 500-685 y80-142 | av 40-230 y420-465 | lk 760-960 y200-262
# sm 1020-1192 y84-129 | da 1020-1192 y220-265 | fn 1020-1192 y380-442
# vd 760-930 y560-605 | cv 1020-1192 y540-585 | sv 1020-1192 y660-705
# ═══════════════════════════════════════════════════════════════════
def dialogue_partial():
    d = Diagram(1240, 780, "Dialogue-partial lock — standalone wiring",
                "Dialogue cores protected at exact frames; H3 generates the rest; Finalize restores cores after sampling (workflow standalone/05)")
    d.node("la",  40, 200, 150, T("Load Audio") + S("approved dialogue"), "amber", "file")
    d.node("ta",  250, 200, 190, T("MiniMax H3", "Timed Audio") + S("start_frame · gain_db", "label"), "green")
    d.node("ag",  250, 420, 190, T("Add Guide for", "MiniMax H3") + S("audio · frame_idx", "positive · latent"), "cyan", "hex")
    d.node("bg",  480, 420, 140, T("Basic Guider"), "slate", min_h=45)
    d.node("i2v", 500, 80, 185, T("MiniMax H3 Image", "to Video") + S("native H3 target latent"), "cyan", "hex", min_h=62)
    d.node("av",  40, 420, 190, T("MiniMax H3 audio VAE"), "purple", min_h=45)
    d.node("lk",  760, 200, 200, T("MiniMax H3 Dialogue", "Audio Lock") + S("protect dialogue cores"), "green", min_h=62)
    d.node("sm",  1020, 84, 172, T("H3 sampler"), "cyan", "hex", min_h=45)
    d.node("da",  1020, 220, 172, T("VAE Decode Audio") + S("generated_audio"), "cyan")
    d.node("fn",  1020, 380, 172, T("Dialogue Audio", "Finalize"), "green", min_h=62)
    d.node("vd",  760, 560, 170, T("VAE Decode") + S("video + H3 audio"), "cyan")
    d.node("cv",  1020, 540, 172, T("Create Video") + S("images + final audio"), "slate")
    d.node("sv",  1020, 660, 172, T("Save Video"), "slate", "file", min_h=45)

    d.pts([(190,222.5),(220,222.5),(220,236),(250,236)], "data")      # la -> ta
    d.pts([(115,245),(115,400),(240,400),(240,456),(250,456)], "data")  # la -> ag (left vertex)
    d.pts([(230,442.5),(246,442.5),(246,408),(292,408),(292,420)], "data")  # av -> ag audio_vae
    d.pts([(345,272),(345,420)], "control", thick=True)            # ta -> ag
    d.pts([(685,111),(730,111),(730,211),(760,211)], "data")        # i2v -> lk LATENT
    d.pts([(440,236),(742,236),(742,231),(760,231)], "data", thick=True)  # ta -> lk
    d.pts([(230,442.5),(230,520),(720,520),(720,251),(760,251)], "data")   # av -> lk
    d.pts([(170,465),(170,570),(760,570)], "data")                 # av -> vd audio VAE
    d.pts([(620,142),(620,362),(390,362),(390,420)], "guide")       # i2v -> ag positive
    d.pts([(560,142),(560,350),(320,350),(320,420)], "data")        # i2v -> ag LATENT
    d.pts([(440,456),(460,456),(460,442.5),(480,442.5)], "guide")   # ag -> bg conditioning
    d.pts([(620,442.5),(710,442.5),(710,70),(1060,70),(1060,84)], "guide")  # bg -> sm guider
    d.pts([(960,231),(990,231),(990,106.5),(1020,106.5)], "data", thick=True)  # lk -> sm
    d.pts([(1106,129),(1106,220)], "data")                         # sm -> da
    d.pts([(1126,129),(1210,129),(1210,620),(728,620),(728,582.5),(760,582.5)], "data")  # sm -> vd video latent
    d.pts([(1106,265),(1106,380)], "data")                         # da -> fn generated_audio
    d.pts([(840,262),(840,340),(1000,340),(1000,401),(1020,401)], "data")   # lk -> fn reference
    d.pts([(880,262),(880,352),(1008,352),(1008,421),(1020,421)], "data")   # lk -> fn manifest
    d.pts([(1106,442),(1106,540)], "data", thick=True)             # fn -> cv final_audio
    d.pts([(930,582.5),(975,582.5),(975,562.5),(1020,562.5)], "data")  # vd -> cv images
    d.pts([(1106,585),(1106,660)], "write")                        # cv -> sv
    d.legend = [("data","audio / latent"), ("control","frame / INT"), ("guide","conditioning"), ("write","file save")]
    d.notes = ["do not mux dialogue_reference_audio as the complete soundtrack — use Finalize output",
               "Add Guide.positive → Basic Guider → sampler.guider"]
    d.render("dialogue-partial-lock.svg")
dialogue_partial()

# ═══════════════════════════════════════════════════════════════════
# D11 — scene dialogue lock, Context Loop
# left stack x40-266 | lk 360-593 y180-242 | fn 360-593 y340-402
# sm 720-892 y84-129 | da 720-892 y200-245 | vd 720-892 y340-385
# lt 720-910 y480-542 | nx 1020-1220 y480-525
# ═══════════════════════════════════════════════════════════════════
def scene_dialogue():
    d = Diagram(1240, 700, "Scene Dialogue Audio Lock — Context Loop wiring",
                "Recursive/Context Loop partial lock with post-sampling Finalize into Loop Trim (workflows context_loop/02, 04)")
    d.node("st",  40, 100, 210, T("Scene Timed Audio", "events") + S("scene_index + local", "start_frame"), "green")
    d.node("cs",  40, 220, 226, T("Current Scene Dialogue") + S("approved event set (optional)"), "green", "rect", True, 45)
    d.node("cc",  40, 340, 210, T("MiniMax H3 Chain", "Current") + S("clip_index (one-based)"), "orange", min_h=62)
    d.node("cx",  40, 460, 210, T("MiniMax H3 Chain", "Context") + S("continuation latent"), "orange", min_h=62)
    d.node("av",  40, 580, 210, T("MiniMax H3 audio VAE"), "purple", min_h=45)
    d.node("lk",  360, 180, 233, T("MiniMax H3 Scene Dialogue", "Audio Lock") + S("scene_timed_audios · event set"), "green", min_h=62)
    d.node("sm",  720, 84, 172, T("H3 sampler"), "cyan", "hex", min_h=45)
    d.node("da",  720, 200, 172, T("VAE Decode Audio") + S("generated_audio"), "cyan")
    d.node("vd",  720, 340, 172, T("VAE Decode") + S("video"), "cyan")
    d.node("fn",  360, 340, 233, T("MiniMax H3 Dialogue", "Audio Finalize") + S("restores dialogue cores"), "green", min_h=62)
    d.node("lt",  720, 480, 190, T("MiniMax H3 Loop Trim") + S("images + audio + state"), "cyan", "hex", min_h=62)
    d.node("nx", 1020, 480, 200, T("Segment Save → Review") + S("→ next scene"), "orange")

    d.pts([(250,136),(305,136),(305,199),(360,199)], "data", thick=True)  # st -> lk
    d.pts([(266,247.5),(313,247.5),(313,207),(360,207)], "data")          # cs -> lk event set
    d.pts([(250,371),(297,371),(297,215),(360,215)], "control")          # cc -> lk clip_index
    d.pts([(250,491),(321,491),(321,223),(360,223)], "data")             # cx -> lk LATENT
    d.pts([(250,602.5),(329,602.5),(329,231),(360,231)], "data")         # av -> lk audio_vae
    d.pts([(593,211),(656,211),(656,106.5),(720,106.5)], "data", thick=True)  # lk -> sm
    d.pts([(806,129),(806,200)], "data")                                # sm -> da
    d.pts([(806,245),(925,245),(925,575),(332,575),(332,359),(360,359)], "data")  # da -> fn generated_audio
    d.pts([(826,129),(942,129),(942,560),(688,560),(688,362.5),(720,362.5)], "data")  # sm -> vd video latent
    d.pts([(460,242),(460,340)], "data")                                # lk -> fn reference
    d.pts([(493,242),(493,340)], "data")                               # lk -> fn manifest
    d.pts([(476.5,402),(476.5,511),(720,511)], "data", thick=True)      # fn -> lt final_audio
    d.pts([(125,522),(125,545),(340,545),(340,410),(770,410),(770,480)], "data")  # cx -> lt trim_frames
    d.pts([(125,402),(125,445),(750,445),(750,480)], "data")            # cc -> lt state
    d.pts([(814,385),(814,480)], "data")                               # vd -> lt images
    d.pts([(910,511),(965,511),(965,507.5),(1020,507.5)], "loop")       # lt -> nx segment
    d.legend = [("data","audio / latent"), ("control","scene index"), ("loop","recursive loop")]
    d.notes = ["Chain Current.state + Chain Context.trim_frames feed Loop Trim",
               "scene lock sits between Chain Context.latent and the sampler · Finalize runs after H3 audio decode"]
    d.render("scene-dialogue-lock.svg")
scene_dialogue()

# ═══════════════════════════════════════════════════════════════════
# D12 — scene exact lock, Context Loop
# left stack x40-240 | lk 360-593 y220-282 | sm 700-872 y100-145
# vd 700-872 y260-305 | lt 700-890 y420-482 | nx 1000-1200 y420-465
# ═══════════════════════════════════════════════════════════════════
def scene_exact():
    d = Diagram(1240, 680, "Scene Exact Audio Lock — Context Loop wiring",
                "Full scene lock: only current_scene events mixed; exact_audio is the deterministic scene master (workflows context_loop/01, 03, 05)")
    d.node("la",  40, 100, 200, T("Approved AUDIO take"), "amber", "file", min_h=45)
    d.node("st",  40, 220, 200, T("MiniMax H3 Scene", "Timed Audio") + S("scene_index + local", "start_frame"), "green")
    d.node("cc",  40, 340, 200, T("MiniMax H3 Chain", "Current") + S("clip_index (one-based)"), "orange", min_h=62)
    d.node("cx",  40, 460, 200, T("MiniMax H3 Chain", "Context") + S("continuation latent"), "orange", min_h=62)
    d.node("av",  40, 580, 200, T("MiniMax H3 audio VAE"), "purple", min_h=45)
    d.node("lk",  360, 220, 233, T("MiniMax H3 Scene Exact", "Audio Lock") + S("scene_timed_audios · event set"), "green", min_h=62)
    d.node("sm",  700, 100, 172, T("H3 sampler"), "cyan", "hex", min_h=45)
    d.node("vd",  700, 260, 172, T("VAE Decode") + S("video"), "cyan", min_h=45)
    d.node("lt",  700, 420, 190, T("MiniMax H3 Loop Trim") + S("images + audio + state"), "cyan", "hex", min_h=62)
    d.node("nx", 1000, 420, 200, T("Segment Save → Review") + S("→ next scene"), "orange")

    d.pts([(140,145),(140,220)], "data")                                # la -> st
    d.pts([(240,256),(305,256),(305,239),(360,239)], "data", thick=True)  # st -> lk
    d.pts([(240,371),(297,371),(297,247),(360,247)], "control")         # cc -> lk clip_index
    d.pts([(240,491),(321,491),(321,255),(360,255)], "data")            # cx -> lk LATENT
    d.pts([(240,602.5),(329,602.5),(329,263),(360,263)], "data")        # av -> lk audio_vae
    d.pts([(593,251),(646,251),(646,122.5),(700,122.5)], "data", thick=True)  # lk -> sm
    d.pts([(476.5,282),(476.5,451),(700,451)], "data", thick=True)      # lk -> lt exact_audio
    d.pts([(125,522),(125,545),(340,545),(340,395),(730,395),(730,420)], "data")  # cx -> lt trim_frames
    d.pts([(125,402),(125,408),(760,408),(760,420)], "data")           # cc -> lt state
    d.pts([(786,145),(786,260)], "data")                               # sm -> vd
    d.pts([(786,305),(786,362),(795,362),(795,420)], "data")           # vd -> lt images
    d.pts([(890,451),(945,451),(945,447.5),(1000,447.5)], "loop")      # lt -> nx segment
    d.legend = [("data","audio / latent"), ("control","scene index"), ("loop","recursive loop")]
    d.notes = ["only scene_index == current_scene events are mixed · do not substitute sampler-decoded audio for exact_audio",
               "Loop Trim takes Chain Context.trim_frames + Chain Current.state"]
    d.render("scene-exact-lock.svg")
scene_exact()

# ═══════════════════════════════════════════════════════════════════
# D13 — dialogue finalize chain
# sm 60-232 y100-145 | da 60-232 y260-305 | lk 360-600 y100-162
# fn 360-600 y260-322 | ot 700-900 y260-305
# ═══════════════════════════════════════════════════════════════════
def finalize():
    d = Diagram(1100, 420, "MiniMax H3 Dialogue Audio Finalize — post-sampling authority",
                "Restores supplied dialogue sample-for-sample inside the manifest's exact cores; H3 audio outside the cores is preserved")
    d.node("sm",  60, 100, 172, T("H3 sampler"), "cyan", "hex", min_h=45)
    d.node("da",  60, 260, 172, T("VAE Decode Audio") + S("generated_audio"), "cyan")
    d.node("lk",  360, 100, 240, T("Dialogue / Scene Dialogue", "Audio Lock") + S("reference + manifest"), "green", min_h=62)
    d.node("fn",  360, 260, 240, T("MiniMax H3 Dialogue", "Audio Finalize") + S("restores dialogue cores"), "green", min_h=62)
    d.node("ot",  700, 260, 200, T("Create Video / mux") + S("or MiniMax H3 Loop Trim"), "slate")

    d.pts([(146,145),(146,260)], "data")                               # sm -> da latent
    d.pts([(232,282.5),(310,282.5),(310,283),(360,283)], "data")       # da -> fn generated_audio
    d.pts([(450,162),(450,240),(320,240),(320,291),(360,291)], "data") # lk -> fn reference
    d.pts([(510,162),(510,248),(336,248),(336,299),(360,299)], "data") # lk -> fn manifest
    d.pts([(600,291),(650,291),(650,287.5),(700,287.5)], "data", thick=True)  # fn -> ot final_audio
    d.legend = [("data","audio / latent")]
    d.notes = ["the finalizer — not a second VAE decode — makes the exported soundtrack exact at the sample boundary"]
    d.render("dialogue-finalize.svg")
finalize()

# ═══════════════════════════════════════════════════════════════════
# D14 — recommended production workflow
# ═══════════════════════════════════════════════════════════════════
def production():
    d = Diagram(860, 860, "Recommended production workflow — scene-aware dialogue",
                "Approve the voice once per line; changing video seed, camera, prompt or upscale never changes the approved waveform")
    steps = [
        ("v1", "Persistent character voice", "voice clone · TTS", "amber", "hex"),
        ("v2", "Render & approve each line once", "Audio Review / Accept Gate", "green", "rect"),
        ("v3", "Scene Timed Audio", "scene_index + local start_frame", "green", "rect"),
        ("v4", "Scene Exact / Dialogue", "Audio Lock · current_scene only", "green", "rect"),
        ("v5", "H3 sampler", "SamplerCustomAdvanced", "cyan", "hex"),
        ("v6", "Deterministic final audio path", "exact_audio · Dialogue Audio Finalize", "green", "rect"),
        ("v7", "Review", "segment review · loop end", "rose", "diamond"),
    ]
    y = 80
    prev = None
    for nid, t, s, tone, shape in steps:
        n = d.node(nid, 280, y, 300, T(t) + S(s), tone, shape, min_h=90 if shape == "diamond" else None)
        if prev is not None:
            d.pts([(430, prev.y + prev.h), (430, n.y)], "data", thick=(nid == "v5"))
        prev = n
        y += 108
    d.legend = [("data","flow")]
    d.notes = ["if a performance changes, re-render that TTS line deliberately and update its Scene Timed Audio input"]
    d.render("production-workflow.svg")
production()

print("done")
