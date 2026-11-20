#!/usr/bin/env python3
"""
Panel web para monitor_directos.py: añadir canales de Twitch y Kick, pausarlos,
quitarlos y ver los picos detectados, todo desde el navegador.

Uso:
    export PANEL_CLAVE="una-contraseña-larga"
    python3 panel.py                       # http://127.0.0.1:8080 (solo esta máquina)
    python3 panel.py --host 0.0.0.0        # accesible desde fuera (ver README, usa HTTPS)

El usuario del navegador puede ser cualquiera; lo que se comprueba es la contraseña.
Los canales se guardan en panel_canales.json y se retoman al reiniciar.
Los picos se escriben en picos/picos.jsonl, igual que con monitor_directos.py.

Requisitos: pip install aiohttp websockets curl_cffi
"""
import argparse
import asyncio
import contextlib
import hmac
import ipaddress
import json
import os
import re
import secrets
import sys
import time
from base64 import b64decode
from pathlib import Path

from aiohttp import web

import monitor_directos as md

NOMBRE_OK = {"twitch": re.compile(r"^[A-Za-z0-9_]{2,25}$"),
             "kick": re.compile(r"^[A-Za-z0-9_-]{2,30}$")}


class Gestor:
    """Arranca y detiene el monitoreo de cada canal dentro del mismo proceso."""

    def __init__(self, opciones, ruta_estado):
        self.a = opciones
        self.salida = Path(opciones.salida)
        self.salida.mkdir(parents=True, exist_ok=True)
        self.ruta_estado = ruta_estado
        self.canales = {}   # (plataforma, canal) -> {"activo", "f", "task", "desde"}
        self.cid = os.environ.get("TWITCH_CLIENT_ID")
        self.secreto = os.environ.get("TWITCH_CLIENT_SECRET")

    # ---- persistencia
    def cargar(self):
        if not self.ruta_estado.exists():
            return
        for c in json.loads(self.ruta_estado.read_text(encoding="utf-8")):
            clave = (c["plataforma"], c["canal"])
            self.canales[clave] = {"activo": False, "f": None, "task": None, "desde": None}
            if c.get("activo"):
                self._iniciar(clave)

    def guardar(self):
        datos = [{"plataforma": p, "canal": c, "activo": v["activo"]}
                 for (p, c), v in self.canales.items()]
        tmp = self.ruta_estado.with_suffix(".tmp")
        tmp.write_text(json.dumps(datos, indent=2), encoding="utf-8")
        tmp.replace(self.ruta_estado)

    # ---- control
    def _iniciar(self, clave):
        plataforma, canal = clave
        f = md.Fuente(plataforma, canal)
        det = md.crear_detector(self.a)
        if plataforma == "twitch":
            coros = [md.chat_twitch(f), md.ciclo(f, det, self.a, self.salida)]
            if self.cid and self.secreto:
                coros.append(md.viewers_twitch(f, self.cid, self.secreto, self.a.sondeo))
        else:
            coros = [md.chat_kick(f), md.sondeo_kick(f, self.a.sondeo),
                     md.ciclo(f, det, self.a, self.salida)]

        async def correr():
            await asyncio.gather(*coros)

        v = self.canales[clave]
        v.update(activo=True, f=f, task=asyncio.create_task(correr()), desde=time.time())
        print(f"[panel] monitoreando {f.nombre}")

    async def _detener(self, clave):
        v = self.canales[clave]
        if v["task"]:
            v["task"].cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await v["task"]
            print(f"[panel] detenido {plataforma_canal(clave)}")
        v.update(activo=False, task=None)

    async def agregar(self, clave):
        if clave not in self.canales:
            self.canales[clave] = {"activo": False, "f": None, "task": None, "desde": None}
        if not self.canales[clave]["activo"]:
            self._iniciar(clave)
        self.guardar()

    async def pausar(self, clave):
        await self._detener(clave)
        self.guardar()

    async def reanudar(self, clave):
        if not self.canales[clave]["activo"]:
            self._iniciar(clave)
        self.guardar()

    async def quitar(self, clave):
        await self._detener(clave)
        del self.canales[clave]
        self.guardar()

    async def pausar_todos(self):
        for clave in list(self.canales):
            await self._detener(clave)
        self.guardar()

    async def cerrar(self):
        for clave in list(self.canales):
            v = self.canales[clave]
            if v["task"]:
                v["task"].cancel()
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await v["task"]

    # ---- lectura
    def estado(self):
        filas = []
        for (p, c), v in self.canales.items():
            f, u = v["f"], (v["f"].ultimo if v["f"] else None)
            if v["task"] and v["task"].done() and not v["task"].cancelled():
                chat = f"detenido por error: {v['task'].exception()}"
            else:
                chat = f.chat if (f and v["activo"]) else "-"
            filas.append({
                "plataforma": p, "canal": c, "activo": v["activo"], "chat": chat,
                "desde": v["desde"] if v["activo"] else None,
                "msgs": u["msgs"] if u else None, "viewers": u["viewers"] if u else None,
                "puntaje": u["puntaje"] if u else None,
                "calentando": u["calentando"] if u else v["activo"],
                "en_pico": u["en_pico"] if u else False,
                "picos": f.n_picos if f else 0,
            })
        return filas

    def picos(self, n=30):
        ruta = self.salida / "picos.jsonl"
        if not ruta.exists():
            return []
        with open(ruta, "rb") as fh:
            fh.seek(0, 2)
            fh.seek(max(0, fh.tell() - 256 * 1024))
            lineas = fh.read().decode("utf-8", errors="replace").splitlines()[-n:]
        salida = []
        for l in reversed(lineas):
            with contextlib.suppress(ValueError):
                salida.append(json.loads(l))
        return salida


