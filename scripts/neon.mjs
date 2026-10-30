// Explicit Neon entrypoint: Node loads .env.local; credentials go only to the Python child.
import { spawn } from 'node:child_process'
import { fileURLToPath } from 'node:url'
import path from 'node:path'

export const root = fileURLToPath(new URL('../', import.meta.url))
export function neonCommand(action, source = process.env, extraArgs = []) {
  if (!['init', 'serve', 'import', 'pair'].includes(action)) throw new Error('Usa neon.mjs init, serve, import o pair.')
  const databaseUrl = source.LIFE_OS_DATABASE_URL ||
    (action !== 'serve' ? source.DATABASE_URL_UNPOOLED : source.DATABASE_URL)
  if (!databaseUrl) throw new Error('Falta la conexión Neon. Ejecuta neon env pull para crear .env.local.')
  if (action !== 'serve' && new URL(databaseUrl).hostname.includes('-pooler')) {
    throw new Error('La inicialización necesita DATABASE_URL_UNPOOLED o una conexión directa explícita.')
  }
  const python = path.join(root, '.venv', process.platform === 'win32' ? 'Scripts/python.exe' : 'bin/python')
  const env = { ...source, LIFE_OS_DATABASE_URL: databaseUrl }
  let args
  if (action === 'init') args = ['-I', '-m', 'life_os.manage', 'postgres-init']
  else if (action === 'import') args = ['-I', '-m', 'life_os.manage', 'postgres-import', ...extraArgs]
  else if (action === 'pair') args = ['-I', '-m', 'life_os.manage', 'pair-cloud', ...extraArgs]
  else {
    if (!env.LIFE_OS_DEV_TOKEN) throw new Error('Configura LIFE_OS_DEV_TOKEN en esta terminal antes de arrancar.')
    const port = Number(env.LIFE_OS_PORT || '8000')
    if (!Number.isInteger(port) || port < 1 || port > 65535) throw new Error('LIFE_OS_PORT debe ser un puerto válido.')
    env.LIFE_OS_MODE = 'development'
    args = ['-I', '-m', 'uvicorn', 'life_os.api:create_app', '--factory', '--host', '127.0.0.1', '--port', String(port), '--no-proxy-headers']
  }
  return { command: python, args, env, cwd: root }
}

const normalize = value => process.platform === 'win32' ? value.toLowerCase() : value
if (process.argv[1] && normalize(path.resolve(process.argv[1])) === normalize(fileURLToPath(import.meta.url))) {
  const settings = neonCommand(process.argv[2], process.env, process.argv.slice(3))
  const child = spawn(settings.command, settings.args, { cwd: root, env: settings.env, stdio: 'inherit', windowsHide: true })
  child.on('error', () => { console.error('No se pudo ejecutar el Python de .venv.'); process.exitCode = 1 })
  child.on('exit', code => { process.exitCode = code ?? 1 })
  for (const signal of ['SIGINT', 'SIGTERM']) process.on(signal, () => child.kill(signal))
}
