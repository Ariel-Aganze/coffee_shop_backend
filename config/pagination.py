from rest_framework.pagination import PageNumberPagination


class StandardPagination(PageNumberPagination):
    """
    Default list pagination: 20 per page; clients may ask for up to 100 with
    `?page_size=` (NFR-005). Screens that genuinely need everything at once
    (the POS catalogue) use dedicated unpaginated endpoints instead.
    """
    page_size = 20
    page_size_query_param = "page_size"
    max_page_size = 100
