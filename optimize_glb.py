#!/usr/bin/env python3
"""
optimize_glb.py - bake transforms, real-world scale and a base-centred pivot into a glTF binary,
merge primitives that share a material, use 16-bit indices, drop unused vertex data and
re-encode textures. Pure Python (numpy + Pillow); no native tools required.

    python3 optimize_glb.py in.glb out.glb --width-cm 12 [--max-tex 1024] [--color-q 80] [--data-q 80]

--width-cm  real-world width of the dish's footprint (largest of X/Z), in centimetres.
Result: metres, Y-up, origin at the centre of the base, no node hierarchy.

Limits (by design): static triangle meshes only. Skins, animations and morph targets are rejected.
Not done here: Draco/meshopt compression (use `gltf-transform optimize` if you want to go further;
Scene Viewer and Quick Look are happiest with plain glTF, so test on device if you do).
"""
import argparse, io, json, struct, sys
import numpy as np
from PIL import Image

CT = {5120: np.int8, 5121: np.uint8, 5122: np.int16, 5123: np.uint16, 5125: np.uint32, 5126: np.float32}
NC = {"SCALAR": 1, "VEC2": 2, "VEC3": 3, "VEC4": 4, "MAT4": 16}


def read_glb(path):
    d = open(path, "rb").read()
    magic, ver, _ = struct.unpack("<4sII", d[:12])
    if magic != b"glTF" or ver != 2:
        sys.exit("not a glTF 2.0 binary")
    off, js, bn = 12, None, b""
    while off < len(d):
        n, t = struct.unpack("<I4s", d[off:off + 8])
        body = d[off + 8:off + 8 + n]
        if t == b"JSON":
            js = json.loads(body)
        elif t == b"BIN\x00":
            bn = body
        off += 8 + n
    return js, bn


def accessor(j, bn, i):
    a, bv = j["accessors"][i], j["bufferViews"][j["accessors"][i]["bufferView"]]
    n, dt = NC[a["type"]], CT[a["componentType"]]
    item = n * np.dtype(dt).itemsize
    stride = bv.get("byteStride") or item
    off = bv.get("byteOffset", 0) + a.get("byteOffset", 0)
    if stride == item:
        arr = np.frombuffer(bn, dtype=dt, count=a["count"] * n, offset=off).reshape(a["count"], n)
    else:
        raw = np.frombuffer(bn, dtype=np.uint8, count=stride * a["count"], offset=off).reshape(a["count"], stride)
        arr = np.ascontiguousarray(raw[:, :item]).view(dt).reshape(a["count"], n)
    return arr.copy()