def plataforma_canal(clave):
    return f"{clave[0]}/{clave[1]}"


# ---------------------------------------------------------------- web

PAGINA = """<!doctype html>
<html lang="es"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Monitor de directos</title>
<style>
  body { font-family: system-ui, sans-serif; margin: 0; background: #14161a; color: #e6e6e6; }
  main { max-width: 1100px; margin: 0 auto; padding: 20px; }
  h1 { font-size: 1.4rem; margin: 0 0 16px; }
  h2 { font-size: 1.1rem; margin: 28px 0 10px; }
  form, .barra { display: flex; gap: 8px; flex-wrap: wrap; align-items: center; }
  input, select, button { font: inherit; padding: 7px 10px; border-radius: 6px;
    border: 1px solid #3a3f47; background: #1e2127; color: inherit; }
  button { cursor: pointer; background: #2b3038; }
  button:hover { background: #363c46; }
  button.prim { background: #2f6fd6; border-color: #2f6fd6; }
  button.peligro { color: #ff9b9b; }
  table { width: 100%; border-collapse: collapse; font-size: .92rem; }
  th, td { text-align: left; padding: 7px 8px; border-bottom: 1px solid #2a2e35; vertical-align: top; }
  th { color: #9aa3ad; font-weight: 600; }
  .ok { color: #7ddc8a; } .warn { color: #f0c36b; } .off { color: #8a929c; } .pico { color: #ff7a7a; font-weight: 700; }
  .ejemplo { color: #9aa3ad; font-size: .85rem; }
  #error { color: #ff9b9b; min-height: 1.2em; }
  .vacio { color: #8a929c; padding: 12px 8px; }
</style></head>
<body><main>
<h1>Monitor de directos</h1>
<form id="nuevo">
  <select id="plataforma"><option value="twitch">Twitch</option><option value="kick">Kick</option></select>
  <input id="canal" placeholder="usuario del canal" required autocomplete="off">
  <button class="prim" type="submit">Añadir y monitorear</button>
  <button type="button" id="todos" class="peligro">Pausar todos</button>
  <a href="picos.jsonl" download style="color:#8fb4ff;margin-left:auto">Descargar picos.jsonl</a>
</form>
<p id="error"></p>
<h2>Canales</h2>
<table><thead><tr><th>Canal</th><th>Estado</th><th>Chat</th><th>Msgs/5 s</th>
<th>Espectadores</th><th>Puntaje</th><th>Picos</th><th></th></tr></thead>
<tbody id="canales"></tbody></table>
<h2>Últimos picos</h2>
<table><thead><tr><th>Canal</th><th>Hora</th><th>Rango sugerido</th><th>Duración</th>
<th>Puntaje</th><th>Mensajes</th><th>Ejemplo del chat</th></tr></thead>
<tbody id="picos"></tbody></table>
</main>
<script>
const CSRF = "__CSRF__";
const $ = (id) => document.getElementById(id);
function el(tag, texto, clase) {
  const e = document.createElement(tag);
  if (texto !== undefined && texto !== null) e.textContent = texto;   // nunca innerHTML: el chat no es de fiar
  if (clase) e.className = clase;
  return e;
}
async function accion(datos) {
  $("error").textContent = "";
  const r = await fetch("api/canal", { method: "POST",
    headers: { "Content-Type": "application/json", "X-CSRF-Token": CSRF },
    body: JSON.stringify(datos) });
  if (!r.ok) $("error").textContent = (await r.json().catch(() => ({}))).error || ("Error " + r.status);
  await refrescar();
}
function boton(texto, datos, clase) {
  const b = el("button", texto, clase);
  b.onclick = () => accion(datos);
  return b;
}
function duracion(seg) {
  const m = Math.floor(seg / 60), h = Math.floor(m / 60);
  return h ? `${h} h ${m % 60} min` : `${m} min`;
}
async function refrescar() {
  const r = await fetch("api/estado", { cache: "no-store" });
  if (!r.ok) { $("error").textContent = "No pude leer el estado (" + r.status + ")"; return; }
  const d = await r.json();
  const tb = $("canales"); tb.replaceChildren();
  if (!d.canales.length) {
    const tr = el("tr"); const td = el("td", "Todavía no hay canales. Añade uno arriba.", "vacio");
    td.colSpan = 8; tr.append(td); tb.append(tr);
  }
  for (const c of d.canales) {
    const tr = el("tr");
    const id = { plataforma: c.plataforma, canal: c.canal };
    tr.append(el("td", `${c.plataforma}/${c.canal}`));
    let est;
    if (!c.activo) est = el("td", "pausado", "off");
    else if (c.en_pico) est = el("td", "¡pico en curso!", "pico");
    else if (c.calentando) est = el("td", "calentando (~90 s)", "warn");
    else est = el("td", "monitoreando · " + duracion(d.ahora - c.desde), "ok");
    tr.append(est);
    tr.append(el("td", c.chat, c.chat === "conectado" ? "ok" : (c.activo ? "warn" : "off")));
    tr.append(el("td", c.msgs ?? "-"));
    tr.append(el("td", c.viewers ?? "-"));
    tr.append(el("td", c.puntaje ?? "-"));
    tr.append(el("td", c.picos));
    const acc = el("td");
    acc.append(c.activo ? boton("Pausar", { ...id, accion: "pausar" })
                        : boton("Reanudar", { ...id, accion: "reanudar" }), " ");
    const q = el("button", "Quitar", "peligro");
    q.onclick = () => confirm(`¿Quitar ${c.plataforma}/${c.canal}? Los picos ya guardados se conservan.`)
                      && accion({ ...id, accion: "quitar" });
    acc.append(q); tr.append(acc); tb.append(tr);
  }
  const tp = $("picos"); tp.replaceChildren();
  if (!d.picos.length) {
    const tr = el("tr"); const td = el("td", "Sin picos todavía.", "vacio");
    td.colSpan = 7; tr.append(td); tp.append(tr);
  }
  for (const p of d.picos) {
    const tr = el("tr");
    const base = p.VOD_ini !== undefined ? "VOD" : "monitor";
    tr.append(el("td", p.fuente), el("td", `${p.reloj_ini} - ${p.reloj_fin}`),
              el("td", `${base} ${p[base + "_ini"]} - ${p[base + "_fin"]}`),
              el("td", p.duracion_s + " s"), el("td", p.puntaje), el("td", p.mensajes));
    tr.append(el("td", (p.ejemplos || [])[0] || "", "ejemplo"));
    tp.append(tr);
  }
}
$("nuevo").onsubmit = (e) => {
  e.preventDefault();
  const canal = $("canal").value.trim().replace(/^@/, "");
  if (canal) accion({ accion: "agregar", plataforma: $("plataforma").value, canal });
  $("canal").value = "";
};
$("todos").onclick = () => confirm("¿Pausar todos los canales?") && accion({ accion: "pausar_todos" });
refrescar(); setInterval(refrescar, 5000);
</script></body></html>
"""


