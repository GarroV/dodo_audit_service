# Блок: domain

## Назначение

Единственная точка доступа к предметной области: чек-лист, зоны, состояние проверки, вызов движка. Ни один другой блок не открывает `inspection.json` и не запускает `audit.py`.

## API-контракт

```python
list_items(zone: str | None = None) -> list[ChecklistItem]
list_zones() -> list[Zone]
allowed_levels(code: str) -> list[str]
get_state(chat_id: int) -> Inspection | None
start_inspection(chat_id: int, unit: str, kind: str, report_lang: str) -> Inspection
add_finding(chat_id: int, code: str, level: str, zone: str, text: str) -> Finding
edit_finding(chat_id: int, n: int, **fields) -> Finding
drop_finding(chat_id: int, n: int) -> None
attach_photo(chat_id: int, n: int, file_id: str) -> None
score(chat_id: int) -> Score
```

`Score` строится разбором вывода `audit.py score`. Собственного расчёта в блоке нет и быть не может — это проверяется контрактом `engine-not-imported` в `lint-imports`.

Данные читаются из каталога `AUDIT_DATA_DIR`. Переменная не задана или каталог неполный — падение на старте с внятным сообщением.

## Зависимости

`engine-fix`.

## Definition of Done блока

- [ ] Чек-лист, зоны и допустимые классы читаются из `AUDIT_DATA_DIR`; отсутствие каталога — падение на старте, а не пустой чек-лист.
- [ ] Состояние хранится в папке на чат, запись атомарна, одновременная запись двух кадров альбома не теряет запись и не портит файл (тест обязателен).
- [ ] `add_finding` отклоняет дубль пары «код + зона» и класс, не разрешённый для этого пункта.
- [ ] `score` возвращает то же, что печатает `audit.py score`, — сверено тестом на данных `examples/`.
- [ ] Наличие форка `checklist_data/` в рабочем каталоге обнаруживается и приводит к отказу запуска.
- [ ] Три языковых поля (интерфейс, речь аудитора, отчёт) хранятся раздельно; версия чек-листа записана в проверку.
- [ ] `mypy --strict` чист, `lint-imports` зелёный.

## Статус

`todo`
