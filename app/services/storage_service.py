"""Payment slips live in a private bucket; viewers get short-lived signed URLs."""
SLIP_BUCKET = "santhai-slips"


def upload_slip(sb, path: str, data: bytes, content_type: str) -> str:
    sb.storage.from_(SLIP_BUCKET).upload(path, data, {"content-type": content_type})
    return path


def signed_slip_url(sb, value):
    """Stored value is a bucket path; legacy rows hold an old public URL."""
    if not value or value.startswith("http"):
        return value
    try:
        return sb.storage.from_(SLIP_BUCKET).create_signed_url(value, 3600)["signedURL"]
    except Exception as e:
        print(f"slip signing failed: {e}")
        return None
