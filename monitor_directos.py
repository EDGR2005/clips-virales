#!/usr/bin/env python3
"""
Monitor de directos (Twitch y Kick) que detecta picos de audiencia y de chat
y propone rangos de tiempo donde sacar clips virales.

Cada BIN segundos mide por canal:
  - mensajes de chat, chatters únicos, mensajes "hype" (KEKW, jaja, OMG, clip...)
  - espectadores (si hay datos)
Compara contra la mediana de los últimos 10 min (z-score robusto con MAD) y
combina todo en un puntaje. Cuando el puntaje supera el umbral abre un pico,
y al calmarse lo cierra y emite el rango sugerido (con margen antes y después).

Uso:
    python3 monitor_directos.py --twitch canal1 --kick canal2
    python3 monitor_directos.py --twitch canal --umbral 2.5 --antes 25
    python3 monitor_directos.py --demo          # prueba sin conexión

Requisitos:
    pip install websockets
    Opcional (espectadores de Twitch): variables de entorno TWITCH_CLIENT_ID y
        TWITCH_CLIENT_SECRET (app gratis en https://dev.twitch.tv/console).
        Sin ellas se usa solo el chat.
    Opcional (Kick, si Cloudflare bloquea): pip install curl_cffi

Salida (carpeta --salida, por defecto picos/):
    picos.jsonl                   un JSON por pico detectado
    <plataforma>_<canal>_<fecha>_muestras.csv   métricas de cada bin
"""
import argparse
import asyncio
import csv
import json
import math
import random
import re
import statistics
import sys
import time
import urllib.parse
import urllib.request
import uuid
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

HYPE = re.compile(
    r"kekw|\blulw?\b|omegalul|\bpog\w*|\bxd+\b|\b(?:ja){2,}\w*|\bje(?:je)+\w*|\bha(?:ha)+\w*"
    r"|\blol\b|\bwtf\b|\bomg\b|\bclip\w*|no way|\bno+o+\b|\bgg\b|\brip\b|monka\w*"
    r"|\bw\b|insane|😂|🤣|💀|🔥|😱",
    re.I,
)
KICK_EMOTE = re.compile(r"\[emote:\d+:([^\]]+)\]")
# Desde octubre de 2026 Kick sirve el chat con Centrifugo (antes era Pusher).
KICK_WS = "wss://realtime.us-east-1.platform.kick.com/connection/websocket"
KICK_TOKEN = "https://web.kick.com/api/v1/realtime/auth/connection"
UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
      "Chrome/124.0 Safari/537.36")


def hms(seg):
    seg = max(0, int(seg))
    return f"{seg // 3600:02d}:{seg % 3600 // 60:02d}:{seg % 60:02d}"


def reloj(epoch):
    return datetime.fromtimestamp(epoch).strftime("%H:%M:%S")


@dataclass
class Muestra:
    t: float                  # fin del bin (epoch)
    msgs: int
    uniq: int
    hype: int
    viewers: float | None
    textos: list = field(default_factory=list)


