📌 Resumen técnico – Infra + n8n + Ollama (SerenArt / OPIT)
1️⃣ Infraestructura base
VPS

Proveedor: OVH

SO: Debian 12

Specs: 4 vCPU · 8 GB RAM · 75 GB NVMe

Ubicación: Europa (Francia)

Acceso: SSH con usuario admin, root deshabilitado

Seguridad básica:

SSH en puerto 50022

UFW activo

Fail2ban funcionando

Acceso por clave SSH

2️⃣ Stack Docker (infra)

Todo corre bajo Docker Compose, con red interna dedicada:

infra/
 ├─ docker-compose.yml
 ├─ nginx/
 ├─ n8n/
 ├─ postgres/
 ├─ redis/
 ├─ ollama/
 └─ qdrant/

Servicios activos

nginx → reverse proxy + TLS

certbot → certificados Let’s Encrypt (en contenedor)

n8n → automatizaciones (único punto de entrada público)

postgres → DB principal de n8n

redis → colas / cache

ollama → LLMs locales

qdrant → vector DB (embeddings)

Todos conectados por red Docker interna (serenart_net).

3️⃣ Dominio + HTTPS

Dominio: optimumprimeit.com

Subdominio usado:
👉 automation.optimumprimeit.com

DNS: A Record → IP del VPS

HTTPS:

Certbot en contenedor

Certificados válidos

Nginx configurado con SSL

Resultado:

n8n accesible solo por HTTPS

Secure cookies activas (sin errores)

4️⃣ n8n (estado actual)

Instancia: Self-hosted

Licencia: Lifetime cargada ✅

Acceso:
👉 https://automation.optimumprimeit.com

Persistencia:

Volumen ./n8n con permisos corregidos

Uso previsto:

Backend de orquestación IA para SerenArt

Recepción de recursos (files)

Llamadas a Ollama

En el futuro: embeddings + Qdrant

5️⃣ Ollama
Modelos descargados y funcionando

Texto / chat

llama3.2:3b

qwen2.5:7b

gemma2:2b

phi4-mini

Code

qwen2.5-coder:7b

Vision

moondream

llava:7b

qwen2.5-vl:7b

Embeddings

nomic-embed-text

bge-m3

👉 Ollama responde correctamente vía HTTP desde n8n.

6️⃣ Primer flujo de n8n (Resources Intake)
Entrada

Webhook POST

Recibe un objeto de recurso (imagen, pdf, audio, etc.)

Ejemplo:

{
  "resource_id": "...",
  "url": "...",
  "type": "images",
  "title": "...",
  ...
}

Flujo lógico

Webhook (POST)

DetectType

Normaliza type

Define detected_type (image, document, audio…)

RouteByType

Si image → rama Vision

DownloadImageFile

Descarga la imagen (Response = File)

El archivo queda en binary.data

LLM Vision (Ollama – moondream)

Describe lo que ve en la imagen

Respond to Webhook

Devuelve la descripción a la plataforma SerenArt

Problemas resueltos

❌ undefined url → causado por pérdida de JSON tras download

✅ Solución:
Usar referencias explícitas:

{{$node["DetectType"].json.url}}


❌ Error Unused Respond to Webhook

✅ Solución: un solo Respond to Webhook correctamente conectado

7️⃣ Estado actual

✅ Infra estable
✅ HTTPS funcionando
✅ n8n operativo
✅ Ollama operativo
✅ Primer flujo funcional
✅ Vision AI funcionando
🔒 Único endpoint público: n8n

8️⃣ Próximos pasos naturales (cuando sigamos)

Audio → Whisper (STT)

PDFs → parsing + embeddings

Guardar embeddings en Qdrant

Conversaciones multimodales

Versionado de flujos

Auth por API keys entre SerenArt ↔ n8n

Eventual exposición controlada de Ollama

Si querés, en el próximo mensaje podemos:

Congelar este resumen como “baseline”

O empezar directo con el flujo de audio / embeddings

O diseñar la arquitectura semántica (RAG) de SerenArt