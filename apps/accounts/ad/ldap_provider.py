"""Провайдер Active Directory по протоколу LDAP/LDAPS (библиотека ldap3).

Поддерживает:
  * простую привязку сервисной учёткой для чтения каталога;
  * постраничный поиск пользователей (paged results) — важно для домена
    на 400+ учётных записей;
  * LDAPS либо StartTLS;
  * инкрементальное чтение по атрибуту ``whenChanged``;
  * проверку пароля сотрудника через bind (SSO-вход в портал).
"""
from __future__ import annotations

import logging
import ssl
from datetime import datetime, timedelta, timezone as dt_timezone

from django.conf import settings

from ..utils import cn_from_dn, domain_from_base_dn, parse_ad_date, parse_ad_datetime
from .base import AdDirectory, AdError, AdGroupRecord, AdHealth, AdUser

logger = logging.getLogger(__name__)

try:  # ldap3 — единственная внешняя зависимость слоя AD
    from ldap3 import (
        ALL,
        BASE,
        NONE,
        SIMPLE,
        SUBTREE,
        Connection,
        Server,
        Tls,
    )
    from ldap3.core.exceptions import LDAPException
except ImportError:  # pragma: no cover - сработает только при урезанной установке
    ALL = BASE = NONE = SIMPLE = SUBTREE = None
    Connection = Server = Tls = None  # type: ignore[assignment]
    LDAPException = Exception  # type: ignore[assignment,misc]

USER_ATTRIBUTES = [
    "sAMAccountName",
    "userPrincipalName",
    "mail",
    "givenName",
    "sn",
    "middleName",
    "displayName",
    "cn",
    "title",
    "department",
    "company",
    "telephoneNumber",
    "mobile",
    "ipPhone",
    "physicalDeliveryOfficeName",
    "l",
    "employeeID",
    "employeeNumber",
    "whenCreated",
    "whenChanged",
    "userAccountControl",
    "objectGUID",
    "distinguishedName",
    "memberOf",
    "manager",
]

ACCOUNTDISABLE = 0x0002