def local_matrix(n):
    if "matrix" in n:
        return np.array(n["matrix"], dtype=np.float64).reshape(4, 4).T
    t = np.array(n.get("translation", [0, 0, 0]), dtype=np.float64)
    x, y, z, w = n.get("rotation", [0, 0, 0, 1])
    r = np.array([[1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w)],
                  [2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w)],
                  [2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y)]])
    s = np.array(n.get("scale", [1, 1, 1]), dtype=np.float64)
    m = np.eye(4)
    m[:3, :3] = r @ np.diag(s)
    m[:3, 3] = t
    return m


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("src"); ap.add_argument("dst")
    ap.add_argument("--width-cm", type=float, required=True)
    ap.add_argument("--max-tex", type=int, default=1024)
    ap.add_argument("--color-q", type=int, default=80, help="JPEG quality for colour textures (4:2:0)")
    ap.add_argument("--data-q", type=int, default=80, help="JPEG quality for normal/ORM maps (4:4:4)")
    args = ap.parse_args()

    j, bn = read_glb(args.src)
    for k in ("skins", "animations"):
        if j.get(k):
            sys.exit("%s are not supported" % k)

    # 1. gather triangles per material, transformed into world space
    groups = {}  # material index -> lists of (pos, nrm, uv, idx)
    def walk(i, parent):
        n = j["nodes"][i]
        w = parent @ local_matrix(n)
        if "mesh" in n:
            rot = w[:3, :3]
            nrot = np.linalg.inv(rot).T
            flip = np.linalg.det(rot) < 0
            for p in j["meshes"][n["mesh"]]["primitives"]:
                if p.get("mode", 4) != 4:
                    sys.exit("only TRIANGLES primitives are supported")
                if p.get("targets"):
                    sys.exit("morph targets are not supported")
                at = p["attributes"]
                pos = accessor(j, bn, at["POSITION"]).astype(np.float64)
                pos = (pos @ rot.T) + w[:3, 3]
                if "NORMAL" in at:
                    nrm = accessor(j, bn, at["NORMAL"]).astype(np.float64) @ nrot.T
                    nrm /= np.maximum(np.linalg.norm(nrm, axis=1, keepdims=True), 1e-12)
                else:
                    nrm = None
                uv = accessor(j, bn, at["TEXCOORD_0"]).astype(np.float32) if "TEXCOORD_0" in at else None
                idx = accessor(j, bn, p["indices"]).reshape(-1).astype(np.uint32) if "indices" in p else np.arange(len(pos), dtype=np.uint32)
                if flip:
                    idx = idx.reshape(-1, 3)[:, ::-1].reshape(-1)
                groups.setdefault(p.get("material", -1), []).append((pos, nrm, uv, idx))
        for c in n.get("children", []):
            walk(c, w)
    for r in j["scenes"][j.get("scene", 0)]["nodes"]:
        walk(r, np.eye(4))

    # 2. bake scale + pivot (centre of base at the origin, metres)
    allpos = np.concatenate([g[0] for lst in groups.values() for g in lst])
    lo, hi = allpos.min(0), allpos.max(0)
    size = hi - lo
    scale = (args.width_cm / 100.0) / max(size[0], size[2])
    pivot = np.array([(lo[0] + hi[0]) / 2, lo[1], (lo[2] + hi[2]) / 2])

    bin_parts, views, accs, prims = [], [], [], []
    def add_view(buf, target=None):
        while sum(len(b) for b in bin_parts) % 4:
            bin_parts.append(b"\0")
        off = sum(len(b) for b in bin_parts)
        bin_parts.append(bytes(buf))
        v = {"buffer": 0, "byteOffset": off, "byteLength": len(buf)}
        if target:
            v["target"] = target
        views.append(v)
        return len(views) - 1
    def add_acc(view, ctype, count, typ, mn=None, mx=None):
        a = {"bufferView": view, "componentType": ctype, "count": int(count), "type": typ}
        if mn is not None:
            a["min"], a["max"] = [float(x) for x in mn], [float(x) for x in mx]
        accs.append(a)
        return len(accs) - 1

    final_lo, final_hi = np.full(3, 1e9), np.full(3, -1e9)
    tris = verts = 0
    for mat, lst in groups.items():
        P, N, U, I, base = [], [], [], [], 0
        for pos, nrm, uv, idx in lst:
            P.append((pos - pivot) * scale)
            if nrm is not None: N.append(nrm)
            if uv is not None: U.append(uv)
            I.append(idx + base)
            base += len(pos)
        P = np.concatenate(P).astype(np.float32)
        I = np.concatenate(I)
        if I.max() > 65535:
            sys.exit("primitive exceeds 65535 vertices; split it or keep 32-bit indices")
        attrs = {}
        attrs["POSITION"] = add_acc(add_view(P.tobytes(), 34962), 5126, len(P), "VEC3", P.min(0), P.max(0))
        if N: attrs["NORMAL"] = add_acc(add_view(np.concatenate(N).astype(np.float32).tobytes(), 34962), 5126, len(P), "VEC3")
        if U: attrs["TEXCOORD_0"] = add_acc(add_view(np.concatenate(U).tobytes(), 34962), 5126, len(P), "VEC2")
        ia = add_acc(add_view(I.astype(np.uint16).tobytes(), 34963), 5123, len(I), "SCALAR")
        prim = {"attributes": attrs, "indices": ia, "mode": 4}
        if mat >= 0: prim["material"] = mat
        prims.append(prim)
        final_lo, final_hi = np.minimum(final_lo, P.min(0)), np.maximum(final_hi, P.max(0))
        tris += len(I) // 3; verts += len(P)

    # 3. textures: keep indices, re-encode (colour -> 4:2:0, data maps -> 4:4:4)
    colour = set(); data_maps = set()
    def tex_of(m, *path):
        for k in path:
            m = m.get(k) if isinstance(m, dict) else None
            if m is None: return None
        return m.get("index")
    for m in j.get("materials", []):
        for path in (("pbrMetallicRoughness", "baseColorTexture"), ("emissiveTexture",)):
            t = tex_of(m, *path)
            if t is not None: colour.add(j["textures"][t]["source"])
        for path in (("normalTexture",), ("occlusionTexture",), ("pbrMetallicRoughness", "metallicRoughnessTexture")):
            t = tex_of(m, *path)
            if t is not None: data_maps.add(j["textures"][t]["source"])
    images = []
    tex_bytes = 0
    for i, im in enumerate(j.get("images", [])):
        bv = j["bufferViews"][im["bufferView"]]
        raw = bn[bv.get("byteOffset", 0):bv.get("byteOffset", 0) + bv["byteLength"]]
        img = Image.open(io.BytesIO(raw))
        if max(img.size) > args.max_tex:
            r = args.max_tex / max(img.size)
            img = img.resize((round(img.width * r), round(img.height * r)), Image.LANCZOS)
        out = io.BytesIO()
        if "A" in img.getbands():
            img.save(out, "PNG", optimize=True); mime = "image/png"
        else:
            img = img.convert("RGB")
            if i in data_maps:
                img.save(out, "JPEG", quality=args.data_q, subsampling=0, optimize=True)
            else:
                img.save(out, "JPEG", quality=args.color_q, subsampling=2, optimize=True)
            mime = "image/jpeg"
        tex_bytes += out.tell()
        images.append({"bufferView": add_view(out.getvalue()), "mimeType": mime, "name": im.get("name", "image%d" % i)})

    asset = dict(j.get("asset", {}))
    asset["generator"] = "optimize_glb.py (baked transforms, %.1f cm footprint)" % args.width_cm
    out = {
        "asset": asset,
        "scene": 0, "scenes": [{"nodes": [0]}],
        "nodes": [{"name": "dish", "mesh": 0}],
        "meshes": [{"name": "dish", "primitives": prims}],
        "materials": j.get("materials", []),
        "textures": j.get("textures", []), "images": images,
        "samplers": j.get("samplers", []),
        "accessors": accs, "bufferViews": views,
    }
    if j.get("extensionsUsed"): out["extensionsUsed"] = j["extensionsUsed"]
    binbuf = b"".join(bin_parts)
    while len(binbuf) % 4: binbuf += b"\0"
    out["buffers"] = [{"byteLength": len(binbuf)}]
    jb = json.dumps(out, separators=(",", ":")).encode()
    while len(jb) % 4: jb += b" "
    total = 12 + 8 + len(jb) + 8 + len(binbuf)
    with open(args.dst, "wb") as f:
        f.write(struct.pack("<4sII", b"glTF", 2, total))
        f.write(struct.pack("<I4s", len(jb), b"JSON")); f.write(jb)
        f.write(struct.pack("<I4s", len(binbuf), b"BIN\0")); f.write(binbuf)
    ext = final_hi - final_lo
    print("wrote %s  %.1f KB  (%d verts, %d tris, textures %.1f KB)" % (args.dst, total / 1024, verts, tris, tex_bytes / 1024))
    print("size (m): %.4f x %.4f x %.4f   min y = %.5f" % (ext[0], ext[1], ext[2], final_lo[1]))


if __name__ == "__main__":
    main()
