from typing import Annotated, Literal, Union

from pydantic import Field

from src.models.CountryConfig import CamelModel


JsonPrimitive = str | int | float | bool | None


class Condition(CamelModel):
    type: Literal["condition"] = "condition"
    field: str
    operator: str
    value: JsonPrimitive


class Group(CamelModel):
    type: Literal["group"] = "group"
    connect_via: str
    children: list["FormulaNode"]


FormulaNode = Annotated[Union[Condition, Group], Field(discriminator="type")]
Group.model_rebuild()  # resolve the recursive "FormulaNode" reference


class FilterConfig(CamelModel):
    filter: FormulaNode | None = None


class LoanSummaryConfig(CamelModel):
    total_by_field: str | None = None
    filter: FormulaNode | None = None


class GroupClientAggregationConfig(CamelModel):
    days_window: int | None = None
    filter: FormulaNode | None = None


class SiteSummaryFormulasConfig(CamelModel):
    group_totals: GroupClientAggregationConfig | None = None
    client_totals: GroupClientAggregationConfig | None = None
    grouped_healthy_un_healthy_clients: FilterConfig | None = None
    loan_summary: LoanSummaryConfig | None = None
    over_due_client: FilterConfig | None = None


class FormulaConfig(CamelModel):
    site_summary: SiteSummaryFormulasConfig | None = None