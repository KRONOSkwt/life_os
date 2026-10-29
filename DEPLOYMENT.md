# Desplegar Life OS con Vercel y Neon

`vercel.json` despliega Vue y FastAPI juntos con Vercel Services, en beta y disponible en todos los planes: https://vercel.com/docs/services. Se conserva la autenticación por dispositivo de Life OS.

## Configuración

1. Vincula el proyecto desde la raíz del repositorio. La raíz en Vercel debe ser `.`.
2. Configura `LIFE_OS_DATABASE_URL` como secreto de producción con la conexión agrupada de Neon. También se admite `DATABASE_URL`. No uses el prefijo `VITE_`.
3. Inicializa el esquema con la conexión directa antes de desplegar. El arranque no crea ni migra tablas.
4. Comprueba `/health`: debe devolver `paired-https`. `/api/v1/sync/pull` sin credenciales debe responder 401.

El servicio `server` usa la raíz para incluir el contrato JSON y el paquete Python. `requirements.txt` instala PostgreSQL. El servicio `web` usa `apps/web`. `/api/*` y `/health` van a FastAPI; las demás rutas van a la web. Ambas comparten origen.

`app.py` requiere PostgreSQL y fuerza el modo de dispositivos vinculados aunque existan variables de desarrollo. El token local no habilita acceso público. La conexión usa TLS y los ajustes SQL duran solamente una transacción.

## Entornos y acceso

Los previews deben usar una rama Neon independiente. No copies automáticamente las credenciales de producción a preview. El despliegue no configura servicios de pago ni crea otra base.

Las credenciales web se emiten mediante la administración privada de `Credentials`, se conservan localmente y se pegan en el campo de conexión de la web. Solo su hash se guarda en PostgreSQL. La administración no tiene una ruta pública.

No se migra SQLite ni cambia el servidor del teléfono. Android necesita una invitación compatible con el certificado público antes de enlazarse al servidor nuevo.

## Verificación y reversión

```powershell
python -m pytest apps/server/tests -q
pnpm test
pnpm build
```

`test_cloud.py` comprueba la entrada real, rechazo del token local, autenticación por dispositivo, HTTPS y ausencia de caché de API. Las pruebas de escritura en Neon deben usar ramas desechables.

La unidad de despliegue comprende `app.py`, `requirements.txt`, `vercel.json`, `.vercelignore` y `test_cloud.py`. Puede revertirse sin borrar PostgreSQL ni modificar SQLite. Los respaldos PostgreSQL y la migración del teléfono son trabajo independiente.
