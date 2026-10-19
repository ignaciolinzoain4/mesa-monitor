# Instalación

Mesa Monitor corre en una PC del local con Docker. Funciona con placa NVIDIA (recomendado) o
solo con el procesador (más lento: menos fps y modelos más chicos).

## 1. Requisitos

| | Mínimo | Recomendado |
|---|---|---|
| Sistema | Windows 10/11 (64 bits) o Linux | Windows 11 o Ubuntu 24.04 |
| RAM | 8 GB | 16 GB |
| Placa de video | — (modo CPU) | NVIDIA GTX 1660 / RTX 3060 o superior, 6 GB+ |
| Disco | 20 GB libres | SSD |
| Red | Misma red que el NVR | Cable (no Wi-Fi) |

> Placas NVIDIA RTX 50xx: necesitan CUDA 12.8 o superior. Avisar para cambiar la variante de
> torch (`pytorch-cu126` → `pytorch-cu130` en `pyproject.toml`).

## 2. Windows (Docker Desktop + WSL2)

1. **Driver NVIDIA** (solo con placa NVIDIA): instalar el último driver "Game Ready" o "Studio"
   desde nvidia.com. No hace falta instalar CUDA aparte.
2. **WSL2**: abrir PowerShell como administrador y ejecutar `wsl --install`. Reiniciar.
3. **Docker Desktop**: instalar desde docker.com. En *Settings → General* dejar marcado
   "Use the WSL 2 based engine".
4. Verificar la GPU dentro de Docker (PowerShell):
   ```powershell
   docker run --rm --gpus all nvidia/cuda:12.6.3-base-ubuntu24.04 nvidia-smi
   ```
   Tiene que mostrar el nombre de la placa. Si falla, actualizar el driver y Docker Desktop.
5. **Git**: instalar Git for Windows y descargar el proyecto:
   ```powershell
   git clone https://github.com/ignaciolinzoain4/mesa-monitor.git
   cd mesa-monitor
   ```

## 3. Linux (Ubuntu)

```sh
# Docker
curl -fsSL https://get.docker.com | sh
sudo usermod -aG docker $USER   # cerrar sesión y volver a entrar
# Solo con placa NVIDIA: driver + NVIDIA Container Toolkit
sudo ubuntu-drivers install
# seguir https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/latest/install-guide.html
docker run --rm --gpus all nvidia/cuda:12.6.3-base-ubuntu24.04 nvidia-smi
git clone https://github.com/ignaciolinzoain4/mesa-monitor.git && cd mesa-monitor
```

## 4. Configuración

1. Copiar el archivo de secretos y completarlo:
   ```sh
   cp .env.example .env
   ```
   - `COMPOSE_PROFILES=gpu` si hay placa NVIDIA, `cpu` si no.
   - `POSTGRES_PASSWORD`, `ADMIN_PASSWORD`: contraseñas nuevas (solo letras, números, `-` y `_`).
   - `JWT_SECRET`: un valor largo y aleatorio. Generarlo con
     `python -c "import secrets; print(secrets.token_urlsafe(48))"`.
   - `NVR_HOST`, `NVR_PORT`, `NVR_USER`, `NVR_PASSWORD`: los mismos datos que usa la app DMSS.
2. Copiar la lista de cámaras y completarla:
   ```sh
   mkdir -p config/local
   cp config/example/cameras.yaml config/local/cameras.yaml
   ```
   Para cada cámara, poner el número de canal del NVR (el mismo que se ve en DMSS). Para otras
   marcas o cámaras sueltas, ver "Conectar cualquier sistema de cámaras" en el [README](../README.md).
3. Levantar todo:
   ```sh
   docker compose up -d --build
   ```
   La primera vez tarda (descarga varias imágenes y el modelo de detección).
4. Abrir `http://localhost:8000` (o `http://IP-DE-LA-PC:8000` desde otra PC del local), entrar
   con `ADMIN_USERNAME` / `ADMIN_PASSWORD` y revisar **Cámaras**: todas tienen que estar
   "En línea" y mostrar imagen.

### Si una cámara aparece "Sin señal"

- Verificar el canal en `cameras.yaml` y los datos del NVR en `.env`.
- En el NVR (o DMSS), el **substream** (secundario) de esa cámara tiene que estar habilitado
  en **H.264 o H.265**. No usar MJPEG en el substream (Dahua no entrega snapshots en ese modo).
- Probar el stream desde la PC: `http://localhost:1984` abre la consola de go2rtc (solo desde
  la misma PC).

### Si aparece "Con problemas" (resolución distinta)

La resolución del substream no coincide con `expected_resolution`. Corregirla en el NVR o en
`cameras.yaml`. Si cambia después de dibujar las zonas, las zonas siguen sirviendo (se guardan
en proporciones), pero conviene revisarlas.

## 5. Cambios de cámaras

Después de editar `config/local/cameras.yaml`:
```sh
docker compose up -d --force-recreate go2rtc-config go2rtc
docker compose restart api worker-gpu   # o worker-cpu
```

## 6. Benchmark (elegir modelo y fps)

Con las cámaras andando:
```sh
docker compose run --rm worker-gpu python tools/benchmark.py     # o worker-cpu
```
Mide la velocidad real de cada modelo con imágenes de las cámaras y guarda la recomendación en
`config/local/recommended.yaml`, que el worker usa al arrancar. Opciones: `--seconds 10` (más
preciso), `--tiles-per-frame 1` (si las cámaras están cerca de las mesas y no hace falta dividir
la imagen).

## 7. Acceso desde fuera del local (opcional)

Por defecto todo funciona solo dentro de la red del local. Para verlo desde afuera usar
**Tailscale** (recomendado, gratis para uso personal) o **Cloudflare Tunnel**. **Nunca** abrir
puertos del router hacia el NVR, la base de datos o go2rtc. Ver [acceso-remoto.md](acceso-remoto.md).
