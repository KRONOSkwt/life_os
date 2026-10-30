# Pasar el servidor local y Android a la nube

La migración conserva identidad, registros, versiones, cambios ordenados y comprobantes de operación. Las credenciales web existentes de Neon se conservan. SQLite y los datos Room no se borran.

## Importar SQLite

1. Detén las escrituras del servidor anterior y la app del teléfono. Comprueba que no quedan operaciones móviles pendientes. Guarda una copia SQLite consistente y una copia de los datos del teléfono antes de actualizar el APK.
2. Prueba la importación en una rama Neon aislada. El destino debe tener esquema inicializado y estar vacío de registros, operaciones, cambios e invitaciones activas. Puede contener credenciales web.
3. Con `.env.local` del destino correcto y su conexión directa, ejecuta desde la raíz:

```powershell
npm run db:neon:import -- --source RUTA-A-LA-COPIA.sqlite3 --dry-run
npm run db:neon:import -- --source RUTA-A-LA-COPIA.sqlite3
```

El modo de comprobación ejecuta y revierte la importación. Una importación completa compara los registros copiados, preserva los números de secuencia y actualiza su generador en la misma transacción. Si falla, revierte todo. Repetir la misma copia devuelve `already_imported`; una copia distinta se rechaza para evitar sobrescribir cambios nuevos. El comprobante SHA-256 y los conteos no contienen claves.

El archivo de origen se abre en modo de solo lectura, exige versión 2 y pasa `integrity_check`. El destino adopta la identidad original para conservar los cursores de Android. Después de importar, despliega de nuevo la API para que todas sus instancias carguen esa identidad; recarga las webs que estaban abiertas con un cursor de la base vacía anterior.

## Vincular Android

La producción debe admitir peticiones nativas sin una sesión de Vercel; conserva la autenticación de Life OS y limita la protección adicional de Vercel a previews. No incluyas secretos de bypass de Vercel en el APK.

```powershell
npm run pair:neon -- --url https://TU-PRODUCCION.vercel.app --output .cache/cloud-invitation.json
```

El archivo contiene un código de un solo uso que caduca a los cinco minutos. No lo publiques. Actualiza el APK mediante `adb install -r` para conservar Room y Keystore. En **Mi espacio → Nueva invitación**, pega el JSON en **Invitación de vinculación** y pulsa **Vincular**.

La invitación cloud incluye `tls_mode: system`: Android usa las CA del sistema y verifica el hostname. La rotación normal de certificados públicos no exige una huella fija. Las invitaciones domésticas anteriores siguen usando su huella explícita; no se aceptan certificados autofirmados en el modo público ni se siguen redirecciones. El servidor indicado debe conservar la identidad de los datos existentes.

## Comprobar el resultado

Comprueba los conteos e identificadores de ambas bases, historial web y Android, cero operaciones pendientes y una segunda sincronización sin duplicados. Reabre Android sin red para comprobar que conserva el historial. Los recorridos nuevos que escriban sesiones de prueba deben usar un entorno desechable.

La unidad comprende el importador y sus pruebas, comandos administrativos, cliente HTTPS Android y persistencia del modo de confianza en la bóveda. Puede revertirse el código sin borrar las copias locales; tras empezar a escribir en Neon no se debe volver a SQLite sin reconciliar esos cambios. Este procedimiento de corte no sustituye un sistema de respaldos PostgreSQL recurrentes.
