# Приоры каналов по архиву аналитиков

Топ типов событий по числу сюжетов; посты аналитических каналов в течение суток после события, факторы с цитатой размечает Gemma (шаг knowledge_archive). Приор = (вверх − вниз) / оценок; покрытие < 5 каналов — «мало покрытия», в скоринг не идёт. «Ручной» — `prior` из `config/event_types.yaml` (Р2).

| Тип | Сюжетов | Постов про событие | Фактор | Приор | Ручной | Каналов | Покрытие |
|---|---|---|---|---|---|---|---|
| corporate_results | 47 | 2 | trade_restrictions | -1.00 | — | 1 | мало покрытия |
| corporate_results | 47 | 2 | tax_burden | -1.00 | — | 1 | мало покрытия |
| monetary_signal | 46 | 11 | subsidized_lending | -1.00 | — | 3 | мало покрытия |
| monetary_signal | 46 | 11 | inflation | +0.71 | — | 3 | мало покрытия |
| monetary_signal | 46 | 11 | key_rate | +0.33 | — | 3 | мало покрытия |
| monetary_signal | 46 | 11 | labor_costs | -1.00 | — | 1 | мало покрытия |
| regulation_proposed | 38 | 2 | regulation | +1.00 | 0.25 | 1 | мало покрытия |
| regulation_proposed | 38 | 2 | labor_costs | +1.00 | — | 1 | мало покрытия |
| commodity_price | 26 | 7 | oil_gas | +0.50 | — | 4 | мало покрытия |
| commodity_price | 26 | 7 | petroleum_products | +1.00 | — | 3 | мало покрытия |
| commodity_price | 26 | 7 | import_prices | +1.00 | — | 2 | мало покрытия |
| commodity_price | 26 | 7 | logistics_costs | +1.00 | — | 2 | мало покрытия |
| inflation_data | 22 | 9 | inflation | -0.50 | — | 4 | мало покрытия |
| inflation_data | 22 | 9 | consumer_demand | +1.00 | — | 2 | мало покрытия |
| inflation_data | 22 | 9 | key_rate | +0.33 | — | 2 | мало покрытия |
| inflation_data | 22 | 9 | fx_rub | +1.00 | — | 1 | мало покрытия |
| energy_policy | 17 | 4 | oil_gas | +1.00 | — | 1 | мало покрытия |
| energy_policy | 17 | 4 | export_demand | +1.00 | — | 1 | мало покрытия |
| fx_move | 17 | 0 | — | — | — | 0 | мало покрытия |
| labor_policy | 17 | 1 | labor_costs | +1.00 | — | 1 | мало покрытия |
| state_support | 16 | 5 | subsidized_lending | -1.00 | — | 3 | мало покрытия |
| state_support | 16 | 5 | inflation | +1.00 | — | 1 | мало покрытия |
| state_support | 16 | 5 | key_rate | +1.00 | — | 1 | мало покрытия |
| state_support | 16 | 5 | regulation | -1.00 | — | 1 | мало покрытия |
| regulation_adopted | 15 | 1 | regulation | +1.00 | 0.5 | 1 | мало покрытия |
