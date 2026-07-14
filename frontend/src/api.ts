// Backend base URL. Empty in dev (Vite proxy handles /api); set to the
// Render backend URL at build time via VITE_API_BASE for the Pages deploy.
export const API_BASE: string = import.meta.env.VITE_API_BASE ?? "";