def crear_app(gestor, clave):
    csrf = secrets.token_urlsafe(32)

    @web.middleware
    async def autenticar(request, handler):
        if clave is not None:
            ok = False
            cab = request.headers.get("Authorization", "")
            if cab.startswith("Basic "):
                with contextlib.suppress(Exception):
                    _, _, pw = b64decode(cab[6:]).decode("utf-8").partition(":")
                    ok = hmac.compare_digest(pw.encode(), clave.encode())
            if not ok:
                await asyncio.sleep(1)   # frena los intentos por fuerza bruta
                return web.Response(status=401, text="Se necesita contraseña",
                                    headers={"WWW-Authenticate": 'Basic realm="monitor", charset="UTF-8"'})
        resp = await handler(request)
        resp.headers["X-Frame-Options"] = "DENY"
        resp.headers["X-Content-Type-Options"] = "nosniff"
        resp.headers["Referrer-Policy"] = "no-referrer"
        return resp

    async def inicio(request):
        return web.Response(text=PAGINA.replace("__CSRF__", csrf), content_type="text/html",
                            headers={"Cache-Control": "no-store"})

    async def api_estado(request):
        return web.json_response({"ahora": time.time(), "canales": gestor.estado(),
                                  "picos": gestor.picos()})

    async def api_canal(request):
        if not hmac.compare_digest(request.headers.get("X-CSRF-Token", ""), csrf):
            return web.json_response({"error": "token inválido; recarga la página"}, status=403)
        try:
            d = await request.json()
        except ValueError:
            return web.json_response({"error": "JSON inválido"}, status=400)
        acc = d.get("accion")
        if acc == "pausar_todos":
            await gestor.pausar_todos()
            return web.json_response({"ok": True})
        plataforma, canal = d.get("plataforma"), str(d.get("canal", "")).strip()
        if plataforma not in NOMBRE_OK or not NOMBRE_OK[plataforma].match(canal):
            return web.json_response({"error": "nombre de canal no válido"}, status=400)
        clave_c = (plataforma, canal.lower())
        if acc == "agregar":
            await gestor.agregar(clave_c)
        elif clave_c not in gestor.canales:
            return web.json_response({"error": "ese canal no está en la lista"}, status=404)
        elif acc == "pausar":
            await gestor.pausar(clave_c)
        elif acc == "reanudar":
            await gestor.reanudar(clave_c)
        elif acc == "quitar":
            await gestor.quitar(clave_c)
        else:
            return web.json_response({"error": "acción desconocida"}, status=400)
        return web.json_response({"ok": True})

    async def descargar_jsonl(request):
        ruta = gestor.salida / "picos.jsonl"
        if not ruta.exists():
            return web.Response(text="", content_type="application/x-ndjson")
        return web.FileResponse(ruta, headers={
            "Content-Disposition": 'attachment; filename="picos.jsonl"'})

    async def al_arrancar(app):
        gestor.cargar()

    async def al_cerrar(app):
        await gestor.cerrar()

    app = web.Application(middlewares=[autenticar], client_max_size=16 * 1024)
    app.add_routes([web.get("/", inicio), web.get("/api/estado", api_estado),
                    web.post("/api/canal", api_canal), web.get("/picos.jsonl", descargar_jsonl)])
    app.on_startup.append(al_arrancar)
    app.on_cleanup.append(al_cerrar)
    return app


