# La Centralita — Base legal (privacidad, términos y cookies)

> **Documento base de trabajo — versión 0.2 · 21/10/2026.**
> Cobertura: Unión Europea (RGPD/ePrivacy/Reglamento de IA), EE. UU.
> (**TCPA**, grabación de llamadas por estado, CCPA/CPRA) y Latinoamérica
> (ver §8). Describe el tratamiento de datos del producto en su estado
> actual de prototipo y sirve de punto de partida para su comercialización.
> **No constituye asesoramiento jurídico.** Antes de vender o desplegar el
> producto con clientes reales debe revisarlo un abogado especializado en
> protección de datos y telecomunicaciones (ver la lista de brechas al
> final).

---

## 1. Quién es quién (roles RGPD)

- **Proveedor del producto:** WELux Digital Services, Luxemburgo
  (en adelante, «WELux»). Contacto en materia de privacidad:
  **info@weluxevents.com**.
- Cuando un negocio contrata La Centralita, **el negocio es el responsable
  del tratamiento** de los datos de sus llamantes y **WELux actúa como
  encargado del tratamiento** (art. 28 RGPD): trata los datos solo por
  cuenta del negocio y siguiendo sus instrucciones documentadas (contrato
  de encargo / DPA pendiente de formalizar — ver checklist).
- WELux es responsable únicamente de los datos de su propia web/panel de
  demostración y de la relación comercial con el negocio cliente.

## 2. Qué datos personales toca el producto

| Categoría | Datos | Origen |
|---|---|---|
| Voz del llamante | Audio de la llamada en tiempo real (streaming) | Llamada telefónica / web |
| Transcripción | Texto íntegro de la conversación generado por STT | Deepgram sobre el audio |
| Datos del lead | Nombre, teléfono, motivo de la llamada y resumen extraídos de la transcripción | Extracción automática (LLM) |
| Datos de contacto del negocio | Nombre del negocio, correo del propietario, configuración del agente | Alta del servicio |
| Datos técnicos | Sala/room, marcas de tiempo, registros de conexión, IP del visitante de la web pública | LiveKit / servidor |
| Credencial del operador | Token de acceso al panel (se guarda en `localStorage` del navegador del operador) | Panel de administración |

**La voz es un dato especialmente delicado en la práctica**: puede revelar
salud, opiniones o identidad, y la transcripción conserva el contenido de
lo que el llamante diga. El sistema debe tratarse con la diligencia de una
categoría especial aunque la voz no se use con fines biométricos (no se
realiza identificación biométrica; si algún día se identificara por voz,
aplicarían además leyes de biometría como la BIPA de Illinois).

## 3. Finalidades y base jurídica (UE)

| Finalidad | Base jurídica (a confirmar por el responsable) |
|---|---|
| Atender la llamada con un agente de voz con IA | Ejecución del servicio solicitado por el llamante / interés legítimo del negocio |
| Transcribir la conversación para poder responder y generar el lead | La misma; **la transcripción se anuncia al inicio de la llamada** (ver §7) |
| Extraer y entregar el lead al negocio (panel, hoja de cálculo o webhook) | Ejecución del contrato negocio–WELux |
| Avisar al propietario de la llamada/lead | Ejecución del contrato |
| Seguridad y prevención de abusos (autenticación del panel, registros) | Interés legítimo |

Si en producción se **graba el audio** de las llamadas (p. ej. con LiveKit
Egress), la base y el régimen cambian: ver §7 y el checklist. En el estado
actual el sistema **transcribe en tiempo real y conserva la transcripción y
el lead**; no hay grabación de audio persistente configurada.

## 4. Encargados y terceros que intervienen (subencargados)

