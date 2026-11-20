# Clips virales: detectar, cortar, reencuadrar y subtitular

Herramientas en Python para convertir directos y videos largos en clips verticales (9:16) con subtítulos.

| Herramienta | Qué hace |
|---|---|
| `monitor_directos.py` | Vigila el chat y la audiencia de un directo (Twitch / Kick) y avisa en qué rangos de tiempo hubo picos, o sea, posibles clips virales. |
| `hacer_clips.py` | Corta un video en clips de 40 s, los pasa a vertical, los transcribe con Whisper, traduce si hace falta y pega los subtítulos. |
| `descargar_picos.py` | Baja desde el VOD de Twitch los picos que anotó el monitor. |
| `panel.py` | Panel web para añadir, pausar y quitar canales del monitor desde el navegador. Pensado para dejarlo en un VPS. |
| `seguir_caras.py` | Reencuadre vertical que sigue a la persona que habla (se usa desde `hacer_clips.py` con `--seguir-caras` o por separado). |
| `docs/guia_manual.md` | Guía paso a paso con comandos sueltos de yt-dlp, FFmpeg y Whisper. |

Flujo completo recomendado:

```
monitor_directos.py  ->  descargar el VOD/fragmento (yt-dlp)  ->  hacer_clips.py
   (dónde hay clip)          (el video del rango)                  (clip vertical con subs)
```

---

## 1. Instalación

Probado en Ubuntu/Linux. En Windows debería funcionar con los mismos pasos (activar el entorno con `.venv\Scripts\activate`), pero no está probado.

### 1.1 Programas del sistema

```bash
sudo apt update
sudo apt install ffmpeg python3-venv git
ffmpeg -version
```

Para descargar videos de YouTube con `yt-dlp` mira la sección 2 de [`docs/guia_manual.md`](docs/guia_manual.md).

### 1.2 Entorno de Python

**Usa Python 3.10 a 3.12.** `mediapipe==0.10.14` no tiene paquetes para Python 3.13. Revisa con `python3 --version`.

```bash
git clone https://github.com/EDGR2005/clips-virales.git
cd clips-virales

python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

`mediapipe` está fijado a la versión 0.10.14 a propósito. Las versiones nuevas quitaron la API `mp.solutions` que usa `seguir_caras.py` y dan el error `module 'mediapipe' has no attribute 'solutions'`.

Cada vez que abras una terminal nueva, activa el entorno: `source .venv/bin/activate`.

---

## 2. Usar la tarjeta gráfica (NVIDIA)

Lo único que usa la GPU es **Whisper** (la transcripción), y es donde más se nota. El seguimiento de caras (MediaPipe) y FFmpeg corren en CPU.

1. Comprueba el driver: `nvidia-smi`.
2. Comprueba que PyTorch ve la tarjeta:

   ```bash
   python3 -c "import torch; print(torch.cuda.is_available(), torch.version.cuda)"
   ```

   - `True`: ya está. Whisper usará la GPU sin que hagas nada más.
   - `False`: tienes un PyTorch solo para CPU. Instala la versión con CUDA siguiendo el selector de https://pytorch.org/get-started/locally/ (elige Pip + tu versión de CUDA) y repite la comprobación.
3. Con GPU puedes usar un modelo más preciso. Referencia de memoria de video (VRAM) que indica el proyecto Whisper:

   | Modelo | VRAM aprox. | Uso recomendado |
   |---|---|---|
   | `tiny`, `base` | ~1 GB | Pruebas rápidas |
   | `small` | ~2 GB | Buen equilibrio |
   | `medium` | ~5 GB | Mejor precisión en español |
   | `large` | ~10 GB | Máxima precisión |

   ```bash
   python3 hacer_clips.py video.mp4 --idioma-origen es --modelo medium
   ```

Si Whisper se queda sin memoria (`CUDA out of memory`), baja al modelo anterior.

---

## 3. `hacer_clips.py`: clips verticales con subtítulos

Por cada clip de 40 s hace: cortar, pasar a 9:16, transcribir (Whisper), traducir al español si el idioma original no es español, y pegar los subtítulos.

```bash
# Todos los clips consecutivos de 40 s, video en español
python3 hacer_clips.py video.mp4 --idioma-origen es

