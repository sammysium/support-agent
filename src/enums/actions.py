from enum import Enum


class DataStage(str, Enum):
    app = "app"
    firebase = "firebase"
    couchbase = "couchbase"
    sync_gateway = "sync_gateway"
    fineract = "fineract"
    odoo = "odoo"

class RecommendedAction(str, Enum):
    RESTART_DEVICE  = "RESTART_DEVICE"
    CREATE_SERVICE_DESK_TICKET = "CREATE_SERVICE_DESK_TICKET"
    ESCALALTE_TO_DEVELOPER = "ESCALALTE_TO_DEVELOPER"
    LOG_IN_AGAIN = "LOG_IN_AGAIN"  # the device's saved sync password is out of date; logging in fetches it again

# SOURCE is the system the record comes from, searched once Couchbase has no match.
# keyed by the document name the model sends ("Client.displayName"); the name doubles as the query alias
DOCUMENT_TYPES = {
    "Client": {
        "DOC_TYPE": "fineract_client",
        "SOURCE": DataStage.fineract,
        "SELECTABLE_FIELDS": ["displayName", "accountNo", "nationalIdNumber", "syncTs", "officeId"],
        # match: "partial" is a case-insensitive contains, "exact" must equal the value
        "SEARCHABLE_FIELDS": {
            "displayName": {"match": "partial"},
            "accountNo": {"match": "exact"},
        },
        # the officer gives name and account number together; either one finding the client is enough
        "MATCH_ANY": True,
    },
    "Group": {
        "DOC_TYPE": "fineract_group",
        "SOURCE": DataStage.fineract,
        "SELECTABLE_FIELDS": ["name", "accountNo", "status", "syncTs", "officeId"],
        # group names are unique within a country
        "SEARCHABLE_FIELDS": {
            "name": {"match": "partial"},
        },
    },
    "Loan": {
        "DOC_TYPE": "fineract_loan",
        "SOURCE": DataStage.fineract,
        "SELECTABLE_FIELDS": ["accountNo", "loanProductName", "status", "principal", "totalOutstanding", "syncTs", "officeId"],
        # Loan.clientAccountNo is mostly empty, so a loan is found through its client
        "SEARCHABLE_FIELDS": {
            "accountNo": {"match": "exact"},
        },
    },
    "Repayment": {
        "DOC_TYPE": "fineract_repayment",
        "SOURCE": DataStage.fineract,
        "SELECTABLE_FIELDS": ["amount", "clientAccountNo", "mnoTransactionCode", "paymentDate", "syncTs", "officeId"],
        "SEARCHABLE_FIELDS": {
            "amount": {"match": "exact"},
            "clientAccountNo": {"match": "exact"},
            "mnoTransactionCode": {"match": "exact"},
        },
    },
    "ClientOrder": {
        "DOC_TYPE": "odoo_order",
        "SOURCE": DataStage.odoo,
        "SELECTABLE_FIELDS": ["orderRef", "orderId", "clientId", "status", "fulfillmentStatus", "loanProductName", "amountTotal", "createdDate", "syncTs", "officeId"],
        # orderRef and orderId are each unique; refs look like "FS66129" and officers often drop the prefix.
        # clientName is rarely set on orders, so a client's name or account number goes through the Client join
        "SEARCHABLE_FIELDS": {
            "orderRef": {"match": "partial"},
            "orderId": {"match": "exact"},
            "clientId": {"match": "exact"},
        },
        "MATCH_ANY": True,
    },
}

# search_guide_code -> the primary document, the one the officer can't find
SEARCH_GUIDES = {
    "find_client": "Client",
    "find_repayment": "Repayment",
    "find_group": "Group",
    "find_loan": "Loan",
    "find_order": "ClientOrder",
}

# how another document joins the primary, keyed by (primary, joined); aliases backticked as some are reserved words
JOINS = {
    ("Repayment", "Client"): "`Client`.clientId = `Repayment`.clientId",
    ("Loan", "Client"): "`Client`.clientId = `Loan`.clientId",
    ("ClientOrder", "Client"): "`Client`.clientId = `ClientOrder`.clientId",
}
