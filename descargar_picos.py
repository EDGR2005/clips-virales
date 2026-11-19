#!/usr/bin/env python3
"""
Descarga desde el VOD de Twitch los clips de los picos que anotó monitor_directos.py.

Lee picos/picos.jsonl, busca el VOD del canal que contiene cada pico (también el
del directo que sigue en curso) y baja solo ese rango con yt-dlp. Se puede
ejecutar varias veces: los clips que ya existen se saltan.

Uso:
    python3 descargar_picos.py                        # todos los canales de Twitch del jsonl
    python3 descargar_picos.py --canal auronplay
    python3 descargar_picos.py --canal auronplay --calidad "best[height<=720]" --margen 5
    python3 descargar_picos.py --fecha 2026-11-18     # si los picos son de otro día

Requisitos: yt-dlp y ffmpeg instalados.
"""
import argparse
import json
import subprocess
import sys
import time
from datetime import date, datetime, timedelta
from pathlib import Path


def epoch_pico(reg, fecha):
    """Hora de inicio y fin del pico en epoch.

    Los registros nuevos traen t_ini/t_fin. Los antiguos solo traen la hora del
    reloj (HH:MM:SS), así que se combinan con la fecha indicada."""
    if "t_ini" in reg:
        return reg["t_ini"], reg["t_fin"]
    d = fecha or date.today()
    ini = datetime.combine(d, datetime.strptime(reg["reloj_ini"], "%H:%M:%S").time())
    fin = datetime.combine(d, datetime.strptime(reg["reloj_fin"], "%H:%M:%S").time())
    if fecha is None and ini.timestamp() > time.time() + 60:
        ini -= timedelta(days=1)     # sin fecha: el pico fue antes de medianoche
        fin -= timedelta(days=1)
    if fin < ini:
        fin += timedelta(days=1)     # el pico cruzó la medianoche
    return ini.timestamp(), fin.timestamp()


def vods_twitch(canal, n):
    """Últimos n VODs (archivos de directos), con inicio y duración."""
    url = f"https://www.twitch.tv/{canal}/videos?filter=archives&sort=time"
    out = subprocess.run(
        ["yt-dlp", "--no-warnings", "--playlist-items", f"1:{n}",
         "--print", "%(webpage_url)s\t%(timestamp)s\t%(duration)s", url],
        capture_output=True, text=True)
    vods = []
    for linea in out.stdout.splitlines():
        try:
            u, ts, dur = linea.split("\t")
            vods.append({"url": u, "inicio": float(ts), "dur": float(dur)})
        except ValueError:
            pass
    if not vods:
        print(f"[{canal}] no encontré VODs ({out.stderr.strip()[-200:] or 'sin detalle'}). "
              "El canal puede tener los VODs desactivados o solo para suscriptores.")
    return vods


def hms(seg):
    seg = max(0, int(seg))
    return f"{seg // 3600}:{seg % 3600 // 60:02d}:{seg % 60:02d}"


def main():
    ap = argparse.ArgumentParser(description="Descargar desde el VOD los picos de picos.jsonl")
    ap.add_argument("--jsonl", default="picos/picos.jsonl")
    ap.add_argument("--canal", help="solo este canal de Twitch")
    ap.add_argument("--salida", default="picos/clips")
    ap.add_argument("--calidad", default="best[height<=1080]/best", help="formato de yt-dlp")
    ap.add_argument("--margen", type=int, default=0,
                    help="segundos extra antes y después de cada pico")
    ap.add_argument("--fecha", type=date.fromisoformat,
                    help="día de los picos (AAAA-MM-DD) para registros sin fecha; por defecto hoy")
    ap.add_argument("--vods", type=int, default=5, help="cuántos VODs recientes revisar")
    ap.add_argument("--cookies-from-browser", metavar="NAVEGADOR",
                    help="para VODs solo de suscriptores (ej. firefox)")
    a = ap.parse_args()

    ruta = Path(a.jsonl)
    if not ruta.exists():
        sys.exit(f"No existe {ruta}")
    regs = [json.loads(l) for l in ruta.read_text(encoding="utf-8").splitlines() if l.strip()]
    regs = [r for r in regs if r["fuente"].startswith("twitch/")]
    if a.canal:
        regs = [r for r in regs if r["fuente"].lower() == f"twitch/{a.canal.lower()}"]
    if not regs:
        sys.exit("No hay picos de Twitch que descargar en " + str(ruta))

    salida = Path(a.salida)
    salida.mkdir(parents=True, exist_ok=True)
    cache = {}
    hechos = saltados = fallos = 0

    for r in regs:
        canal = r["fuente"].split("/", 1)[1]
        ini, fin = epoch_pico(r, a.fecha)
        ini, fin = ini - a.margen, fin + a.margen
        destino = salida / (f"twitch_{canal}_{datetime.fromtimestamp(ini):%Y%m%d_%H%M%S}"
                            f"_p{r['puntaje']:.0f}.mp4")
        if destino.exists():
            saltados += 1
            continue
        if canal not in cache:
            cache[canal] = vods_twitch(canal, a.vods)
        def buscar():
            # Basta con que el VOD contenga el inicio; el final se recorta al del VOD
            # (pasa con picos justo al terminar el directo).
            return next((v for v in cache[canal]
                         if v["inicio"] <= ini < v["inicio"] + v["dur"] - 5), None)
        vod = buscar()
        if not vod or fin > vod["inicio"] + vod["dur"]:
            # El VOD en curso crece: recargar por si el pico es muy reciente
            cache[canal] = vods_twitch(canal, a.vods)
            vod = buscar()
        if not vod:
            print(f"[{canal}] pico {r['reloj_ini']}: ningún VOD lo contiene (¿otra fecha? usa --fecha)")
            fallos += 1
            continue
        desde = ini - vod["inicio"]
        hasta = min(fin, vod["inicio"] + vod["dur"]) - vod["inicio"]
        print(f"[{canal}] pico {r['reloj_ini']} (puntaje {r['puntaje']}) -> "
              f"{vod['url']} {hms(desde)}-{hms(hasta)}")
        cmd = ["yt-dlp", "--no-warnings", "--quiet", "--progress",
               "--download-sections", f"*{desde:.0f}-{hasta:.0f}",
               "-f", a.calidad, "--merge-output-format", "mp4",
               "-o", str(destino), vod["url"]]
        if a.cookies_from_browser:
            cmd[1:1] = ["--cookies-from-browser", a.cookies_from_browser]
        if subprocess.run(cmd).returncode == 0 and destino.exists():
            print(f"    guardado: {destino}")
            hechos += 1
        else:
            print("    falló la descarga")
            fallos += 1

    print(f"\nListo: {hechos} descargados, {saltados} ya existían, {fallos} con problemas. "
          f"Carpeta: {salida}")


if __name__ == "__main__":
    main()
