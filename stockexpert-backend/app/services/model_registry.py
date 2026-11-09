"""Server-owned model profiles and immutable, credential-free runtime snapshots."""
import hashlib
import json
import re
from typing import Literal
import httpx
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from app.core.config import settings


class LLMConfig(BaseModel):
    model_config = ConfigDict(frozen=True, extra='forbid')
    profile_id: str
    label: str
    provider: Literal['ollama', 'deepseek']
    model: str = Field(min_length=1, max_length=200)
    thinking: bool = False
    reasoning_effort: Literal['high', 'max'] = 'high'
    max_tokens: int = Field(ge=256, le=65536)
    num_ctx: int = Field(default=16384, ge=8192, le=32768)
    concurrency: int = Field(ge=1, le=16)
    timeout: int = Field(ge=1, le=3600)
    quantization: str | None = None

    def fingerprint(self):
        # Operational limits do not change the semantic identity of the extractor.
        keys = ('provider', 'model', 'thinking', 'reasoning_effort', 'max_tokens', 'num_ctx')
        data = self.model_dump(include=set(keys))
        return hashlib.sha256(json.dumps(data, sort_keys=True).encode()).hexdigest()


LOCAL_MODELS = (
    ('qwen25-7b-q4', 'Qwen2.5-7B-Instruct · Q4', 'qwen2.5:7b-instruct-q4_K_M', 'Q4_K_M'),
    ('qwen25-7b-q5', 'Qwen2.5-7B-Instruct · Q5', 'qwen2.5:7b-instruct-q5_K_M', 'Q5_K_M'),
    ('qwen3-8b', 'Qwen3-8B · без рассуждений', 'qwen3:8b-q4_K_M', 'Q4_K_M'),
    ('qwen25-14b', 'Qwen2.5-14B-Instruct · резерв', 'qwen2.5:14b-instruct-q4_K_M', 'Q4_K_M'),
)


def profiles():
    result = [LLMConfig(profile_id=pid, label=label, provider='ollama', model=model,
                        quantization=quant, thinking=False,
                        max_tokens=settings.OLLAMA_LLM_MAX_TOKENS,
                        num_ctx=settings.OLLAMA_LLM_NUM_CTX,
                        concurrency=settings.OLLAMA_LLM_CONCURRENCY,
                        timeout=settings.OLLAMA_LLM_TIMEOUT)
              for pid, label, model, quant in LOCAL_MODELS]
    model = settings.DEEPSEEK_MODEL or (settings.LLM_MODEL if settings.LLM_PROVIDER == 'deepseek' else 'deepseek-v4-pro')
    result.append(LLMConfig(profile_id='deepseek', label='DeepSeek · внешний API', provider='deepseek',
                           model=model, thinking=settings.DEEPSEEK_THINKING,
                           reasoning_effort=settings.DEEPSEEK_REASONING_EFFORT,
                           max_tokens=settings.LLM_MAX_TOKENS,
                           concurrency=settings.LLM_CONCURRENCY, timeout=settings.LLM_TIMEOUT))
    return result


def legacy_config(provider=None, model=None):
    provider = provider or settings.LLM_PROVIDER
    model = model or settings.LLM_MODEL
    return LLMConfig(profile_id='server-default', label='Прежняя настройка сервера · ' + model,
                     provider=provider, model=model,
                     thinking=settings.DEEPSEEK_THINKING if provider == 'deepseek' else False,
                     reasoning_effort=settings.DEEPSEEK_REASONING_EFFORT,
                     max_tokens=settings.LLM_MAX_TOKENS if provider == 'deepseek' else settings.OLLAMA_LLM_MAX_TOKENS,
                     num_ctx=settings.OLLAMA_LLM_NUM_CTX,
                     concurrency=settings.LLM_CONCURRENCY if provider == 'deepseek' else settings.OLLAMA_LLM_CONCURRENCY,
                     timeout=settings.LLM_TIMEOUT if provider == 'deepseek' else settings.OLLAMA_LLM_TIMEOUT)


def default_config():
    configured = profiles()
    if settings.LLM_DEFAULT_PROFILE:
        return get_profile(settings.LLM_DEFAULT_PROFILE)
    if settings.LLM_PROVIDER == 'deepseek':
        return configured[-1]
    aliases = {'qwen2.5:7b': 'qwen25-7b-q4', 'qwen2.5:14b': 'qwen25-14b', 'qwen3:8b': 'qwen3-8b'}
    for config in configured:
        if config.provider == 'ollama' and (config.model == settings.LLM_MODEL or config.profile_id == aliases.get(settings.LLM_MODEL)):
            return config
    return legacy_config()


def get_profile(profile_id):
    for config in profiles():
        if config.profile_id == profile_id:
            return config
    if profile_id == 'server-default' and not settings.LLM_DEFAULT_PROFILE:
        return legacy_config()
    raise ValueError('Неизвестная модель. Выберите модель из списка сервера.')


