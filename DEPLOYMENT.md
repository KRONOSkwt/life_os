# Desplegar Life OS con Vercel y Neon

`vercel.json` despliega Vue y FastAPI juntos con Vercel Services, en beta y disponible en todos los planes: https://vercel.com/docs/services. Se conserva la autenticación por dispositivo de Life OS.

## Configuración

1. Vincula el proyecto desde la raíz del repositorio. La raíz en Vercel debe ser `.`.
2. Configura `LIFE_OS_DATABASE_URL` como secreto de producción con la conexión agrupada de Neon. También se admite `DATABASE_URL`. No uses el prefijo `VITE_`.
3. Inicializa el esquema con la conexión directa antes de desplegar. El arranque no crea ni migra tablas.
4. Comprueba `/health`: debe devolver `paired-https`. `/api/v1/sync/pull` sin credenciales debe responder 401.

El servicio `server` usa la raíz para incluir el contrato JSON y el paquete Python. `pyproject.toml` declara el paquete local por su nombre real e instala PostgreSQL. El servicio `web` usa `apps/web`. `/api/*` y `/health` van a FastAPI; las demás rutas van a la web. Ambas comparten origen.

La dirección antigua `lifeos-pc4x.vercel.app` pertenece a una segunda importación del repositorio y redirige a `https://lifeos-sigma-sooty.vercel.app`, el proyecto principal configurado con Neon. La regla se limita al hostname antiguo; no cambia otros dominios ni requiere copiar credenciales a otro proyecto.

El arranque web también comprueba ese hostname y cambia de dirección antes de montar la aplicación, conservando ruta, parámetros y sección. Esta comprobación cubre despliegues Services donde la redirección de plataforma no se aplica.

`app.py` requiere PostgreSQL y fuerza el modo de dispositivos vinculados aunque existan variables de desarrollo. El token local no habilita acceso público. La conexión usa TLS y los ajustes SQL duran solamente una transacción.

## Entornos y acceso

Los previews deben usar una rama Neon independiente. No copies automáticamente las credenciales de producción a preview. El despliegue no configura servicios de pago ni crea otra base.

Las credenciales web se emiten mediante la administración privada de `Credentials` y se introducen una vez en «Clave de acceso». Solo su hash se guarda en PostgreSQL. La administración no tiene una ruta pública.

La web intercambia la clave por una cookie de acceso con HttpOnly, Secure y SameSite=Strict, válida en el navegador durante 30 días. JavaScript no guarda ni vuelve a leer la clave; la sesión se recupera al recargar. «Cerrar sesión» borra la cookie de este navegador. Revocar la credencial en el servidor invalida también el acceso por cookie. El inicio, el cierre y las escrituras con cookie comprueban el origen para impedir solicitudes de otros sitios. Los clientes nativos conservan su autenticación Bearer.

La [guía de migración](CLOUD_MIGRATION.md) permite importar SQLite conservando identidad y vincular Android mediante HTTPS público. Los previews conservan la protección de Vercel; la producción permite clientes nativos y exige credenciales de Life OS para acceder a los datos.

## Verificación y reversión

```powershell
python -m pytest apps/server/tests -q
pnpm test
pnpm build
```

`test_cloud.py` comprueba la entrada real, rechazo del token local, autenticación por dispositivo, HTTPS y ausencia de caché de API. Las pruebas de escritura en Neon deben usar ramas desechables.

La unidad de acceso recordado comprende `api.py`, `test_browser_session.py`, `access.ts`, `App.vue`, `sync.ts`, sus pruebas web y el recorrido Neon actualizado. Verificación local: 75 pruebas Python aprobadas y 9 PostgreSQL omitidas, 17 pruebas web aprobadas, compilación aprobada y 4 recorridos Edge aprobados, incluido recargar sin repetir la clave y cerrar sesión. Revertir esta unidad devuelve el acceso manual sin modificar tablas, datos ni la autenticación Android.

La unidad de despliegue comprende `app.py`, `pyproject.toml`, `vercel.json`, `.vercelignore` y `test_cloud.py`. Puede revertirse sin borrar PostgreSQL ni modificar SQLite. Los respaldos PostgreSQL y la migración del teléfono son trabajo independiente.