# Probar con un solo clip
python3 hacer_clips.py video.mp4 --idioma-origen es --clips 1

# Empezar en un minuto concreto y que el encuadre siga a quien habla
python3 hacer_clips.py video.mp4 --idioma-origen es --inicio 00:10:30 --clips 3 --seguir-caras

# Video en inglés: se traduce a español
python3 hacer_clips.py video.mp4 --idioma-origen en --modelo small
```

| Opción | Por defecto | Qué hace |
|---|---|---|
| `video` | (obligatorio) | Video de entrada, horizontal. |
| `--inicio HH:MM:SS` | `0` | Dónde empieza el primer clip. |
| `--clips N` | `0` (hasta el final) | Cantidad de clips. |
| `--idioma-origen` | `en` | Idioma hablado (`es`, `en`, ...). Con `es` no se traduce. |
| `--modelo` | `base` | Modelo de Whisper (ver tabla de la sección 2). |
| `--salida` | `clips` | Carpeta de salida. |
| `--seguir-caras` | apagado | Sigue a quien habla en vez de recortar el centro. |

Resultados en `clips/`:
- `clipN_subs.mp4`: el clip final con subtítulos.
- `clipN.mp4`: el clip vertical sin subtítulos.
- `clipN.srt`: subtítulos en el idioma original. Se pueden corregir a mano.
- `clipN_es.srt`: subtítulos traducidos (solo si el idioma original no es español).

Notas:
- La duración (40 s) está en la constante `DURACION` al inicio de `hacer_clips.py`.
- La primera vez que traduce, `argostranslate` descarga el paquete de idioma, así que necesita internet. Whisper también descarga el modelo la primera vez.
- El estilo de los subtítulos (tamaño, posición) está en la línea `force_style` del script. `MarginV` sube o baja el texto.

---

## 4. `seguir_caras.py`: reencuadre que sigue a quien habla

Detecta hasta 4 caras con MediaPipe, decide quién habla según el movimiento de la boca y desliza el recorte 9:16 hacia esa persona.

Se activa solo con `--seguir-caras` en `hacer_clips.py`, o se usa directo sobre cualquier video horizontal:

```bash
python3 seguir_caras.py entrada.mp4 salida_vertical.mp4
```

Es lento porque procesa cuadro a cuadro en CPU. Prueba primero con un clip corto.

Parámetros ajustables (constantes al inicio del archivo):

| Constante | Efecto |
|---|---|
| `CADA` | Analiza 1 de cada N cuadros. Más alto = más rápido, menos preciso. |
| `ALPHA` | Suavidad de la cámara. Menor = movimientos más lentos. |
| `ZONA_MUERTA` | Ignora desplazamientos pequeños para evitar temblores. |
| `MIN_ENTRE_CAMBIOS` | Segundos mínimos antes de pasar a otra persona. |

Limitaciones: si no hay ninguna cara visible, el encuadre se queda en la última posición conocida (o en el centro al principio). Con una sola persona, la sigue siempre.

---

## 5. `monitor_directos.py`: detectar picos para clips virales

Se conecta al chat de Twitch y Kick y mide cada 5 s: mensajes, chatters únicos, mensajes "hype" (KEKW, jaja, OMG, clip, emojis de risa) y espectadores. Compara cada muestra con los últimos 10 minutos y combina todo en un puntaje. Cuando el puntaje pasa el umbral abre un pico, y cuando el chat se calma entrega un rango sugerido para el clip.

```bash
# Un canal de cada plataforma
python3 monitor_directos.py --twitch nombre_canal --kick nombre_canal

# Más sensible y con más margen antes del momento
python3 monitor_directos.py --twitch nombre_canal --umbral 2.5 --antes 30