class Detector:
    PESOS = {"msgs": 0.45, "uniq": 0.20, "hype": 0.20, "viewers": 0.15}

    def __init__(self, umbral=3.0, bin_s=5, antes=20, despues=10, min_s=15,
                 max_s=60, min_msgs=5, enfriar_s=10, historia_s=600, calentar_s=90):
        self.umbral, self.bin_s = umbral, bin_s
        self.antes, self.despues = antes, despues
        self.min_s, self.max_s, self.min_msgs = min_s, max_s, min_msgs
        self.enfriar = max(1, math.ceil(enfriar_s / bin_s))
        self.calentar = max(4, int(calentar_s / bin_s))
        n = max(self.calentar + 1, int(historia_s / bin_s))
        self.hist = {k: deque(maxlen=n) for k in self.PESOS}
        self.ev = None
        self.calma = 0

    def _z(self, clave, x):
        h = self.hist[clave]
        if len(h) < self.calentar:
            return None
        med = statistics.median(h)
        mad = statistics.median([abs(v - med) for v in h])
        piso = {"msgs": max(1.5, 0.25 * med), "uniq": max(1.5, 0.25 * med),
                "hype": max(1.0, 0.25 * med), "viewers": max(2.0, 0.02 * med)}[clave]
        return max(0.0, (x - med) / max(1.4826 * mad, piso))

    def puntaje(self, m):
        valores = {"msgs": m.msgs, "uniq": m.uniq, "hype": m.hype, "viewers": m.viewers}
        num = den = 0.0
        detalle = {}
        for k, x in valores.items():
            if x is None:
                continue
            z = self._z(k, x)
            if z is not None:
                num += self.PESOS[k] * z
                den += self.PESOS[k]
                detalle[k] = round(z, 1)
        for k, x in valores.items():
            if x is not None:
                self.hist[k].append(x)
        return (num / den if den else 0.0), detalle

    def push(self, m):
        """Procesa una muestra. Devuelve (puntaje, pico_cerrado_o_None)."""
        s, detalle = self.puntaje(m)
        cerrado = None
        if self.ev is None:
            if s >= self.umbral and m.msgs >= self.min_msgs:
                self.ev = {"inicio": m.t - self.bin_s, "t_pico": m.t, "pico": s,
                           "ultimo_alto": m.t, "msgs": 0, "textos": [],
                           "v0": m.viewers, "vmax": m.viewers, "detalle": detalle}
                self.calma = 0
        if self.ev is not None:
            ev = self.ev
            ev["msgs"] += m.msgs
            for t in m.textos:
                if len(ev["textos"]) < 5:
                    ev["textos"].append(t)
            if m.viewers is not None:
                ev["vmax"] = max(ev["vmax"] or 0, m.viewers)
            if s > ev["pico"]:
                ev["pico"], ev["t_pico"], ev["detalle"] = s, m.t, detalle
            if s >= self.umbral * 0.5:
                ev["ultimo_alto"] = m.t
                self.calma = 0
            else:
                self.calma += 1
                if self.calma >= self.enfriar:
                    cerrado = self._cerrar()
        return s, cerrado

    def flush(self):
        return self._cerrar() if self.ev else None

    def _cerrar(self):
        ev, self.ev = self.ev, None
        ini = ev["inicio"] - self.antes
        fin = ev["ultimo_alto"] + self.despues
        if fin - ini > self.max_s:
            ini = ev["t_pico"] - 0.6 * self.max_s
            fin = ini + self.max_s
        if fin - ini < self.min_s:
            fin = ini + self.min_s
        return {"ini": ini, "fin": fin, "t_pico": ev["t_pico"],
                "puntaje": round(ev["pico"], 2), "mensajes": ev["msgs"],
                "viewers_inicio": ev["v0"], "viewers_max": ev["vmax"],
                "detalle_z": ev["detalle"], "ejemplos": ev["textos"]}


class Fuente:
    """Un canal de una plataforma: acumula chat y espectadores entre bins."""

    def __init__(self, plataforma, canal):
        self.plataforma, self.canal = plataforma, canal
        self.nombre = f"{plataforma}/{canal}"
        self.t0 = time.time()
        self.inicio_stream = None   # epoch de inicio del directo, si se conoce
        self.viewers = None
        self.chatroom_id = None
        self._reset()

    def _reset(self):
        self.msgs, self.users, self.hype, self.textos = 0, set(), 0, []

    def mensaje(self, usuario, texto):
        self.msgs += 1
        self.users.add(usuario)
        if HYPE.search(texto):
            self.hype += 1
            if len(self.textos) < 3:
                self.textos.append(f"{usuario}: {texto[:80]}")

    def evento(self, peso=3):
        """Subs, regalos, raids: cuentan como interacción fuerte."""
        self.hype += peso

    def cerrar_bin(self, t):
        m = Muestra(t, self.msgs, len(self.users), self.hype, self.viewers, self.textos)
        self._reset()
        return m

    def base_tiempo(self):
        return (self.inicio_stream, "VOD") if self.inicio_stream else (self.t0, "monitor")


# ---------------------------------------------------------------- red

def http_json(url, headers=None, data=None):
    req = urllib.request.Request(url, data=data, headers=headers or {})
    with urllib.request.urlopen(req, timeout=15) as r:
        return json.load(r)