def session_config(db, session):
    if session.llm_config:
        return LLMConfig.model_validate(session.llm_config)
    # Old sessions can retain the known provider/model from an earlier run.
    # Other historic parameters were not recorded in 3.4 and cannot be reconstructed.
    from app.db import Job
    for job in db.scalars(select(Job).where(Job.session_id == session.id).order_by(Job.created_at.desc(), Job.id.desc())):
        if job.params.get('llm_config'):
            return LLMConfig.model_validate(job.params['llm_config'])
        runtime = job.params.get('runtime', {})
        if runtime.get('provider') in ('deepseek', 'ollama') and runtime.get('model'):
            return legacy_config(runtime['provider'], runtime['model'])
    return default_config()


def pin_job_config(db, session, job):
    if job.params.get('llm_config'):
        return LLMConfig.model_validate(job.params['llm_config'])
    config = session_config(db, session)
    if not session.llm_config:
        session.llm_config = config.model_dump()
    job.params = {**job.params, 'llm_config': config.model_dump()}
    return config


def selection_locked(db, session):
    from app.db import Job
    if session.normalization_method == 'regex':
        # Preparing columns and CPU H1 do not consume the H2 model.
        used = bool(db.scalar(select(Job.id).where(Job.session_id == session.id, Job.kind.in_(['taxonomy','full','match'])).limit(1)))
        return used or bool(not session.base_snapshot.get('reused_passports') and session.schema_doc.get('taxonomy_status') in ('review','approved'))
    started=bool(db.scalar(select(Job.id).where(Job.session_id == session.id).limit(1)))
    # Copied passports do not lock the model for a new requirement.
    if session.base_snapshot.get('reused_passports'):return started
    return bool(session.schema_doc or session.approved or started)


def probe_ollama():
    """Metadata only; never downloads weights or sends a document to a model."""
    try:
        with httpx.Client(timeout=5, follow_redirects=False) as client:
            tags = client.get(settings.OLLAMA_URL.rstrip('/') + '/api/tags')
            tags.raise_for_status()
            models = tags.json()['models']
            names = {m.get('name', m.get('model', '')) for m in models}
            version = None
            try:
                response = client.get(settings.OLLAMA_URL.rstrip('/') + '/api/version')
                response.raise_for_status()
                version = response.json().get('version')
            except (httpx.HTTPError, ValueError, KeyError, TypeError):
                pass
            return {'reachable': True, 'names': names, 'version': version}
    except (httpx.HTTPError, ValueError, KeyError, TypeError, AttributeError):
        return {'reachable': False, 'names': set(), 'version': None}


def availability(config, probe=None):
    if config.provider == 'deepseek':
        ready = bool(settings.DEEPSEEK_API_KEY.get_secret_value())
        return {'status': 'configured' if ready else 'key_missing', 'available': ready,
                'message': 'Ключ задан; доступ и баланс проверяются при запросе.' if ready else 'Администратору нужно задать DEEPSEEK_API_KEY на сервере.'}
    probe = probe if probe is not None else probe_ollama()
    if not probe['reachable']:
        return {'status': 'unreachable', 'available': False, 'message': 'Ollama недоступна. Проверьте контейнер и OLLAMA_URL.'}
    if config.model not in probe['names'] and config.model + ':latest' not in probe['names']:
        return {'status': 'not_installed', 'available': False, 'message': 'Модель ещё не загружена в Ollama.'}
    if config.model.split(':')[0] == 'qwen3':
        match = re.match(r'^(\d+)\.(\d+)\.(\d+)', probe.get('version') or '')
        if not match or tuple(map(int, match.groups())) < (0, 9, 0):
            return {'status': 'unsupported_version', 'available': False,
                    'message': 'Для явного отключения рассуждений Qwen3 нужна Ollama 0.9.0 или новее с доступным /api/version.'}
    return {'status': 'installed', 'available': True,
            'message': 'Модель загружена. Скорость и доступную память проверьте на пробной сессии.'}


def preflight(config):
    from app.services.llm import LLMError
    result = availability(config)
    if not result['available']:
        hint = f' Выполните: ollama pull {config.model}' if result['status'] == 'not_installed' else ''
        raise LLMError(result['message'] + hint)


def catalogue():
    configured = profiles()
    default = default_config()
    if default.profile_id not in {p.profile_id for p in configured}:
        configured.append(default)
    probe = probe_ollama()
    return {'default_profile_id': default.profile_id, 'ollama_version': probe['version'],
            'models': [{**p.model_dump(), 'external_processing': p.provider == 'deepseek',
                        'availability': availability(p, probe),
                        'pull_command': f'ollama pull {p.model}' if p.provider == 'ollama' else None}
                       for p in configured]}