# Probar el detector sin conexión (datos sintéticos)
python3 monitor_directos.py --demo
```

Déjalo corriendo mientras dura el directo. `Ctrl+C` lo termina.

### 5.1 Guardar los clips automáticamente (`--grabar`)

Con `--grabar`, el monitor graba el directo con `yt-dlp` mientras vigila el chat. Cuando se cierra un pico, corta ese rango de la grabación y lo guarda como `.mp4`, sin que hagas nada.

```bash
python3 monitor_directos.py --twitch nombre_canal --grabar
python3 monitor_directos.py --twitch nombre_canal --grabar --calidad "best[height<=720]" --borrar-grabacion
```

| Opción | Por defecto | Qué hace |
|---|---|---|
| `--grabar` | apagado | Graba el directo y corta un clip por cada pico. |
| `--calidad` | `best[height<=1080]/best` | Formato de yt-dlp. `best[height<=720]` ocupa la mitad. |
| `--borrar-grabacion` | apagado | Al terminar borra la grabación completa y conserva solo los clips. |

Resultados en `picos/`:
- `clips/<plataforma>_<canal>_<fecha_hora>_p<puntaje>.mp4`: un clip horizontal por pico, listo para pasarlo a `hacer_clips.py`.
- `<plataforma>_<canal>_<fecha_hora>.ts`: la grabación completa del directo.
- `picos.jsonl`: cada pico incluye el campo `clip` con la ruta del archivo.

Cómo funciona y qué tener en cuenta:
- El clip se corta unos 8 s después de terminar el pico, para asegurar que ese tramo ya está grabado.
- La grabación empieza cuando lanzas el monitor, no desde el inicio del directo. Si el canal todavía no está en vivo, reintenta cada 60 s. Si el directo se corta, abre una grabación nueva.
- **Disco:** a 1080p60 son unos 3,5 GB por hora y a 720p unos 1,5 GB por hora. Revisa el espacio libre antes de dejarlo toda la noche.
- Un pico que sigue abierto al pulsar `Ctrl+C` queda anotado en `picos.jsonl`, pero sin clip.
- La alineación entre chat y video es aproximada, del orden de unos pocos segundos. El margen de `--antes` lo cubre.
- Sin suscripción, los anuncios de Twitch pueden quedar dentro de la grabación.
- Solo está probado con Twitch. La grabación de Kick usa el mismo mecanismo de yt-dlp, pero no está probada.

Para pasarlos a vertical con subtítulos, ejecuta `hacer_clips.py` sobre cada clip. Como esos clips suelen durar menos de 40 s, baja antes la constante `DURACION` de `hacer_clips.py`, o el script dirá que el video no alcanza.

| Opción | Por defecto | Qué hace |
|---|---|---|
| `--twitch CANAL ...` | | Canales de Twitch. |
| `--kick CANAL ...` | | Canales de Kick. |
| `--kick-chatroom-id N` | | ID del chatroom de Kick si su API falla. |
| `--umbral` | `3.0` | Menor = más picos. Mayor = solo los grandes. |
| `--antes` / `--despues` | `20` / `10` | Segundos extra alrededor del pico. |
| `--min-duracion` / `--max-duracion` | `15` / `60` | Largo mínimo y máximo del rango sugerido. |
| `--min-msgs` | `5` | Mensajes mínimos por muestra para abrir un pico (evita falsos positivos en chats casi vacíos). |
| `--bin` | `5` | Segundos por muestra. |
| `--sondeo` | `20` | Segundos entre consultas de espectadores. |
| `--salida` | `picos` | Carpeta de resultados. |
| `--verbose` | apagado | Imprime cada muestra. |

### Qué verás

```
>>> PICO [twitch/canal] puntaje 17.7  clip sugerido VOD 01:12:09 - 01:13:09 (60 s, reloj 21:44:10-21:45:10)
    espectadores 820 -> 905
    · usuario: KEKW no puede ser
