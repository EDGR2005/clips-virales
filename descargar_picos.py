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
import urllib.parse
import urllib.request
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


def _get(url, intentos=3):
    for i in range(intentos):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=30) as r:
                return r.read()
        except Exception:
            if i == intentos - 1:
                raise
            time.sleep(2 * (i + 1))


def bajar_tramo(vod_url, desde, hasta, calidad, destino, cookies=None):
    """Baja [desde, hasta] (segundos del VOD) y lo guarda en destino.

    No usa --download-sections de yt-dlp: con los VODs nuevos de Twitch (HLS con
    fragmentos fMP4) ffmpeg 6 genera un MP4 vacío al saltar dentro de la playlist.
    En su lugar baja solo los segmentos que cubren el rango, los une y corta el
    archivo local. Devuelve True si algún segmento estaba silenciado por Twitch."""
    cmd = ["yt-dlp", "--no-warnings", "-f", calidad, "-g", vod_url]
    if cookies:
        cmd[1:1] = ["--cookies-from-browser", cookies]
    p = subprocess.run(cmd, capture_output=True, text=True)
    url_pl = p.stdout.strip().splitlines()[-1] if p.stdout.strip() else ""
    if p.returncode or not url_pl.startswith("http"):
        raise RuntimeError(f"yt-dlp no dio la playlist ({p.stderr.strip()[-200:]})")

    texto = _get(url_pl).decode("utf-8", errors="replace")
    mapa, segmentos, t, dur = None, [], 0.0, None
    for linea in texto.splitlines():
        linea = linea.strip()
        if linea.startswith("#EXT-X-MAP:"):
            mapa = linea.split('URI="', 1)[1].split('"', 1)[0]
        elif linea.startswith("#EXTINF:"):
            dur = float(linea[8:].split(",", 1)[0])
        elif linea and not linea.startswith("#") and dur is not None:
            segmentos.append((t, dur, linea))
            t, dur = t + dur, None
    elegidos = [s for s in segmentos if s[0] + s[1] > desde and s[0] < hasta]
    if not elegidos:
        raise RuntimeError("el rango está fuera de la playlist")

    tmp = destino.with_name(destino.stem + ".partes")
    try:
        with open(tmp, "wb") as fh:
            if mapa:
                fh.write(_get(urllib.parse.urljoin(url_pl, mapa)))
            for _, _, uri in elegidos:
                fh.write(_get(urllib.parse.urljoin(url_pl, uri)))
        ini = desde - elegidos[0][0]
        rc = subprocess.run(
            ["ffmpeg", "-v", "error", "-y", "-ss", f"{ini:.2f}", "-i", str(tmp),
             "-t", f"{hasta - desde:.2f}", "-c", "copy", "-movflags", "+faststart",
             str(destino)], capture_output=True, text=True)
        if rc.returncode:
            raise RuntimeError(f"ffmpeg: {rc.stderr.strip()[-200:]}")
    finally:
        tmp.unlink(missing_ok=True)

    sonda = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v", "-show_entries",
         "format=duration", "-of", "csv=p=0", str(destino)], capture_output=True, text=True)
    try:
        ok = float(sonda.stdout.strip() or 0) > 1
    except ValueError:
        ok = False
    if not ok:
        destino.unlink(missing_ok=True)
        raise RuntimeError("el clip salió vacío")
    return any("-muted" in uri for _, _, uri in elegidos)


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
    ap.add_argument("--top", type=int, default=0,
                    help="bajar solo los N picos de mayor puntaje de cada canal (0 = todos)")
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
    if a.top:
        por_canal = {}
        for r in regs:
            por_canal.setdefault(r["fuente"], []).append(r)
        regs = [r for rs in por_canal.values()
                for r in sorted(rs, key=lambda r: -r["puntaje"])[:a.top]]

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
        if destino.exists() and destino.stat().st_size > 10_000:
            saltados += 1
            continue
        destino.unlink(missing_ok=True)   # restos vacíos de un intento fallido
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
        try:
            silenciado = bajar_tramo(vod["url"], desde, hasta, a.calidad, destino,
                                     a.cookies_from_browser)
        except Exception as e:
            destino.unlink(missing_ok=True)
            print(f"    falló la descarga: {e}")
            fallos += 1
            continue
        aviso = ("  ⚠ SIN AUDIO: Twitch silenció esta parte del VOD (música con copyright)"
                 if silenciado else "")
        print(f"    guardado: {destino}{aviso}")
        hechos += 1

    print(f"\nListo: {hechos} descargados, {saltados} ya existían, {fallos} con problemas. "
          f"Carpeta: {salida}")


if __name__ == "__main__":
    main()