async def chat_twitch(f):
    import websockets
    espera = 2
    while True:
        try:
            async with websockets.connect("wss://irc-ws.chat.twitch.tv:443") as ws:
                await ws.send("CAP REQ :twitch.tv/tags twitch.tv/commands")
                await ws.send("PASS SCHMOOCHIES")
                await ws.send(f"NICK justinfan{random.randint(10000, 99999)}")
                await ws.send(f"JOIN #{f.canal.lower()}")
                print(f"[{f.nombre}] chat conectado")
                espera = 2
                async for raw in ws:
                    for linea in raw.split("\r\n"):
                        if linea.startswith("PING"):
                            await ws.send("PONG :tmi.twitch.tv")
                        elif " PRIVMSG " in linea:
                            try:
                                if linea.startswith("@"):
                                    linea = linea.split(" ", 1)[1]
                                usuario = linea[1:linea.index("!")]
                                texto = linea.split(" PRIVMSG ", 1)[1].split(" :", 1)[1]
                                f.mensaje(usuario, texto)
                            except (ValueError, IndexError):
                                pass
                        elif " USERNOTICE " in linea:
                            f.evento()
        except Exception as e:
            print(f"[{f.nombre}] chat desconectado ({e}); reintento en {espera}s")
        await asyncio.sleep(espera)
        espera = min(espera * 2, 60)


async def viewers_twitch(f, cid, secreto, cada):
    token, vence = None, 0
    while True:
        try:
            if time.time() >= vence:
                datos = urllib.parse.urlencode({
                    "client_id": cid, "client_secret": secreto,
                    "grant_type": "client_credentials"}).encode()
                r = await asyncio.to_thread(http_json, "https://id.twitch.tv/oauth2/token", None, datos)
                token, vence = r["access_token"], time.time() + r["expires_in"] - 300
            url = "https://api.twitch.tv/helix/streams?user_login=" + urllib.parse.quote(f.canal)
            d = await asyncio.to_thread(http_json, url, {
                "Client-Id": cid, "Authorization": f"Bearer {token}"})
            if d["data"]:
                s = d["data"][0]
                f.viewers = s["viewer_count"]
                f.inicio_stream = datetime.fromisoformat(
                    s["started_at"].replace("Z", "+00:00")).timestamp()
            else:
                f.viewers = None
        except Exception as e:
            print(f"[{f.nombre}] error consultando espectadores: {e}")
        await asyncio.sleep(cada)


def _kick_http(url, body=None):
    """GET (o POST JSON si hay body). Usa curl_cffi si está, por Cloudflare."""
    try:
        from curl_cffi import requests as cr
        if body is None:
            r = cr.get(url, impersonate="chrome", timeout=15)
        else:
            r = cr.post(url, json=body, impersonate="chrome", timeout=15)
        r.raise_for_status()
        return r.json()
    except ImportError:
        hdr = {"User-Agent": UA, "Accept": "application/json"}
        if body is None:
            return http_json(url, hdr)
        hdr["Content-Type"] = "application/json"
        return http_json(url, hdr, json.dumps(body).encode())


def kick_info(slug):
    return _kick_http(f"https://kick.com/api/v2/channels/{urllib.parse.quote(slug)}")


def kick_token():
    """Token anónimo (invitado) para leer el chat."""
    return _kick_http(KICK_TOKEN, {"client_id": str(uuid.uuid4())})["data"]["token"]


async def sondeo_kick(f, cada):
    avisado = False
    while True:
        try:
            info = await asyncio.to_thread(kick_info, f.canal)
            if f.chatroom_id is None:
                f.chatroom_id = info["chatroom"]["id"]
            live = info.get("livestream")
            f.viewers = live["viewer_count"] if live else None
            if live and live.get("start_time"):
                f.inicio_stream = datetime.strptime(
                    live["start_time"], "%Y-%m-%d %H:%M:%S").replace(
                    tzinfo=timezone.utc).timestamp()
            if live is None and not avisado:
                print(f"[{f.nombre}] el canal no está en directo ahora mismo")
                avisado = True
        except Exception as e:
            if not avisado and f.chatroom_id is None:
                print(f"[{f.nombre}] no pude consultar la API de Kick ({e}). "
                      "Prueba pip install curl_cffi o pasa --kick-chatroom-id.")
                avisado = True
        await asyncio.sleep(cada)


def _kick_evento(f, ev, datos):
    if ev.endswith("ChatMessageEvent"):
        d = json.loads(datos) if isinstance(datos, str) else datos
        texto = KICK_EMOTE.sub(r"\1", d.get("content", ""))
        f.mensaje(d["sender"]["username"], texto)
    elif ev.endswith(("SubscriptionEvent", "GiftedSubscriptionsEvent", "StreamHostEvent")):
        f.evento()


