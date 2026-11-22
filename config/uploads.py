"""Shared checks for uploaded images (business logos, product photos)."""
from rest_framework import serializers

MAX_IMAGE_BYTES = 2 * 1024 * 1024
IMAGE_TYPES = {"image/png", "image/jpeg", "image/webp"}


def validate_image_upload(value, label: str = "The image"):
    """2 MB max, PNG/JPEG/WebP only. None (= remove the image) passes."""
    if value is None:
        return value
    if value.size > MAX_IMAGE_BYTES:
        raise serializers.ValidationError(f"{label} must be 2 MB or smaller.")
    content_type = getattr(value, "content_type", None)
    if content_type and content_type not in IMAGE_TYPES:
        raise serializers.ValidationError("Use a PNG, JPEG or WebP image.")
    return value
