# ✈️ Flight Hunter — Cazador de vuelos baratos

Monitor automático que rastrea **3 tramos one-way** en Google Flights (vía `fast-flights`), con fechas semi-flexibles (±3 días), y te avisa por **Telegram** cuando aparece un precio excepcional. Corre solo cada 6 horas en **GitHub Actions** (gratis, sin dejar tu PC encendida).

**Tu viaje:** Tokio → China → Los Ángeles (LAX) → Panamá (PTY / Tocumen)

---

## Cómo funciona

- Para cada tramo prueba **todas las combinaciones** de aeropuertos posibles (ej. NRT/HND × PVG/PEK/CAN/HKG) y las fechas dentro de ±3 días, y se queda con **el más barato**.
- Guarda un **historial de precios** (`history.json`) para calcular una **línea base** (precio promedio y mínimo visto).
- **Te alerta** cuando: (a) el precio baja de un umbral que fijes, o (b) si no fijaste umbral, cuando el precio cae al menos un 12% bajo el promedio histórico **y** es el más barato registrado.
- Una vez al día te manda un **resumen** con el mejor precio actual de cada tramo.

> ⚠️ Es una herramienta de apoyo, no infalible. El scraping puede fallar puntualmente si Google cambia algo o bloquea temporalmente; el sistema simplemente reintenta en la siguiente corrida.

---

## Instalación (15 min, una sola vez)

### 1. Crea tu bot de Telegram
1. En Telegram, abre **@BotFather** → envía `/newbot` → sigue los pasos.
2. Copia el **token** que te da (algo como `123456:ABC-DEF...`).
3. Escríbele un mensaje cualquiera a **tu nuevo bot** (para "activarlo").
4. Obtén tu **CHAT_ID**: abre en el navegador
   `https://api.telegram.org/bot<TU_TOKEN>/getUpdates`
   y busca `"chat":{"id":XXXXXXX}`. Ese número es tu chat_id.

### 2. Sube el proyecto a GitHub
1. Crea un repositorio **privado** nuevo en GitHub (ej. `flight-hunter`).
2. Sube todos estos archivos (arrastra la carpeta o usa git).

### 3. Guarda tus credenciales como secretos
En tu repo: **Settings → Secrets and variables → Actions → New repository secret**. Crea dos:
- `TELEGRAM_BOT_TOKEN` → el token de BotFather
- `TELEGRAM_CHAT_ID` → tu chat_id

### 4. Activa Actions
Ve a la pestaña **Actions** del repo y habilítalas si te lo pide. El monitor ya correrá cada 6 horas.

### 5. Pruébalo ahora mismo
En **Actions → Flight Hunter → Run workflow**. Revisa los logs; en unas corridas deberías empezar a recibir el resumen en Telegram.

---

## Personalizar tu viaje

Edita **solo `config.json`**:

### Formatos de fecha (cada tramo acepta uno de estos tres)

```jsonc
"fechas": ["2027-04-03", "2027-04-04"]                    // lista exacta de días
"rango_fechas": { "desde": "2027-04-17", "hasta": "2027-04-23" }  // rango inclusivo
"fecha_salida": "2027-04-03"                              // usa flex_days (±N días)
```

| Campo | Qué es |
|---|---|
| `fechas` / `rango_fechas` / `fecha_salida` | Días a rastrear (ver arriba). |
| `flex_days` | Margen ± usado solo si el tramo usa `fecha_salida`. |
| `origen` / `destino` | Lista de aeropuertos candidatos. Puedes añadir/quitar (ej. agregar `"KIX"` de Osaka). |
| `umbral_precio` | Pon un número (ej. `350`) para alertar bajo ese precio. Déjalo en `null` para usar la línea base automática. |
| `baja_pct_alerta` | % de caída bajo el promedio que dispara alerta (ahora 12). |
| `clase` | `economy`, `premium-economy`, `business`, `first`. |
| `max_escalas` | `null` = sin límite, o un número (ej. `1`). |

Tras cambiar el precio umbral, no necesitas hacer nada más: la próxima corrida ya lo usa.

---

## Notas sobre horarios
GitHub Actions usa **UTC**. Panamá es **UTC−5**. El cron `0 */6 * * *` corre a las 00:00, 06:00, 12:00, 18:00 UTC (= 19:00, 01:00, 07:00, 13:00 en Panamá). El resumen diario sale en la corrida de las 00:00 UTC.

## Costo
Todo gratis: GitHub Actions da minutos de sobra para esto y Telegram no cuesta nada.