class LdapAdProvider(AdDirectory):
    """Чтение каталога и проверка паролей через LDAP."""

    name = "ldap"

    def __init__(self, config: dict | None = None):
        self.config = dict(config or settings.AD)
        self._dn_cache: dict[str, str] = {}

    # -- низкий уровень ---------------------------------------------------
    def _tls(self):
        validate = ssl.CERT_REQUIRED if self.config.get("VERIFY_TLS") else ssl.CERT_NONE
        kwargs = {"validate": validate}
        ca_file = self.config.get("CA_CERT_FILE") or ""
        if ca_file:
            kwargs["ca_certs_file"] = ca_file
        return Tls(**kwargs)

    def _server(self):
        if Server is None:  # pragma: no cover
            raise AdError("Библиотека ldap3 не установлена — интеграция с AD недоступна")
        host = self.config.get("SERVER") or ""
        if not host:
            raise AdError("Не задан адрес контроллера домена (AD_SERVER)")
        return Server(
            host,
            port=int(self.config.get("PORT") or 389),
            use_ssl=bool(self.config.get("USE_SSL")),
            tls=self._tls(),
            get_info=NONE,
            connect_timeout=int(self.config.get("TIMEOUT") or 10),
        )

    def _service_connection(self):
        bind_dn = self.config.get("BIND_DN") or ""
        password = self.config.get("BIND_PASSWORD") or ""
        if not bind_dn or not password:
            raise AdError("Не заданы учётные данные сервисной записи (AD_BIND_DN / AD_BIND_PASSWORD)")
        try:
            conn = Connection(
                self._server(),
                user=bind_dn,
                password=password,
                authentication=SIMPLE,
                auto_bind=False,
                receive_timeout=int(self.config.get("TIMEOUT") or 10),
                raise_exceptions=False,
            )
            if not conn.bind():
                raise AdError(self._describe(conn, "Не удалось привязаться к каталогу сервисной учётной записью"))
            if self.config.get("START_TLS") and not self.config.get("USE_SSL"):
                if not conn.start_tls():
                    raise AdError(self._describe(conn, "Не удалось установить StartTLS"))
            return conn
        except AdError:
            raise
        except LDAPException as exc:
            raise AdError(f"Ошибка подключения к контроллеру домена: {exc}") from exc

    @staticmethod
    def _describe(conn, prefix: str) -> str:
        detail = ""
        if conn is not None and getattr(conn, "result", None):
            detail = f"{conn.result.get('description', '')}: {conn.result.get('message', '')}".strip(": ")
        return f"{prefix}. {detail}".strip()

    def _search(self, conn, base: str, ldap_filter: str, attributes: list[str], size_limit: int = 0):
        try:
            entries = conn.extend.standard.paged_search(
                search_base=base,
                search_filter=ldap_filter,
                search_scope=SUBTREE,
                attributes=attributes,
                paged_size=int(self.config.get("PAGE_SIZE") or 500),
                paged_cookie=None,
                generator=True,
                size_limit=size_limit,
            )
            yield from entries
        except LDAPException as exc:
            raise AdError(f"Ошибка поиска в каталоге: {exc}") from exc

    # -- маппинг ----------------------------------------------------------
    def _map_entry(self, entry) -> AdUser | None:
        attrs = entry.get("attributes") or {}
        username = _first(attrs.get("sAMAccountName"))
        if not username:
            upn = _first(attrs.get("userPrincipalName"))
            username = upn.split("@")[0] if upn else ""
        if not username:
            return None

        uac = _first(attrs.get("userAccountControl"))
        try:
            uac_value = int(uac) if uac is not None else 0
        except (TypeError, ValueError):
            uac_value = 0

        manager_dn = _first(attrs.get("manager")) or ""
        groups = [cn_from_dn(g) for g in _as_list(attrs.get("memberOf"))]
        group_dns = _as_list(attrs.get("memberOf"))
        guid = _first(attrs.get("objectGUID")) or ""

        return AdUser(
            username=username.strip(),
            dn=(_first(attrs.get("distinguishedName")) or entry.get("dn") or ""),
            guid=_guid_str(guid),
            email=(_first(attrs.get("mail")) or "").strip(),
            first_name=_first(attrs.get("givenName")) or "",
            last_name=_first(attrs.get("sn")) or "",
            middle_name=_first(attrs.get("middleName")) or "",
            display_name=_first(attrs.get("displayName")) or _first(attrs.get("cn")) or "",
            job_title=_first(attrs.get("title")) or "",
            department_name=_first(attrs.get("department")) or "",
            manager_username="",
            manager_dn=manager_dn,
            phone_internal=_first(attrs.get("ipPhone")) or "",
            phone_mobile=_first(attrs.get("mobile")) or _first(attrs.get("telephoneNumber")) or "",
            office=_first(attrs.get("physicalDeliveryOfficeName")) or "",
            city=_first(attrs.get("l")) or "",
            employee_no=_first(attrs.get("employeeID")) or _first(attrs.get("employeeNumber")) or "",
            hire_date=parse_ad_date(_first(attrs.get("whenCreated"))),
            is_enabled=not bool(uac_value & ACCOUNTDISABLE),
            groups=groups,
            group_dns=group_dns,
            when_changed=parse_ad_datetime(_first(attrs.get("whenChanged"))),
        )

    # -- публичный интерфейс ---------------------------------------------
    def fetch_users(self, since: datetime | None = None) -> list[AdUser]:
        base = self.config.get("USER_SEARCH_BASE") or self.config.get("BASE_DN")
        if not base:
            raise AdError("Не задана база поиска пользователей (AD_USER_SEARCH_BASE / AD_BASE_DN)")
        ldap_filter = self.config.get("USER_FILTER") or "(objectClass=user)"
        if since is not None:
            stamp = _ad_timestamp(since - timedelta(minutes=10))
            ldap_filter = f"(&{ldap_filter}(whenChanged>={stamp}))"

        users: dict[str, AdUser] = {}
        with self._service_connection() as conn:
            if conn is None:  # pragma: no cover
                raise AdError("Не удалось установить соединение с каталогом")
            for entry in self._search(conn, base, ldap_filter, USER_ATTRIBUTES):
                mapped = self._map_entry(entry)
                if mapped is not None:
                    users[mapped.username.lower()] = mapped
            # Резолвим руководителей в логины (DN -> sAMAccountName)
            manager_dns = {u.manager_dn for u in users.values() if u.manager_dn}
            if manager_dns:
                for dn, sam in self._resolve_manager_logins(conn, manager_dns).items():
                    self._dn_cache[dn.lower()] = sam
                for user in users.values():
                    if user.manager_dn:
                        user.manager_username = self._dn_cache.get(user.manager_dn.lower(), "")
        return list(users.values())

    def _resolve_manager_logins(self, conn, dns: set[str]) -> dict[str, str]:
        resolved: dict[str, str] = {}
        cache = {dn.lower(): dn for dn in dns}
        for dn in dns:
            if dn in self._dn_cache:
                resolved[dn] = self._dn_cache[dn]
        pending = [dn for dn in dns if dn not in resolved]
        if not pending:
            return resolved
        ldap_filter = "(|" + "".join(f"(distinguishedName={_escape(dn)})" for dn in pending) + ")"
        manager_dn = _first_dn(pending[0])
        base = manager_dn or self.config.get("BASE_DN") or ""
        try:
            found = self._search(conn, base, ldap_filter, ["sAMAccountName", "distinguishedName"])
        except AdError:
            logger.warning("Не удалось разрешить руководителей AD (база %s)", base)
            return resolved
        for entry in found:
            attrs = entry.get("attributes") or {}
            sam = _first(attrs.get("sAMAccountName"))
            dn = _first(attrs.get("distinguishedName")) or entry.get("dn") or ""
            if sam and dn:
                resolved[dn] = sam
                self._dn_cache[dn.lower()] = sam
        del cache
        return resolved

    def fetch_user(self, username: str) -> AdUser | None:
        base = self.config.get("USER_SEARCH_BASE") or self.config.get("BASE_DN")
        if not base:
            raise AdError("Не задана база поиска пользователей (AD_USER_SEARCH_BASE / AD_BASE_DN)")
        ldap_filter = (
            f"(&{self.config.get('USER_FILTER') or '(objectClass=user)'}"
            f"(sAMAccountName={_escape(username)}))"
        )
        with self._service_connection() as conn:
            for entry in self._search(conn, base, ldap_filter, USER_ATTRIBUTES, size_limit=1):
                user = self._map_entry(entry)
                if user is not None:
                    if user.manager_dn:
                        resolved = self._resolve_manager_logins(conn, {user.manager_dn})
                        user.manager_username = resolved.get(user.manager_dn, "")
                    return user
        return None

    def fetch_groups(self) -> list[AdGroupRecord]:
        base = self.config.get("GROUP_SEARCH_BASE") or self.config.get("BASE_DN")
        if not base:
            return []
        records: list[AdGroupRecord] = []
        try:
            with self._service_connection() as conn:
                for entry in self._search(
                    conn,
                    base,
                    "(&(objectCategory=group)(objectClass=group))",
                    ["cn", "description", "member", "distinguishedName"],
                ):
                    attrs = entry.get("attributes") or {}
                    dn = _first(attrs.get("distinguishedName")) or entry.get("dn") or ""
                    name = _first(attrs.get("cn")) or cn_from_dn(dn)
                    if not name:
                        continue
                    records.append(
                        AdGroupRecord(
                            dn=dn,
                            name=name,
                            description=_first(attrs.get("description")) or "",
                            member_count=len(_as_list(attrs.get("member"))),
                        )
                    )
        except AdError:
            logger.warning("Справочник групп AD недоступен", exc_info=True)
        return records

    def authenticate(self, username: str, password: str) -> bool:
        if Connection is None:  # pragma: no cover
            raise AdError("Библиотека ldap3 не установлена")
        if not password:
            return False
        login = self._principal(username)
        try:
            conn = Connection(
                self._server(),
                user=login,
                password=password,
                authentication=SIMPLE,
                auto_bind=False,
                receive_timeout=int(self.config.get("TIMEOUT") or 10),
                raise_exceptions=False,
            )
            if not conn.bind():
                result = conn.result or {}
                code = str(result.get("description", ""))
                if code in {"invalidCredentials", "invalidCredentials()"}:
                    return False
                raise AdError(self._describe(conn, "Каталог отклонил подключение"))
            if self.config.get("START_TLS") and not self.config.get("USE_SSL"):
                conn.start_tls()
            conn.unbind()
            return True
        except AdError:
            raise
        except LDAPException as exc:
            raise AdError(f"Ошибка проверки пароля в AD: {exc}") from exc

    def _principal(self, username: str) -> str:
        username = username.strip()
        if "\\" in username or "@" in username:
            return username
        netbios = self.config.get("DOMAIN_NETBIOS") or ""
        if netbios:
            return f"{netbios}\\{username}"
        domain = domain_from_base_dn(self.config.get("BASE_DN") or "")
        return f"{username}@{domain}" if domain else username

    def healthcheck(self) -> AdHealth:
        if Connection is None:  # pragma: no cover
            return AdHealth(False, "Библиотека ldap3 не установлена: pip install ldap3")
        try:
            with self._service_connection() as conn:
                info = conn.server.info
                return AdHealth(
                    True,
                    "Связь с каталогом установлена",
                    {
                        "server": self.config.get("SERVER"),
                        "port": self.config.get("PORT"),
                        "ssl": bool(self.config.get("USE_SSL")),
                        "base_dn": self.config.get("BASE_DN"),
                        "naming_contexts": list(getattr(info, "naming_contexts", []) or [])[:3],
                    },
                )
        except AdError as exc:
            return AdHealth(False, str(exc))
        except LDAPException as exc:
            return AdHealth(False, f"Ошибка LDAP: {exc}")


