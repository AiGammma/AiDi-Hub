"""Telegram handlers. Thin: parse input, call services, format output."""

from __future__ import annotations

import io
import json
import logging
from html import escape

from aiogram import Bot, F, Router
from aiogram.filters import Command, CommandObject
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    BufferedInputFile,
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)

from ..adapters import kinopoisk_csv
from ..app import Container
from ..domain.models import ConsentPurpose, Mode
from ..services import entitlements as ent
from ..services.directors import split_names
from ..services.taste import describe
from .formatting import pick_caption, poster_url, vocab_message

log = logging.getLogger(__name__)
router = Router()

MAX_UPLOAD_BYTES = 2_000_000


class Import(StatesGroup):
    ratings = State()
    wishlist = State()


HELP = (
    "<b>Что я умею</b>\n"
    "/import — загрузить оценки из Кинопоиска\n"
    "/wishlist — загрузить вишлист\n"
    "/directors Нолан, Вильнёв — любимые режиссёры\n"
    "/pick — подборка под твой вкус\n"
    "/english — подборка для практики американского английского\n"
    "/profile — что я понял про твой вкус\n"
    "/plan — тариф и лимиты\n"
    "/privacy — согласия на использование данных\n"
    "/my_data — выгрузить все мои данные\n"
    "/delete_me — удалить мои данные"
)


async def _user(message: Message | CallbackQuery, c: Container):
    return await c.users.get_or_create(message.from_user.id)


# ---- onboarding -----------------------------------------------------------


@router.message(Command("start"))
async def start(message: Message, command: CommandObject, c: Container) -> None:
    user = await c.users.get_or_create(message.from_user.id, referrer=(command.args or "")[:64] or None)
    await message.answer(
        "Привет! Я подберу фильмы и сериалы лично под тебя — по оценкам из Кинопоиска, "
        "вишлисту и любимым режиссёрам. А ещё могу выбирать то, на чём удобно "
        "тренировать американский английский.\n\n"
        "<b>Начни так:</b>\n"
        "1. /import — пришли файл с оценками\n"
        "2. /wishlist — пришли вишлист\n"
        "3. /directors — назови любимых режиссёров\n"
        "4. /pick или /english\n\n"
        + ("" if not user.is_new else "Данные используются только для твоих подборок; подробности в /privacy.")
    )


@router.message(Command("help"))
async def help_cmd(message: Message) -> None:
    await message.answer(HELP)


# ---- import ---------------------------------------------------------------


@router.message(Command("import"))
async def import_cmd(message: Message, state: FSMContext, c: Container) -> None:
    await _user(message, c)
    await state.set_state(Import.ratings)
    await message.answer(
        "Пришли файл (CSV/TXT) с оценками из Кинопоиска одним документом.\n"
        "Нужны колонки: <code>Название; Год; Моя оценка</code> "
        "(опционально — Оригинальное название, Дата). Разделитель — запятая, точка с запятой или таб.\n"
        "Отмена — /cancel."
    )


@router.message(Command("wishlist"))
async def wishlist_cmd(message: Message, state: FSMContext, c: Container) -> None:
    await _user(message, c)
    await state.set_state(Import.wishlist)
    await message.answer(
        "Пришли вишлист файлом (CSV/TXT) или просто текстом: по фильму на строку, "
        "например <code>Начало (2010)</code>. Отмена — /cancel."
    )


@router.message(Command("cancel"))
async def cancel(message: Message, state: FSMContext) -> None:
    await state.clear()
    await message.answer("Ок, отменила.")


async def _read_upload(message: Message, bot: Bot) -> bytes | None:
    doc = message.document
    if doc is None:
        return None
    if (doc.file_size or 0) > MAX_UPLOAD_BYTES:
        await message.answer("Файл слишком большой (лимит 2 МБ).")
        return None
    buf = io.BytesIO()
    await bot.download(doc, destination=buf)
    return buf.getvalue()


async def _run_import(message: Message, c: Container, rows, kind: str) -> None:
    user = await _user(message, c)
    if not rows:
        await message.answer("Не нашла в файле ни одной строки.")
        return
    status = await message.answer(f"Нашла {len(rows)} записей. Сопоставляю с базой фильмов…")

    async def progress(done: int, total: int) -> None:
        try:
            await status.edit_text(f"Сопоставляю с базой фильмов: {done}/{total}…")
        except Exception:  # message not modified / flood control: not worth failing the import
            pass

    report = await c.importer.import_rows(user.id, rows, kind, progress)  # type: ignore[arg-type]
    text = f"Готово: сопоставлено {report.matched} из {report.total}."
    if report.unmatched:
        shown = "\n".join(f"• {escape(t)}" for t in report.unmatched[:10])
        more = f"\n…и ещё {len(report.unmatched) - 10}" if len(report.unmatched) > 10 else ""
        text += f"\n\nНе нашла:\n{shown}{more}"
    await message.answer(text + ("\n\nТеперь /directors и /pick." if kind == "ratings" else ""))