def es_local(host):
    with contextlib.suppress(ValueError):
        return ipaddress.ip_address(host).is_loopback
    return host == "localhost"


def main():
    ap = argparse.ArgumentParser(description="Panel web del monitor de directos")
    ap.add_argument("--host", default="127.0.0.1",
                    help="127.0.0.1 = solo esta máquina; 0.0.0.0 = accesible desde fuera")
    ap.add_argument("--port", type=int, default=8080)
    ap.add_argument("--salida", default="picos", help="carpeta de picos.jsonl y CSV")
    ap.add_argument("--estado", default="panel_canales.json", help="archivo con la lista de canales")
    ap.add_argument("--umbral", type=float, default=3.0, help="igual que en monitor_directos.py")
    ap.add_argument("--sin-clave", action="store_true",
                    help="no pedir contraseña (solo se permite con --host 127.0.0.1)")
    a = ap.parse_args()

    clave = os.environ.get("PANEL_CLAVE")
    if a.sin_clave:
        if not es_local(a.host):
            sys.exit("--sin-clave solo se permite escuchando en 127.0.0.1")
        clave = None
    elif not clave or len(clave) < 10:
        sys.exit("Define la contraseña del panel (10+ caracteres):\n"
                 '    export PANEL_CLAVE="una-contraseña-larga"')
    # En Docker escucha en 0.0.0.0 dentro del contenedor, pero compose solo
    # publica el puerto en 127.0.0.1 del host: ahí el aviso no aplica.
    if not es_local(a.host) and not os.environ.get("PANEL_EN_CONTENEDOR"):
        print("Aviso: el panel es accesible desde fuera. Sin HTTPS la contraseña viaja "
              "sin cifrar; usa un túnel SSH o un proxy con HTTPS (ver README).")

    opciones = md.crear_parser().parse_args([])
    opciones.salida, opciones.umbral = a.salida, a.umbral
    gestor = Gestor(opciones, Path(a.estado))
    print(f"Panel en http://{a.host}:{a.port}")
    web.run_app(crear_app(gestor, clave), host=a.host, port=a.port, print=None)


if __name__ == "__main__":
    main()
