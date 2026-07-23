from __future__ import annotations

import asyncio
import json
import logging

from aiogram import Bot, Dispatcher, F
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.filters import Command, CommandStart
from aiogram.types import (
    BufferedInputFile,
    KeyboardButton,
    Message,
    ReplyKeyboardMarkup,
)

from app.config import Settings
from app.database import User
from app.services.agent import AgentService
from app.services.auth_service import AuthService
from app.services.chart_renderer import render_chart_png
from app.services.agent import REPORT_CONTENT_TYPE
from app.services.telegram_proxy import TelegramProxyService

logger = logging.getLogger(__name__)

QUICK_QUERIES = {
    "📊 Отчёт за месяц": "Сделай отчёт по продажам за текущий месяц",
    "📈 Воронка продаж": "Покажи конверсию по воронке за текущий месяц",
    "👥 Топ менеджеров": "Покажи топ-10 менеджеров за текущий месяц",
    "⏰ Просроченные задачи": "Покажи все просроченные невыполненные задачи",
}


class DealLookup(StatesGroup):
    waiting_for_id = State()


def main_menu_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [
                KeyboardButton(text="📊 Отчёт за месяц"),
                KeyboardButton(text="📈 Воронка продаж"),
            ],
            [
                KeyboardButton(text="👥 Топ менеджеров"),
                KeyboardButton(text="⏰ Просроченные задачи"),
            ],
            [KeyboardButton(text="🔎 Сделка по ID")],
            [
                KeyboardButton(text="🧹 Очистить диалог"),
                KeyboardButton(text="ℹ️ Помощь"),
            ],
        ],
        resize_keyboard=True,
        is_persistent=True,
        input_field_placeholder="Выберите действие или задайте вопрос",
    )


def cancel_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text="⬅️ Отмена")]],
        resize_keyboard=True,
        input_field_placeholder="Введите числовой ID сделки",
    )


