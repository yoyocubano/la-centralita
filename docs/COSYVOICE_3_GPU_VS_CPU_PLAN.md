# Plan Técnico: CosyVoice 3 (GPU) y Alternativas Viables en CPU (Costo Cero)
**Proyecto:** La Centralita (WELUX Events S.à r.l.)  
**Fecha:** Octubre 2026

---

## 1. Por qué CosyVoice 3 no es viable en la máquina actual (CPU)

CosyVoice (desarrollado por Alibaba Tongyi Lab) es una arquitectura generativa de síntesis de voz basada en modelos de difusión y *flow-matching*:
* **Requisitos de Hardware:** Exige una tarjeta gráfica dedicada NVIDIA con arquitectura CUDA y un mínimo de **8 GB a 12 GB de VRAM** para inferencia en coma flotante FP16 o BF16.
* **Inferencia en CPU (Sin GPU):**
  * El factor de tiempo real (*Real-Time Factor* o RTF) en procesadores CPU estándar ronda entre **3.5 y 8.0**. Esto significa que generar 1 segundo de audio tarda entre 3.5 y 8 segundos de reloj.
  * En telefonía interactiva en tiempo real (WebRTC/SIP), la latencia perceptible debe ser **inferior a 800 ms** (idealmente < 300 ms). Un retraso de varios segundos interrumpe el diálogo natural y hace imposible una conversación fluida.

---

## 2. Alternativas Inmediatas y Viables Sin GPU (Fase Costo Cero)

Para mantener la centralita funcionando en tiempo real con cero costes y sin necesidad de tarjeta gráfica:

### Alternativa A: Piper TTS (Implementada y Activa en el Proyecto)
* **Tecnología:** Modelo VITS exportado a ONNX Runtime optimizado para CPU.
* **Voz Actual:** `es_ES-sharvard-medium.onnx` (español peninsular profesional).
* **Métricas Reales en CPU:**
  * **RTF (Real-Time Factor):** ~0.08 a 0.12 (genera 1 segundo de audio en ~80 milisegundos).
  * **Latencia al primer chunk de audio:** **120 – 180 ms**.
  * **Consumo de Memoria:** ~130 MB de RAM.
  * **Coste:** **0,00 €** (código abierto, sin llamadas a APIs de pago, totalmente local).
* **Evaluación:** Perfecta para pruebas de extremo a extremo y fase inicial de llamadas.

### Alternativa B: Kokoro-82M TTS (ONNX CPU)
* **Tecnología:** Modelo moderno de 82 millones de parámetros con calidad acústica muy superior, capaz de competir con voces comerciales cerradas.
* **Rendimiento:** Dispone de pesos cuantizados en ONNX que corren en CPU con latencias inferiores a 220 ms.
* **Soporte:** Voces en español disponibles.

### Alternativa C: Cloud TTS de Baja Latencia (Vía Créditos Gratuitos Existentes)
Si se desea una calidad de voz ultra-realista antes de contratar un servidor con GPU:
* **Deepgram Aura:** Síntesis de voz conversacional streaming con latencia < 200 ms. Utiliza los mismos créditos de prueba ($200) ya activos en la cuenta de Deepgram sin coste extra.
* **Cartesia Sonic:** Modelo de voz de última generación con latencia de streaming de 90-130 ms.

---

## 3. Plan de Despliegue para CosyVoice 3 (Al Disponer de VPS con GPU)

Cuando la empresa decida dar el salto a CosyVoice 3 con clonación de voz personalizada (voz corporativa idéntica de la marca WELUX):

### Arquitectura de Producción con GPU

```
[Cliente Teléfono / WebRTC] 
           │
           ▼
[LiveKit Server / Audio Room] 
           │
           ▼
[Agent Worker (Python)] ───(HTTP Streaming / gRPC)───► [Contenedor Docker CosyVoice 3]
                                                       (NVIDIA CUDA 12.x / vLLM / Triton)
                                                       (NVIDIA RTX 4000 Ada / L4 / RTX 3090)
```

### Opciones de Alojamiento para la GPU

1. **Opción 1: Servidor VPS con GPU en Europa (Hetzner / Scaleway / OVH)**
   * **Instancia:** Servidor dedicado o cloud con GPU NVIDIA (ej. Scaleway H100/L4 o subasta Hetzner con GPU).
   * **Despliegue:** Imagen Docker oficial `cosyvoice:v3` sirviendo un endpoint compatible con la API de OpenAI Audio (`/v1/audio/speech`).
   * **Ventaja:** Conexión de latencia fija muy baja (< 20 ms con el resto del backend).

2. **Opción 2: Serverless GPU (RunPod Serverless / GCP Cloud Run con GPU L4)** *(Recomendada)*
   * **Funcionamiento:** El contenedor de CosyVoice 3 se mantiene en espera o despierta solo cuando entra una llamada.
   * **Coste:** Solo se paga por los segundos exactos en que la IA está hablando (~0.0004 € por minuto de generación de voz). Cuando no hay llamadas entrantes, el coste es **0,00 €**.
   * **Escalabilidad:** Escala a cero sin pagar el coste mensual fijo de mantener una GPU encendida 24/7 (que suele costar entre 80 € y 250 €/mes).