```

Los archivos quedan en `picos/`:
- `picos.jsonl`: un JSON por pico detectado.
- `<plataforma>_<canal>_<fecha>_muestras.csv`: métricas de cada muestra, útil para revisar o graficar.

### Detalles importantes

- Necesita unos 90 s de calentamiento para tener una referencia. En ese tiempo no detecta nada.
- **Espectadores de Twitch:** requieren credenciales gratuitas. Crea una app en https://dev.twitch.tv/console y exporta:

  ```bash
  export TWITCH_CLIENT_ID="tu_id"
  export TWITCH_CLIENT_SECRET="tu_secreto"
  ```

  Sin ellas usa solo el chat, que suele ser la señal más fuerte. Con ellas, el rango se da en tiempo del VOD (desde el inicio del directo).
- **Kick:** usa su API no oficial, sin cuenta. Da espectadores y hora de inicio del directo, así que el rango sale en tiempo del VOD. Desde octubre de 2026 el chat de Kick va por Centrifugo (antes Pusher); si Kick vuelve a cambiarlo, el chat dejará de conectar y habrá que actualizar `chat_kick()`. `curl_cffi` evita bloqueos de Cloudflare.
- **Estado de las pruebas:** probado en noviembre de 2026 con directos reales de Twitch y Kick (chat y espectadores de Kick). Los espectadores de Twitch con credenciales no se han probado.
- En Twitch sin credenciales no se conoce la hora de inicio del directo. El rango se da entonces como tiempo desde que arrancó el monitor (`monitor`) y como hora del reloj.
- Los primeros 90 s el puntaje sale en `0.0`: es el calentamiento, no un fallo.

---

### 5.2 Descargar los picos desde el VOD (`descargar_picos.py`)

Si monitoreaste sin `--grabar`, este script baja los picos de `picos/picos.jsonl` desde el VOD de Twitch. Funciona también con el directo todavía en curso, porque Twitch va publicando el VOD mientras se transmite. No hace falta parar el monitor.

```bash
python3 descargar_picos.py                         # todos los canales de Twitch del jsonl
python3 descargar_picos.py --canal nombre_canal
python3 descargar_picos.py --canal nombre_canal --calidad "best[height<=720]" --margen 5
python3 descargar_picos.py --jsonl ruta/picos.jsonl --top 4   # solo los 4 mejores de cada canal
```

| Opción | Por defecto | Qué hace |
|---|---|---|
| `--canal` | todos | Solo los picos de ese canal. |
| `--top N` | todos | Solo los N picos con más puntaje de cada canal. |
| `--calidad` | `best[height<=1080]/best` | Formato de yt-dlp. |
| `--margen` | `0` | Segundos extra antes y después de cada pico. |
| `--fecha AAAA-MM-DD` | hoy | Día de los picos, solo para registros antiguos sin fecha. |
| `--cookies-from-browser` | | Para VODs solo de suscriptores (ej. `firefox`). |
| `--salida` | `picos/clips` | Carpeta de los clips. |

- Se puede ejecutar varias veces. Los clips que ya existen se saltan, así que solo baja los picos nuevos. Los archivos de 10 KB o menos (descargas fallidas) se vuelven a bajar.
- Baja directamente los segmentos HLS del VOD que cubren el pico y los corta con ffmpeg sin recodificar. `yt-dlp --download-sections` genera MP4 vacíos con los VODs nuevos de Twitch (fMP4).
- Si Twitch silenció un tramo por copyright, el script avisa con `⚠ SIN AUDIO`. Ese clip se baja igual, pero sin sonido.
- Mientras el canal está en directo, a veces el VOD no aparece hasta que termina. Si falla, vuelve a intentarlo cuando acabe el directo.
- Necesita que el canal guarde VODs. Si los tiene desactivados, usa `--grabar` en el monitor.
- Por ahora solo funciona con Twitch.

### 5.3 Panel web (`panel.py`)

Hace lo mismo que `monitor_directos.py`, pero los canales se manejan desde el navegador en vez de por línea de comandos. Puedes añadir varios canales de Twitch y Kick, pausarlos, reanudarlos o quitarlos sin reiniciar nada. La página muestra en vivo los mensajes, espectadores y puntaje de cada canal y los últimos picos, y permite descargar `picos.jsonl`.

```bash
export PANEL_CLAVE="una-contraseña-larga"
python3 panel.py                 # http://127.0.0.1:8080
```

El navegador pide usuario y contraseña. El usuario puede ser cualquiera; solo se comprueba la contraseña de `PANEL_CLAVE` (mínimo 10 caracteres).

| Opción | Por defecto | Qué hace |
|---|---|---|
| `--host` | `127.0.0.1` | Dirección donde escucha. `127.0.0.1` solo acepta conexiones de la misma máquina. |
| `--port` | `8080` | Puerto. |
| `--umbral` | `3.0` | Sensibilidad del detector, igual que en el monitor. |
| `--salida` | `picos` | Carpeta de `picos.jsonl` y los CSV. |
| `--estado` | `panel_canales.json` | Dónde se guarda la lista de canales. |
| `--sin-clave` | apagado | No pide contraseña. Solo se permite con `--host 127.0.0.1`. |

- La lista de canales y si están pausados se guarda en `panel_canales.json`. Si el panel se reinicia, retoma los canales activos.
- El panel no graba (`--grabar`). Está pensado para un VPS que solo detecta; las descargas se hacen en tu PC con `descargar_picos.py`.
- Pausar un canal con un pico en curso lo anota en `picos.jsonl` igual que `Ctrl+C`.

#### En un VPS con Docker (recomendado)

El contenedor aísla el panel del resto del servidor:
- corre con un usuario sin privilegios (uid 10001);
- su sistema de archivos es de solo lectura, salvo la carpeta de datos;
- no tiene capacidades de Linux y tiene límites de memoria (256 MB) y procesos;
- el puerto solo se publica en `127.0.0.1` del VPS, así que no queda expuesto a internet.

La imagen solo lleva el panel y el monitor. No incluye Whisper, mediapipe ni ffmpeg, y ocupa unos 260 MB.

Requiere Docker con el plugin Compose (`sudo apt install docker.io docker-compose-v2` en Ubuntu, o la [instalación oficial](https://docs.docker.com/engine/install/)).

```bash
sudo mkdir -p /opt/clips-virales && sudo chown "$USER":"$USER" /opt/clips-virales
git clone https://github.com/EDGR2005/clips-virales.git /opt/clips-virales
cd /opt/clips-virales

