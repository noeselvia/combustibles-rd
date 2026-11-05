# Combustibles RD

**Ver en vivo:** https://noeselvia.github.io/combustibles-rd/

Monitor de los precios oficiales de combustibles en República Dominicana. Junta en una sola página lo que hoy está repartido entre PDFs del MICM, hojas de Excel del Banco Central y series de EE. UU.:

- **Precios de esta semana** de gasolina, gasoil, GLP y kerosene, con el cambio y el subsidio por galón
- **Desglose del precio**: cuánto es costo de importación, impuestos, márgenes y transporte
- **Histórico semanal desde 2010**, con filtros por período y combustible
- **Petróleo WTI vs. gasolina** en la misma base, y **gasolina RD vs. EE. UU.** en dólares
- **Calculadora** de gasto semanal, mensual y anual según vehículo y kilómetros
- **Estaciones registradas** por provincia

> **English summary:** A data pipeline + dashboard for Dominican Republic fuel prices. Python scrapes the government's weekly PDF notices and open-data files, cleans 16 years of messy weekly data, joins it with oil prices (FRED) and the central bank's exchange rate, and a GitHub Action refreshes it every Saturday.

## Cómo funciona

```
MICM (CSV histórico + avisos PDF) ─┐
Banco Central (tasa, Excel) ───────┼─► pipeline/build_data.py ─► data/fuel.json ─► index.html
FRED (WTI, gasolina EE. UU.) ──────┤
MICM (estaciones registradas) ─────┘
   GitHub Actions lo corre el viernes en la noche (y el sábado de respaldo), guarda los datos y publica en GitHub Pages
```

La página tiene tres piezas propias del tema: el **letrero** de la bomba con dígitos LED, el **recibo de un galón** que separa importación, impuestos, márgenes y subsidio, y el **surtidor** que calcula tu gasto.

**Limpieza que hace el pipeline** (los datos públicos vienen con errores):

- Fechas de semana inconsistentes en el CSV del MICM ("MES" a veces es el del inicio y a veces el del final; erratas como "Febreo"). Se reconstruye cada semana anclándola al sábado.
- Picos de una sola semana que vuelven al valor anterior (p. ej. GLP 133 → 164 → 133) se marcan como error de captura y se omiten.
- El CSV trae precios redondeados; las semanas recientes se toman de los avisos PDF, con decimales y con el desglose de impuestos, márgenes y subsidio.
- Nombres de provincias escritos de varias formas se unifican.
- Si una fuente no responde, se usa la última copia descargada.

## Correrlo

```bash
pip install -r requirements.txt
python pipeline/build_data.py
python -m http.server 8000
```

Luego abre http://localhost:8000. Para generar la carpeta que publica GitHub Pages: `python pipeline/build_site.py`.

## Actualización automática

El workflow [`.github/workflows/actualizar.yml`](.github/workflows/actualizar.yml) corre los viernes a las 7:00 p. m. (hora de RD), cuando el MICM ya anunció los precios de la semana, y los sábados a las 10:00 a. m. de respaldo por si el aviso se subió tarde. También corre con cada push a `main` o a mano desde la pestaña Actions:

1. Descarga y limpia todas las fuentes.
2. Si hay datos nuevos, los guarda en el repo con un commit.
3. Arma `_site/` y lo publica en GitHub Pages.

## Fuentes

| Dato | Fuente |
|---|---|
| Precios semanales 2010-hoy | [MICM, datos abiertos](https://datos.gob.do/dataset/precios-de-los-combustibles) |
| Avisos semanales con desglose | [MICM, avisos de precios](https://micm.gob.do/direcciones/combustibles/avisos-semanales-de-precios/avisos-semanales-de-precios-de-combustibles/) |
| Tasa de cambio | [Banco Central RD](https://www.bancentral.gov.do/a/d/2538-mercado-cambiario) |
| Petróleo WTI | [FRED DCOILWTICO](https://fred.stlouisfed.org/series/DCOILWTICO) |
| Gasolina regular EE. UU. | [FRED GASREGW](https://fred.stlouisfed.org/series/GASREGW) |
| Estaciones registradas | [MICM, datos abiertos](https://datos.gob.do/dataset/estaciones-registradas) |

Los rendimientos de la calculadora son valores típicos, no oficiales.