# ---------------------------------------------------------------------------
# вспомогательные функции
# ---------------------------------------------------------------------------
def _first(value):
    if isinstance(value, (list, tuple)):
        return value[0] if value else None
    return value


def _as_list(value) -> list[str]:
    if value is None:
        return []
    if isinstance(value, (list, tuple)):
        return [str(v) for v in value if v]
    return [str(value)]


def _first_dn(dn: str) -> str:
    """База поиска для резолва DN — контекст именования домена."""
    parts = [p for p in dn.split(",") if p.strip().upper().startswith("DC=")]
    return ",".join(parts)


def _guid_str(value) -> str:
    if not value:
        return ""
    if isinstance(value, (bytes, bytearray)):
        import uuid

        try:
            return str(uuid.UUID(bytes_le=bytes(value)))
        except (ValueError, TypeError):
            return bytes(value).hex()
    return str(value)


def _escape(value: str) -> str:
    """Экранирование спецсимволов LDAP-фильтра (RFC 4515)."""
    replacements = {
        "\\": "\\5c",
        "*": "\\2a",
        "(": "\\28",
        ")": "\\29",
        "\x00": "\\00",
        "/": "\\2f",
    }
    return "".join(replacements.get(ch, ch) for ch in value)


def _ad_timestamp(moment: datetime) -> str:
    if moment.tzinfo is not None:
        moment = moment.astimezone(dt_timezone.utc).replace(tzinfo=None)
    return moment.strftime("%Y%m%d%H%M%S") + ".0Z"
