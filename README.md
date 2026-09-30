# Senderos Acueducto — monitor y auto-reserva

Script para vigilar los cupos de las caminatas de [Senderos del Acueducto de Bogotá](https://acueducto.com.co/senderos/),
avisar al celular cuando cambian y, opcionalmente, hacer **una** reserva automática cuando hay cupo.

Python 3, sin dependencias externas.

## Archivos

| Archivo         | Descripción                                                                          |
| --------------- | ------------------------------------------------------------------------------------ |
| `monitor.py`    | El script                                                                            |
| `config.json`   | Configuración y datos personales (**permisos 600, no se sube a git**)                |
| `config.example.json` | Plantilla de configuración                                                     |
| `state.json`    | Último estado visto de las franjas (se genera solo)                                  |
| `history.jsonl` | Histórico de cada consulta, para analizar a qué hora se abren cupos (se genera solo) |
| `booking.json`  | Registro de intentos de reserva (se genera solo)                                     |

## Cómo funciona la API

Endpoint único: `POST https://acueducto.com.co/senderos/s/` con JSON.

**Calendario:**

```json
{ "action": "calendar", "site": "18" }
```

Devuelve una lista de días con sus franjas:

```json
[
  {
    "date": "03/10/2026",
    "label": "sábado, 03 de octubre de 2026",
    "schedules": [
      {
        "id": 51,
        "schedule": "7:30 a.m.(Recorrido Guiado)",
        "capacity": 30,
        "count": 8
      }
    ]
  }
]
```

- `capacity` = cupo total, `count` = inscritos → **libres = capacity − count**.

**Reserva:**

```json
{
  "action": "save",
  "email": "...",
  "date": "03/10/2026",
  "site": "18",
  "schedule": "51",
  "participants": [
    {
      "name": "...",
      "gender": "1",
      "schooling": "5",
      "documentType": "0",
      "idnumber": "...",
      "country": "Colombia",
      "state": "BOGOTÁ D.C.",
      "city": "BOGOTA",
      "phone": "...",
      "eps": "...",
      "emergency": "...",
      "purpose": "0"
    }
  ]
}
```

Respuesta exitosa:

```json
[
  {
    "status": true,
    "message": "Se creó la reserva 859953 para 1 participantes.",
    "reservationId": "859953",
    "quantity": "1"
  }
]
```

No requiere CAPTCHA ni token.

## Configuración (`config.json`)

```bash
cp config.example.json config.json && chmod 600 config.json
```

```json
{
  "sites": { "17": [42, 44], "18": [51, 53], "22": null },
  "dates": [],
  "auto_book": false,
  "ntfy_topic": "",
  "email": "...",
  "participants": [ { ... } ]
}
```

| Campo                   | Descripción                                                                                           |
| ----------------------- | ----------------------------------------------------------------------------------------------------- |
| `sites`                 | Sitio → ids de franja a vigilar, en orden de preferencia. `null` = las 2 primeras franjas de cada día |
| `dates`                 | Fechas permitidas para reservar (`"dd/mm/aaaa"`). Vacío = cualquiera                                  |
| `auto_book`             | `true` para reservar automáticamente. Por defecto `false` (solo avisa)                                |
| `ntfy_topic`            | Topic de [ntfy.sh](https://ntfy.sh) para notificaciones. Vacío = solo imprime en consola              |
| `email`, `participants` | Datos del formulario de reserva. Un objeto por persona                                                |

Franjas conocidas (sep-2026):

| Sitio | id  | Franja                       |
| ----- | --- | ---------------------------- |
| 17    | 42  | 7:30 a.m.                    |
| 17    | 44  | 8:30 a.m. (Recorrido Guiado) |
| 18    | 51  | 7:30 a.m. (Recorrido Guiado) |
| 18    | 53  | 8:30 a.m.                    |
| 22    | —   | sin fechas publicadas aún    |

## Uso

```bash
python3 monitor.py              # una consulta
python3 monitor.py --loop 300   # cada 5 min (mínimo 60 s)
```

Con cron cada 5 minutos (`crontab -e`):

```
*/5 * * * * /usr/bin/python3 /home/fabian-reyes/senderos-monitor/monitor.py >> /home/fabian-reyes/senderos-monitor/cron.log 2>&1
```

### Notificaciones

1. Instalar la app **ntfy** (Android/iOS) y suscribirse a un topic difícil de adivinar (cualquiera que lo conozca puede leer los avisos).
2. Poner ese nombre en `ntfy_topic`.

Avisa cuando cambian los cupos libres de una franja vigilada, aparece una fecha nueva o desaparece una. La primera ejecución solo guarda el estado inicial.

## Lógica de reserva

- Elige la franja con cupo para todos los participantes: **fecha más próxima**, luego orden de `sites`, luego orden de franjas.
- Hace **una sola reserva en total** entre todos los sitios:
  - `status: true` → reserva hecha, se detiene.
  - `status: false` → rechazo explícito, reintenta en la siguiente ejecución (máx. 3).
  - Error de red / respuesta desconocida → se detiene para no duplicar. Revisar correo o la web.
- Para volver a habilitar la reserva automática: borrar `booking.json`.

## Buenas prácticas

- No bajar el intervalo de 1 minuto: es un servicio público.
- Reservar solo para quienes realmente van a asistir; cancelar si no se va a ir.
- Mantener `config.json` privado (contiene documento, EPS, teléfono).

## Historial

- 2026-09-28 — Reserva **859953**: sitio 18, sábado 03/10/2026, 7:30 a.m. (Recorrido Guiado), 1 participante.
