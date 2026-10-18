# Comparativa Técnica de Hosting e Infraestructura: Hetzner vs Google Cloud vs Cloudflare
**Proyecto:** La Centralita (WELUX Events S.à r.l.)  
**Fecha:** Octubre 2026  
**Criterio:** Datos verificados de arquitecturas, capacidades reales de cómputo, límites de red y costes operativos.

---

## 1. Alcance y Cobertura Real por Plataforma

| Componente / Servicio | **Hetzner Online** | **Google Cloud Platform (GCP)** | **Cloudflare** |
| :--- | :--- | :--- | :--- |
| **VPS Tradicional (Docker / Linux)** | **Excelente**: Servidores Cloud (CPX/CAX) con vCPUs compartidas o dedicadas, NVMe rápido y root access. | **Completo**: Compute Engine (VMs e2/n2/c3) y Cloud Run (contenedores serverless autogestionados). | **No soportado**: No ofrece VPS Linux estándar ni acceso root; su arquitectura es serverless (V8 isolates). |
| **GPU para Inferencia (CosyVoice / LLM)** | **Parcial / Limitado**: Subasta de servidores dedicados y GPUs puntuales (RTX 4000 Ada); no disponible en VPS estándar. | **Líder absoluto**: A100, H100, L4, T4 en Compute Engine, Vertex AI y Cloud Run con GPU (on-demand). | **Workers AI**: Inferencia serverless de modelos preempaquetados (Whisper, Llama), pero sin contenedores con GPU propia. |
| **DNS Autoritativo & Anycast** | DNS básico sin Anycast global avanzado. | Cloud DNS con SLA 100%, pero con coste mensual por zona y consulta. | **Líder del mercado**: Anycast DNS ultrarrápido (< 12 ms global), gratuito e instantáneo. |
| **Reverse Proxy, WAF & Seguridad** | No nativo (requiere configurar Nginx/Caddy y CrowdSec/UFW en el VPS). | Cloud Armor (WAF empresarial avanzado, de alto coste base ~$5-50+/mes). | **Líder del mercado**: WAF, mitigación DDoS L3/L4/L7 ilimitada, SSL universal, Bot Management. |
| **Edge Compute (Workers)** | No soportado. | Cloud Functions / Cloud Run (arranque en frío de 300-1500 ms en contenedores). | **Líder del mercado**: Cloudflare Workers (arranque en 0 ms, 100.000 peticiones/día gratis). |
| **Hosting Frontend Estático** | Requiere servir mediante Nginx/Docker en el VPS. | Firebase Hosting (CDN global Google, plan Spark gratuito sin tarjeta). | **Cloudflare Pages**: Hosting Jamstack gratuito, ancho de banda ilimitado, previews automáticas por commit. |
| **Almacenamiento de Objetos (S3)** | Hetzner Storage Box / Object Storage (coste bajo, pero sin CDN global). | Google Cloud Storage (GCS) (alta disponibilidad, pero cobra salida a internet ~$0.12/GB). | **Cloudflare R2**: 100% compatible con AWS S3 y **0 € en tarifas de salida de datos (Zero Egress Fees)**. |
| **Túneles Seguros (Exposición sin IP pública)** | No nativo (requiere VPN WireGuard o Tailscale manual). | Identity-Aware Proxy (IAP) o VPC Peering. | **Cloudflare Tunnels (`cloudflared`)**: Expone servicios locales o Docker sin abrir puertos en el firewall. |

---

## 2. Análisis Detallado por Proveedor

### A. Hetzner Cloud (El motor de cómputo y ahorro)
* **Puntos Fuertes:**
  * **Relación Rendimiento/Precio imbatible:** Instancias como **CAX21** (4 vCPU ARM64, 8 GB RAM, 80 GB NVMe, 20 TB tráfico) por **~6.00 €/mes**, o **CPX21** (3 vCPU AMD, 4 GB RAM) por **~7.05 €/mes**.
  * **Ubicación Europea Óptima:** Centros de datos en Falkenstein, Núremberg (Alemania) y Helsinki (Finlandia). La latencia de red directa con Luxemburgo es de **14-18 ms**, ideal para WebRTC y telefonía.
  * **Libertad de Despliegue:** Docker Compose con FastAPI, n8n self-hosted, DocuSeal, LiveKit Agent y Piper TTS corriendo en el mismo nodo sin cuotas por petición.
* **Limitaciones:**
  * Mantenimiento 100% autogestionado (backups, parches de kernel, firewall UFW).
  * No tiene CDN global propia ni WAF de borde.

### B. Google Cloud Platform (La potencia enterprise y serverless)
* **Puntos Fuertes:**
  * **Cloud Run:** Despliegue de contenedores sin servidor con escalado a cero (paga solo por milisegundos de CPU consumidos). Ideal para FastAPI y webhooks de baja frecuencia.
  * **Cloud Run con GPU NVIDIA L4:** Permite correr modelos pesados como CosyVoice 3 o transcripción masiva pagando exclusivamente por segundo de llamada activa.
  * **Ecosistema Integrado:** Firebase Hosting, Secret Manager, Cloud Logging y Google Workspace / Sheets API con autenticación nativa por IAM.
* **Limitaciones:**
  * **Coste de Ancho de Banda Egress:** Transferir audio o datos hacia internet cuesta ~$0.08 a $0.12 por GB tras agotar el free tier.
  * Las VMs Compute Engine estándar son entre 3 y 5 veces más caras que Hetzner para specs equivalentes.

### C. Cloudflare (El escudo perimetral y borde serverless)
* **Puntos Fuertes:**
  * **Cloudflare Tunnels (`cloudflared`):** Permite conectar cualquier VPS de Hetzner o máquina local directamente a la red perimetral de Cloudflare. **No requiere abrir el puerto 80/443 en el router ni IP estática pública.**
  * **Cloudflare Pages:** Hosting estático para el panel de cliente de La Centralita con CDN global ultrarrápida, SSL automático y commits vinculados a GitHub.
  * **Cloudflare R2:** Almacenamiento de grabaciones de llamadas de audio (.wav / .mp3) con **cero coste de descarga**, a diferencia de GCS o AWS S3.
* **Limitaciones:**
  * No puede ejecutar procesos de larga duración (como el bucle WebRTC continuo de LiveKit o workers de n8n con temporizadores pesados).

---

## 3. Arquitectura Recomendada para La Centralita (La Trinidad Híbrida)

Para conseguir el **máximo rendimiento, máxima seguridad perimetral y coste cercano a cero**:

1. **Borde y Seguridad (Cloudflare):**
   * DNS para `weluxevents.com`.
   * Cloudflare Tunnel (`cloudflared`) conectando el dominio del panel y API hacia el backend.
   * Cloudflare Pages (o Firebase Hosting) sirviendo el Frontend del panel.
   * Cloudflare R2 para el archivo de grabaciones de llamadas.

2. **Cómputo Central (Hetzner Cloud VPS):**
   * VPS **CAX21** (ARM 4 vCPU, 8 GB RAM, ~6 €/mes) corriendo Docker Compose con:
     * `server/app.py` (FastAPI).
     * `agent/agent.py` (LiveKit Voice Agent con Piper TTS en CPU).
     * `n8n` (Self-hosted Community Edition ilimitado).
     * `docuseal` (Contratos locales).

3. **Inferencia Pesada / GPU (Bajo Demanda):**
   * En fase inicial: Piper TTS local en Hetzner (0 € en GPU).
   * Al requerir CosyVoice 3 con GPU: Endpoint serverless en **RunPod Serverless** o **GCP Cloud Run con GPU L4** que despierta solo cuando entra una llamada telefónica real.