cp .env.example .env
nano .env                                   # pon tu contraseña en PANEL_CLAVE
chmod 600 .env

mkdir -p datos && sudo chown 10001:10001 datos   # el contenedor escribe aquí
docker compose up -d --build
docker compose logs -f                      # ver la salida (Ctrl+C solo cierra la vista)
```

- Los datos quedan en `/opt/clips-virales/datos/`: `picos/picos.jsonl`, los CSV y `panel_canales.json`. Sobreviven a reinicios y a recrear el contenedor.
- El contenedor arranca solo al encender el VPS (`restart: unless-stopped`).
- Para entrar, usa el mismo túnel SSH o proxy HTTPS de más abajo; el puerto sigue siendo el 8080.
- Actualizar: `git pull && docker compose up -d --build`.
- Parar: `docker compose down` (los datos se conservan).
- Traer los picos a tu PC: el enlace del panel, o `rsync -av usuario@IP_DEL_VPS:/opt/clips-virales/datos/picos/ picos_vps/`.

Si al arrancar ves `Permission denied` en `/data`, falta el `chown 10001:10001 datos`.

#### En un VPS sin Docker

Instalación (Ubuntu/Debian):

```bash
sudo apt update && sudo apt install -y git python3-venv
git clone https://github.com/EDGR2005/clips-virales.git
cd clips-virales
python3 -m venv .venv
.venv/bin/pip install websockets curl_cffi aiohttp
```

Servicio de systemd para que arranque solo y se reinicie si falla. Crea `/etc/systemd/system/panel-clips.service` (cambia `usuario` y la contraseña):

```ini
[Unit]
Description=Panel del monitor de directos
After=network-online.target
Wants=network-online.target