| Tercero | Función | Territorio / nota |
|---|---|---|
| **LiveKit Cloud** | Transporte de audio en tiempo real (WebRTC) | EE. UU. — exige DPA y cláusulas tipo (SCC) |
| **Deepgram** | Transcripción voz→texto (STT) | EE. UU. — exige DPA y SCC |
| **DeepSeek** | Extracción del lead desde la transcripción (LLM) | **China — país sin decisión de adecuación de la UE. Riesgo alto: antes de producción hay que sustituirlo por un proveedor con garantías del art. 46 RGPD o alojado en la UE, o anonimizar la transcripción antes de enviarla.** |
| **Piper TTS** | Síntesis de voz | Local (en el servidor): sin transferencia |
| **ElevenLabs** (solo si se activa TTS premium) | Síntesis de voz | EE. UU. — exige DPA y SCC |
| **n8n Cloud** (solo si se usa el flujo webhook) | Automatización: entrega del lead y avisos | Según instancia; exige DPA |
| **Google Sheets** (solo si el negocio vuelca leads ahí) | Hoja de leads del negocio | Google (EE. UU./global) — cuenta del negocio |
| **DocuSeal** | Firma de contratos (enlace desde la web) | Tercero independiente para la firma |
| **jsDelivr + Google Fonts** | CDN de la librería LiveKit y tipografías de la web pública | Reciben la IP del visitante al cargar la página |
| **Operador de telefonía** (Telnyx/Vonage u otro, fase 2) | Número de teléfono y encaminamiento de llamadas | Pendiente de contratar: exigir DPA y números del territorio del cliente |

La lista vigente de subencargados debe mantenerse actualizada y comunicarse
al negocio cliente antes de cualquier cambio.

## 5. Conservación

- Transcripciones y leads: **solo el tiempo necesario para entregar el
  lead al negocio**; la política de retención concreta (p. ej. 30 días)
  debe fijarla el contrato con cada cliente antes de producción (hoy no
  hay una política de borrado automático implementada — ver checklist).
- Registros técnicos: máximo 90 días recomendado.
- El negocio cliente es responsable de cuánto conserva los leads en sus
  propios sistemas (panel, hoja de cálculo, CRM).

## 6. Derechos de las personas (UE)

Los llamantes pueden ejercer los derechos de acceso, rectificación,
supresión, oposición, limitación y portabilidad (arts. 15–22 RGPD) ante el
**negocio al que llamaron** (responsable). WELux asistirá al negocio para
atenderlos. Para datos de los que WELux es responsable (web/panel demo y
relación comercial): **info@weluxevents.com**. Respuesta en el plazo legal
de un mes. También cabe reclamar ante la autoridad de control (en
Luxemburgo, la CNPD).

## 7. Aviso al llamante: IA, transcripción y grabación (UE)

1. **Transparencia de IA (art. 50 del Reglamento de IA de la UE):** el
   llamante debe saber, desde el inicio de la llamada, que habla con un
   sistema de IA y no con una persona. El saludo del agente debe decirlo
   expresamente.
2. **Transcripción:** el aviso inicial debe indicar que la conversación se
   transcribe para atender la solicitud y generar un resumen para el
   negocio.
3. **Grabación (si se activa):** grabar comunicaciones está regulado
   además por la normativa de secreto de las comunicaciones y ePrivacy; el
   criterio prudente en Luxemburgo/UE es **consentimiento de todos los
   interlocutores** antes de grabar, con opción de continuar sin
   grabación. La redacción exacta del aviso y la validez del
   consentimiento telefónico debe confirmarla un abogado luxemburgués
   antes de activar cualquier grabación en producción.
4. **Menores y datos sensibles:** si un llamante revela datos de salud u
   otros datos sensibles, el negocio debe tratarlos con las garantías del
   art. 9 RGPD; el agente no debe solicitarlos activamente.

   **Guion mínimo del aviso inicial (a validar por abogado):** «Hola,
   gracias por llamar a [negocio]. Soy un asistente virtual con
   inteligencia artificial. Esta llamada se transcribe para poder atender
   su solicitud y pasar el recado al equipo. Si continuamos, entendemos
   que está de acuerdo.» — Si hay grabación: «…y también se graba…» antes
   de cualquier contenido.

## 8. Cobertura EE. UU. y Latinoamérica

**EE. UU. — el régimen de llamadas es el punto crítico**
- **TCPA + FCC (llamadas con voz de IA):** la FCC (2024) declaró que la
  voz generada por IA es «voz artificial» a efectos del TCPA. Para
  llamadas **salientes** de marketing con voz de IA se exige
  **consentimiento previo expreso y por escrito** del destinatario;
  prohibido llamar a números del registro **Do Not Call**, con deberes de
  identificación (quién llama y en nombre de quién) y de baja/opt-out
  también para mensajes. La Centralita está pensada para llamadas
  **entrantes**, pero cualquier devolución de llamada automática hereda
  este régimen.
