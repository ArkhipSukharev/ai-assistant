from __future__ import annotations

import hashlib
import json
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerifyMismatchError
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.config import Settings
from app.database import AuditLog, AuthSession, Database, User, utcnow


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


class AuthService:
    def __init__(self, database: Database, settings: Settings) -> None:
        self.database = database
        self.settings = settings
        self.password_hasher = PasswordHasher()

    def hash_password(self, password: str) -> str:
        if len(password) < 10:
            raise ValueError("Пароль должен содержать минимум 10 символов")
        return self.password_hasher.hash(password)

    def verify_password(self, password_hash: str, password: str) -> bool:
        try:
            return self.password_hasher.verify(password_hash, password)
        except (VerifyMismatchError, InvalidHashError):
            return False

    async def bootstrap_admin(self) -> None:
        if not self.settings.admin_email or not self.settings.admin_password:
            return

        email = self.settings.admin_email.strip().lower()
        async with self.database.session_factory() as session:
            existing = await session.scalar(select(User).where(User.email == email))
            if existing:
                return
            user = User(
                name="Administrator",
                email=email,
                password_hash=self.hash_password(self.settings.admin_password),
                role="admin",
                is_active=True,
                request_limit_per_day=1000,
            )
            session.add(user)
            await session.commit()

    async def authenticate(self, email: str, password: str) -> tuple[User, str] | None:
        async with self.database.session_factory() as session:
            user = await session.scalar(
                select(User).where(User.email == email.strip().lower())
            )
            if not user or not user.is_active:
                return None
            if not self.verify_password(user.password_hash, password):
                return None

            token = secrets.token_urlsafe(48)
            auth_session = AuthSession(
                token_hash=_token_hash(token),
                user_id=user.id,
                expires_at=utcnow() + timedelta(days=self.settings.auth_session_days),
            )
            session.add(auth_session)
            await self._audit(session, user.id, "auth.login", user.id)
            await session.commit()
            return user, token

    async def logout(self, token: str | None) -> None:
        if not token:
            return
        async with self.database.session_factory() as session:
            await session.execute(
                delete(AuthSession).where(AuthSession.token_hash == _token_hash(token))
            )
            await session.commit()

    async def user_from_token(self, token: str | None) -> User | None:
        if not token:
            return None
        async with self.database.session_factory() as session:
            auth_session = await session.scalar(
                select(AuthSession)
                .options(selectinload(AuthSession.user))
                .where(AuthSession.token_hash == _token_hash(token))
            )
            if not auth_session:
                return None
            if _as_utc(auth_session.expires_at) <= utcnow():
                await session.delete(auth_session)
                await session.commit()
                return None
            if not auth_session.user.is_active:
                return None
            return auth_session.user

    async def user_by_telegram_id(self, telegram_id: int) -> User | None:
        async with self.database.session_factory() as session:
            return await session.scalar(
                select(User).where(
                    User.telegram_id == telegram_id,
                    User.is_active.is_(True),
                )
            )

    async def list_users(self) -> list[User]:
        async with self.database.session_factory() as session:
            result = await session.scalars(select(User).order_by(User.created_at.desc()))
            return list(result)

    async def create_user(
        self,
        *,
        actor_id: int,
        name: str,
        email: str,
        password: str,
        role: str,
        telegram_id: int | None,
        request_limit_per_day: int,
        amocrm_user_id: int | None = None,
    ) -> User:
        if role not in {"admin", "user"}:
            raise ValueError("Недопустимая роль")
        async with self.database.session_factory() as session:
            user = User(
                name=name.strip(),
                email=email.strip().lower(),
                password_hash=self.hash_password(password),
                role=role,
                telegram_id=telegram_id,
                amocrm_user_id=amocrm_user_id,
                request_limit_per_day=request_limit_per_day,
            )
            session.add(user)
            await session.flush()
            await self._audit(session, actor_id, "user.create", user.id)
            await session.commit()
            await session.refresh(user)
            return user

    async def update_user(
        self, user_id: int, actor_id: int, values: dict[str, Any]
    ) -> User | None:
        async with self.database.session_factory() as session:
            user = await session.get(User, user_id)
            if not user:
                return None
            if "role" in values and values["role"] not in {"admin", "user"}:
                raise ValueError("Недопустимая роль")
            if "password" in values and values["password"]:
                user.password_hash = self.hash_password(values.pop("password"))
            else:
                values.pop("password", None)
            for field in (
                "name",
                "email",
                "role",
                "telegram_id",
                "amocrm_user_id",
                "is_active",
                "request_limit_per_day",
            ):
                if field in values:
                    value = values[field]
                    if field == "email" and value:
                        value = value.strip().lower()
                    setattr(user, field, value)
            user.updated_at = utcnow()
            await self._audit(session, actor_id, "user.update", user.id, values)
            await session.commit()
            await session.refresh(user)
            return user

    async def consume_request(self, user_id: int) -> bool:
        async with self.database.session_factory() as session:
            user = await session.get(User, user_id)
            if not user or not user.is_active:
                return False
            now = utcnow()
            reset_at = _as_utc(user.requests_reset_at)
            if reset_at.date() != now.date():
                user.requests_today = 0
                user.requests_reset_at = now
            if user.requests_today >= user.request_limit_per_day:
                return False
            user.requests_today += 1
            await session.commit()
            return True

    async def recent_audit_logs(self, limit: int = 100) -> list[AuditLog]:
        async with self.database.session_factory() as session:
            result = await session.scalars(
                select(AuditLog).order_by(AuditLog.created_at.desc()).limit(limit)
            )
            return list(result)

    async def _audit(
        self,
        session: AsyncSession,
        actor_user_id: int | None,
        action: str,
        target_user_id: int | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        safe_details = dict(details or {})
        safe_details.pop("password", None)
        session.add(
            AuditLog(
                actor_user_id=actor_user_id,
                target_user_id=target_user_id,
                action=action,
                details=json.dumps(safe_details, ensure_ascii=False, default=str),
            )
        )
