-- 0010_retracted_inspections.sql
--
-- T210, T233: сданную проверку можно СНЯТЬ из истории. Неверный отчёт не
-- правится — заводится новый, а старый снимается (D086), и снимается пометкой,
-- а не удалением строки (D089).
--
-- ПОЧЕМУ ПОМЕТКОЙ. История проверок точки — документ. По пометке видно, что
-- проверка была и почему её больше нет; по пустому месту не видно ничего, и
-- через полгода никто не отличит «сняли по ошибке в отчёте» от «никогда не
-- приезжали». Пометка к тому же обратима, а удалённая строка — нет.
--
-- ЧТО ЗДЕСЬ НОВОГО ПО СУТИ. Появляется ВТОРАЯ роль. До этой миграции у базы
-- был ровно один непривилегированный принципал — `dodo_audit_app`, и все
-- запреты выражались словами «нельзя никому». Снятые проверки видны не всем
-- (D089), то есть впервые появляется право, которое у одного есть, а у
-- другого нет. Правами в Postgres владеют роли, поэтому заводится роль
-- `dodo_audit_admin`: администратор истории. Разграничение легло построчными
-- политиками — тем же способом, что запрет правки завершённой проверки в
-- `0004`, а не проверкой в коде: проверку в коде снимают вместе с кодом.
--
-- Раннер оборачивает файл в одну транзакцию сам — begin/commit здесь не нужны.

-- --- пометка снятия ----------------------------------------------------------
--
-- Две колонки, а не одна: причина снятия — обязательное поле (D089), и
-- «снято без причины» не бывает. Обязательность держат ограничения, а не
-- договорённость: `not null` тут не годится — у неснятой проверки причины нет
-- и быть не должно, поэтому обязательность выражена связью двух колонок.

alter table inspections add column retracted_at timestamptz;
alter table inspections add column retraction_reason text;

alter table inspections add constraint inspections_retraction_has_reason
    check ((retracted_at is null) = (retraction_reason is null));

alter table inspections add constraint inspections_retraction_reason_is_not_empty
    check (retraction_reason is null or btrim(retraction_reason) <> '');

comment on column inspections.retracted_at is
    'Когда проверку сняли из истории (D086, D089). NULL — проверка живая. '
    'Снятая строка не удаляется: видно, что проверка была и почему её больше '
    'нет. Обычной роли снятая проверка не видна вовсе — её прячет политика '
    'inspections_retracted_are_admin_only, а не фильтр в запросе.';
comment on column inspections.retraction_reason is
    'Почему проверку сняли. Обязательна ровно тогда, когда проверка снята: '
    'ограничение inspections_retraction_has_reason не даёт ни снятия без '
    'причины, ни причины у живой проверки. Пустая строка запрещена отдельно — '
    'она выглядела бы названной причиной, которой не прочитать.';

-- --- отпечаток: снятая проверка в сверке не участвует ------------------------
--
-- Уникальность отпечатка была сплошной, и это ровно то место, где снятие
-- сломало бы слив молча. Сценарий из решения: отчёт неверный → заводим новый,
-- старый снимаем. Если новая проверка содержательно совпадёт со снятой (а
-- она совпадёт, когда снимали не из-за содержимого — перепутали точку в
-- шапке, сняли и слили заново), сплошной уникальный индекс не пустил бы её:
-- `on conflict do nothing` вернул бы пустоту, а следом идущий поиск по
-- отпечатку не нашёл бы снятую строку (её прячет политика) — и слив упал бы
-- отказом «Postgres не вернул строку после INSERT», не сказав ни слова о
-- снятии.
--
-- Поэтому уникальность становится ЧАСТИЧНОЙ: отпечаток уникален среди живых
-- проверок. Снятая проверка выбывает из сверки, оставаясь в истории.

alter table inspections drop constraint inspections_source_fingerprint_key;

create unique index inspections_live_fingerprint_idx
    on inspections (source_fingerprint) where retracted_at is null;

comment on index inspections_live_fingerprint_idx is
    'Отпечаток содержимого уникален среди ЖИВЫХ проверок. Повторный слив той '
    'же проверки по-прежнему не создаёт дубль, а слив заново после снятия — '
    'создаёт новую строку, и это и есть «завели новый отчёт» (D086).';

-- --- кадры: отметка о том, что объект убран из хранилища ---------------------
--
-- Кадры снятой проверки убираются из хранилища (D089). Строка кадра при этом
-- остаётся — по той же причине, по какой остаётся строка проверки, — а факт
-- уборки записывается отдельной колонкой.
--
-- ПОЧЕМУ НЕ ЗАТИРАЕТСЯ `storage_path`. Затёртый путь превратил бы кадр в
-- «ещё не выгруженный»: выгрузка (`upload_photos`) берёт кадры по
-- `storage_path is null` и залила бы его обратно. Записанный путь — это ещё и
-- единственный ответ на вопрос «что именно убрали», а он нужен, если объект
-- придётся искать в резервной копии.
alter table photos add column purged_at timestamptz;

