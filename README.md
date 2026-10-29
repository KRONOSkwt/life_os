# Life OS

Aplicación personal con web Vue y Android nativo. El primer recorrido permite crear rutinas, registrar entrenamientos sin conexión en Android y sincronizarlos mediante FastAPI. Salud, Deporte y Finanzas están pendientes.

## Desarrollo

Requisitos: Node 24.12 o posterior, pnpm 10 y Python 3.12 o posterior.

```powershell
pnpm install --frozen-lockfile
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/setup-server.ps1
pnpm test
pnpm build
```

El servidor utiliza SQLite localmente y admite PostgreSQL con `life-os-server[postgres]`. Para la API local, configura un `LIFE_OS_DEV_TOKEN` privado de al menos 32 caracteres y usa `scripts/start-server.ps1`.

## Despliegue

[DEPLOYMENT.md](DEPLOYMENT.md) explica Vercel Services, Neon, variables privadas y comprobaciones de acceso. Las notas personales, bases de datos y credenciales permanecen fuera de Git.

## Estructura

- `apps/web`: Vue, TypeScript y Vite; dashboard bento y páginas por módulo.
- `apps/server`: FastAPI, autenticación por dispositivo y SQLite/PostgreSQL.
- `apps/android`: Kotlin, Compose, Room y WorkManager.
- `contracts`: contrato JSON de sincronización compartido.
- `scripts`: preparación y recorridos de verificación.