class TelegramBotService:
    def __init__(
        self,
        settings: Settings,
        agent: AgentService,
        auth: AuthService,
        proxy_url: str | None = None,
    ) -> None:
        self._settings = settings
        self._agent = agent
        self._auth = auth
        session = AiohttpSession(proxy=proxy_url) if proxy_url else None
        self._bot = Bot(token=settings.telegram_bot_token, session=session)
        self._dp = Dispatcher()
        self._register_handlers()

    async def _account(self, user_id: int) -> User | None:
        allowed = self._settings.telegram_allowed_user_ids
        if allowed and user_id not in allowed:
            return None
        if not self._settings.telegram_require_account:
            return User(id=0, name="Public", email="", password_hash="")
        return await self._auth.user_by_telegram_id(user_id)

    async def _deny(self, message: Message) -> None:
        await message.answer(
            "Ваш Telegram не привязан к активному аккаунту.\n"
            f"Передайте администратору ID: {message.from_user.id}"
        )

    async def _process_query(
        self, message: Message, account: User, query: str
    ) -> None:
        if (
            not self._settings.f5ai_api_key
            or self._settings.f5ai_api_key == "sk-f5ai-..."
        ):
            await message.answer(
                "F5AI API пока не настроен. Обратитесь к администратору.",
                reply_markup=main_menu_keyboard(),
            )
            return
        if account.id and not await self._auth.consume_request(account.id):
            await message.answer(
                "Дневной лимит запросов исчерпан.",
                reply_markup=main_menu_keyboard(),
            )
            return

        session_id = f"tg:{message.from_user.id}"
        await message.chat.do("typing")
        try:
            _, reply, attachments = await self._agent.chat(
                query,
                session_id,
                user_id=account.id,
                user_role=account.role or "user",
            )
        except RuntimeError as exc:
            await message.answer(str(exc), reply_markup=main_menu_keyboard())
            return
        except Exception:
            logger.exception("Telegram chat failed")
            await message.answer(
                "Не удалось обработать запрос. Попробуйте позже.",
                reply_markup=main_menu_keyboard(),
            )
            return

        chunks = [
            reply[start : start + 4000]
            for start in range(0, len(reply), 4000)
        ] or [reply]
        for index, chunk in enumerate(chunks):
            await message.answer(
                chunk,
                reply_markup=main_menu_keyboard()
                if index == len(chunks) - 1
                else None,
            )

        for attachment in attachments:
            if attachment.content_type == "text/csv":
                await message.answer_document(
                    BufferedInputFile(
                        attachment.content.encode("utf-8"),
                        filename=attachment.filename,
                    )
                )
            elif attachment.content_type == REPORT_CONTENT_TYPE:
                try:
                    chart_png = render_chart_png(json.loads(attachment.content))
                except (json.JSONDecodeError, TypeError, ValueError):
                    logger.exception("Could not render Telegram report chart")
                    chart_png = None
                if chart_png:
                    await message.answer_photo(
                        BufferedInputFile(
                            chart_png, filename="report-chart.png"
                        )
                    )

    def _register_handlers(self) -> None:
        @self._dp.message(CommandStart())
        async def start_handler(message: Message) -> None:
            account = await self._account(message.from_user.id)
            if not account:
                await self._deny(message)
                return
            await message.answer(
                "Привет! Я F5 Assistant для amoCRM.\n\n"
                "Примеры запросов:\n"
                "• Сделай отчёт по сделкам за март\n"
                "• Сколько новых лидов за неделю?\n"
                "• Топ-3 менеджера по сумме сделок\n\n"
                "Используйте кнопки ниже или задайте вопрос своими словами.",
                reply_markup=main_menu_keyboard(),
            )

        @self._dp.message(Command("help"))
        @self._dp.message(F.text == "ℹ️ Помощь")
        async def help_handler(message: Message) -> None:
            if not await self._account(message.from_user.id):
                await self._deny(message)
                return
            await message.answer(
                "Я умею:\n"
                "• формировать отчёты по сделкам, воронке и менеджерам\n"
                "• показывать лиды, контакты и задачи\n"
                "• анализировать конкретную сделку по ID\n"
                "• отвечать на вопросы по данным amoCRM\n\n"
                "/menu — показать кнопки\n"
                "/clear — сбросить контекст диалога",
                reply_markup=main_menu_keyboard(),
            )

        @self._dp.message(Command("clear"))
        @self._dp.message(F.text == "🧹 Очистить диалог")
        async def clear_handler(message: Message) -> None:
            if not await self._account(message.from_user.id):
                await self._deny(message)
                return
            session_id = f"tg:{message.from_user.id}"
            await self._agent.clear_session(session_id)
            await message.answer(
                "Контекст диалога очищен.",
                reply_markup=main_menu_keyboard(),
            )

        @self._dp.message(Command("menu"))
        async def menu_handler(message: Message) -> None:
            if not await self._account(message.from_user.id):
                await self._deny(message)
                return
            await message.answer(
                "Выберите действие или задайте вопрос:",
                reply_markup=main_menu_keyboard(),
            )

        @self._dp.message(F.text == "🔎 Сделка по ID")
        async def lead_lookup_start(
            message: Message, state: FSMContext
        ) -> None:
            if not await self._account(message.from_user.id):
                await self._deny(message)
                return
            await state.set_state(DealLookup.waiting_for_id)
            await message.answer(
                "Введите числовой ID сделки amoCRM:",
                reply_markup=cancel_keyboard(),
            )

        @self._dp.message(
            DealLookup.waiting_for_id,
            F.text == "⬅️ Отмена",
        )
        async def lead_lookup_cancel(
            message: Message, state: FSMContext
        ) -> None:
            await state.clear()
            await message.answer(
                "Поиск сделки отменён.",
                reply_markup=main_menu_keyboard(),
            )

        @self._dp.message(DealLookup.waiting_for_id, F.text)
        async def lead_lookup_id(
            message: Message, state: FSMContext
        ) -> None:
            account = await self._account(message.from_user.id)
            if not account:
                await state.clear()
                await self._deny(message)
                return
            lead_id = message.text.strip()
            if not lead_id.isdigit():
                await message.answer(
                    "ID должен состоять только из цифр. Попробуйте ещё раз:",
                    reply_markup=cancel_keyboard(),
                )
                return
            await state.clear()
            await self._process_query(
                message,
                account,
                f"Проведи полный анализ сделки по ID {lead_id}",
            )

        @self._dp.message(F.text)
        async def text_handler(message: Message) -> None:
            account = await self._account(message.from_user.id)
            if not account:
                await self._deny(message)
                return
            query = QUICK_QUERIES.get(message.text, message.text)
            await self._process_query(message, account, query)

    async def start_polling(self) -> None:
        logger.info("Starting Telegram bot polling")
        while True:
            try:
                await self._dp.start_polling(self._bot)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.warning("Telegram polling failed; retrying in 30 seconds: %s", exc)
            else:
                logger.warning("Telegram polling stopped; restarting in 30 seconds")
            await asyncio.sleep(30)

    async def stop(self) -> None:
        await self._bot.session.close()

    async def send_report(
        self,
        chat_id: int,
        text: str,
        presentation: dict,
        files: list[tuple[bytes, str, str]],
    ) -> None:
        for start in range(0, len(text), 4000):
            await self._bot.send_message(chat_id, text[start : start + 4000])

        has_png = any(content_type == "image/png" for _, _, content_type in files)
        if not has_png:
            chart = render_chart_png(presentation)
            if chart:
                await self._bot.send_photo(
                    chat_id,
                    BufferedInputFile(chart, filename="report-chart.png"),
                )

        for content, filename, content_type in files:
            input_file = BufferedInputFile(content, filename=filename)
            if content_type == "image/png":
                await self._bot.send_photo(chat_id, input_file)
            else:
                await self._bot.send_document(chat_id, input_file)


class TelegramBotManager:
    def __init__(
        self,
        settings: Settings,
        agent: AgentService,
        auth: AuthService,
        proxy: TelegramProxyService,
    ) -> None:
        self._settings = settings
        self._agent = agent
        self._auth = auth
        self._proxy = proxy
        self._service: TelegramBotService | None = None
        self._task: asyncio.Task | None = None
        self._lock = asyncio.Lock()

    async def start(self) -> None:
        async with self._lock:
            await self._start_unlocked()

    async def restart(self) -> None:
        async with self._lock:
            await self._stop_unlocked()
            await self._start_unlocked()

    async def stop(self) -> None:
        async with self._lock:
            await self._stop_unlocked()

    async def send_report(
        self,
        chat_id: int,
        text: str,
        presentation: dict,
        files: list[tuple[bytes, str, str]],
    ) -> None:
        async with self._lock:
            if not self._service:
                raise RuntimeError("Telegram-бот не запущен")
            await self._service.send_report(chat_id, text, presentation, files)

    async def _start_unlocked(self) -> None:
        if not (
            self._settings.telegram_bot_enabled
            and self._settings.telegram_bot_token
        ):
            logger.warning("Telegram bot is disabled or token is missing")
            return
        config = await self._proxy.get_config()
        proxy_url = self._proxy.build_proxy_url(config)
        self._service = TelegramBotService(
            self._settings,
            self._agent,
            self._auth,
            proxy_url=proxy_url,
        )
        self._task = asyncio.create_task(self._service.start_polling())
        logger.info(
            "Telegram bot started%s",
            " through configured proxy" if proxy_url else " directly",
        )

    async def _stop_unlocked(self) -> None:
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
        if self._service:
            await self._service.stop()
            self._service = None
