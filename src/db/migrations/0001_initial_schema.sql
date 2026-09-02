-- 0001_initial_schema.sql
--
-- Схема блока db (T091): арендатор, точка, проверка, находка, кадр.
-- Оценку эта схема не считает нигде — сюда кладётся только то, что вернул
-- `audit.py score` (конституция, принцип 2). Формулировки хранятся строками
-- (entity_type, entity_id, field, lang) в таблице translations — сущности
-- связаны кодами, добавление языка не требует новой миграции (D025).
--
-- Раннер оборачивает файл в одну транзакцию сам — begin/commit здесь не нужны.

create table tenants (
    code text primary key,
    name text not null default '',
    created_at timestamptz not null default now()
);

comment on table tenants is
    'Арендатор — пространство управляющей компании или партнёра (D017). '
    'В MVP используется только код "default"; строки заводятся по факту '
    'первого использования, отдельного справочника нет.';

create table units (
    id uuid primary key default gen_random_uuid(),
    tenant_code text not null references tenants (code),
    name text not null,
    name_normalized text not null,
    created_at timestamptz not null default now(),
    unique (tenant_code, name_normalized)
);

comment on table units is
    'Точка со стабильным идентификатором. Название в MVP приходит текстом '
    '(D051) — справочник и карта синонимов добавляются задачей T092, после MVP.';
comment on column units.name_normalized is
    'Ключ сопоставления повторного ввода: обрезанный нижний регистр без '
    'внутренних лишних пробелов. Не подменяет будущий справочник синонимов.';

create table inspections (
    id uuid primary key default gen_random_uuid(),
    tenant_code text not null references tenants (code),
    unit_id uuid not null references units (id),
    chat_id bigint not null,
    kind text not null,
    inspection_date date not null,
    report_lang text not null,
    ui_lang text not null,
    speech_lang text not null,
    checklist_version text not null,
    auditor text not null default '',
    city text not null default '',
    partner text not null default '',
    contact text not null default '',
    pct numeric(5, 2) not null,
    grade text not null,
    deductions numeric(7, 2) not null default 0,
    counts jsonb not null default '{}'::jsonb,
    by_zone jsonb not null default '{}'::jsonb,
    repeat_of_id uuid references inspections (id),
    source_fingerprint text not null unique,
    pushed_at timestamptz not null default now()
);

comment on column inspections.pct is
    'Процент — только то, что вернул audit.py score. Пересчёта в базе нет '
    'и не будет (конституция, принцип 2; D033 — отчёт не пересчитывается задним числом).';
comment on column inspections.counts is
    'Score.counts движка как есть: число записей по классам D0..D3.';
comment on column inspections.by_zone is
    'Score.by_zone движка как есть: разбивка по зонам с долей, вычетом и остатком.';
comment on column inspections.repeat_of_id is
    'Связь с исходной проверкой той же точки. Поле заложено, логики удвоения '
    'вычета нет — функция снята решением D029 до следующего года. Домен пока '
    'не отдаёт эту связь, поэтому колонка всегда NULL до появления источника данных.';
comment on column inspections.source_fingerprint is
    'Отпечаток содержимого проверки (chat_id, точка, находки, оценка). '
    'Уникальный индекс на нём — гарантия того, что повторный push_inspection '
    'не создаёт вторую строку.';

create index inspections_unit_idx on inspections (unit_id);
create index inspections_chat_idx on inspections (chat_id);
create index inspections_tenant_idx on inspections (tenant_code);

create table findings (
    id uuid primary key default gen_random_uuid(),
    inspection_id uuid not null references inspections (id) on delete cascade,
    n integer not null,
    code text not null,
    level text not null,
    zone text not null,
    zone_unusual boolean not null default false,
    source text,
    created_at timestamptz not null default now(),
    unique (inspection_id, n)
);

comment on column findings.source is
    'Источник записи — со слов аудитора или самостоятельное распознавание по '
    'кадру (D044). NULL, пока domain не отдаёт это поле (задача T065): форма '
    'заложена заранее, значение появится без новой миграции.';
comment on column findings.zone_unusual is
    'Зона не из списка зон этого пункта методики — движок такую запись не '
    'отбрасывает, только помечает.';

create index findings_inspection_idx on findings (inspection_id);

create table photos (
    id uuid primary key default gen_random_uuid(),
    finding_id uuid not null references findings (id) on delete cascade,
    inspection_id uuid not null references inspections (id) on delete cascade,
    telegram_file_id text not null,
    storage_path text,
    uploaded_at timestamptz,
    created_at timestamptz not null default now()
);

comment on table photos is
    'Кадр находки, идентификатор телеграма без открытия наружу. '
    'storage_path и uploaded_at заполняет выгрузка в Storage (задача T094, '
    'не эта миграция) — до неё это мёртвая вовне ссылка, но живая в базе.';

create index photos_inspection_idx on photos (inspection_id);
create index photos_finding_idx on photos (finding_id);

create table translations (
    entity_type text not null,
    entity_id uuid not null,
    field text not null,
    lang text not null,
    text text not null,
    updated_at timestamptz not null default now(),
    primary key (entity_type, entity_id, field, lang)
);

comment on table translations is
    'Формулировки строками (entity_type, entity_id, field, lang) вместо '
    'колонки на язык (D025). Используется для текста и комментария находки '
    '(entity_type=finding) и для буквенной подписи оценки (entity_type=inspection, '
    'field=grade_label). Добавление языка — новая строка, не новая миграция.';