async def chat_kick(f):
    import websockets
    espera = 2
    while f.chatroom_id is None:
        await asyncio.sleep(1)
    while True:
        try:
            token = await asyncio.to_thread(kick_token)
            async with websockets.connect(KICK_WS, origin="https://kick.com",
                                          user_agent_header=UA) as ws:
                await ws.send(json.dumps({"connect": {"token": token}, "id": 1}))
                resp = json.loads(await asyncio.wait_for(ws.recv(), 15))
                if "error" in resp:
                    raise RuntimeError(f"connect rechazado: {resp['error']}")
                await ws.send(json.dumps({"subscribe": {
                    "channel": f"chatrooms.{f.chatroom_id}.v2"}, "id": 2}))
                print(f"[{f.nombre}] chat conectado")
                espera = 2
                async for raw in ws:
                    # Centrifugo puede mandar varios JSON por trama, uno por línea
                    for linea in raw.splitlines():
                        linea = linea.strip()
                        if not linea:
                            continue
                        if linea == "{}":          # ping del servidor
                            await ws.send("{}")
                            continue
                        msg = json.loads(linea)
                        if "error" in msg:
                            print(f"[{f.nombre}] error de Kick: {msg['error']}")
                            continue
                        pub = msg.get("push", {}).get("pub", {}).get("data")
                        if isinstance(pub, dict):
                            try:
                                _kick_evento(f, pub.get("event", ""), pub.get("data", "{}"))
                            except (KeyError, ValueError, TypeError):
                                pass
        except Exception as e:
            print(f"[{f.nombre}] chat desconectado ({e}); reintento en {espera}s")
        await asyncio.sleep(espera)
        espera = min(espera * 2, 60)


# ---------------------------------------------------------------- salida

def anunciar(f, pico, salida_jsonl):
    base, etiqueta = f.base_tiempo()
    ini, fin = pico["ini"] - base, pico["fin"] - base
    registro = {"fuente": f.nombre, "reloj_ini": reloj(pico["ini"]), "reloj_fin": reloj(pico["fin"]),
                f"{etiqueta}_ini": hms(ini), f"{etiqueta}_fin": hms(fin),
                "duracion_s": round(pico["fin"] - pico["ini"]), **{
                    k: pico[k] for k in ("puntaje", "mensajes", "viewers_inicio",
                                         "viewers_max", "detalle_z", "ejemplos")}}
    with open(salida_jsonl, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(registro, ensure_ascii=False) + "\n")
    print(f"\n>>> PICO [{f.nombre}] puntaje {pico['puntaje']}  "
          f"clip sugerido {etiqueta} {hms(ini)} - {hms(fin)} "
          f"({registro['duracion_s']} s, reloj {registro['reloj_ini']}-{registro['reloj_fin']})")
    if pico["viewers_inicio"] is not None:
        print(f"    espectadores {pico['viewers_inicio']} -> {pico['viewers_max']}")
    print(f"    mensajes en el pico: {pico['mensajes']}  componentes z: {pico['detalle_z']}")
    for t in pico["ejemplos"][:3]:
        print(f"    · {t}")
    print()


async def ciclo(f, det, a, salida):
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    ruta_csv = salida / f"{f.plataforma}_{f.canal}_{stamp}_muestras.csv"
    ruta_jsonl = salida / "picos.jsonl"
    ultimo_estado = 0
    with open(ruta_csv, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["reloj", "mensajes", "unicos", "hype", "viewers", "puntaje"])
        try:
            while True:
                await asyncio.sleep(a.bin - (time.time() % a.bin))
                m = f.cerrar_bin(time.time())
                s, pico = det.push(m)
                w.writerow([reloj(m.t), m.msgs, m.uniq, m.hype, m.viewers, round(s, 2)])
                fh.flush()
                if pico:
                    anunciar(f, pico, ruta_jsonl)
                if a.verbose or m.t - ultimo_estado >= 30:
                    ultimo_estado = m.t
                    print(f"[{reloj(m.t)}] {f.nombre}: {m.msgs} msgs, {m.uniq} chatters, "
                          f"{m.hype} hype, viewers={m.viewers}, puntaje={s:.1f}")
        finally:
            pico = det.flush()
            if pico:
                anunciar(f, pico, ruta_jsonl)


def crear_detector(a):
    return Detector(a.umbral, a.bin, a.antes, a.despues, a.min_duracion,
                    a.max_duracion, a.min_msgs)


