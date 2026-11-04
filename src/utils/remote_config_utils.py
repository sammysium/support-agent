from typing import Any, Literal, get_args, get_origin

from pydantic import BaseModel

from src.enums.actions import RecommendedAction
from src.models.CountryConfig import CountryConfig
from src.utils.log import log_me

TICKET = RecommendedAction.CREATE_SERVICE_DESK_TICKET
RESTART = RecommendedAction.RESTART_DEVICE
ESCALATE = RecommendedAction.ESCALALTE_TO_DEVELOPER 



def _model_class(annotation: Any) -> type[BaseModel] | None:
    """Return the pydantic model inside an annotation like `X` or `X | None`."""
    for candidate in (annotation, *get_args(annotation)):
        if get_origin(candidate) is None and isinstance(candidate, type) and issubclass(candidate, BaseModel):
            return candidate
    return None


def _is_toggle_annotation(annotation: Any) -> bool:
    """True for fields that act as on/off switches: bool, list or dict (optionally `| None`)."""
    for candidate in (annotation, *get_args(annotation)):
        if candidate is bool or get_origin(candidate) in (list, dict):
            return True
    return False


def _feature_paths() -> list[str]:
    """Every on/off field of CountryConfig as a path: "create_client_order" at the
    root, "group_actions_configs.create_group" inside a nested section."""
    paths = []
    for name, field in CountryConfig.model_fields.items():
        section = _model_class(field.annotation)
        if section is not None:
            paths.extend(f"{name}.{child}" for child, f in section.model_fields.items() if _is_toggle_annotation(f.annotation))
        elif _is_toggle_annotation(field.annotation):
            paths.append(name)
    return paths


# The agent picks from this list instead of guessing which section a feature lives in.
FEATURE_PATHS = _feature_paths()
FeatureKey = Literal[*FEATURE_PATHS]


def is_feature_enabled(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    if isinstance(value, (list, dict)):
        return len(value) > 0
    return None


def feature_is_enabled(config: CountryConfig | None, feature_path: str) -> bool | None:
    """Resolve a feature's on/off state from one config (device or live remote).

    Returns None when the config doesn't contain the field, e.g. a stale device
    config from before the field existed. Uses `model_fields_set`, so a field
    that pydantic filled in with its default counts as missing, not as False.
    """
    if config is None:
        return None

    parent_key, _, disabled_feature_key = feature_path.rpartition(".")
    target: BaseModel = config
    if parent_key:
        if parent_key not in config.model_fields_set:
            log_me(f"parent key {parent_key} not present in config")
            return None

        target = getattr(config, parent_key)
        if target is None:
            # Nullable section explicitly set to null (e.g. site_level_reports) means the whole section is off.
            log_me(f"{parent_key} is null; all its features are off")
            return False

        if getattr(target, "enabled", True) is False:
            # Master switch (input_distribution.enabled, group_actions_configs.enabled) hides every child feature.
            log_me(f"{parent_key}.enabled is False; all its features are off")
            return False

    if disabled_feature_key not in target.model_fields_set:
        log_me(f"{disabled_feature_key} not present under {parent_key or 'root'}")
        return None

    value = getattr(target, disabled_feature_key)
    log_me(f"found {disabled_feature_key}={value!r} under {parent_key or 'root'}")
    return is_feature_enabled(value)


REMOTE_CONFIG_DIAGNOSIS: dict[tuple[bool | None, bool | None], tuple[str, list[RecommendedAction]]] = {
    (True, True): (
        "The feature is enabled on your device and remotely, but it is still hidden. This is an application bug.",
        [ESCALATE],
    ),
    (False, True): (
        "The feature is enabled remotely but your device has not synced yet. Restart your device.",
        [RESTART],
    ),
    (None, True): (
        "The feature is enabled remotely but your device has an older config. Restart your device.",
        [RESTART],
    ),
    (True, False): (
        "The feature is disabled remotely. Your device still marks it as enabled but hides it, which is also a bug. "
        "You can request it via service desk.",
        [TICKET, ESCALATE],
    ),
    (False, False): (
        "The feature is disabled for your country. You can request it via service desk.",
        [TICKET],
    ),
    (None, False): (
        "The feature is disabled remotely. You can request it via service desk.",
        [TICKET],
    ),
    (True, None): (
        "The feature is enabled on your device but the remote setting could not be read, and it is still hidden. "
        "Restart your device; if it stays hidden this is an application bug.",
        [RESTART, ESCALATE],
    ),
    (False, None): (
        "The feature is disabled on your device and the remote setting could not be read. Restart your device to sync.",
        [RESTART, ESCALATE],
    ),
    (None, None): (
        "Neither your device nor the remote config contains this setting, so it cannot be diagnosed here.",
        [ESCALATE],
    ),
}
 