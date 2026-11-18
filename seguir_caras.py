#!/usr/bin/env python3
"""
Reencuadre vertical 9:16 que sigue a la persona que habla.

Detecta caras (MediaPipe), decide quién habla según el movimiento de la boca
y desliza el recorte suavemente hacia esa cara.

Uso:
    python3 seguir_caras.py entrada.mp4 salida.mp4

Requisitos (en el venv):
    pip install mediapipe opencv-python numpy
"""
import subprocess
import sys
from collections import deque
from pathlib import Path

import cv2
import mediapipe as mp
import numpy as np

OUT_W, OUT_H = 1080, 1920
DET_W = 640          # ancho al que se analiza (más rápido)
CADA = 3             # analizar 1 de cada N frames
UPPER_LIP, LOWER_LIP, TOP_FACE, CHIN = 13, 14, 10, 152
ALPHA = 0.06         # suavizado de la cámara (menor = más lento)
ZONA_MUERTA = 0.02   # fracción del ancho: ignora movimientos mínimos
MIN_ENTRE_CAMBIOS = 1.0  # segundos


def analizar(src):
    """Devuelve {frame: [(cx_norm, apertura_boca), ...]}, fps, ancho, alto, total."""
    cap = cv2.VideoCapture(str(src))
    fps = cap.get(cv2.CAP_PROP_FPS) or 30
    w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    escala = DET_W / w
    mesh = mp.solutions.face_mesh.FaceMesh(
        static_image_mode=False, max_num_faces=4,
        min_detection_confidence=0.5, min_tracking_confidence=0.5)
    obs, i = {}, 0
    while True:
        ok, fr = cap.read()
        if not ok:
            break
        if i % CADA == 0:
            chico = cv2.resize(fr, (DET_W, int(h * escala)))
            res = mesh.process(cv2.cvtColor(chico, cv2.COLOR_BGR2RGB))
            caras = []
            for f in (res.multi_face_landmarks or []):
                lm = f.landmark
                alto = abs(lm[CHIN].y - lm[TOP_FACE].y) or 1e-6
                apertura = abs(lm[LOWER_LIP].y - lm[UPPER_LIP].y) / alto
                cx = float(np.mean([p.x for p in lm]))
                caras.append((cx, apertura))
            obs[i] = caras
        i += 1
    cap.release()
    return obs, fps, w, h, i


def elegir_objetivo(obs, fps, total):
    """Posición horizontal (0..1) a seguir en cada frame."""
    tracks, actual, ultimo_cambio = [], None, -99.0
    puntos = {}

    def score(tr):
        return float(np.std(tr["hist"])) if len(tr["hist"]) >= 4 else 0.0

    for fidx in sorted(obs):
        t = fidx / fps
        vistos = []
        for cx, ap in obs[fidx]:
            cand = min(tracks, key=lambda tr: abs(tr["cx"] - cx), default=None)
            if cand is None or abs(cand["cx"] - cx) > 0.12 or cand["id"] in [v["id"] for v in vistos]:
                cand = {"id": len(tracks), "cx": cx, "hist": deque(maxlen=10)}
                tracks.append(cand)
            cand["cx"] = cx
            cand["hist"].append(ap)
            vistos.append(cand)

        if not vistos:
            puntos[fidx] = None
            continue
        mejor = max(vistos, key=score)
        ids = [v["id"] for v in vistos]
        if actual is None or actual["id"] not in ids:
            actual, ultimo_cambio = mejor, t
        elif (mejor["id"] != actual["id"]
              and score(mejor) > 1.5 * score(actual) + 0.005
              and t - ultimo_cambio > MIN_ENTRE_CAMBIOS):
            actual, ultimo_cambio = mejor, t
        puntos[fidx] = actual["cx"]

    # Rellenar huecos (sin caras) con el último valor conocido
    xs, ys, ult = [], [], 0.5
    for fidx in sorted(puntos):
        if puntos[fidx] is not None:
            ult = puntos[fidx]
        xs.append(fidx)
        ys.append(ult)
    if not xs:
        return np.full(total, 0.5)
    return np.interp(np.arange(total), xs, ys)


def renderizar(src, tmp, objetivo, fps, w, h):
    cap = cv2.VideoCapture(str(src))
    out = cv2.VideoWriter(str(tmp), cv2.VideoWriter_fourcc(*"mp4v"), fps, (OUT_W, OUT_H))
    crop_w = min(int(h * 9 / 16), w)
    pos = objetivo[0] if len(objetivo) else 0.5
    for i in range(len(objetivo)):
        ok, fr = cap.read()
        if not ok:
            break
        if abs(objetivo[i] - pos) * w > ZONA_MUERTA * w:
            pos += (objetivo[i] - pos) * ALPHA
        x0 = int(np.clip(pos * w - crop_w / 2, 0, w - crop_w))
        recorte = fr[:, x0:x0 + crop_w]
        out.write(cv2.resize(recorte, (OUT_W, OUT_H), interpolation=cv2.INTER_AREA))
    cap.release()
    out.release()


def reencuadrar(src, dst):
    src, dst = Path(src), Path(dst)
    obs, fps, w, h, total = analizar(src)
    if total == 0:
        sys.exit(f"No pude leer {src}")
    objetivo = elegir_objetivo(obs, fps, total)
    tmp = dst.with_name(dst.stem + "_tmp.mp4")
    renderizar(src, tmp, objetivo, fps, w, h)
    # Recodificar a H.264 y recuperar el audio original
    subprocess.run([
        "ffmpeg", "-y", "-i", str(tmp), "-i", str(src),
        "-map", "0:v:0", "-map", "1:a:0?",
        "-c:v", "libx264", "-c:a", "aac", "-shortest", str(dst)], check=True)
    tmp.unlink(missing_ok=True)


if __name__ == "__main__":
    if len(sys.argv) != 3:
        sys.exit("Uso: python3 seguir_caras.py entrada.mp4 salida.mp4")
    reencuadrar(sys.argv[1], sys.argv[2])