comment on column photos.purged_at is
    'Когда объект кадра убран из хранилища при снятии проверки (D089). '
    'NULL — объект на месте (или его там никогда не было: у невыгруженного '
    'кадра storage_path пуст). Путь остаётся записанным намеренно: затёртый, '
    'он сделал бы кадр «ещё не выгруженным», и выгрузка залила бы его обратно.';

-- --- роль администратора истории ---------------------------------------------
--
-- Заводится тем же способом и с той же проверкой, что роль приложения в
-- `0004`: роли в Postgres общие на кластер, а рядом живут одноразовые
-- тестовые базы, поэтому создание идёт через проверку существования. И так же
-- проверяется, что роль не всесильна: суперпользователь обходит RLS ВСЕГДА, и
-- разграничение видимости на такой роли выглядело бы работающим, не будучи им.
--
-- Без пароля намеренно — секретам не место в git; пароль ставит накат из
-- `DATABASE_RETRACTION_PASSWORD` (см. `src/db/migrate.py`), то есть из .env.

do $$
begin
    if not exists (select 1 from pg_roles where rolname = 'dodo_audit_admin') then
        create role dodo_audit_admin login;
    end if;
    if exists (
        select 1 from pg_roles
        where rolname = 'dodo_audit_admin' and (rolsuper or rolbypassrls)
    ) then
        raise exception
            'Роль администратора истории dodo_audit_admin обходит RLS '
            '(rolsuper/rolbypassrls). Тогда разграничение видимости снятых '
            'проверок не значит ничего: суперпользователь обходит политики '
            'всегда. Снять эти атрибуты и повторить накат.';
    end if;
end
$$;

grant usage on schema public to dodo_audit_admin;

-- Читает администратор весь документ: шапку, находки, формулировки,
-- информационную часть, кадры — и точку, без которой у проверки нет названия.
grant select on table
    units, inspections, findings, photos, translations, inspection_info
    to dodo_audit_admin;

-- А ПИШЕТ РОВНО ДВЕ КОЛОНКИ, и это здесь главный заслон.
--
-- Политика такого сказать не может: `with check` видит только новую строку и
-- не видит старой, поэтому «менять разрешено только пометку» в ней не
-- выражается вовсе — выражается лишь «после правки строка обязана быть
-- снятой», а это пропустило бы `set pct = 100, retracted_at = now()` одним
-- запросом. Привилегия на КОЛОНКИ говорит ровно то, что нужно, и проверяется
-- одним взглядом в `\dp`.
grant update (retracted_at, retraction_reason) on inspections to dodo_audit_admin;
grant update (purged_at) on photos to dodo_audit_admin;

-- Ни INSERT, ни DELETE администратору не выдано: снятие — это пометка, а
-- заводит проверки приложение.

-- --- политики: что администратору вообще проходит ----------------------------
--
-- Разрешающие политики `0004`/`0009` выданы `to dodo_audit_app`, то есть на
-- новую роль не действуют, а с включённым RLS и без единой разрешающей
-- политики не проходит НИЧЕГО. Поэтому роли нужны свои — и они уже, чем у
-- приложения: только чтение плюс правка двух колонок.

create policy inspections_admin_reads on inspections
    for select to dodo_audit_admin using (true);
create policy inspections_admin_retracts on inspections
    for update to dodo_audit_admin using (true) with check (true);

create policy findings_admin_reads on findings
    for select to dodo_audit_admin using (true);
create policy translations_admin_reads on translations
    for select to dodo_audit_admin using (true);
create policy inspection_info_admin_reads on inspection_info
    for select to dodo_audit_admin using (true);

create policy photos_admin_reads on photos
    for select to dodo_audit_admin using (true);
create policy photos_admin_purges on photos
    for update to dodo_audit_admin using (true) with check (true);

-- --- политики: снятая проверка видна только администратору --------------------
--
-- `as restrictive` и без `to <роль>` — по тем же двум причинам, что в `0004`:
-- разрешающие политики объединяются через ИЛИ, поэтому «сужающая»
-- разрешающая не сужает ничего; а запрет обязан действовать и на того, кто
-- придёт к базе потом (клиент партнёра, будущий веб), а не только на роль, о
-- которой мы подумали сегодня.
--
-- Исключение выражено через `pg_has_role`, а не через `current_user =
-- 'dodo_audit_admin'`: право обязано следовать за членством в роли, иначе
-- заведённая завтра вторая административная роль тихо ничего не увидит. Сама
-- роль членом себя является, поэтому для неё выражение истинно.
--
-- Членство приложение себе не выдаст: `grant` в роль требует прав на неё, и
-- у `dodo_audit_app` их нет. Это и делает разграничение заслоном, а не
-- договорённостью.

create policy inspections_retracted_are_admin_only on inspections
    as restrictive
    using (retracted_at is null or pg_has_role(current_user, 'dodo_audit_admin', 'member'))
    with check (retracted_at is null or pg_has_role(current_user, 'dodo_audit_admin', 'member'));