async def monitorear(a):
    import os
    salida = Path(a.salida)
    salida.mkdir(parents=True, exist_ok=True)
    tareas = []
    cid, secreto = os.environ.get("TWITCH_CLIENT_ID"), os.environ.get("TWITCH_CLIENT_SECRET")
    for canal in a.twitch:
        f = Fuente("twitch", canal)
        tareas += [chat_twitch(f), ciclo(f, crear_detector(a), a, salida)]
        if cid and secreto:
            tareas.append(viewers_twitch(f, cid, secreto, a.sondeo))
        else:
            print(f"[{f.nombre}] sin TWITCH_CLIENT_ID/SECRET: se usa solo el chat")
    for canal in a.kick:
        f = Fuente("kick", canal)
        f.chatroom_id = a.kick_chatroom_id
        tareas += [chat_kick(f), sondeo_kick(f, a.sondeo), ciclo(f, crear_detector(a), a, salida)]
    print("Monitoreando... Ctrl+C para terminar. "
          f"Umbral {a.umbral}, bin {a.bin}s, calentamiento ~90s.")
    await asyncio.gather(*tareas)


# ---------------------------------------------------------------- demo

def demo(a):
    """Chat sintético con dos ráfagas, para probar el detector sin red."""
    random.seed(7)
    det = crear_detector(a)
    t, n_bins = 1_700_000_000.0, 360
    rafagas = {(120, 134): 5, (260, 270): 8}
    espectadores = 800.0
    cierres = []
    for i in range(n_bins):
        factor = next((k for (ini, fin), k in rafagas.items() if ini <= i < fin), 1)
        base = max(0, int(random.gauss(8, 2.5)))
        msgs = base * factor
        uniq = min(msgs, int(msgs * (0.7 if factor == 1 else 0.85)))
        hype = int(msgs * (0.05 if factor == 1 else 0.4))
        if factor > 1:
            espectadores *= 1.01
        espectadores += random.gauss(0, 3)
        t += a.bin
        _, pico = det.push(Muestra(t, msgs, uniq, hype, round(espectadores)))
        if pico:
            cierres.append(pico)
    pico = det.flush()
    if pico:
        cierres.append(pico)
    t0 = 1_700_000_000.0
    print(f"Ráfagas inyectadas en 00:{120 * a.bin // 60:02d}:{120 * a.bin % 60:02d} y "
          f"00:{260 * a.bin // 60:02d}:{260 * a.bin % 60:02d} (desde el inicio)")
    for p in cierres:
        print(f"Pico detectado: {hms(p['ini'] - t0)} - {hms(p['fin'] - t0)} "
              f"({round(p['fin'] - p['ini'])} s) puntaje {p['puntaje']}")
    return 0 if len(cierres) == 2 else 1


def main():
    ap = argparse.ArgumentParser(description="Detector de picos de chat/audiencia para clips")
    ap.add_argument("--twitch", nargs="*", default=[], metavar="CANAL")
    ap.add_argument("--kick", nargs="*", default=[], metavar="CANAL")
    ap.add_argument("--kick-chatroom-id", type=int, help="ID del chatroom de Kick si la API falla")
    ap.add_argument("--bin", type=int, default=5, help="segundos por muestra")
    ap.add_argument("--umbral", type=float, default=3.0, help="puntaje mínimo de pico (menor = más sensible)")
    ap.add_argument("--antes", type=int, default=20, help="segundos extra antes del pico")
    ap.add_argument("--despues", type=int, default=10, help="segundos extra después del pico")
    ap.add_argument("--min-duracion", type=int, default=15)
    ap.add_argument("--max-duracion", type=int, default=60)
    ap.add_argument("--min-msgs", type=int, default=5, help="mensajes mínimos por bin para abrir un pico")
    ap.add_argument("--sondeo", type=int, default=20, help="segundos entre consultas de espectadores")
    ap.add_argument("--salida", default="picos")
    ap.add_argument("--verbose", action="store_true", help="imprimir cada bin")
    ap.add_argument("--demo", action="store_true", help="probar el detector con datos sintéticos")
    a = ap.parse_args()

    if a.demo:
        sys.exit(demo(a))
    if not (a.twitch or a.kick):
        ap.error("indica al menos un canal con --twitch o --kick (o usa --demo)")
    try:
        asyncio.run(monitorear(a))
    except KeyboardInterrupt:
        print("\nTerminado.")


if __name__ == "__main__":
    main()
