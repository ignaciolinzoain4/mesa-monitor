# Acceso remoto (opcional)

Mesa Monitor está pensado para usarse **dentro de la red del local**. Si el dueño quiere ver el
dashboard desde su casa o el celular, hay dos opciones seguras. Ninguna abre puertos en el router.

## Opción A — Tailscale (recomendada)

1. Crear una cuenta en tailscale.com.
2. Instalar Tailscale en la PC de Mesa Monitor y en el celular/notebook del dueño, con la misma
   cuenta.
3. Desde el celular, abrir `http://NOMBRE-DE-LA-PC:8000` (el nombre aparece en la app de Tailscale).

Solo los dispositivos de la cuenta pueden entrar. No hace falta configurar el router.

## Opción B — Cloudflare Tunnel

Para tener una dirección web fija (ej. `mesas.midominio.com`) con login de Cloudflare Access:

1. Tener un dominio administrado en Cloudflare.
2. Instalar `cloudflared` en la PC y crear un túnel hacia `http://localhost:8000`.
3. **Obligatorio**: proteger la dirección con Cloudflare Access (login por email) además del
   login de Mesa Monitor.

## Lo que nunca hay que hacer

- Abrir en el router los puertos del NVR (554, 37777, 80), de go2rtc (1984, 8554, 8555) o de la
  base de datos (5432).
- Compartir el archivo `.env`.
