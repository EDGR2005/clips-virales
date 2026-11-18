# Guía: crear clips con yt-dlp, FFmpeg y Whisper (Ubuntu/Linux)

Flujo completo:

1. **yt-dlp**: descargar el video (opcional, si no lo tienes ya).
2. **FFmpeg**: cortar el clip y pasarlo a vertical.
3. **Whisper**: transcribir y generar subtítulos.
4. **FFmpeg**: pegar los subtítulos al video.

---

## 0. Preparar una carpeta de trabajo

```bash
mkdir -p ~/Descargas/clipsPrueba
cd ~/Descargas/clipsPrueba
```

---

## 1. Instalar FFmpeg

```bash
sudo apt update
sudo apt install ffmpeg
ffmpeg -version
```

Si aparece la versión, está listo.

---

## 2. yt-dlp: descargar videos de YouTube

### 2.1 Instalar

Opción recomendada (binario oficial, siempre actualizado):

```bash
sudo curl -L https://github.com/yt-dlp/yt-dlp/releases/latest/download/yt-dlp -o /usr/local/bin/yt-dlp
sudo chmod a+rx /usr/local/bin/yt-dlp
yt-dlp --version
```

Para actualizarlo más adelante:

```bash
sudo yt-dlp -U
```

> El `yt-dlp` de `apt` suele estar desactualizado y YouTube cambia seguido, por eso se recomienda el binario.

### 2.2 Deno (solo si yt-dlp lo pide)

Las versiones recientes de yt-dlp pueden necesitar un entorno de JavaScript para YouTube. Si ves advertencias sobre "JS runtime", instala Deno.

**Ojo:** la URL correcta es `install.sh`. Usar solo `https://deno.land` da error, porque devuelve una página web y no un script.

```bash
curl -fsSL https://deno.land/install.sh | sh
```

Cierra y abre la terminal después de instalar.

### 2.3 Descargar un video

```bash
yt-dlp -f "bv*+ba/b" --merge-output-format mp4 -o "video.%(ext)s" "URL_DEL_VIDEO"
```

- `-f "bv*+ba/b"`: mejor video + mejor audio.
- `--merge-output-format mp4`: lo junta en un `.mp4`.
- `-o "video.%(ext)s"`: lo guarda como `video.mp4`.

### 2.4 Descargar solo un fragmento

Útil para no bajar un video de horas completo:

```bash
yt-dlp --download-sections "*00:10:30-00:11:15" -f "bv*+ba/b" --merge-output-format mp4 -o "fragmento.%(ext)s" "URL_DEL_VIDEO"
```

### 2.5 Si YouTube pide iniciar sesión o detecta un bot

Usa tus cookies. Dos opciones:

```bash
# Opción A: leer las cookies directamente del navegador
yt-dlp --cookies-from-browser firefox -f "bv*+ba/b" --merge-output-format mp4 -o "video.%(ext)s" "URL_DEL_VIDEO"

# Opción B: usar un archivo cookies.txt exportado
yt-dlp --cookies cookies.txt -f "bv*+ba/b" --merge-output-format mp4 -o "video.%(ext)s" "URL_DEL_VIDEO"
```

Cambia `firefox` por `chrome`, `chromium`, etc. según tu navegador.

> Trata `cookies.txt` como una contraseña: da acceso a tu sesión. No lo compartas ni lo subas a ningún sitio.

### 2.6 Solo audio (opcional)

```bash
yt-dlp -x --audio-format mp3 -o "audio.%(ext)s" "URL_DEL_VIDEO"
```

> Descarga solo contenido propio o que tengas permiso de usar.

---

## 3. FFmpeg: cortar clips

### 3.1 Ver la duración de un video

```bash
ffprobe -v error -show_entries format=duration -of default=noprint_wrappers=1:nokey=1 video.mp4
```

Devuelve la duración en segundos.

### 3.2 Corte rápido (sin recodificar)

Casi instantáneo, aunque el corte puede quedar impreciso por un par de segundos.

```bash
ffmpeg -ss 00:00:10 -to 00:00:40 -i video.mp4 -c copy corte1.mp4
```

### 3.3 Corte preciso (recodificando)

```bash
ffmpeg -ss 00:00:10 -to 00:00:40 -i video.mp4 -c:v libx264 -c:a aac corte2.mp4
```

### 3.4 Corte + formato vertical 9:16 (TikTok / Reels / Shorts)

```bash
ffmpeg -ss 00:00:10 -to 00:00:40 -i video.mp4 \
  -vf "crop=ih*9/16:ih,scale=1080:1920" \
  -c:v libx264 -c:a aac corte_vertical.mp4
```

