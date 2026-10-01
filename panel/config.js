/**
 * Configuración de despliegue del panel (sin secretos).
 *
 *  apiBase: URL del backend FastAPI, p. ej. "https://api.tu-dominio.lu".
 *           - Vacío ("") cuando el panel lo sirve el propio backend en /panel (mismo origen).
 *           - En hosting estático (Firebase / GitHub Pages) sin backend, déjalo vacío:
 *             el panel funcionará en MODO DEMO y lo indicará claramente.
 *  transport: "auto" (por defecto: WebSocket y, si no hay, polling HTTP) o "polling"
 *             para forzar GET /api/events. Con backend en Vercel el propio backend
 *             anuncia polling en /api/status/details; ver docs/VERCEL.md.
 *  pollIntervalMs: intervalo del polling con la pestaña visible (mín. 2000; oculta: 15 s).
 *  El token de operador NUNCA va aquí: se introduce en el panel (o se pasa una vez
 *  como #token=... en la URL) y vive solo en sessionStorage.
 */
window.CENTRALITA_CONFIG = {
  apiBase: "",
  transport: "auto",
  pollIntervalMs: 5000
};