[Service]
User=usuario
WorkingDirectory=/home/usuario/clips-virales
Environment=PANEL_CLAVE=una-contraseña-larga
ExecStart=/home/usuario/clips-virales/.venv/bin/python -u panel.py
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
```

```bash
sudo chmod 600 /etc/systemd/system/panel-clips.service   # la contraseña está dentro
sudo systemctl daemon-reload
sudo systemctl enable --now panel-clips
journalctl -u panel-clips -f        # ver la salida
```

Cómo entrar al panel desde tu PC. Hay dos opciones:

1. **Túnel SSH (recomendado, no expone nada a internet).** El panel sigue en `127.0.0.1` del VPS. En tu PC:

   ```bash
   ssh -N -L 8080:127.0.0.1:8080 usuario@IP_DEL_VPS
   ```

   Mientras ese comando esté abierto, entra en `http://localhost:8080`.

2. **Dominio con HTTPS.** Si quieres entrar desde el móvil u otro sitio sin SSH, pon delante un proxy con HTTPS, por ejemplo [Caddy](https://caddyserver.com/docs/quick-starts/reverse-proxy). Con un dominio apuntando al VPS, basta con `sudo caddy reverse-proxy --from tu-dominio.com --to 127.0.0.1:8080`. No abras el puerto 8080 directamente con `--host 0.0.0.0`: sin HTTPS, la contraseña viaja sin cifrar.

Para traer los picos a tu PC, usa el enlace **Descargar picos.jsonl** del panel o `rsync`, y luego:

```bash
python3 descargar_picos.py --jsonl ruta/a/picos.jsonl --canal nombre_canal
```

Actualizar el código del VPS: `git pull && sudo systemctl restart panel-clips`.

## 6. Del pico al clip final

Con `--grabar` (sección 5.1) o `descargar_picos.py` (sección 5.2) los clips se bajan solos. El proceso manual es:

1. Con el monitor, anota el rango (por ejemplo VOD `01:12:09 - 01:13:09`).
2. Baja ese fragmento. Para un VOD de Twitch o un video de YouTube:

   ```bash
   yt-dlp --download-sections "*01:12:09-01:13:09" -f "bv*+ba/b" \
     --merge-output-format mp4 -o "fragmento.%(ext)s" "URL_DEL_VOD"
   ```

   También sirve cualquier grabación propia del directo.
3. Genera el clip con subtítulos:

   ```bash
   python3 hacer_clips.py fragmento.mp4 --idioma-origen es --clips 1 --seguir-caras
   ```

`hacer_clips.py` corta clips de 40 s. Si el rango sugerido es más largo, usa `--clips 2`, o ajusta `DURACION` en el script.

---

## 7. Solución de problemas

| Problema | Causa / solución |
|---|---|
| `module 'mediapipe' has no attribute 'solutions'` | Versión nueva de mediapipe. Instala `pip install mediapipe==0.10.14` (requiere Python 3.12 o menor). |
| `No matching distribution found for mediapipe==0.10.14` | Tu Python es 3.13. Crea el entorno con Python 3.12. |
| `externally-managed-environment` con pip | Usa el entorno virtual (sección 1.2). |
| Whisper tarda mucho | Revisa que PyTorch use la GPU (sección 2). En CPU usa `--modelo base`. |
| `CUDA out of memory` | Usa un modelo más pequeño. |
| `ModuleNotFoundError: websockets` | `pip install -r requirements.txt` con el entorno activado. |
| El monitor no detecta nada | Espera el calentamiento de 90 s, baja `--umbral` o `--min-msgs`. |
| Kick: `App key ... not in this cluster` | Versión vieja del script (Pusher). Haz `git pull`. |
| Kick no conecta | Instala `curl_cffi` y revisa la sección 5, apartado Kick. |
| `No such file or directory` en FFmpeg | El nombre del archivo no coincide. Si tiene espacios, ponlo entre comillas. |
| YouTube pide iniciar sesión | Usa `--cookies-from-browser` en yt-dlp (ver `docs/guia_manual.md`). **Nunca subas `cookies.txt` a GitHub**, ya está en `.gitignore`. |

---

## 8. Aviso

Descarga y procesa solo contenido propio o que tengas permiso de usar. Al monitorear directos de terceros, respeta las condiciones de uso de Twitch y Kick.
