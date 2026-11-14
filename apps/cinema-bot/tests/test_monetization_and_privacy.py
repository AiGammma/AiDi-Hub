import pytest
from conftest import RATINGS_CSV

from cinebot.adapters import kinopoisk_csv
from cinebot.config import Settings
from cinebot.domain.models import ConsentPurpose
from cinebot.domain.ports import PaymentEvent
from cinebot.services import entitlements as ent
from cinebot.services.export import ExportError, TasteExportService


async def _user_with_ratings(c, tg_id):
    user = await c.users.get_or_create(tg_id)
    await c.importer.import_rows(user.id, kinopoisk_csv.parse_ratings(RATINGS_CSV), "ratings")
    return user


# ---- entitlements -----------------------------------------------------------


async def test_monetization_off_means_unlimited_but_usage_is_recorded(container):
    user = await container.users.get_or_create(1)
    for _ in range(10):
        d = await container.entitlements.check(user.id, ent.PICK_TASTE)
        assert d.allowed and d.remaining is None
        await container.entitlements.record(user.id, ent.PICK_TASTE)
    # Switching monetization on makes the recorded usage count immediately.
    on = Settings(_env_file=None, monetization_enabled=True)
    svc = ent.EntitlementService(container.sm, on)
    assert (await svc.check(user.id, ent.PICK_TASTE)).allowed is False


async def test_free_plan_quota_and_owner_bypass(container):
    on = Settings(_env_file=None, monetization_enabled=True, owner_telegram_ids="999")
    svc = ent.EntitlementService(container.sm, on)
    user = await container.users.get_or_create(1)
    owner = await container.users.get_or_create(999)

    for _ in range(3):
        assert (await svc.check(user.id, ent.PICK_TASTE)).allowed
        await svc.record(user.id, ent.PICK_TASTE)
    blocked = await svc.check(user.id, ent.PICK_TASTE)
    assert not blocked.allowed and blocked.remaining == 0 and blocked.plan_code == "free"

    for _ in range(20):
        await svc.record(owner.id, ent.PICK_TASTE)
    assert (await svc.check(owner.id, ent.PICK_TASTE)).allowed


async def test_paid_subscription_lifts_limits_and_webhooks_are_idempotent(container):
    on = Settings(_env_file=None, monetization_enabled=True)
    svc = ent.EntitlementService(container.sm, on)
    user = await container.users.get_or_create(5)
    for _ in range(3):
        await svc.record(user.id, ent.PICK_TASTE)
    assert not (await svc.check(user.id, ent.PICK_TASTE)).allowed

    event = PaymentEvent("stars", "pay_1", user.id, "plus", 150, "XTR", 30)
    assert await container.subscriptions.activate(event) is True
    assert await container.subscriptions.activate(event) is False  # replayed webhook
    assert (await svc.plan_for(user.id)).code == "plus"
    assert (await svc.check(user.id, ent.PICK_TASTE)).allowed


# ---- privacy / export ---------------------------------------------------------


async def test_export_only_includes_consenting_users(container):
    a = await _user_with_ratings(container, 11)
    b = await _user_with_ratings(container, 12)
    await _user_with_ratings(container, 13)  # never consents
    for u in (a, b):
        await container.privacy.set_consent(u.id, ConsentPurpose.DATA_EXPORT, True)

    vectors = await container.exporter.vectors("ACME")
    assert len(vectors) == 2
    assert all("user" in v and "ratings" not in v for v in vectors)
    # No raw ids or person-level features leak
    flat = str(vectors)
    assert a.public_id not in flat and "director:" not in flat


async def test_pseudonyms_change_with_salt_and_salt_is_mandatory(container, settings):
    a = await _user_with_ratings(container, 21)
    await container.privacy.set_consent(a.id, ConsentPurpose.DATA_EXPORT, True)
    first = (await container.exporter.vectors("x"))[0]["user"]
    rotated = TasteExportService(container.sm, settings.model_copy(update={"export_salt": "other"}))
    assert (await rotated.vectors("x"))[0]["user"] != first
    unsalted = TasteExportService(container.sm, settings.model_copy(update={"export_salt": ""}))
    with pytest.raises(ExportError):
        await unsalted.vectors("x")


async def test_aggregate_suppresses_small_groups(container):
    users = [await _user_with_ratings(container, 30 + i) for i in range(2)]
    for u in users:
        await container.privacy.set_consent(u.id, ConsentPurpose.DATA_EXPORT, True)
    rows = await container.exporter.aggregate("x")
    assert rows and all(r["n_users"] >= 2 for r in rows)

    strict = TasteExportService(container.sm, container.settings.model_copy(update={"export_min_group_size": 3}))
    assert await strict.aggregate("x") == []


async def test_revoking_consent_and_deleting_remove_user_from_exports(container):
    u = await _user_with_ratings(container, 41)
    await container.privacy.set_consent(u.id, ConsentPurpose.DATA_EXPORT, True)
    assert len(await container.exporter.vectors("x")) == 1
    await container.privacy.set_consent(u.id, ConsentPurpose.DATA_EXPORT, False)
    assert await container.exporter.vectors("x") == []

    await container.privacy.set_consent(u.id, ConsentPurpose.DATA_EXPORT, True)
    await container.privacy.purge_user(u.id)
    assert await container.exporter.vectors("x") == []
    again = await container.users.get_or_create(41)
    assert again.id != u.id  # the telegram id was released; a fresh account starts empty
    assert (await container.profiles.load(again.id)).n_ratings == 0


async def test_exports_are_audited(container):
    from sqlalchemy import select

    from cinebot.db.models import ExportLog

    await container.exporter.aggregate("Acme Research")
    async with container.sm() as s:
        log = (await s.execute(select(ExportLog))).scalars().all()
    assert [(row.requested_by, row.dataset) for row in log] == [("Acme Research", "aggregate")]


async def test_payments_survive_account_deletion(container):
    from sqlalchemy import select

    from cinebot.db.models import Payment

    u = await container.users.get_or_create(51)
    await container.subscriptions.activate(PaymentEvent("stars", "p1", u.id, "plus", 100, "XTR", 30))
    await container.privacy.purge_user(u.id)
    async with container.sm() as s:
        pay = (await s.execute(select(Payment))).scalars().one()
    assert pay.user_id is None and pay.amount_minor == 100


async def test_my_data_export_contains_ratings(container):
    u = await _user_with_ratings(container, 61)
    data = await container.privacy.export_user_data(u.id)
    assert {t["title"] for t in data["titles"]} >= {"Inception", "Friends"}
