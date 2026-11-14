from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field


class SearchRequest(BaseModel):
    model_config = {'populate_by_name': True}

    query: str = Field(min_length=1, max_length=500)
    source_types: List[str] = Field(default_factory=lambda: ['news', 'image', 'video', 'forum_post', 'internal_data'])
    async_: bool = Field(default=False, alias='async')
    start_time: Optional[str] = None
    end_time: Optional[str] = None
    confidence: Optional[str] = None
    sentiment: Optional[str] = None
    entity_type: Optional[str] = None
    exclude_words: Optional[str] = None
    task_name: Optional[str] = None


class LoginRequest(BaseModel):
    username: str = Field(min_length=1, max_length=100)
    password: str = Field(min_length=1, max_length=500)


class ForgotPasswordRequest(BaseModel):
    username: str = Field(min_length=1, max_length=100)
    reset_token: str = Field(min_length=1, max_length=500)
    new_password: str = Field(min_length=8, max_length=500)


class ConfigRequest(BaseModel):
    model_config = {'extra': 'allow'}


class GraphQueryRequest(BaseModel):
    report_id: str = 'latest'
    query: str = ''
    relation_type: Optional[str] = None


class HistoryRequest(BaseModel):
    date: Optional[str] = None


class ReportUpdateRequest(BaseModel):
    model_config = {'extra': 'forbid'}

    status: Optional[str] = None
    risk_level: Optional[str] = None
    analyst_opinion: Optional[str] = None
    manual_conclusion: Optional[str] = None
    starred: Optional[bool] = None


class ReportDeleteRequest(BaseModel):
    report_ids: List[str] = Field(min_length=1, max_length=100)
