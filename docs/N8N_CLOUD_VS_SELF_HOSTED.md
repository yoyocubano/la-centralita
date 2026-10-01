# Estrategia y Comparativa n8n: Cloud (SaaS) vs Autoalojado (Self-Hosted)
**Proyecto:** La Centralita (WELUX Events S.à r.l.)  
**Fecha Límite de Decisión:** Antes del 14 de octubre de 2026 (vencimiento del periodo de prueba Cloud).

---

## 1. Comparativa Directa: n8n Cloud vs Self-Hosted

| Criterio | **n8n Cloud (SaaS)** | **n8n Self-Hosted (Docker en VPS)** |
| :--- | :--- | :--- |
| **Coste Mensual** | **20 €/mes** (anual) o **24 €/mes** (Starter con 2.500 ejecuciones). Pro sube a **50 €/mes** (10.000 ejecuciones). | **0 € adicionales** (se aloja en el mismo VPS Hetzner de ~6-7 €/mes de la centralita). |
| **Límite de Ejecuciones** | **Estricto**: 2.500 ejecuciones/mes en plan base. Cada webhook, reintento o evento de WhatsApp descuenta 1. | **Ilimitado**: Cero restricción de volumen de llamadas, webhooks o tareas periódicas. |
| **Privacidad de Datos (RGPD Luxemburgo / CNPD)** | Los datos de contacto, nombres, teléfonos y transcripciones viajan por servidores multi-tenant de n8n. | **100% Soberanía**: Los datos nunca salen del contenedor Docker en Alemania/Luxemburgo. |
| **Control de Versiones & Backups** | Gestionado por n8n Cloud. | Exportación automática de flujos en JSON o guardado directo en repositorio Git local. |
| **Acceso a Red Local / Base de Datos** | Requiere exponer endpoints públicos con autenticación hacia internet. | Acceso directo por red interna Docker (`bridge`/`network`) a FastAPI, DocuSeal o PostgreSQL sin exponer puertos. |
| **Mantenimiento y Actualizaciones** | Cero mantenimiento; actualizaciones automáticas. | Requiere ejecutar `docker compose pull && docker compose up -d` periódicamente. |

---

## 2. Impacto Operativo para La Centralita

En un entorno comercial real con recepción de llamadas telefónicas y WhatsApp:
* Cada llamada genera al menos **3 ejecuciones**: (1) Extracción y parseo de lead, (2) Inserción idempotente en Google Sheets, (3) Notificación por email a gerencia.
* Si se activan eventos de WhatsApp Business (mensajes entrantes, notas de voz, confirmación de entrega), el consumo de ejecuciones se acelera rápidamente.
* En **n8n Cloud**, sobrepasar las 2.500 ejecuciones obliga a escalar al plan Pro (50 €/mes = 600 €/año).
* En **n8n Self-Hosted**, el coste marginal por llamada adicional es **0,00 €**.

---

## 3. Plan de Acción y Migración Recomendado (Antes del 14/10/2026)

### Paso 1: Respaldo del Workflow Actual (Desde n8n Cloud)
1. Acceder al dashboard de n8n Cloud actual (`weluxdigitalservices.app.n8n.cloud`).
2. Abrir el flujo de *La Centralita — Post-Call Processing*.
3. Hacer clic en los tres puntos superiores `...` y seleccionar **Download** (exporta el archivo `la_centralita_workflow.json`).

### Paso 2: Despliegue en Docker Compose (VPS Hetzner)
Se incluye en el `docker-compose.yml` del proyecto:
```yaml
version: '3.8'

services:
  n8n:
    image: docker.n8n.io/n8nio/n8n:latest
    container_name: centralita-n8n
    restart: unless-stopped
    ports:
      - "127.0.0.1:5678:5678"
    environment:
      - N8N_HOST=n8n.weluxevents.com
      - N8N_PORT=5678
      - N8N_PROTOCOL=https
      - NODE_ENV=production
      - WEBHOOK_URL=https://n8n.weluxevents.com/
      - GENERIC_TIMEZONE=Europe/Luxembourg
    volumes:
      - n8n_data:/home/node/.n8n

volumes:
  n8n_data:
```

### Paso 3: Exposición Segura con Cloudflare Tunnel
Vincular el servicio local `http://localhost:5678` a `n8n.weluxevents.com` mediante `cloudflared`. Queda protegido tras el WAF y SSL de Cloudflare sin abrir puertos en el cortafuegos.

### Paso 4: Importación y Actualización de URL en el Agente
1. Abrir `https://n8n.weluxevents.com/` e importar el archivo `la_centralita_workflow.json`.
2. Actualizar en el archivo `.env` del agente:
   ```bash
   N8N_WEBHOOK_URL=https://n8n.weluxevents.com/webhook/centralita-post-call
   ```

---

## 4. Conclusión y Recomendación

**Decisión Recomendada:** **Migrar a n8n Self-Hosted antes del 14 de octubre de 2026.**  
Permite mantener el presupuesto en **0 € de coste de suscripción**, elimina el riesgo de cortes de servicio por agotamiento de ejecuciones y asegura el cumplimiento estricto del RGPD para los clientes corporativos de Luxemburgo.
