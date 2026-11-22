import logging

from rest_framework.views import exception_handler
from rest_framework.response import Response
from rest_framework import status

logger = logging.getLogger(__name__)


def custom_exception_handler(exc, context):
    """
    Normalize all DRF error responses to a common shape:
    {
        "error": "...",
        "details": {...}   # optional
    }
    """
    response = exception_handler(exc, context)

    if response is None:
        # Unexpected crash: log the full traceback for us (NFR-020) and tell the
        # client nothing about internals (no exception text in the response).
        view = context.get("view")
        logger.exception(
            "Unhandled error in %s", view.__class__.__name__ if view else "API view", exc_info=exc,
        )
        return Response(
            {"error": "Internal server error", "details": {}},
            status=status.HTTP_500_INTERNAL_SERVER_ERROR,
        )

    if isinstance(response.data, dict):
        if "detail" in response.data:
            response.data = {
                "error": response.data["detail"],
                "details": {},
            }
        elif "error" not in response.data:
            response.data = {"error": "Validation error", "details": response.data}
    elif isinstance(response.data, list):
        response.data = {"error": "Validation error", "details": response.data}

    return response