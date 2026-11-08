# Tiempo Campero — datos

Ayudante automático de la app **Tiempo Campero** (Mendoza, Argentina).

Dos veces por día calcula el puntaje de zonda para tres zonas de Mendoza y lo deja en
[`datos/zonda.json`](datos/zonda.json), que la app lee:

- **19:30 h**: aviso para mañana (modelos globales de Open-Meteo, ensamble GEFS de NOAA y WRF 4 km del SMN, corrida 18 UTC).
- **06:00 h**: actualización para hoy (suma las corridas 00 y 06 UTC del WRF).

Zonas: `norte` (Gran Mendoza y el Este), `valle` (Valle de Uco), `sur` (San Rafael, General Alvear, Malargüe).
Niveles: `2` = zonda probable, `1` = posible zonda, `0` = sin aviso.

El puntaje (`modelo_zonda.json`) se entrenó con los zondas medidos en El Plumerillo y San Rafael
(2024-2026) y, en el Valle de Uco, con días de aire muy seco en estaciones del INTA.
En la validación, con el aviso más estricto acertó alrededor de 72% en Gran Mendoza, 41% en San Rafael y 45% en el Valle de Uco.

Fuentes: Open-Meteo, NOAA GEFS (AWS Open Data), SMN Argentina WRF (AWS Open Data).