@router.message(Import.ratings, F.document)
async def got_ratings(message: Message, bot: Bot, state: FSMContext, c: Container) -> None:
    data = await _read_upload(message, bot)
    if data is None:
        return
    try:
        rows = kinopoisk_csv.parse_ratings(data)
    except ValueError as e:
        await message.answer(str(e))
        return
    await state.clear()
    await _run_import(message, c, rows, "ratings")


@router.message(Import.wishlist, F.document)
async def got_wishlist_file(message: Message, bot: Bot, state: FSMContext, c: Container) -> None:
    data = await _read_upload(message, bot)
    if data is None:
        return
    try:
        rows = kinopoisk_csv.parse_wishlist(data)
    except ValueError as e:
        await message.answer(str(e))
        return
    await state.clear()
    await _run_import(message, c, rows, "wishlist")


@router.message(Import.wishlist, F.text & ~F.text.startswith("/"))
async def got_wishlist_text(message: Message, state: FSMContext, c: Container) -> None:
    rows = kinopoisk_csv.parse_wishlist(message.text)
    await state.clear()
    await _run_import(message, c, rows, "wishlist")


# ---- directors / profile ----------------------------------------------------


@router.message(Command("directors"))
async def directors_cmd(message: Message, command: CommandObject, c: Container) -> None:
    user = await _user(message, c)
    if not command.args:
        current = await c.directors.list(user.id)
        await message.answer(
            "Твои режиссёры: " + (", ".join(escape(d.name) for d in current) or "пока пусто")
            + "\n\nЗадать список: <code>/directors Кристофер Нолан, Дени Вильнёв</code>"
        )
        return
    names = split_names(command.args)
    resolved, unresolved = await c.directors.set_directors(user.id, names)
    text = "Запомнила: " + (", ".join(escape(p.name) for p in resolved) or "никого")
    if unresolved:
        text += "\nНе нашла: " + ", ".join(escape(n) for n in unresolved) + " (попробуй имя латиницей)"
    await message.answer(text)


@router.message(Command("profile"))
async def profile_cmd(message: Message, c: Container) -> None:
    user = await _user(message, c)
    profile = await c.profiles.load(user.id)
    if not profile.n_ratings:
        await message.answer("Пока нечего показывать — загрузи оценки через /import.")
        return
    likes, dislikes = describe(profile)
    await message.answer(
        f"По {profile.n_ratings} оценкам:\n\n"
        f"👍 <b>Тебе заходит:</b> {escape(', '.join(likes) or '—')}\n"
        f"👎 <b>Не твоё:</b> {escape(', '.join(dislikes) or '—')}"
    )


# ---- recommendations --------------------------------------------------------

PAYWALL = (
    "Лимит подборок на этой неделе исчерпан (тариф «{plan}»). "
    "Лимиты обновляются по понедельникам. Расширенный тариф скоро появится — /plan."
)


def _feedback_kb(rec_id: int) -> InlineKeyboardMarkup:
    def b(text: str, code: str) -> InlineKeyboardButton:
        return InlineKeyboardButton(text=text, callback_data=f"fb:{rec_id}:{code}")

    return InlineKeyboardMarkup(
        inline_keyboard=[
            [b("👍", "l"), b("👎", "d"), b("✅ Смотрел(а)", "w"), b("🔖 В вишлист", "s")]
        ]
    )


async def _send_picks(message: Message, c: Container, mode: Mode) -> None:
    user = await _user(message, c)
    feature = ent.PICK_TASTE if mode is Mode.TASTE else ent.PICK_ENGLISH
    decision = await c.entitlements.check(user.id, feature)
    if not decision.allowed:
        await message.answer(PAYWALL.format(plan=decision.plan_code))
        return

    profile = await c.profiles.load(user.id)
    if not profile.n_ratings and not await c.directors.list(user.id):
        await message.answer("Сначала загрузи оценки (/import) или назови режиссёров (/directors).")
        return

    wait = await message.answer("Подбираю…")
    llm_ok = c.has_llm and (await c.entitlements.check(user.id, ent.LLM_REASONS)).allowed
    picks = await c.recommender.recommend(user.id, mode, n=5, use_llm=llm_ok)
    await wait.delete()
    if not picks:
        await message.answer("Ничего подходящего не нашлось. Добавь оценки, вишлист или режиссёров.")
        return

    await c.entitlements.record(user.id, feature)
    if llm_ok:
        await c.entitlements.record(user.id, ent.LLM_REASONS)

    for i, p in enumerate(picks, 1):
        kb = _feedback_kb(p.recommendation_id) if p.recommendation_id else None
        caption = pick_caption(p, i)
        url = poster_url(p.meta.poster_path)
        try:
            if url:
                await message.answer_photo(url, caption=caption, reply_markup=kb)
            else:
                await message.answer(caption, reply_markup=kb)
        except Exception:  # poster fetch can fail; the text still matters
            await message.answer(caption, reply_markup=kb)
        vocab = vocab_message(p)
        if vocab:
            await message.answer(vocab)


