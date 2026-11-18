#!/usr/bin/env python3
"""
Genera clips verticales (9:16) de 40 s con subtítulos en español.

Pasos por clip: cortar -> transcribir (Whisper, idioma original) ->
traducir a español (argostranslate, offline) -> pegar subtítulos.

Uso:
    python3 hacer_clips.py video.mp4                  # clips consecutivos de 40 s
    python3 hacer_clips.py video.mp4 --seguir-caras   # el encuadre sigue a quien habla
    python3 hacer_clips.py video.mp4 --inicio 00:10:30 --clips 3
    python3 hacer_clips.py video.mp4 --idioma-origen en --modelo small

Requisitos (en el venv de Whisper):
    pip install openai-whisper argostranslate
"""
import argparse
import subprocess
import sys
from pathlib import Path

DURACION = 40


def run(cmd):
    subprocess.run(cmd, check=True)


def a_segundos(t):
    partes = [float(p) for p in t.split(":")]
    s = 0.0
    for p in partes:
        s = s * 60 + p
    return s


def duracion_video(video):
    out = subprocess.check_output([
        "ffprobe", "-v", "error", "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1", str(video)])
    return float(out)


def cortar(video, inicio, salida, seguir=False):
    if not seguir:
        run(["ffmpeg", "-y", "-ss", str(inicio), "-t", str(DURACION), "-i", str(video),
             "-vf", "crop=ih*9/16:ih,scale=1080:1920",
             "-c:v", "libx264", "-c:a", "aac", str(salida)])
        return
    # Corte sin recorte y luego reencuadre siguiendo a quien habla
    crudo = salida.with_name(salida.stem + "_crudo.mp4")
    run(["ffmpeg", "-y", "-ss", str(inicio), "-t", str(DURACION), "-i", str(video),
         "-c:v", "libx264", "-c:a", "aac", str(crudo)])
    from seguir_caras import reencuadrar
    reencuadrar(crudo, salida)
    crudo.unlink(missing_ok=True)


def transcribir(clip, modelo, idioma, carpeta):
    run(["whisper", str(clip), "--model", modelo, "--language", idioma,
         "--output_format", "srt", "--output_dir", str(carpeta)])
    return carpeta / (clip.stem + ".srt")


def preparar_traductor(origen):
    import argostranslate.package as pkg
    import argostranslate.translate as tr
    instalados = {(l.code) for l in tr.get_installed_languages()}
    langs = tr.get_installed_languages()
    o = next((l for l in langs if l.code == origen), None)
    d = next((l for l in langs if l.code == "es"), None)
    if not (o and d and o.get_translation(d)):
        pkg.update_package_index()
        for p in pkg.get_available_packages():
            if p.from_code == origen and p.to_code == "es":
                pkg.install_from_path(p.download())
                break
        langs = tr.get_installed_languages()
        o = next(l for l in langs if l.code == origen)
        d = next(l for l in langs if l.code == "es")
    return o.get_translation(d).translate


def traducir_srt(srt_in, srt_out, traducir):
    bloques = srt_in.read_text(encoding="utf-8").strip().split("\n\n")
    salida = []
    for b in bloques:
        lineas = b.split("\n")
        if len(lineas) >= 3:
            texto = traducir(" ".join(lineas[2:]))
            salida.append("\n".join(lineas[:2] + [texto]))
    srt_out.write_text("\n\n".join(salida) + "\n", encoding="utf-8")


def pegar_subs(clip, srt, salida):
    estilo = ("FontName=DejaVu Sans,FontSize=14,Bold=1,Outline=2,"
              "Alignment=2,MarginV=60")
    run(["ffmpeg", "-y", "-i", str(clip),
         "-vf", f"subtitles={srt.name}:force_style='{estilo}'",
         "-c:a", "copy", str(salida)])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("video")
    ap.add_argument("--inicio", default="0", help="HH:MM:SS donde empieza el primer clip")
    ap.add_argument("--clips", type=int, default=0, help="cantidad (0 = hasta el final)")
    ap.add_argument("--idioma-origen", default="en", help="idioma hablado (en, es, ...)")
    ap.add_argument("--modelo", default="base")
    ap.add_argument("--salida", default="clips")
    ap.add_argument("--seguir-caras", action="store_true",
                    help="el encuadre sigue a quien habla (requiere mediapipe)")
    a = ap.parse_args()

    video = Path(a.video).resolve()
    out = Path(a.salida).resolve()
    out.mkdir(exist_ok=True)

    inicio = a_segundos(a.inicio)
    total = int((duracion_video(video) - inicio) // DURACION)
    n = min(a.clips, total) if a.clips else total
    if n < 1:
        sys.exit("El video no alcanza para un clip de 40 s desde ese inicio.")

    traducir = None
    if a.idioma_origen != "es":
        traducir = preparar_traductor(a.idioma_origen)

    for i in range(1, n + 1):
        ini = inicio + (i - 1) * DURACION
        base = out / f"clip{i}"
        clip = base.with_suffix(".mp4")
        cortar(video, ini, clip, a.seguir_caras)
        srt = transcribir(clip, a.modelo, a.idioma_origen, out)
        if traducir:
            srt_es = out / f"clip{i}_es.srt"
            traducir_srt(srt, srt_es, traducir)
            srt = srt_es
        final = out / f"clip{i}_subs.mp4"
        # subtitles= se resuelve desde el directorio actual
        subprocess.run(["ffmpeg", "-y", "-i", str(clip),
                        "-vf", f"subtitles={srt.name}:force_style='FontName=DejaVu Sans,FontSize=14,Bold=1,Outline=2,Alignment=2,MarginV=60'",
                        "-c:a", "copy", str(final)], check=True, cwd=out)
        print(f"Listo: {final}")


if __name__ == "__main__":
    main()