- **Grabación por estados (one-party / two-party):** la ley federal permite
  grabar con el consentimiento de **una** parte, pero estados como
  **California, Florida, Illinois, Maryland, Massachusetts, Pensilvania o
  Washington exigen el consentimiento de TODAS las partes**. Regla de
  diseño obligatoria: **aviso de grabación/transcripción al inicio de cada
  llamada y consentimiento del llamante**, válido en cualquier estado; en
  la práctica, aplicar el estándar «two-party» siempre.
- **CCPA/CPRA (California):** aviso en la recogida (la locución inicial
  cumple esa función), derechos de saber, borrar y corregir de llamantes
  californianos; **no vendemos datos personales**; la transcripción es
  «información personal» y su contenido sensible goza de límite de uso.
- **FTC (protección del consumidor):** prohibido que la voz de IA se
  presente como humana o prometa lo que el negocio no cumple; la
  suplantación por IA es una prioridad de enforcement.
- **ADA:** la web comercial y el panel deben cumplir WCAG antes de
  publicar en EE. UU.; el servicio telefónico con IA debe prever derivación
  a una persona para quien no pueda usarlo.

**Latinoamérica (si se ofrece allí)**
- **México (LFPDPPP):** aviso de privacidad (la locución inicial + aviso
  completo accesible), derechos **ARCO** del llamante; la grabación de
  llamadas exige aviso y finalidad declarada.
- **Brasil (LGPD):** base legal y derechos del titular (ANPD); la grabación
  con aviso previo es práctica estándar en atención al cliente; portugués
  si se opera en Brasil.
- **Argentina (Ley 25.326)**, **Colombia (Ley 1581)** y **Cuba (Ley de
  Protección de Datos Personales):** consentimiento/autorización del
  titular para tratar su voz y sus datos, con autoridades AAIP/SIC y
  derechos equivalentes.
- En todos: la defensa del consumidor sanciona que un bot se haga pasar
  por humano o prometa condiciones falsas en nombre del negocio.

## 9. Cookies y almacenamiento local (web pública y panel)

- La web pública y el panel **no usan cookies propias ni analítica ni
  rastreadores publicitarios** en su estado actual.
- Almacenamiento técnico: el panel guarda la credencial del operador en
  `localStorage` del navegador (necesario para el acceso; se borra al
  cerrar sesión). Es almacenamiento estrictamente necesario (exento de
  consentimiento ePrivacy), pero se declara aquí por transparencia.
- Recursos de terceros al cargar la web (librería LiveKit desde jsDelivr,
  Google Fonts, imágenes): el navegador del visitante contacta con esos
  terceros, que reciben su IP. Antes de publicar, valorar el
  autoalojamiento de fuentes y librerías para eliminar esa cesión de IP.
- **Banner de cookies: no instalado** (prototipo no publicado y sin cookies
  no esenciales). Será obligatorio antes de publicar si se añade analítica
  o cualquier cookie/almacenamiento no esencial.

## 10. Términos de uso (resumen base)

- El servicio atiende llamadas con un agente de voz con IA y entrega al
  negocio un resumen (lead) de cada llamada. **No sustituye la atención
  humana** en urgencias ni en asuntos que requieran criterio profesional
  (médico, legal, emergencias): el agente debe derivar esos casos.
- La calidad de la transcripción y de la extracción puede contener
  errores; el negocio debe verificar los leads antes de actuar sobre
  ellos.
- Uso aceptable: prohibido usar el servicio para llamadas no solicitadas
  en frío incumpliendo la normativa de comunicaciones comerciales
  (TCPA/ePrivacy y equivalentes), suplantación o fines ilícitos.
- Venta a consumidores en la UE: antes de vender a consumidores habrá que
  añadir la información precontractual, el derecho de desistimiento y las
  condiciones de la Directiva (UE) 2019/770 de contenidos digitales.

## 11. Footer-ready conditions (English — copy-paste for website footer)