@router.message(Command("pick"))
async def pick_cmd(message: Message, c: Container) -> None:
    await _send_picks(message, c, Mode.TASTE)


@router.message(Command("english"))
async def english_cmd(message: Message, c: Container) -> None:
    await _send_picks(message, c, Mode.ENGLISH)


_FEEDBACK = {"l": "liked", "d": "disliked", "w": "watched", "s": "saved"}
_FEEDBACK_ACK = {
    "liked": "Записала 👍",
    "disliked": "Больше такое не покажу 👎",
    "watched": "Отметила как просмотренное ✅",
    "saved": "Добавила в вишлист 🔖",
}


@router.callback_query(F.data.startswith("fb:"))
async def feedback(cb: CallbackQuery, c: Container) -> None:
    user = await _user(cb, c)
    _, rec_id, code = cb.data.split(":")
    fb = _FEEDBACK.get(code)
    ok = fb is not None and await c.recommender.record_feedback(user.id, int(rec_id), fb)
    await cb.answer(_FEEDBACK_ACK[fb] if ok else "Не получилось сохранить", show_alert=False)


# ---- plan / privacy / data --------------------------------------------------


@router.message(Command("plan"))
async def plan_cmd(message: Message, c: Container) -> None:
    user = await _user(message, c)
    plan = await c.entitlements.plan_for(user.id)
    lines = [f"Тариф: <b>{escape(plan.title)}</b>"]
    if not c.settings.monetization_enabled:
        lines.append("Сейчас всё без ограничений.")
    else:
        for feature, d in (await c.entitlements.usage_summary(user.id)).items():
            left = "∞" if d.remaining is None else str(d.remaining)
            lines.append(f"• {feature}: осталось {left}")
    await message.answer("\n".join(lines))


def _consent_kb(consents: dict[ConsentPurpose, bool]) -> InlineKeyboardMarkup:
    rows = []
    for purpose, label in (
        (ConsentPurpose.ANALYTICS, "Анонимная статистика"),
        (ConsentPurpose.DATA_EXPORT, "Делиться обезличенным вкусом с партнёрами"),
    ):
        on = consents.get(purpose, False)
        rows.append(
            [
                InlineKeyboardButton(
                    text=f"{'✅' if on else '⬜️'} {label}",
                    callback_data=f"consent:{purpose.value}:{0 if on else 1}",
                )
            ]
        )
    return InlineKeyboardMarkup(inline_keyboard=rows)


@router.message(Command("privacy"))
async def privacy_cmd(message: Message, c: Container) -> None:
    user = await _user(message, c)
    await message.answer(
        "<b>Приватность</b>\n"
        "Твои оценки и вишлист нужны только для твоих подборок. "
        "Дополнительное использование — только по твоему явному согласию, "
        "его можно отозвать в любой момент:",
        reply_markup=_consent_kb(await c.privacy.consents(user.id)),
    )


@router.callback_query(F.data.startswith("consent:"))
async def consent_toggle(cb: CallbackQuery, c: Container) -> None:
    user = await _user(cb, c)
    _, purpose, flag = cb.data.split(":")
    await c.privacy.set_consent(user.id, ConsentPurpose(purpose), flag == "1")
    await cb.message.edit_reply_markup(reply_markup=_consent_kb(await c.privacy.consents(user.id)))
    await cb.answer("Сохранено")


@router.message(Command("my_data"))
async def my_data(message: Message, c: Container) -> None:
    user = await _user(message, c)
    data = await c.privacy.export_user_data(user.id)
    payload = json.dumps(data, ensure_ascii=False, indent=2).encode()
    await message.answer_document(BufferedInputFile(payload, filename="my_data.json"))


@router.message(Command("delete_me"))
async def delete_me(message: Message) -> None:
    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="Да, удалить всё", callback_data="del:yes"),
                InlineKeyboardButton(text="Отмена", callback_data="del:no"),
            ]
        ]
    )
    await message.answer("Удалю оценки, вишлист, профиль вкуса и согласия. Это необратимо.", reply_markup=kb)


@router.callback_query(F.data.in_({"del:yes", "del:no"}))
async def delete_confirm(cb: CallbackQuery, c: Container) -> None:
    if cb.data == "del:yes":
        user = await _user(cb, c)
        await c.privacy.purge_user(user.id)
        await cb.message.edit_text("Готово, данные удалены. Если захочешь вернуться — /start.")
    else:
        await cb.message.edit_text("Ок, ничего не удаляю.")
    await cb.answer()
