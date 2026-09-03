-- 0004_finalized_write_protection.sql
--
-- T111: завершённую проверку нельзя переписать и нельзя удалить.
--
-- До этой миграции схема не защищала ничего. Проверено живым прогоном:
--     update inspections set pct = 100, grade = 'A+' where ... ;  -- UPDATE 1
--     delete from inspections where ... ;                         -- DELETE 1
-- Оба прошли без препятствий: ни триггеров, ни REVOKE, ни RLS. А проверка в
-- базе — это документ, который уже ушёл партнёру и стал основанием для
-- требований к нему (конституция, принцип 1).
--
-- ЗАСАДА, РАДИ КОТОРОЙ ЭТА МИГРАЦИЯ УСТРОЕНА ИМЕННО ТАК. Роль, под которой
-- идут и миграции, и приложение, — суперпользователь Postgres (дефолт
-- официального образа): `rolsuper=t`, `rolbypassrls=t`. Суперпользователь
-- обходит RLS ВСЕГДА. Политики, повешенные поверх такой роли, зелены и не
-- держат ничего — и об этом никто не узнает, потому что выглядит защитой.
-- Поэтому здесь сперва заводится отдельная непривилегированная роль
-- приложения, и только потом политики.
--
-- Раннер оборачивает файл в одну транзакцию сам — begin/commit здесь не нужны.

-- --- статус проверки ---------------------------------------------------------
--
-- Запрет держится СТАТУСОМ В САМОЙ ПОЛИТИКЕ, а не соглашением в коде и не
-- сплошным «нельзя ничего»: сплошной запрет невозможно отличить от
-- работающего правила, и через год никто не скажет, что именно он охраняет.
--
-- Оба значения настоящие, а не форма про запас. `push_inspection` кладёт
-- проверку как `draft` и запечатывает её в `finalized` последним действием той
-- же транзакции. Это не церемония: пока проверка `draft`, к ней дописываются
-- находки, формулировки и кадры; после печати дописать нельзя ничего — иначе
-- «переписать проверку» осталось бы возможным через добавление находки.

alter table inspections add column status text not null default 'finalized';

alter table inspections add constraint inspections_status_known
    check (status in ('draft', 'finalized'));

comment on column inspections.status is
    'draft — проверка собирается в базе (внутри транзакции слива); '
    'finalized — запечатана, дальше её нельзя ни переписать, ни удалить, ни '
    'дополнить. Переход односторонний: политики ниже пускают draft → finalized '
    'и не пускают обратно.';

-- --- роль приложения ---------------------------------------------------------
--
-- Роли в Postgres общие на кластер, а не на базу, поэтому создание идёт через
-- проверку существования: на этой же машине рядом живут одноразовые тестовые
-- базы, и вторая миграция обязана не спотыкаться о роль, заведённую первой.
--
-- Без пароля намеренно: пароль — секрет, а секретам не место в git
-- (конституция). Локально сюда ходят по peer/trust, на стенде пароль ставит
-- накат из `DATABASE_APP_PASSWORD` (см. `src/db/migrate.py`), то есть из .env.

do $$
begin
    if not exists (select 1 from pg_roles where rolname = 'dodo_audit_app') then
        -- Атрибуты по умолчанию: ни суперпользователя, ни bypassrls, ни права
        -- заводить роли и базы. Именно это здесь и нужно — явное перечисление
        -- только создало бы вид, будто в другой ветке бывает иначе.
        create role dodo_audit_app login;
    end if;
    -- Роль могли завести и раньше, руками, и с лишними атрибутами. Тогда все
    -- политики ниже — декорация, и узнать об этом надо сейчас, а не по факту
    -- переписанного отчёта. Снятие атрибутов здесь не делается намеренно:
    -- `nobypassrls` требует суперпользователя, которого на управляемой площадке
    -- (Supabase) у наката нет, и «попытались, не смогли, поехали дальше» — это
    -- ровно тот тихий отказ, от которого задача и заводилась.
    if exists (
        select 1 from pg_roles
        where rolname = 'dodo_audit_app' and (rolsuper or rolbypassrls)
    ) then
        raise exception
            'Роль приложения dodo_audit_app обходит RLS (rolsuper/rolbypassrls). '
            'Политики этой миграции не удержат ничего: суперпользователь обходит '
            'RLS всегда. Снять эти атрибуты и повторить накат.';
    end if;
end
$$;

grant usage on schema public to dodo_audit_app;

-- Справочник точек и арендаторов правится по делу: синоним заводят,
-- переименовывают, точку заводят при первом сливе. Замораживать тут нечего —
-- связь идёт по идентификатору (T092), а не по строке. Поэтому и политик здесь
-- нет, а решает привилегия: DELETE не выдан, потому что продукт не удаляет.
grant select, insert on tenants to dodo_audit_app;
grant select, insert, update on units to dodo_audit_app;
grant select, insert, update on unit_aliases to dodo_audit_app;

-- А здесь наоборот: полный набор прав выдан НАМЕРЕННО, и решает политика.
-- Иначе получилось бы два запрета друг поверх друга, и снятие политики ничего
-- бы не изменило — то есть проверить, что политика вообще работает, стало бы
-- нечем. Один заслон, который можно снять и увидеть красное, честнее двух, из
-- которых держит неизвестно какой.
grant select, insert, update, delete on inspections to dodo_audit_app;
grant select, insert, update, delete on findings to dodo_audit_app;
grant select, insert, update, delete on photos to dodo_audit_app;
grant select, insert, update, delete on translations to dodo_audit_app;

-- `schema_migrations` не отдаётся вовсе: историю схемы ведёт накат под
-- привилегированной ролью, приложение к ней отношения не имеет.

