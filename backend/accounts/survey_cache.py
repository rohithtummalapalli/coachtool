from __future__ import annotations

from datetime import datetime, timezone
from threading import RLock
from typing import Any

import pandas as pd

_LOCK = RLock()
_GLOBAL_SURVEY_DF = pd.DataFrame()
_SURVEY_DF_BY_USER: dict[str, pd.DataFrame] = {}
_UPDATED_AT_BY_USER: dict[str, datetime] = {}


def set_user_survey_dataframe(user_id: str, data: Any) -> pd.DataFrame:
    """Store survey data as DataFrame for a user and as latest global snapshot."""
    global _GLOBAL_SURVEY_DF
    df = pd.DataFrame(data if data is not None else [])
    now = datetime.now(timezone.utc)
    with _LOCK:
        _SURVEY_DF_BY_USER[user_id] = df
        _UPDATED_AT_BY_USER[user_id] = now
        _GLOBAL_SURVEY_DF = df
    return df


def get_user_survey_dataframe(user_id: str) -> pd.DataFrame:
    with _LOCK:
        return _SURVEY_DF_BY_USER.get(user_id, pd.DataFrame())


def get_global_survey_dataframe() -> pd.DataFrame:
    with _LOCK:
        return _GLOBAL_SURVEY_DF


def get_user_survey_updated_at(user_id: str) -> datetime | None:
    with _LOCK:
        return _UPDATED_AT_BY_USER.get(user_id)