-- --- политики: кто и как снимает ---------------------------------------------
--
-- Заморозка завершённой проверки из `0004` запрещала правку целиком, поэтому
-- переписывается: снятие — это правка запечатанной строки, и другого способа
-- её записать нет.
--
-- Пропускается ровно один переход: незапечатанная строка (черновик слива, как
-- и раньше) — либо запечатанная и ещё не снятая, и только администратором.
-- Обратно `using` не пускает: у снятой строки `retracted_at` уже не NULL, то
-- есть снятие односторонне, как и печать.
--
-- `with check (true)` остаётся дословно тем же, что было, и по той же
-- причине: без него PostgreSQL применил бы `using` и к новой строке, и
-- запечатать черновик (draft → finalized) стало бы нельзя. Ограничивает
-- администратора не это выражение, а привилегия на колонки выше.

drop policy inspections_finalized_is_frozen on inspections;

create policy inspections_finalized_is_frozen on inspections
    as restrictive for update
    using (
        status <> 'finalized'
        or (retracted_at is null and pg_has_role(current_user, 'dodo_audit_admin', 'member'))
    )
    with check (true);

-- --- политики: тело документа живёт ровно постольку, поскольку видна шапка ----
--
-- ЗАСАДА, РАДИ КОТОРОЙ ЭТИ ЧЕТЫРЕ ПОЛИТИКИ И ЗАВЕДЕНЫ. Все заслоны `0004` и
-- `0009` на находках, формулировках, кадрах и информационной части написаны
-- как `not exists (select 1 from inspections i where ... status = 'finalized')`.
-- Такой запрет спрашивает у базы, ВИДНА ли запечатанная проверка, — а после
-- этой миграции снятая проверка обычной роли не видна. Значит `not exists`
-- становится истинным, и все три запрета (дописать, поправить, удалить)
-- разом открываются: тело снятой проверки стало бы правимым, притом молча.
-- Ровно тот случай, когда защита выглядит целой и не держит.
--
-- Поэтому добавляется заслон, закрывающийся при невидимой шапке, а не
-- открывающийся: строка тела проходит, только если её проверка ВИДНА
-- спрашивающему. Он же убирает находки и поля снятой проверки из чтения — их
-- незачем отдавать тому, кто не видит самой проверки.
--
-- Существующие политики при этом не переписываются: сужающие политики
-- объединяются через И, и добавленная рядом закрывает дыру целиком. Одна
-- новая мысль записана одной новой формой, а не размазана правками по девяти
-- выражениям.

create policy findings_follow_visible_inspection on findings
    as restrictive
    using (exists (select 1 from inspections i where i.id = findings.inspection_id))
    with check (exists (select 1 from inspections i where i.id = findings.inspection_id));

create policy photos_follow_visible_inspection on photos
    as restrictive
    using (exists (select 1 from inspections i where i.id = photos.inspection_id))
    with check (exists (select 1 from inspections i where i.id = photos.inspection_id));

create policy inspection_info_follow_visible_inspection on inspection_info
    as restrictive
    using (exists (select 1 from inspections i where i.id = inspection_info.inspection_id))
    with check (exists (select 1 from inspections i where i.id = inspection_info.inspection_id));

-- Формулировки принадлежат либо проверке (подпись оценки), либо находке
-- (текст и комментарий) — оба вида перечислены так же, как в `0004`. Строка
-- с любым другим `entity_type` не проходит вовсе: сегодня таких нет, а
-- заслон, который при неизвестном виде открывается, — это тот же тихий отказ,
-- от которого заведена вся эта пачка.
create policy translations_follow_visible_inspection on translations
    as restrictive
    using (exists (
        select 1 from inspections i
        where (translations.entity_type = 'inspection' and i.id = translations.entity_id)
           or (translations.entity_type = 'finding' and exists (
                 select 1 from findings f
                 where f.id = translations.entity_id and f.inspection_id = i.id))))
    with check (exists (
        select 1 from inspections i
        where (translations.entity_type = 'inspection' and i.id = translations.entity_id)
           or (translations.entity_type = 'finding' and exists (
                 select 1 from findings f
                 where f.id = translations.entity_id and f.inspection_id = i.id))));

-- --- политики: уборка кадра снятой проверки ----------------------------------
--
-- `photos_uploaded_only_once` из `0004` замораживает кадр, как только у него
-- появилась ссылка в хранилище, — и это правильно ровно до снятия: отметить
-- убранный объект стало бы нечем. Заслон расширяется одним исключением —
-- кадром СНЯТОЙ проверки, — а не снимается.
--
-- Исключение спрашивает `inspections` и потому действует только для того, кто
-- снятую проверку видит: обычной роли она не видна, `exists` для неё ложен, и
-- уборка кадров остаётся операцией администратора. Это не побочный эффект, а
-- то, что и требовалось: снятие целиком — работа управляющей компании.

drop policy photos_uploaded_only_once on photos;

create policy photos_uploaded_only_once on photos
    as restrictive for update
    using (
        uploaded_at is null
        or exists (
            select 1 from inspections i
            where i.id = photos.inspection_id and i.retracted_at is not null)
    )
    with check (true);
