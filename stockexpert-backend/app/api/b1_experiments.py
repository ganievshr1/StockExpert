from typing import Literal
from fastapi import APIRouter, Depends
from fastapi.responses import Response
from pydantic import BaseModel, ConfigDict, Field, model_validator
from app.api.developer import developer
from app.db import get_db
from app.services import b1_experiments as service

router = APIRouter(prefix='/developer/b1', tags=['B1 архитектурные эксперименты'])


class ExperimentCreate(BaseModel):
    model_config = ConfigDict(extra='forbid')
    scenario: Literal['concurrent_10', 'sequential_10', 'single_user_parallel', 'confirmation']
    base_session_id: str
    requirement_session_id: str
    background_session_id: str | None = None
    campaign_title: str | None = Field(default=None, max_length=160)
    diagnostic_timeout_seconds: int = Field(default=120, ge=30, le=3600, strict=True)

    @model_validator(mode='after')
    def fixed_confirmation_timeout(self):
        if self.scenario == 'confirmation' and self.diagnostic_timeout_seconds != 120:
            raise ValueError('Полный протокол B1 использует фиксированный таймаут 120 секунд')
        return self


@router.get('/config')
def config(db=Depends(get_db), user=Depends(developer)):
    return service.session_options(db, user)


@router.get('/experiments')
def list_experiments(user=Depends(developer)):
    return {'items': service.list_experiments(user)}


@router.post('/experiments', status_code=202)
def create(payload: ExperimentCreate, db=Depends(get_db), user=Depends(developer)):
    return service.create(db, user, payload.model_dump())


@router.get('/experiments/{ident}')
def details(ident: str, user=Depends(developer)):
    return service.read_public(service.owned(ident, user))


@router.post('/experiments/{ident}/continue', status_code=202)
def resume(ident: str, user=Depends(developer)):
    service.owned(ident, user)
    service.queue(ident)
    return service.read_public(service.load(ident))


@router.post('/experiments/{ident}/cancel')
def cancel(ident: str, user=Depends(developer)):
    service.owned(ident, user)
    return service.cancel(ident)


@router.get('/experiments/{ident}/export')
def export(ident: str, format: Literal['json', 'csv', 'zip'] = 'zip', user=Depends(developer)):
    service.owned(ident, user)
    content, media_type, extension = service.export(ident, format)
    return Response(content, media_type=media_type,
                    headers={'Content-Disposition': f'attachment; filename="b1-{ident}.{extension}"'})
