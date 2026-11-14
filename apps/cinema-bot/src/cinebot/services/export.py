"""Taste data export for third parties (the future data product).

Safeguards, all enforced here and not left to the caller:

* Only users with an active DATA_EXPORT consent, not deleted, with at least
  ``export_min_ratings`` ratings (thin profiles are the easiest to re-identify).
* User ids are pseudonymized with an operator-held salt. Rotate the salt and
  two exports can no longer be joined. The salt is mandatory.
* ``aggregate`` is the safe default: per-title statistics suppressed unless at
  least ``export_min_group_size`` distinct users contributed (k-anonymity).
* ``vectors`` exports each user's taste profile (feature weights), never raw
  per-title ratings. Per-title rating rows must be requested explicitly with
  ``include_ratings`` and should only be shared under a data-processing
  agreement, because a combination of a few ratings can identify a person.
* Every export is written to ``export_log``.
"""

from __future__ import annotations

import hashlib
import hmac
from collections import defaultdict
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from ..config import Settings
from ..db.models import Consent, ExportLog, TasteProfileRow, Title, User, UserTitle
from ..domain.models import ConsentPurpose


class ExportError(RuntimeError):
    pass


class TasteExportService:
    def __init__(self, sm: async_sessionmaker, settings: Settings) -> None:
        self._sm = sm
        self._s = settings

    def _pseudonym(self, public_id: str) -> str:
        if not self._s.export_salt:
            raise ExportError("Set CINEBOT_EXPORT_SALT before exporting (pseudonymization is mandatory).")
        return hmac.new(self._s.export_salt.encode(), public_id.encode(), hashlib.sha256).hexdigest()[:20]

    async def _eligible(self) -> list[tuple[int, str]]:
        """(user_id, public_id) of users who consented, are active and have enough ratings."""
        async with self._sm() as s:
            rows = (
                await s.execute(
                    select(User.id, User.public_id, TasteProfileRow.n_ratings)
                    .join(Consent, Consent.user_id == User.id)
                    .join(TasteProfileRow, TasteProfileRow.user_id == User.id)
                    .where(
                        Consent.purpose == ConsentPurpose.DATA_EXPORT.value,
                        Consent.granted.is_(True),
                        User.deleted_at.is_(None),
                        TasteProfileRow.n_ratings >= self._s.export_min_ratings,
                    )
                )
            ).all()
        return [(uid, pid) for uid, pid, _ in rows]

    async def aggregate(self, requested_by: str) -> list[dict[str, Any]]:
        eligible = await self._eligible()
        ids = [u for u, _ in eligible]
        stats: dict[tuple[str, int], list[int]] = defaultdict(list)
        meta: dict[tuple[str, int], str] = {}
        if ids:
            async with self._sm() as s:
                rows = (
                    await s.execute(
                        select(Title.media_type, Title.tmdb_id, Title.title, UserTitle.rating)
                        .join(UserTitle, UserTitle.title_id == Title.id)
                        .where(
                            UserTitle.user_id.in_(ids),
                            UserTitle.status == "rated",
                            UserTitle.rating.is_not(None),
                        )
                    )
                ).all()
            for mt, tid, name, rating in rows:
                stats[(mt, tid)].append(rating)
                meta[(mt, tid)] = name

        k = self._s.export_min_group_size
        out = [
            {
                "tmdb": f"{mt}:{tid}",
                "title": meta[(mt, tid)],
                "n_users": len(r),
                "mean_rating": round(sum(r) / len(r), 2),
            }
            for (mt, tid), r in sorted(stats.items())
            if len(r) >= k
        ]
        await self._log(requested_by, "aggregate", len(out), {"k": k, "users": len(ids)})
        return out

    async def vectors(self, requested_by: str, include_ratings: bool = False) -> list[dict[str, Any]]:
        eligible = await self._eligible()
        out: list[dict[str, Any]] = []
        async with self._sm() as s:
            for uid, public_id in eligible:
                prof = await s.get(TasteProfileRow, uid)
                if prof is None:
                    continue
                item: dict[str, Any] = {
                    "user": self._pseudonym(public_id),
                    "n_ratings": prof.n_ratings,
                    # Rounded and without person-level features (directors/cast
                    # names are fine to share in aggregate, but not as a personal fingerprint).
                    "weights": {
                        k: round(v, 2)
                        for k, v in (prof.weights or {}).items()
                        if k.split(":", 1)[0] in ("genre", "decade", "country", "lang", "media")
                    },
                }
                if include_ratings:
                    rows = (
                        await s.execute(
                            select(Title.media_type, Title.tmdb_id, UserTitle.rating)
                            .join(UserTitle, UserTitle.title_id == Title.id)
                            .where(UserTitle.user_id == uid, UserTitle.status == "rated")
                        )
                    ).all()
                    item["ratings"] = [{"tmdb": f"{m}:{i}", "r": r} for m, i, r in rows]
                out.append(item)
        await self._log(
            requested_by,
            "vectors+ratings" if include_ratings else "vectors",
            len(out),
            {"include_ratings": include_ratings},
        )
        return out

    async def _log(self, requested_by: str, dataset: str, rows: int, params: dict) -> None:
        async with self._sm() as s, s.begin():
            s.add(ExportLog(requested_by=requested_by, dataset=dataset, row_count=rows, params=params))