-- --- политики ----------------------------------------------------------------
--
-- `force` — потому что владелец таблицы иначе не подчиняется собственным
-- политикам. Сегодня владелец ещё и суперпользователь (он обойдёт RLS в любом
-- случае), но как только владельцем станет обычная роль, `force` окажется
-- единственным, что удержит её от правки чужого документа.
--
-- Разрешающая политика на роль приложения нужна отдельно: с включённым RLS и
-- без единой политики не проходит ничего, включая обычный слив.

alter table inspections enable row level security;
alter table inspections force row level security;
create policy inspections_app_access on inspections
    for all to dodo_audit_app using (true) with check (true);

alter table findings enable row level security;
alter table findings force row level security;
create policy findings_app_access on findings
    for all to dodo_audit_app using (true) with check (true);

alter table photos enable row level security;
alter table photos force row level security;
create policy photos_app_access on photos
    for all to dodo_audit_app using (true) with check (true);

alter table translations enable row level security;
alter table translations force row level security;
create policy translations_app_access on translations
    for all to dodo_audit_app using (true) with check (true);

-- Сужающие политики идут `as restrictive` и БЕЗ `to <роль>`.
--
-- `as restrictive` — потому что в PostgreSQL разрешающие политики объединяются
-- через ИЛИ: «сужающая» политика, добавленная разрешающей, не сужает ничего,
-- она просто ещё один способ разрешить. Ровно так выглядит зелёная защита,
-- которая не держит.
--
-- Без `to` — потому что запрет обязан действовать на всех, кто придёт к базе
-- потом (клиент партнёра через PostgREST, будущий веб), а не только на ту
-- роль, о которой мы подумали сегодня.
--
-- `with check (true)` у правки задан явно и это не небрежность: без него
-- PostgreSQL применил бы выражение `using` и к новой строке — и тогда
-- запечатать черновик (draft → finalized) стало бы невозможно, потому что
-- новая строка уже `finalized`. Заслон стоит на СТАРОЙ строке: тронуть
-- запечатанное нельзя, запечатать незапечатанное — можно.

create policy inspections_finalized_is_frozen on inspections
    as restrictive for update
    using (status <> 'finalized')
    with check (true);

create policy inspections_finalized_is_undeletable on inspections
    as restrictive for delete
    using (status <> 'finalized');

-- Находки, формулировки и кадры — это тело того же документа. Заморозить
-- только шапку значило бы оставить возможность переписать сам отчёт, поменяв
-- текст находки, — то есть половину защиты, которая выглядит целой.
--
-- INSERT здесь тоже сужён: дописать находку в запечатанную проверку — это то
-- же самое «переписать», только с другой стороны. Слив от этого не страдает,
-- потому что кладёт находки, пока проверка ещё `draft`.

create policy findings_frozen_with_inspection on findings
    as restrictive for update
    using (not exists (
        select 1 from inspections i
        where i.id = findings.inspection_id and i.status = 'finalized'))
    with check (true);

create policy findings_undeletable_with_inspection on findings
    as restrictive for delete
    using (not exists (
        select 1 from inspections i
        where i.id = findings.inspection_id and i.status = 'finalized'));

create policy findings_not_added_to_finalized on findings
    as restrictive for insert
    with check (not exists (
        select 1 from inspections i
        where i.id = findings.inspection_id and i.status = 'finalized'));

create policy translations_frozen_with_inspection on translations
    as restrictive for update
    using (not exists (
        select 1 from inspections i
        where i.status = 'finalized'
          and (
              (translations.entity_type = 'inspection' and i.id = translations.entity_id)
              or (translations.entity_type = 'finding' and exists (
                  select 1 from findings f
                  where f.id = translations.entity_id and f.inspection_id = i.id))
          )))
    with check (true);

create policy translations_undeletable_with_inspection on translations
    as restrictive for delete
    using (not exists (
        select 1 from inspections i
        where i.status = 'finalized'
          and (
              (translations.entity_type = 'inspection' and i.id = translations.entity_id)
              or (translations.entity_type = 'finding' and exists (
                  select 1 from findings f
                  where f.id = translations.entity_id and f.inspection_id = i.id))
          )));

create policy translations_not_added_to_finalized on translations
    as restrictive for insert
    with check (not exists (
        select 1 from inspections i
        where i.status = 'finalized'
          and (
              (translations.entity_type = 'inspection' and i.id = translations.entity_id)
              or (translations.entity_type = 'finding' and exists (
                  select 1 from findings f
                  where f.id = translations.entity_id and f.inspection_id = i.id))
          )));

create policy photos_not_added_to_finalized on photos
    as restrictive for insert
    with check (not exists (
        select 1 from inspections i
        where i.id = photos.inspection_id and i.status = 'finalized'));

create policy photos_undeletable_with_inspection on photos
    as restrictive for delete
    using (not exists (
        select 1 from inspections i
        where i.id = photos.inspection_id and i.status = 'finalized'));

-- Кадр — единственное исключение, и оно не поблажка, а следствие устройства:
-- выгрузка в хранилище (T094) идёт ПОСЛЕ завершения проверки, отдельными
-- транзакциями, и правит `storage_path`/`uploaded_at` уже у запечатанной.
-- Поэтому заслон здесь стоит не на статусе проверки, а на самом кадре: пока
-- ссылки в хранилище нет — кадр можно выгрузить, как только появилась — строка
-- заморожена. Переписать чужой кадр или подменить ссылку задним числом нельзя.
create policy photos_uploaded_only_once on photos
    as restrictive for update
    using (uploaded_at is null)
    with check (true);

comment on table photos is
    'Кадр находки. storage_path и uploaded_at заполняет выгрузка в хранилище '
    '(T094) уже после завершения проверки — политика photos_uploaded_only_once '
    'пускает это ровно один раз: выгруженный кадр заморожен.';
