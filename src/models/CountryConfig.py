from enum import Enum

from pydantic import BaseModel, ConfigDict, Field
from pydantic.alias_generators import to_camel

# just to keep it with pythons naming convention, okay man?
class CamelModel(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)


DEFAULT_CLIENT_LIST_STATUS_FILTERS = ["Active", "Banned", "Deceased", "Abandoned"]
DEFAULT_GROUP_LIST_STATUS_FILTERS = ["Active", "Banned"]
YOUTH_MAX_AGE = 35  # GlobalConstants.YOUTH_MAX_AGE - confirm the value


class ComparisonType(str, Enum):
    EQUAL_TO = "equalTo"
    BETWEEN = "between"
    GREATER_THAN = "greaterThan"
    LESS_THAN = "lessThan"


class ClientStatValueOption(str, Enum):
    PERCENTAGE = "percentage"
    VALUE = "value"


class GroupActionsConfigs(CamelModel):
    enabled: bool = True
    create_group: bool = False
    show_group_qualification: bool = False
    show_group_summary_card: bool = False
    edit_group_leader: bool = False
    remove_group_member: bool = False
    edit_group_name: bool = False
    add_group_member: bool = False
    only_edit_older_group_name: bool = False
    months_to_old_group: int = 6

    @property
    def can_act_on_group(self) -> bool:
        return self.edit_group_leader or self.remove_group_member or self.edit_group_name or self.add_group_member


class ClientCreditScore(CamelModel):
    loan_product_type: str
    score: int
    client_id: int


class ClientStatusCombo(CamelModel):
    status: str
    sub_status: str


class EditClientOptions(CamelModel):
    editable_combo: list[ClientStatusCombo] = Field(default_factory=list)
    uneditable_combo: list[ClientStatusCombo] = Field(default_factory=list)


class ClientStatConfig(CamelModel):
    ui_label: str
    first_target: float
    second_target: float | None = None
    comparison: ComparisonType
    ui_label_text_color: str | None = None


class SiteReportOption(CamelModel):
    show_healthy_un_healthy: bool = False
    show_loans_overview: bool = True
    show_repayment_overview: int = 5
    show_overdue_clients_overview: int = 10
    show_orders_over_view: bool = True
    show_group_stat: bool = False
    latest_added_in_x_days: int | None = 28
    client_stat_value_option: ClientStatValueOption = ClientStatValueOption.PERCENTAGE
    client_stat_configs: list[ClientStatConfig] = Field(default_factory=list)


class IDSConfig(CamelModel):
    enabled: bool = False
    show_collection_details: bool = False
    allow_same_day_group_deliveries_after_summary: bool = False
    signature_collection_enabled: bool = False
    allow_collection_by_non_client: bool = False
    


class CountryConfig(CamelModel):
    """Per-country feature flags and business rules for the field app.

    One CountryConfig exists per country deployment. Top-level booleans turn
    features on or off (client/order create and edit, credit and loan checks,
    metrics, offline operations, photo liveness). Lists and strings set
    country-specific rules (currency, languages, order statuses, the unique
    client identifier field). Fields serialize with camelCase aliases.

    Nested configurations:

      input_distribution (IDSConfig) -- Input Distribution (IDS):
          The flow for handing over farm inputs (seed, fertilizer, etc.) that
          clients ordered on credit. If `enabled` is False, the whole IDS
          flow is off and every other IDS flag is ignored. It also deals with TMS which is all about declaring items received at the warehouse for distribution and at the end of distribution day, handles returns.
          It is not related to orders at all.
            - enabled: turns the input distribution flow on for this country.
            - show_collection_details: shows who collected a client's inputs
              and when.
            - allow_same_day_group_deliveries_after_summary: allows more
              deliveries to a group on the same day after that group's
              delivery summary has been generated.
            - signature_collection_enabled: requires the collector's signature
              as proof of receipt.
            - allow_collection_by_non_client: allows someone other than the
              client (e.g. a relative or group member) to collect on the
              client's behalf.

      group_actions_configs (GroupActionsConfigs):
          Group management actions a field officer may perform (create, rename,
          change leader, add/remove members). If `only_edit_older_group_name`
          is set, renaming is limited to groups older than
          `months_to_old_group` months.

      site_level_reports (SiteReportOption | None):
          Which site report cards are shown. None means no site reports.
          `client_stat_configs` define target-based stats, each with a label,
          one or two targets, and a comparison type. if empty, it means country opted out of it.

      edit_client_options (EditClientOptions | None):
          Status/sub-status combinations in which client editing is allowed
          (`editable_combo`) or blocked (`uneditable_combo`).
    """
    show_healthy_path_card: bool = False
    show_notification: bool = False
    create_client: bool = False
    edit_client: bool = False
    create_client_order: bool = False
    edit_client_order: bool = False
    currency: str = ""
    check_credit_score: bool = False
    check_client_has_loan: bool = False
    check_client_status: bool = False
    show_repayment_metrics: bool = False
    show_prepayments: bool = False
    combine_order_statuses: dict[str, list[str]] | None = None
    skipped_statuses: list[str] = Field(default_factory=list)
    show_product_recommendation_form: bool = False
    skip_product_recommendation_form: bool = False
    skip_client_insurance_form: bool = False
    use_order_fulfilment_status_field: bool = True
    self_cancelable_status: list[str] = Field(default_factory=lambda: ["pending", "draft", "awaiting_downpayment"])
    non_self_cancelable_status: list[str] = Field(default_factory=lambda: ["sent", "awaiting_fulfillment"])
    country_supported_languages: list[str] = Field(default_factory=list)
    core_loan_product_id: list[int] = Field(default_factory=list)
    allow_offline_operations: bool = False
    should_display_metrics: bool = True
    can_fo_manage_multiple_offices: bool = Field(False, alias="canFOManageMultipleOffices")
    group_actions_configs: GroupActionsConfigs = Field(default_factory=GroupActionsConfigs)
    flow_depends_on_client_status: str | None = None
    editable_statues: list[str] = Field(default_factory=list)
    allow_same_loan_product_for_statuses: list[str] = Field(default_factory=list)
    show_loan_health_status: bool = True
    default_credit_score_data: list[ClientCreditScore | None] | None = None
    unique_client_field: str = "nationalIdNumber"
    short_code: str = ""
    run_client_photo_confirmation: bool = False
    client_photo_liveness_mode: str = "HEAD_TURN"  # HEAD_TURN, FACE_MESH or NONE
    support_national_id_parsing: bool = Field(False, alias="supportNationalIDParsing")
    client_list_status_filters: list[str] = Field(default_factory=lambda: list(DEFAULT_CLIENT_LIST_STATUS_FILTERS))
    group_list_status_filters: list[str] = Field(default_factory=lambda: list(DEFAULT_GROUP_LIST_STATUS_FILTERS))
    edit_client_options: EditClientOptions | None = None
    max_youth_age: int = YOUTH_MAX_AGE
    hide_loan_products_of_type: list[str] = Field(default_factory=list)
    force_await_update_to_sync: bool | None = False
    site_level_reports: SiteReportOption | None = None
    capture_household_information: bool | None = False
    household_info_required: bool = False
    input_distribution: IDSConfig = Field(default_factory=IDSConfig)
    downpayment_percentage: float = 10.0