El `crop` toma el centro del video. Si la persona no está centrada, hay que ajustar el recorte a mano.

### 3.5 Varios clips con un script

Crea `cortar.py`:

```python
import subprocess

video = "video.mp4"
clips = [
    ("00:10:30", "00:11:15"),
    ("00:25:02", "00:25:50"),
    ("00:41:10", "00:42:00"),
]

for i, (inicio, fin) in enumerate(clips, 1):
    subprocess.run([
        "ffmpeg", "-ss", inicio, "-to", fin, "-i", video,
        "-vf", "crop=ih*9/16:ih,scale=1080:1920",
        "-c:v", "libx264", "-c:a", "aac", f"clip{i}.mp4"
    ])
```

Ejecútalo con:

```bash
python3 cortar.py
```

---

## 4. Whisper: generar subtítulos

### 4.1 Instalar (en un entorno virtual)

En Ubuntu reciente, `pip install` directo da el error `externally-managed-environment`, así que se usa un entorno virtual:

```bash
sudo apt install python3-venv
python3 -m venv ~/whisper-env
source ~/whisper-env/bin/activate
pip install openai-whisper
```

Cada vez que abras una terminal nueva, activa el entorno antes de usar Whisper:

```bash
source ~/whisper-env/bin/activate
```

Para salir del entorno: `deactivate`.

### 4.2 Transcribir el clip

Conviene transcribir el clip ya cortado, para que los tiempos empiecen en 0.

```bash
whisper corte_vertical.mp4 --model base --language es --output_format srt
```

- Genera `corte_vertical.srt`.
- La primera vez descarga el modelo, así que necesita internet.
- Modelos: `tiny` y `base` son rápidos; `small` y `medium` son más precisos pero más lentos sin GPU.

Revisa el resultado, porque Whisper puede equivocarse con nombres propios:

```bash
cat corte_vertical.srt
```

Puedes corregir el texto a mano en el `.srt` antes del siguiente paso.

---

## 5. FFmpeg: pegar los subtítulos al video

```bash
ffmpeg -i corte_vertical.mp4 \
  -vf "subtitles=corte_vertical.srt:force_style='FontName=DejaVu Sans,FontSize=14,Bold=1,Outline=2,Alignment=2,MarginV=60'" \
  -c:a copy con_subs.mp4
```

Parámetros de estilo:

| Parámetro | Qué hace |
|---|---|
| `FontSize` | Tamaño del texto (prueba entre 12 y 16) |
| `Bold=1` | Texto en negritas |
| `Outline=2` | Borde negro para que se lea sobre cualquier fondo |
| `Alignment=2` | Centrado abajo |
| `MarginV=60` | Separación del borde inferior (súbelo para que no tape la interfaz de la red social) |

---

## 6. Todo el proceso en una pasada

Con un video ya descargado como `video.mp4`:

```bash
# 1. Cortar y pasar a vertical
ffmpeg -ss 00:00:10 -to 00:00:40 -i video.mp4 \
  -vf "crop=ih*9/16:ih,scale=1080:1920" \
  -c:v libx264 -c:a aac corte_vertical.mp4

# 2. Transcribir (con el entorno de Whisper activado)
whisper corte_vertical.mp4 --model base --language es --output_format srt

# 3. Pegar subtítulos
ffmpeg -i corte_vertical.mp4 \
  -vf "subtitles=corte_vertical.srt:force_style='FontSize=14,Bold=1,Outline=2,Alignment=2,MarginV=60'" \
  -c:a copy con_subs.mp4
```

---

## 7. Solución de problemas

| Problema | Causa / solución |
|---|---|
| `No such file or directory` en FFmpeg | El nombre del archivo no coincide. Revisa con `ls` que exista exactamente igual, con su extensión. |
| Nombres con espacios | Ponlos entre comillas: `-i "mi video.mp4"`. |
| `Syntax error: redirection unexpected` al instalar Deno | Usaste la URL equivocada. Debe ser `https://deno.land/install.sh`. |
| `externally-managed-environment` con pip | Usa un entorno virtual (sección 4.1). |
| yt-dlp falla o da errores raros | Actualízalo: `sudo yt-dlp -U`. |
| YouTube pide iniciar sesión | Usa `--cookies-from-browser` o `--cookies` (sección 2.5). |
| Clip de salida vacío o muy corto | Los tiempos de `-ss` / `-to` superan la duración del video. Revisa con `ffprobe`. |
| El comando `whisper` no se encuentra | Activa el entorno: `source ~/whisper-env/bin/activate`. |