> **Privacy Notice (summary).** La Centralita is a voice-AI answering
> service operated by WELux Digital Services, Luxembourg, on behalf of the
> business you are calling — that business is responsible for your data
> and we process it only on their instructions. Your call is answered by
> an **artificial-intelligence assistant**, and the conversation is
> **transcribed** so the business can receive a summary of your request
> (your name, phone number and reason for calling). We do not sell
> personal information. You can exercise your rights (GDPR in the EU;
> CCPA/CPRA rights — know, delete, correct — in California) with the
> business you called, or contact us at info@weluxevents.com.
>
> **Call notice (played at the start of every call).** "Hello, thank you
> for calling [Business]. You are speaking with an AI virtual assistant.
> This call is transcribed so we can handle your request and pass a
> summary to the team." Where a call is recorded, the notice says so
> before any content is exchanged, and continuing the call after the
> notice constitutes consent where the law requires it.
>
> **Terms of Use (summary).** The AI assistant takes messages and answers
> basic questions; it does not replace human attention for emergencies or
> professional matters. Transcripts and summaries may contain errors — the
> business verifies them before acting. Outbound marketing calls with AI
> voice require the recipient's prior express written consent (TCPA); the
> business is responsible for its calling practices.
>
> **Cookies.** The public site and operator panel use no tracking or
> advertising cookies and no analytics. The panel stores the operator's
> access token in the browser's local storage (strictly necessary to stay
> signed in). Third-party CDNs (LiveKit library, fonts) receive the
> visitor's IP address when pages load.

## 12. Checklist — brechas antes de vender o desplegar con clientes reales

> Marca de protección: qué situación cubre cada medida.

- [ ] Revisión y adaptación de este documento por **abogado** (RGPD +
      telecomunicaciones, Luxemburgo; TCPA/grabación si hay clientes o
      llamantes en EE. UU.).
      → *Protege contra: reclamaciones por grabaciones y transcripciones
      sin consentimiento válido (el mayor riesgo legal del producto).*
- [ ] **Aviso inicial implantado y registrado** en cada llamada (IA +
      transcripción; grabación si se activa), con guion validado y opción
      de hablar con una persona.
      → *Protege contra: disputas «nadie me avisó de que era un robot / de
      que me grababan» (Reglamento de IA art. 50, leyes two-party, FTC).*
- [ ] **Contrato de encargo (DPA) WELux–cliente** firmado, con lista de
      subencargados y mecanismo de notificación de cambios.
      → *Protege contra: disputas de responsabilidad cuando un llamante
      ejerce derechos o hay una brecha (uso de datos).*
- [ ] **DPA/SCC con LiveKit y Deepgram**; decisión documentada sobre
      **DeepSeek (China)**: sustituir, o evaluación de impacto de
      transferencia (TIA) + anonimización previa de la transcripción.
      → *Protege contra: reclamaciones por transferencia internacional de
      conversaciones a China sin garantías.*
- [ ] Reglas TCPA para cualquier llamada saliente: consentimientos,
      Do Not Call, opt-out, identificación.
      → *Protege contra: reclamaciones y multas TCPA (responsabilidad por
      llamada, con daños por infracción).*
- [ ] Política de **retención y borrado automático** de transcripciones y
      leads implementada y probada, con salida del cliente (exportación +
      borrado) pactada en contrato.
      → *Protege contra: disputas a la cancelación del servicio y por
      conservación excesiva de conversaciones.*
- [ ] Registro de actividades de tratamiento (art. 30 RGPD) de WELux y
      plantilla para el cliente.
- [ ] Evaluación de impacto (EIPD/DPIA) recomendada: tratamiento
      sistemático de conversaciones de terceros mediante IA.
- [ ] Contrato con el **operador de telefonía** (números del territorio,
      DPA, secreto de las comunicaciones) antes de la fase 2 con número
      real.
- [ ] Retirar de la web pública cualquier resto de material de prueba
      (p. ej. la URL del webhook de test de n8n aún referenciada en el
      código del frontal) y autoalojar fuentes/librerías si se publica.
- [ ] Banner de consentimiento si se añade analítica o cookies no
      esenciales; **accesibilidad ADA/WCAG** de la web si se publica en
      EE. UU.
- [ ] Procedimiento de brechas de seguridad (notificación 72 h) y canal de
      ejercicio de derechos probado con un cliente piloto.
- [ ] Nada se vende sin probar el servicio de punta a punta con un cliente
      piloto y su DPA firmado.
