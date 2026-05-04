import re


_SA_COUNTRY_CODE = "966"
_MOBILE_PREFIX_RE = re.compile(r"^05\d{8}$")


def normalize_phone(raw: str) -> str:
    """
    Normalize a Saudi phone number to the 12-digit international format: 9665xxxxxxxx

    Supported inputs:
      0555906901        -> 966555906901
      +966555906901     -> 966555906901
      00966555906901    -> 966555906901
      966555906901      -> 966555906901
    """
    phone = re.sub(r"[\s\-()]+", "", raw or "")

    if phone.startswith("+966"):
        phone = _SA_COUNTRY_CODE + phone[4:]
    elif phone.startswith("00966"):
        phone = _SA_COUNTRY_CODE + phone[5:]
    elif phone.startswith("0"):
        phone = _SA_COUNTRY_CODE + phone[1:]
    elif not phone.startswith(_SA_COUNTRY_CODE):
        phone = _SA_COUNTRY_CODE + phone

    if not re.match(r"^966\d{9}$", phone):
        raise ValueError(f"Invalid Saudi phone number: {raw!r} -> {phone!r}")

    return phone
