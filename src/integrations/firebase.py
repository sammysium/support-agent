import json
from pathlib import Path
import firebase_admin
import requests
from firebase_admin import credentials
from src.models.FormulaConfig import FormulaConfig
from src.models.CountryConfig import CountryConfig

current_dir = Path(__file__).resolve().parent
service_account_path = current_dir / "firebase-service-account.json"

if not firebase_admin._apps:
    firebase_admin.initialize_app(credentials.Certificate(str(service_account_path)))

_app = firebase_admin.get_app()
URL = f"https://firebaseremoteconfig.googleapis.com/v1/projects/{_app.project_id}/remoteConfig"


def get_remote_parameters() -> dict:
    """All Remote Config parameters, in one request: read both configs from it instead of fetching twice."""
    token = _app.credential.get_access_token().access_token
    r = requests.get(URL, headers={"Authorization": f"Bearer {token}"}, timeout=20)
    r.raise_for_status()
    return r.json().get("parameters") or {}


def _parameter_json(parameters: dict, key: str):
    parameter = parameters.get(key)
    if parameter is None:
        return None
    return json.loads(parameter["defaultValue"]["value"])


def get_remote_country_config(country: str, user_env: str, parameters: dict | None = None) -> None | CountryConfig:
    """CONFIGS_<country>[<env>]; None if the country or env isn't configured."""
    parameters = parameters if parameters is not None else get_remote_parameters()
    env_config = (_parameter_json(parameters, f"CONFIGS_{country}") or {}).get(user_env.lower())
    return CountryConfig.model_validate(env_config) if env_config is not None else None


def get_remote_country_formula_config(country: str, parameters: dict | None = None) -> None | FormulaConfig:
    """FORMULAS_<country>; None if the country has no formulas (only some do)."""
    parameters = parameters if parameters is not None else get_remote_parameters()
    formula_content = _parameter_json(parameters, f"FORMULAS_{country}")
    return FormulaConfig.model_validate(formula_content) if formula_content is not None else None
