# Política de Cookies y Tecnologías de Almacenamiento Local
**Servicio:** Monitor en Vivo / Panel del Cliente — La Centralita  
**Entidad:** WELUX Events S.à r.l. (Luxemburgo)  
**Fecha de Actualización:** Octubre 2026

---

## 1. ¿Qué son las Cookies y Almacenamiento Local?
Una cookie es un pequeño archivo de texto que los sitios web descargan en su navegador al visitarlos. El almacenamiento local (`localStorage` y `sessionStorage`) permite a las aplicaciones web almacenar datos en el navegador del usuario para recordar estados de sesión y preferencias de visualización.

---

## 2. Uso Estricto y Técnico en el Panel de La Centralita

El panel web de La Centralita está concebido como una herramienta profesional de gestión en tiempo real. **No contiene cookies de publicidad, cookies comportamentales ni rastreadores de terceros.**

Únicamente se emplean tecnologías técnicas estrictamente necesarias para el funcionamiento seguro:

| Elemento / Clave | Tipo | Finalidad | Duración |
| :--- | :--- | :--- | :--- |
| `centralita_token` | Almacenamiento Local (`localStorage`) | Token criptográfico de autenticación del operador/cliente para autorizar peticiones hacia la API (`Authorization: Bearer`). | Hasta cierre de sesión explícito |
| `centralita_dual_mode` | Almacenamiento Local (`localStorage`) | Recuerda la preferencia de interfaz del operador (Pantalla Dual Split, Lado Cliente o Lado Interno). | Persistente (1 año) |
| `centralita_theme` | Almacenamiento Local (`localStorage`) | Almacena el modo de contraste y preferencias de visualización del monitor. | Persistente (1 año) |
| `cf_clearance` / `__cf_bm` | Cookie Técnica (Cloudflare) | Protección perimetral contra ataques DDoS y validación de tráfico humano legítimo. | Sesión / 30 minutos |

---

## 3. Base Jurídica
El uso de cookies y almacenamiento de carácter estrictamente técnico está exento de la obligación de recabar consentimiento previo con arreglo al **Artículo 5(3) de la Directiva ePrivacy (Directiva 2002/58/CE)** y la transposición legal luxemburguesa, al ser estrictamente indispensable para proporcionar un servicio expresamente solicitado por el usuario.

---

## 4. Gestión y Eliminación
El usuario puede en cualquier momento:
1. Eliminar los datos almacenados localmente a través de las opciones de configuración de su navegador web (Historial -> Borrar datos de navegación -> Almacenamiento y cookies).
2. Cerrar la sesión en el panel pulsando el botón "Cerrar Sesión", lo que purga automáticamente el token de acceso local.
