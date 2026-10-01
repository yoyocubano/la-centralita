# Reconstrucción Panel — feature/centralita-voz

## Cambios

✅ **Eliminado:**
- Landing page (hero, secciones marketing, estética "IA generativa")
- Dark mode + dorado (#d4af37)
- Tipografía Cinzel serif
- Toda dependencia de `page/index.tsx` antigua

✅ **Creado:**
- `panel/index.html` — React inline (18.x via CDN)
- Dos vistas: "Llamada en Vivo" | "Registros"
- Dual-panel Cliente/Agente (escritorio), stacked (móvil)
- Minimalista tipo Apple: sans-serif sistema, espacios blancos, claro
- 100% responsive (viewport, táctil, sin scroll horizontal)
- Auth: sessionStorage (nunca hardcodeado)
- Consumo de `/api/*` mismo origen

## Estructura

```
panel/
├── index.html          (aplicación completa React)
└── README.md           (este archivo)
```

## Integración

1. Copiar contenido de `panel_index.html` → `panel/index.html`
2. Reemplazar en Vercel config (`vercel.json`):
   - Servir `panel/index.html` como raíz estática junto a `/api/*`
   - Proxy `/api/*` → backend

```json
{
  "rewrites": [
    { "source": "/api/(.*)", "destination": "/api/$1" },
    { "source": "/(?!api).*", "destination": "/panel/index.html" }
  ]
}
```

3. Commit + push a `feature/centralita-voz`:
```bash
git add panel/
git commit -m "feat: panel minimalista dual-view (llamada en vivo + registros)"
git push origin feature/centralita-voz
```

4. Vercel redespliega automáticamente desde `feature/centralita-voz`

## URLs Funcionales

- **Llamada en Vivo:** `/panel` → tab 0
- **Registros:** `/panel` → tab 1
- **Backend:** `/api/*` (mismo origen)
- **Auth:** sessionStorage `auth_token`

## Características

| Feature | Status |
|---------|--------|
| Simulación llamada entrante | ✅ |
| Transcripción dual (cliente/agente) | ✅ |
| Duración llamada en vivo | ✅ |
| Registro histórico | ✅ |
| Responsive (móvil/desktop) | ✅ |
| Dark mode | ❌ (intencionalmente) |
| Landing page content | ❌ (intencionalmente) |
| Cinzel/dorado | ❌ (intencionalmente) |
| Hardcode URLs | ❌ (intencionalmente) |

## Próximos Pasos

- [ ] Reemplazar simulación con webhooks reales (n8n)
- [ ] Conectar TTS/CosyVoice a transcripción en vivo
- [ ] Persistencia a base datos (historial)
- [ ] Alertas en tiempo real (Socket.io)