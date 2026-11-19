# Clips virales: detectar, cortar, reencuadrar y subtitular

Herramientas en Python para convertir directos y videos largos en clips verticales (9:16) con subtítulos.

| Herramienta | Qué hace |
|---|---|
| `monitor_directos.py` | Vigila el chat y la audiencia de un directo (Twitch / Kick) y avisa en qué rangos de tiempo hubo picos, o sea, posibles clips virales. |
| `hacer_clips.py` | Corta un video en clips de 40 s, los pasa a vertical, los transcribe con Whisper, traduce si hace falta y pega los subtítulos. |
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

## 6. Del pico al clip final

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
