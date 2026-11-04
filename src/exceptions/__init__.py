
class AppException(Exception):
    status_code = 400

    def __init__(self, message: str = "Application exception"):
        super().__init__(message)
        self.message = message

class RemoteConfigNotFoundException(AppException):
    status_code = 400

    def __init__(self, message = "Remote configuration for the given country and envrionment not found"):
        super().__init__(message)

class DocumentNotFoundException(AppException):
    status_code = 404

    def __init__(self, doc_id: str):
        super().__init__(f"No document {doc_id} in Couchbase")

class DeviceDocumentMismatchException(AppException):
    status_code = 400

    def __init__(self, doc_id: str, device_doc_id: str):
        super().__init__(f"device_doc.id {device_doc_id} does not match doc_id {doc_id}")

class NoSearchInChatException(AppException):
    status_code = 400

    def __init__(self, chat_id: str):
        super().__init__(f"Chat {chat_id} has no record search to continue")

class FineractRequestException(AppException):
    status_code = 502

    def __init__(self, message: str):
        super().__init__(f"Fineract request failed: {message}")

class JiraRequestException(AppException):
    status_code = 502

    def __init__(self, message: str):
        super().__init__(f"Jira request failed: {message}")

class RecordNotOfferedException(AppException):
    status_code = 400

    def __init__(self, doc_id: str):
        super().__init__(f"{doc_id} isn't one of the records shown in this chat's last search")

class ChatNotFound(AppException):
    status_code = 404
    def __init__(self, chat_id: str):
        super().__init__(f"Chat {chat_id} not found")

class SyncGatewayRequestException(AppException):
    status_code = 502

    def __init__(self, message: str):
        super().__init__(f"Sync Gateway request failed: {message}")

class CouchbaseRequestException(AppException):
    status_code = 502

    def __init__(self, message: str):
        super().__init__(f"Couchbase request failed: {message}")
